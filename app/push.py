"""
Web push notifications (Sept) — phone notifications (not email, per
Jodie) for upcoming posts, each with a best-time-to-post recommendation
(see app/best_time.py) attached.

Pieces involved:
  - app/static/manifest.json + app/static/js/service-worker.js: what
    makes the site installable ("Add to Home Screen") and able to show a
    notification even when the site isn't open.
  - push_subscriptions table: one row per browser/device that's turned
    notifications on (see app/schema.sql).
  - This module: sending, and the daily reminder sweep.
  - /internal/send-post-reminders (app/routes/cron.py): what a Render
    Cron Job hits once a day to actually trigger the sweep — this app
    has no in-process background scheduler (same reasoning as
    scheduling.ensure_horizon_rolled_forward / social_stats.ensure_synced),
    and a push has to be sent from a real request, not "whenever someone
    next loads a page," so unlike those two this genuinely needs an
    external trigger rather than a piggyback-on-any-request check.

VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_CLAIM_EMAIL and
LOCAL_TIMEZONE are read from the environment — see .env.example.
"""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import current_app

from . import db
from . import best_time as best_time_module

LOCAL_TIMEZONE_ENV = "LOCAL_TIMEZONE"
DEFAULT_TIMEZONE = "America/New_York"


def local_tz():
    return ZoneInfo(os.environ.get(LOCAL_TIMEZONE_ENV, DEFAULT_TIMEZONE))


def local_today_iso():
    return datetime.now(local_tz()).date().isoformat()


def vapid_public_key():
    return os.environ.get("VAPID_PUBLIC_KEY", "")


def _vapid_private_key():
    return os.environ.get("VAPID_PRIVATE_KEY", "")


def _vapid_claim_email():
    email = os.environ.get("VAPID_CLAIM_EMAIL", "")
    return email if email.startswith("mailto:") else f"mailto:{email}" if email else "mailto:admin@example.com"


def push_configured():
    return bool(vapid_public_key() and _vapid_private_key())


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------
def save_subscription(user_id, endpoint, p256dh, auth, user_agent=""):
    existing = db.query_one("SELECT id FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    if existing:
        db.execute(
            "UPDATE push_subscriptions SET user_id = ?, p256dh = ?, auth = ?, user_agent = ? WHERE endpoint = ?",
            (user_id, p256dh, auth, user_agent, endpoint),
        )
        return existing["id"]
    return db.execute(
        "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, user_agent) VALUES (?, ?, ?, ?, ?)",
        (user_id, endpoint, p256dh, auth, user_agent),
    )


def remove_subscription(endpoint):
    db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))


def subscriptions_for_user(user_id):
    return db.rows_to_list(db.query("SELECT * FROM push_subscriptions WHERE user_id = ?", (user_id,)))


def has_subscription(user_id):
    return db.query_one("SELECT id FROM push_subscriptions WHERE user_id = ? LIMIT 1", (user_id,)) is not None


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
def _send_one(subscription, payload_json):
    """Isolated into its own function so tests can monkeypatch just this
    (pywebpush needs real VAPID keys and a real push service round-trip
    to actually work, neither of which a test run has). Returns
    (ok, status_code) — status_code is used to prune subscriptions the
    browser itself says are gone (404/410)."""
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        current_app.logger.warning("pywebpush isn't installed — can't send push notifications.")
        return False, None
    try:
        webpush(
            subscription_info={
                "endpoint": subscription["endpoint"],
                "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]},
            },
            data=payload_json,
            vapid_private_key=_vapid_private_key(),
            vapid_claims={"sub": _vapid_claim_email()},
            ttl=60 * 60 * 12,
        )
        return True, 201
    except WebPushException as e:
        status = e.response.status_code if getattr(e, "response", None) is not None else None
        current_app.logger.warning("Push send failed (status=%s): %s", status, e)
        return False, status


def send_to_user(user_id, title, body, url="/", tag=None):
    """Sends to every device this user has notifications on from. A dead
    subscription (browser returns 404/410 — uninstalled, permission
    revoked) is pruned immediately rather than retried forever."""
    if not push_configured():
        return 0
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag or "tgn-reminder"})
    sent = 0
    for sub in subscriptions_for_user(user_id):
        ok, status = _send_one(sub, payload)
        if ok:
            sent += 1
        elif status in (404, 410):
            remove_subscription(sub["endpoint"])
    return sent


def send_to_users(user_ids, title, body, url="/", tag=None):
    return sum(send_to_user(uid, title, body, url=url, tag=tag) for uid in dict.fromkeys(user_ids) if uid)


# ---------------------------------------------------------------------------
# Daily "post due today" reminder sweep
# ---------------------------------------------------------------------------
def _admin_user_ids():
    rows = db.rows_to_list(db.query(
        "SELECT u.id FROM users u JOIN roles r ON r.id = u.role_id WHERE r.is_admin = 1 AND u.active = 1"
    ))
    return [r["id"] for r in rows]


def _outputs_due_today():
    today_iso = local_today_iso()
    return db.rows_to_list(db.query(
        """SELECT o.*, c.title AS campaign_title, ct.label AS type_label, ct.default_platform_ids
           FROM content_outputs o
           JOIN campaigns c ON c.id = o.campaign_id
           JOIN content_types ct ON ct.id = o.content_type_id
           WHERE o.publish_date = ? AND o.reminder_sent_at IS NULL AND ct.category_key != 'custom'
           ORDER BY o.sort_order""",
        (today_iso,),
    ))


def _platform_keys_for(default_platform_ids_json):
    ids = db.from_json(default_platform_ids_json, default=[])
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = db.rows_to_list(db.query(f"SELECT key FROM platforms WHERE id IN ({placeholders})", tuple(ids)))
    return [r["key"] for r in rows]


def send_post_reminders():
    """What /internal/send-post-reminders calls once a day. For every
    post publishing today that hasn't been reminded about yet: work out
    a best time to post it and push that to whoever it's assigned to (or
    every admin, if nobody's assigned yet). Marks each output reminded
    either way, so a second cron fire the same day — or a retry after a
    failure — never sends the same reminder twice."""
    results = []
    for output in _outputs_due_today():
        platform_keys = _platform_keys_for(output["default_platform_ids"])
        hour, minute, reason = best_time_module.best_time_for_platforms(platform_keys)
        time_label = best_time_module.format_time_label(hour, minute)
        display_title = output["title"] or output["campaign_title"]
        title = f"Today: {display_title}"
        body = f"{output['type_label']} — best time to post: {time_label}. {reason}"
        recipient_ids = [output["assigned_user_id"]] if output["assigned_user_id"] else _admin_user_ids()
        sent = send_to_users(
            recipient_ids, title, body,
            url=f"/calendar/list?campaign={output['campaign_id']}",
            tag=f"tgn-post-{output['id']}",
        )
        db.execute(
            "UPDATE content_outputs SET reminder_sent_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(timespec="seconds"), output["id"]),
        )
        results.append({
            "output_id": output["id"], "title": display_title,
            "best_time": time_label, "recipient_ids": recipient_ids, "sent": sent,
        })
    return results
