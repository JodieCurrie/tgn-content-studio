"""
Web push notifications (Sept) — phone notifications (not email, per
Jodie) for upcoming posts, timed to arrive shortly before each one's
best-time-to-post recommendation (see app/best_time.py), with a "delay
to next best suggested time" action right on the notification.

Pieces involved:
  - app/static/manifest.json + app/static/js/service-worker.js: what
    makes the site installable ("Add to Home Screen") and able to show a
    notification (and act on its "delay" button) even when the site
    isn't open.
  - push_subscriptions table: one row per browser/device that's turned
    notifications on (see app/schema.sql).
  - This module: sending, the reminder sweep, and the delay action.
  - /internal/send-post-reminders (app/routes/cron.py): what a Render
    Cron Job hits every few minutes to actually trigger the sweep — this
    app has no in-process background scheduler (same reasoning as
    scheduling.ensure_horizon_rolled_forward / social_stats.ensure_synced),
    and a push has to be sent from a real request, not "whenever someone
    next loads a page," so unlike those two this genuinely needs an
    external trigger rather than a piggyback-on-any-request check.
  - /api/push/delay-reminder (app/routes/api.py): what the notification's
    own "delay" button calls, from the service worker, when tapped.

VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_CLAIM_EMAIL and
LOCAL_TIMEZONE are read from the environment — see .env.example.

Timing: a reminder isn't sent the moment a post is found due today —
it's held until REMINDER_LEAD_MINUTES before whichever slot is
currently "the" recommended time for it (app/best_time.py's earliest
candidate, the first time; a delayed-to slot after that), then sent.
Because there's no background scheduler, "shortly before" is only as
precise as how often the Render Cron Job fires (every 5 minutes, per
the README) — a reminder goes out on the first sweep at or after its
target-minus-lead mark, so in practice 5-10 minutes ahead of the
recommended time rather than exactly 10.
"""
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app

from . import db
from . import best_time as best_time_module

LOCAL_TIMEZONE_ENV = "LOCAL_TIMEZONE"
DEFAULT_TIMEZONE = "America/New_York"
REMINDER_LEAD_MINUTES = 10


def local_tz():
    return ZoneInfo(os.environ.get(LOCAL_TIMEZONE_ENV, DEFAULT_TIMEZONE))


def _local_now():
    """Isolated so tests can monkeypatch "now" without faking the clock."""
    return datetime.now(local_tz())


def local_today_iso():
    return _local_now().date().isoformat()


def _format_hm(hour, minute):
    return f"{hour:02d}:{minute:02d}"


def _parse_hm(text):
    hour_str, minute_str = text.split(":")
    return int(hour_str), int(minute_str)


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


def send_to_user(user_id, title, body, url="/", tag=None, extra=None):
    """Sends to every device this user has notifications on from. A dead
    subscription (browser returns 404/410 — uninstalled, permission
    revoked) is pruned immediately rather than retried forever. `extra`
    merges additional fields into the payload — e.g. output_id/actions
    for a reminder's "delay to next best time" button (see
    service-worker.js's push handler, which reads them back out)."""
    if not push_configured():
        return 0
    payload_dict = {"title": title, "body": body, "url": url, "tag": tag or "tgn-reminder"}
    if extra:
        payload_dict.update(extra)
    payload = json.dumps(payload_dict)
    sent = 0
    for sub in subscriptions_for_user(user_id):
        ok, status = _send_one(sub, payload)
        if ok:
            sent += 1
        elif status in (404, 410):
            remove_subscription(sub["endpoint"])
    return sent


def send_to_users(user_ids, title, body, url="/", tag=None, extra=None):
    return sum(
        send_to_user(uid, title, body, url=url, tag=tag, extra=extra)
        for uid in dict.fromkeys(user_ids) if uid
    )


# ---------------------------------------------------------------------------
# "Post due today" reminder sweep — fires ~10 min before its best time
# ---------------------------------------------------------------------------
def _admin_user_ids():
    rows = db.rows_to_list(db.query(
        "SELECT u.id FROM users u JOIN roles r ON r.id = u.role_id WHERE r.is_admin = 1 AND u.active = 1"
    ))
    return [r["id"] for r in rows]


def _outputs_due_today():
    """Posts due today not yet reminded about FOR THEIR CURRENT SLOT.
    reminder_sent_at is cleared by delay_reminder() when someone taps
    "delay to next best time," which is what lets the same output show
    up here again once its new, later slot approaches."""
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


