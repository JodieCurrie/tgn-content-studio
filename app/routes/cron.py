"""
Endpoint(s) meant to be hit by an outside scheduler, not a logged-in
person — this app has no background worker/scheduler process of its own
(same situation as scheduling.py's rolling horizon and social_stats.py's
YouTube sync, except those two piggyback on "whenever someone next loads
a page," which doesn't work for a push notification that's supposed to
show up at a predictable time). A Render Cron Job hits this once a day —
see README "Notifications" for the exact setup.

Guarded by a shared secret (CRON_SECRET env var) instead of a login,
since the caller here is a server, not a browser with a session cookie.
"""
import os

from flask import Blueprint, request, jsonify

from .. import push as push_module

bp = Blueprint("cron", __name__, url_prefix="/internal")


def _authorized():
    expected = os.environ.get("CRON_SECRET", "")
    if not expected:
        return False  # never run unauthenticated — an unset secret means "not configured yet," not "open"
    provided = request.headers.get("X-Cron-Secret") or request.args.get("token") or ""
    return provided == expected


@bp.route("/send-post-reminders", methods=["POST", "GET"])
def send_post_reminders():
    if not _authorized():
        return jsonify({"error": "Not authorized."}), 403
    results = push_module.send_post_reminders()
    return jsonify({"ok": True, "reminders_sent": len(results), "results": results})
