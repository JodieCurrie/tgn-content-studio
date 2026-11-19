"""
Posting queue pages + API (Nov 2026) — see app/posting.py for the overall
flow. Everything lives under /post.
"""
import os
import tempfile
from datetime import date, timedelta

from flask import Blueprint, render_template, request, jsonify, g, Response, abort, redirect, url_for

from .. import db
from .. import google_integration as google
from .. import posting as posting_module
from .. import openai_images
from ..auth import login_required

bp = Blueprint("posting", __name__, url_prefix="/post")

ALLOWED_MEDIA_PREFIXES = ("video/", "image/")
MAX_COVER_BYTES = 10 * 1024 * 1024


def _can_approve():
    return bool(g.user["role_is_admin"] or g.user["role_can_manage_all_content"])


def _forbidden():
    return jsonify({"error": "Only an admin can approve or confirm posts."}), 403


# ---------------------------------------------------------------- pages
@bp.route("")
@login_required
def queue():
    today = posting_module.local_today()
    # Cheap and idempotent; means missed posts get moved (silently) even if
    # the cron sweep isn't running, the first time an admin opens this page.
    if g.user["role_is_admin"]:
        posting_module.reschedule_missed(today)
        posting_module.attach_approved_designs()
    upcoming = posting_module.list_outputs(today, today + timedelta(days=14))
    recent_posted = [
        o for o in posting_module.list_outputs(today - timedelta(days=14), today) if o["state"] == "posted"
    ]
    # Approved, day passed, never confirmed as posted — "did it go out?"
    unconfirmed = [
        o for o in posting_module.list_outputs(today - timedelta(days=14), today - timedelta(days=1))
        if o["state"] == "approved"
    ]
    needs_attention = [
        o for o in upcoming
        if o["state"] in ("needs_video", "awaiting") and not o["is_blank"]
        and o["publish_date"] <= (today + timedelta(days=2)).isoformat()
    ]
    days = {}
    for o in upcoming:
        days.setdefault(o["publish_date"], []).append(o)
    return render_template(
        "posting_queue.html", today=today.isoformat(), days=days,
        unconfirmed=unconfirmed, recent_posted=recent_posted[::-1][:10], needs_attention=needs_attention,
        google_connected=_google_connected(), can_approve=_can_approve(),
    )


def _google_connected():
    try:
        conn = google.get_connection()
        return bool(conn and conn.get("refresh_token"))
    except Exception:
        return False


@bp.route("/upload")
@login_required
def upload_page():
    outputs = posting_module.uploadable_outputs()
    selected = request.args.get("output", type=int)
    return render_template(
        "posting_upload.html", outputs=outputs, selected=selected, formats=posting_module.FORMAT_LABELS,
        google_connected=_google_connected(),
    )


@bp.route("/<int:output_id>")
@login_required
def review(output_id):
    output = posting_module.get_output(output_id)
    if not output:
        abort(404)
    all_platforms = db.rows_to_list(db.query("SELECT id, key, label FROM platforms ORDER BY sort_order, id"))
    chosen = {
        r["platform_id"] for r in db.query(
            "SELECT platform_id FROM output_platforms WHERE output_id = ?", (output_id,)
        )
    }
    hour, minute = posting_module.slot_time_for(output)
    from .. import best_time as best_time_module
    return render_template(
        "posting_review.html", o=output, platforms=all_platforms, chosen=chosen,
        formats=posting_module.FORMAT_LABELS, format_hints=posting_module.format_hints(output, [p["key"] for p in all_platforms if p["id"] in chosen] or output["platform_keys"]),
        google_connected_flag=_google_connected(), openai_enabled=openai_images.configured(),
        slot_label=best_time_module.format_time_label(hour, minute),
        can_approve=_can_approve(), is_image=(output.get("final_media_mime") or "").startswith("image/"),
        google_connected=_google_connected(),
    )