def _output_by_id(output_id):
    return db.row_to_dict(db.query_one(
        """SELECT o.*, c.title AS campaign_title, ct.label AS type_label, ct.default_platform_ids
           FROM content_outputs o
           JOIN campaigns c ON c.id = o.campaign_id
           JOIN content_types ct ON ct.id = o.content_type_id
           WHERE o.id = ?""",
        (output_id,),
    ))


def _platform_keys_for(default_platform_ids_json):
    ids = db.from_json(default_platform_ids_json, default=[])
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = db.rows_to_list(db.query(f"SELECT key FROM platforms WHERE id IN ({placeholders})", tuple(ids)))
    return [r["key"] for r in rows]


def _target_for(output, platform_keys):
    """The output's current recommended (hour, minute) — its stored
    reminder_target_time if one's already been picked (including a
    previously delayed-to slot), else the earliest candidate, persisted
    on the way out so it stays stable across sweeps and delay requests."""
    if output.get("reminder_target_time"):
        return _parse_hm(output["reminder_target_time"])
    hour, minute, _reason = best_time_module.best_time_candidates_for_platforms(platform_keys)[0]
    db.execute(
        "UPDATE content_outputs SET reminder_target_time = ? WHERE id = ?",
        (_format_hm(hour, minute), output["id"]),
    )
    return hour, minute


def send_post_reminders():
    """What /internal/send-post-reminders calls every few minutes (see
    the README's Render Cron Job setup). For every post publishing today
    that hasn't been reminded about for its current slot: work out that
    slot's best time, and — once we're within REMINDER_LEAD_MINUTES of
    it — push a reminder to whoever it's assigned to (or every admin, if
    nobody's assigned yet), with a "delay to next best time" action.
    Marks each sent output reminded so a later sweep the same day never
    repeats it — until a delay clears that flag for the new slot."""
    results = []
    now = _local_now()
    for output in _outputs_due_today():
        platform_keys = _platform_keys_for(output["default_platform_ids"])
        hour, minute = _target_for(output, platform_keys)
        notify_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0) - timedelta(minutes=REMINDER_LEAD_MINUTES)
        if now < notify_at:
            continue  # not time yet — its target is set, but too early to notify
        reason = best_time_module.reason_for(hour, minute, platform_keys)
        time_label = best_time_module.format_time_label(hour, minute)
        display_title = output["title"] or output["campaign_title"]
        title = f"Coming up: {display_title}"
        body = (
            f"{output['type_label']} — best time to post is {time_label}, "
            f"about {REMINDER_LEAD_MINUTES} minutes from now. {reason}"
        )
        recipient_ids = [output["assigned_user_id"]] if output["assigned_user_id"] else _admin_user_ids()
        sent = send_to_users(
            recipient_ids, title, body,
            url=f"/calendar/list?campaign={output['campaign_id']}",
            tag=f"tgn-post-{output['id']}",
            extra={
                "output_id": output["id"],
                "actions": [{"action": "delay", "title": "Delay to next best time"}],
            },
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


def delay_reminder(output_id):
    """Called from /api/push/delay-reminder when someone taps "delay to
    next best time" on a reminder notification. Advances that output's
    recommended slot to the next candidate window (across its assigned
    platforms) later today, and clears reminder_sent_at so the sweep
    reminds about it again ~10 minutes before the new slot. Returns a
    small dict the service worker turns into a confirmation notification."""
    output = _output_by_id(output_id)
    if not output:
        return {"ok": False, "message": "Couldn't find that post — it may have been moved or deleted."}
    platform_keys = _platform_keys_for(output["default_platform_ids"])
    if output.get("reminder_target_time"):
        current_hour, current_minute = _parse_hm(output["reminder_target_time"])
    else:
        current_hour, current_minute, _reason = best_time_module.best_time_candidates_for_platforms(platform_keys)[0]
    nxt = best_time_module.next_candidate_after(current_hour, current_minute, platform_keys)
    if not nxt:
        return {
            "ok": False,
            "message": "That was the last suggested time for today — no later window to delay to.",
        }
    hour, minute, reason = nxt
    db.execute(
        "UPDATE content_outputs SET reminder_target_time = ?, reminder_sent_at = NULL WHERE id = ?",
        (_format_hm(hour, minute), output_id),
    )
    time_label = best_time_module.format_time_label(hour, minute)
    return {
        "ok": True,
        "new_time": time_label,
        "message": f"Delayed — next suggested time is {time_label}. {reason}",
    }