# ----------------------------------------------------------- media proxy
def _stream_drive_file(file_id, mime):
    range_header = request.headers.get("Range")
    try:
        upstream = google.open_drive_media(file_id, range_header)
    except google.GoogleNotConnected:
        abort(503)
    except Exception:
        abort(502)

    def generate():
        try:
            while True:
                chunk = upstream.read(256 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()

    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=300"}
    for h in ("Content-Length", "Content-Range"):
        if upstream.headers.get(h):
            headers[h] = upstream.headers[h]
    return Response(generate(), status=upstream.status, mimetype=mime or "application/octet-stream", headers=headers)


@bp.route("/<int:output_id>/media")
@login_required
def media(output_id):
    o = db.row_to_dict(db.query_one(
        "SELECT final_media_drive_id, final_media_mime, final_media_name FROM content_outputs WHERE id = ?", (output_id,)
    ))
    if not o or not o["final_media_drive_id"]:
        abort(404)
    resp = _stream_drive_file(o["final_media_drive_id"], o["final_media_mime"])
    if request.args.get("download"):
        resp.headers["Content-Disposition"] = f'attachment; filename="{o["final_media_name"] or "post"}"'
    return resp


@bp.route("/<int:output_id>/cover")
@login_required
def cover(output_id):
    o = db.row_to_dict(db.query_one("SELECT cover_drive_id FROM content_outputs WHERE id = ?", (output_id,)))
    if not o or not o["cover_drive_id"]:
        abort(404)
    return _stream_drive_file(o["cover_drive_id"], "image/jpeg")


# --------------------------------------------------------- upload API
@bp.route("/api/upload/start", methods=["POST"])
@login_required
def upload_start():
    data = request.get_json(force=True)
    output_id = data.get("output_id")
    filename = (data.get("filename") or "").strip() or "final-video"
    mime = (data.get("mime") or "").strip() or "video/mp4"
    size = int(data.get("size") or 0)
    if not output_id or not db.query_one("SELECT id FROM content_outputs WHERE id = ?", (output_id,)):
        return jsonify({"error": "Pick which post this is first."}), 400
    if not mime.startswith(ALLOWED_MEDIA_PREFIXES):
        return jsonify({"error": "That doesn't look like a video or image file."}), 400
    try:
        folder_id = google.find_or_create_named_folder(posting_module.FINAL_FOLDER_NAME, posting_module.FINAL_FOLDER_STATE_KEY)
        origin = request.headers.get("Origin") or request.url_root.rstrip("/")
        session_url = google.start_resumable_upload(filename, mime, size, origin, folder_id=folder_id)
    except google.GoogleNotConnected as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"session_url": session_url})


@bp.route("/api/upload/complete", methods=["POST"])
@login_required
def upload_complete():
    data = request.get_json(force=True)
    output_id = data.get("output_id")
    drive_id = data.get("drive_id")
    if not output_id or not drive_id:
        return jsonify({"error": "Missing upload details."}), 400
    if not db.query_one("SELECT id FROM content_outputs WHERE id = ?", (output_id,)):
        return jsonify({"error": "That post no longer exists."}), 404
    # Her own uploads need no second sign-off; anyone else's waits for an admin.
    auto = _can_approve()
    posting_module.attach_final_media(
        output_id, drive_id, data.get("filename"), data.get("mime"), caption=(data.get("caption") or None),
        approve_now=auto, post_format=data.get("format"),
    )
    if not auto:
        posting_module.notify_awaiting_approval(output_id, uploader_name=g.user["name"])
    return jsonify({"ok": True, "approved": auto, "review_url": url_for("posting.review", output_id=output_id)})


@bp.route("/api/<int:output_id>/suggest-caption")
@login_required
def suggest_caption(output_id):
    o = posting_module.get_output(output_id)
    if not o:
        return jsonify({"error": "Not found."}), 404
    return jsonify({"caption": posting_module.suggest_caption(o), "format": o["format"]})


@bp.route("/api/<int:output_id>/format", methods=["POST"])
@login_required
def set_format(output_id):
    fmt = (request.get_json(force=True) or {}).get("format")
    if fmt not in posting_module.FORMAT_LABELS:
        return jsonify({"error": "Unknown format."}), 400
    db.execute("UPDATE content_outputs SET post_format = ? WHERE id = ?", (fmt, output_id))
    return jsonify({"ok": True})


@bp.route("/api/<int:output_id>/generate-cover", methods=["POST"])
@login_required
def generate_cover(output_id):
    if not _can_approve():
        return _forbidden()
    if not openai_images.configured():
        return jsonify({"error": "Image generation isn't switched on — add OPENAI_API_KEY in Render first."}), 400
    o = posting_module.get_output(output_id)
    if not o:
        return jsonify({"error": "Not found."}), 404
    note = ((request.get_json(silent=True) or {}).get("note") or "").strip()
    openai_images.generate_cover_async(output_id, note)
    return jsonify({"ok": True, "message": "Generating a cover — it'll appear on this page in about a minute."})


@bp.route("/design/<int:idea_id>")
@login_required
def design_media(idea_id):
    row = db.row_to_dict(db.query_one("SELECT design_drive_id, design_mime FROM content_ideas WHERE id = ?", (idea_id,)))
    if not row or not row["design_drive_id"]:
        abort(404)
    return _stream_drive_file(row["design_drive_id"], row["design_mime"] or "image/png")


@bp.route("/api/<int:output_id>/cover", methods=["POST"])
@login_required
def upload_cover(output_id):
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file."}), 400
    mime = file.mimetype or "image/jpeg"
    if not mime.startswith("image/"):
        return jsonify({"error": "The cover needs to be an image."}), 400
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name
    try:
        if os.path.getsize(tmp_path) > MAX_COVER_BYTES:
            return jsonify({"error": "Cover image is over 10MB."}), 400
        folder_id = google.find_or_create_named_folder(posting_module.FINAL_FOLDER_NAME, posting_module.FINAL_FOLDER_STATE_KEY)
        result = google.upload_file_to_drive(tmp_path, f"cover-{output_id}-{file.filename}", mime, folder_id=folder_id)
    except google.GoogleNotConnected as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    finally:
        os.unlink(tmp_path)
    db.execute("UPDATE content_outputs SET cover_drive_id = ? WHERE id = ?", (result["id"], output_id))
    return jsonify({"ok": True})


# ------------------------------------------------------- approval API
@bp.route("/api/<int:output_id>/caption", methods=["POST"])
@login_required
def save_caption(output_id):
    data = request.get_json(force=True)
    db.execute("UPDATE content_outputs SET post_caption = ? WHERE id = ?", (data.get("caption", ""), output_id))
    return jsonify({"ok": True})


@bp.route("/api/<int:output_id>/approve", methods=["POST"])
@login_required
def approve(output_id):
    if not _can_approve():
        return _forbidden()
    o = db.row_to_dict(db.query_one("SELECT final_media_drive_id FROM content_outputs WHERE id = ?", (output_id,)))
    if not o:
        return jsonify({"error": "Not found."}), 404
    if not o["final_media_drive_id"]:
        return jsonify({"error": "Upload the final video first."}), 400
    data = request.get_json(force=True)
    posting_module.approve(output_id, caption=data.get("caption"), platform_ids=data.get("platform_ids"))
    return jsonify({"ok": True})


@bp.route("/api/<int:output_id>/unapprove", methods=["POST"])
@login_required
def unapprove(output_id):
    if not _can_approve():
        return _forbidden()
    db.execute("UPDATE content_outputs SET approval_status = 'awaiting', approved_at = NULL WHERE id = ?", (output_id,))
    return jsonify({"ok": True})


@bp.route("/api/<int:output_id>/posted", methods=["POST"])
@login_required
def posted(output_id):
    if not _can_approve():
        return _forbidden()
    try:
        posting_module.mark_posted(output_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"ok": True})


@bp.route("/api/<int:output_id>/unposted", methods=["POST"])
@login_required
def unposted(output_id):
    if not _can_approve():
        return _forbidden()
    posting_module.unmark_posted(output_id)
    return jsonify({"ok": True})


@bp.route("/api/<int:output_id>/reschedule", methods=["POST"])
@bp.route("/api/<int:output_id>/move-next", methods=["POST"])
@login_required
def reschedule(output_id):
    """The Reschedule button: filler swaps into the next blank slot; a
    Targeted/Monthly campaign + its pair move to the following week with the
    calendar re-flowing around it (see posting.reschedule_output)."""
    if not _can_approve():
        return _forbidden()
    result = posting_module.reschedule_output(output_id, actor_id=g.user["id"])
    if not result.get("ok"):
        return jsonify({"error": result.get("message", "Couldn't reschedule that.")}), 400
    return jsonify(result)


@bp.route("/api/admin/reshuffle", methods=["POST"])
@login_required
def admin_reshuffle():
    """One-off calendar reset (Nov 2026): everything unposted from before the
    given start date is re-slotted from that day on, two posts a day Mon–Sat.
    dry_run (default true) only reports the plan."""
    if not g.user["role_is_admin"]:
        return _forbidden()
    data = request.get_json(force=True) or {}
    try:
        start = date.fromisoformat(data.get("from"))
    except Exception:
        return jsonify({"error": "Give a start date like 2026-11-20."}), 400
    if data.get("dry_run", True):
        res = posting_module.plan_reshuffle(start, assume_posted=data.get("mark_posted") or [])
        return jsonify({"dry_run": True, "plan": res["plan"], "blanks_to_delete": len(res["delete_blank_ids"])})
    res = posting_module.apply_reshuffle(start, mark_posted_ids=data.get("mark_posted") or [], actor_id=g.user["id"])
    return jsonify({"dry_run": False, **res})
