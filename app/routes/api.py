import os
import uuid
from datetime import date

from flask import Blueprint, request, jsonify, g, current_app
from werkzeug.utils import secure_filename

from .. import db
from ..auth import login_required, admin_required
from .. import content as content_module
from .. import scheduling
from .. import task_engine
from .. import analytics
from .. import pipeline

bp = Blueprint("api", __name__, url_prefix="/api")


def _admin_only():
    if not g.user["role_is_admin"] and not g.user["role_can_manage_all_content"]:
        return jsonify({"error": "You don't have permission to do that."}), 403
    return None


# ---------------------------------------------------------------------- create content
@bp.route("/campaigns/quick-create", methods=["POST"])
@login_required
def quick_create():
    forbidden = _admin_only()
    if forbidden:
        return forbidden
    data = request.get_json(force=True)
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Please enter what you're posting."}), 400
    creation_option_key = data.get("creation_option_key")
    publish_date = data.get("publish_date")
    if not publish_date:
        return jsonify({"error": "Please choose a publish date."}), 400

    option = db.row_to_dict(db.query_one("SELECT * FROM creation_options WHERE key = ?", (creation_option_key,)))
    if not option:
        return jsonify({"error": "Unknown content type."}), 400
    type_ids = db.from_json(option["output_type_ids"], [])
    if not type_ids:
        return jsonify({"error": "That content type has no outputs configured."}), 400

    # Filler Post / Monthly Campaign (Part 8/9): the option lists a MENU of
    # possible types rather than a fixed set of outputs, and the modal's
    # second dropdown submits the one actually chosen as an override.
    extra_output_type_ids = type_ids[1:]
    if option["pick_subtype"]:
        chosen_raw = data.get("content_type_id")
        try:
            chosen_id = int(chosen_raw)
        except (TypeError, ValueError):
            chosen_id = None
        if not chosen_id or chosen_id not in type_ids:
            return jsonify({"error": "Please choose a specific type."}), 400
        primary_type_id = chosen_id
        extra_output_type_ids = []
    else:
        primary_type_id = type_ids[0]

    platform_ids = data.get("platform_ids") or None
    assigned_user_id = data.get("assigned_user_id") or None
    owner_id = data.get("owner_id") or g.user["id"]

    campaign_id = content_module.create_campaign(
        title=title,
        publish_date_iso=publish_date,
        content_type_id=primary_type_id,
        platform_ids=platform_ids,
        owner_id=owner_id,
        assigned_user_id=assigned_user_id,
        concept=data.get("concept", ""),
        created_by=g.user["id"],
        extra_output_type_ids=extra_output_type_ids,
    )
    return jsonify({"campaign_id": campaign_id})


# ---------------------------------------------------------------------- campaign detail / edit
@bp.route("/campaigns/<int:campaign_id>", methods=["PATCH"])
@login_required
def update_campaign(campaign_id):
    campaign = db.row_to_dict(db.query_one("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)))
    if not campaign:
        return jsonify({"error": "Not found"}), 404
    if not content_module.user_can_edit_campaign(g.user, campaign_id):
        return jsonify({"error": "You don't have permission to edit this."}), 403
    data = request.get_json(force=True)
    content_module.update_campaign_fields(campaign_id, data)
    return jsonify({"ok": True})


@bp.route("/campaigns/<int:campaign_id>", methods=["DELETE"])
@login_required
def delete_campaign(campaign_id):
    forbidden = _admin_only()
    if forbidden:
        return forbidden
    content_module.delete_campaign(campaign_id)
    return jsonify({"ok": True})


@bp.route("/campaigns/<int:campaign_id>/drag-options")
@login_required
def drag_options(campaign_id):
    opts = scheduling.get_drag_options(campaign_id)
    if opts is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(opts)


@bp.route("/campaigns/<int:campaign_id>/drag", methods=["POST"])
@login_required
def drag_campaign(campaign_id):
    forbidden = _admin_only()
    if forbidden:
        return forbidden
    data = request.get_json(force=True)
    new_date = data.get("new_date")
    mode = data.get("mode")
    if not new_date:
        return jsonify({"error": "Missing new_date"}), 400
    try:
        date.fromisoformat(new_date)
        scheduling.apply_drag(campaign_id, new_date, mode, actor_id=g.user["id"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/outputs/<int:output_id>", methods=["PATCH"])
@login_required
def update_output(output_id):
    output = db.row_to_dict(db.query_one("SELECT * FROM content_outputs WHERE id = ?", (output_id,)))
    if not output:
        return jsonify({"error": "Not found"}), 404
    if not content_module.user_can_edit_campaign(g.user, output["campaign_id"]):
        return jsonify({"error": "You don't have permission to edit this."}), 403
    data = request.get_json(force=True)
    # Sept: status is never hand-set any more (see pipeline.sync_output_status
    # / mark_output_published) — deliberately left out of the allow-list even
    # if an old client still sends it.
    allowed = {"assigned_user_id", "title", "publish_date"}
    fields = {k: v for k, v in data.items() if k in allowed}
    if fields:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE content_outputs SET {set_clause} WHERE id = ?", (*fields.values(), output_id))
    if "platform_ids" in data:
        db.execute("DELETE FROM output_platforms WHERE output_id = ?", (output_id,))
        for pid in data["platform_ids"]:
            db.execute("INSERT INTO output_platforms (output_id, platform_id) VALUES (?, ?)", (output_id, pid))
    return jsonify({"ok": True})


@bp.route("/outputs/<int:output_id>/mark-published", methods=["POST"])
@login_required
def mark_output_published(output_id):
    """The one manual status action left anywhere in the app (Sept) — every
    other status change is derived automatically from real task/stage
    progress."""
    forbidden = _admin_only()
    if forbidden:
        return forbidden
    try:
        pipeline.mark_output_published(output_id, actor_id=g.user["id"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


# ---------------------------------------------------------------------- tasks
@bp.route("/tasks/<int:task_id>", methods=["PATCH"])
@login_required
def update_task(task_id):
    task = db.row_to_dict(db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,)))
    if not task:
        return jsonify({"error": "Not found"}), 404
    is_owner = task["assigned_user_id"] == g.user["id"]
    if not (g.user["role_is_admin"] or g.user["role_can_manage_all_content"] or is_owner):
        return jsonify({"error": "You can only update tasks assigned to you."}), 403

    data = request.get_json(force=True)
    allowed = {"status", "notes", "due_date", "priority"}
    if not is_owner and not (g.user["role_is_admin"] or g.user["role_can_manage_all_content"]):
        allowed = {"status", "notes"}
    if g.user["role_is_admin"] or g.user["role_can_manage_all_content"]:
        allowed |= {"assigned_user_id"}
    fields = {k: v for k, v in data.items() if k in allowed}

    # Targeted Video production pipeline (Part 14-18): a task tagged with a
    # stage can't move off 'not_started' until every task in the stage
    # before it is 'complete' — reject the request rather than silently
    # letting the pipeline get out of order.
    if "status" in fields and fields["status"] != "not_started" and not pipeline.can_advance_task(task):
        return jsonify({"error": "This task's stage hasn't unlocked yet — the previous stage isn't complete."}), 409

    previous_assigned_user_id = task["assigned_user_id"]
    if fields:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        db.execute(
            f"UPDATE tasks SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            (*fields.values(), task_id),
        )
    if "status" in fields:
        pipeline.after_task_status_change(task_id)
    # Part 23: reassigning an opt-in type's single flat "Create X" task away
    # from whoever held it is what starts its staged production pipeline —
    # replaces the old explicit "Start production workflow" button, for both
    # Monthly and Filler.
    if "assigned_user_id" in fields:
        pipeline.after_task_reassignment(task_id, previous_assigned_user_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------- pipeline: shoot-level scheduling / confirm / hand-off
@bp.route("/campaigns/<int:campaign_id>/pipeline/<stage_key>/schedule", methods=["POST"])
@login_required
def schedule_pipeline_meeting(campaign_id, stage_key):
    forbidden = _admin_only()
    if forbidden:
        return forbidden
    if stage_key not in pipeline.MEETING_STAGE_KEYS:
        return jsonify({"error": "That stage isn't schedulable."}), 400
    data = request.get_json(force=True)
    start = data.get("start")
    end = data.get("end")
    if not start or not end:
        return jsonify({"error": "Please choose a start and end time."}), 400
    participant_ids = [int(i) for i in (data.get("participant_ids") or [])]
    bunch_with_stage_ids = [int(i) for i in (data.get("bunch_with_stage_ids") or [])]
    try:
        pipeline.schedule_meeting(campaign_id, stage_key, start, end, participant_ids, bunch_with_stage_ids)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/pipeline-stages/<int:stage_id>/confirm", methods=["POST"])
@login_required
def confirm_pipeline_stage(stage_id):
    forbidden = _admin_only()
    if forbidden:
        return forbidden
    stage = db.row_to_dict(db.query_one("SELECT * FROM pipeline_stages WHERE id = ?", (stage_id,)))
    if not stage:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(force=True)
    if not data.get("happened"):
        return jsonify({"error": "Use the scheduling form to reschedule this meeting."}), 400
    if stage["stage_key"] == "film":
        return jsonify({"error": "Film/Record confirms through the edit hand-off step."}), 400
    try:
        pipeline.confirm_stage_happened(stage_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/pipeline-stages/<int:stage_id>/confirm-with-delivery", methods=["POST"])
@login_required
def confirm_pipeline_stage_with_delivery(stage_id):
    forbidden = _admin_only()
    if forbidden:
        return forbidden
    data = request.get_json(force=True)
    recipient_user_id = data.get("recipient_user_id")
    deadline = data.get("deadline")
    note = (data.get("note") or "").strip()
    if not recipient_user_id or not deadline:
        return jsonify({"error": "Please choose an editor and a deadline."}), 400
    try:
        pipeline.confirm_film_with_delivery(stage_id, int(recipient_user_id), deadline, note)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


# ---------------------------------------------------------------------- pipeline: production-level assign / review / submit
@bp.route("/pipeline-stages/<int:stage_id>/assign", methods=["POST"])
@login_required
def assign_pipeline_stage(stage_id):
    """Audio's (or Highlights' internal) assign-and-send step."""
    forbidden = _admin_only()
    if forbidden:
        return forbidden
    data = request.get_json(force=True)
    recipient_user_id = data.get("recipient_user_id")
    deadline = data.get("deadline")
    note = (data.get("note") or "").strip()
    if not recipient_user_id or not deadline:
        return jsonify({"error": "Please choose someone and a deadline."}), 400
    try:
        pipeline.assign_and_notify(stage_id, int(recipient_user_id), deadline, note)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/pipeline-stages/<int:stage_id>/approve", methods=["POST"])
@login_required
def approve_pipeline_review(stage_id):
    forbidden = _admin_only()
    if forbidden:
        return forbidden
    data = request.get_json(force=True) or {}
    try:
        pipeline.approve_review(stage_id, (data.get("notes") or "").strip())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/pipeline-stages/<int:stage_id>/reject", methods=["POST"])
@login_required
def reject_pipeline_review(stage_id):
    forbidden = _admin_only()
    if forbidden:
        return forbidden
    data = request.get_json(force=True) or {}
    notes = (data.get("notes") or "").strip()
    if not notes:
        return jsonify({"error": "Please add a note explaining what needs to change."}), 400
    try:
        pipeline.reject_review(stage_id, notes)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


def _stage_task_owner_or_admin(stage_id):
    """True if the current user is an admin, or is assigned the task for
    this stage — used to let compilation's/highlights' own assignee submit
    without needing admin rights."""
    if g.user["role_is_admin"] or g.user["role_can_manage_all_content"]:
        return True
    stage = db.row_to_dict(db.query_one("SELECT * FROM pipeline_stages WHERE id = ?", (stage_id,)))
    if not stage or not stage.get("output_id"):
        return False
    task = db.query_one(
        "SELECT assigned_user_id FROM tasks WHERE output_id = ? AND stage_key = ?", (stage["output_id"], stage["stage_key"])
    )
    return bool(task and task["assigned_user_id"] == g.user["id"])


@bp.route("/pipeline-stages/<int:stage_id>/submit", methods=["POST"])
@login_required
def submit_pipeline_stage(stage_id):
    """Compilation's or Highlights' assignee pastes their finished Drive
    link(s) here — this notifies Jodie but does NOT complete the stage;
    only her explicit "Mark as received" does that (see mark_received)."""
    if not _stage_task_owner_or_admin(stage_id):
        return jsonify({"error": "You can only submit work assigned to you."}), 403
    stage = db.row_to_dict(db.query_one("SELECT * FROM pipeline_stages WHERE id = ?", (stage_id,)))
    if not stage:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(force=True)
    notes = (data.get("notes") or "").strip()
    try:
        if stage["stage_key"] == "compilation":
            link = (data.get("link") or "").strip()
            if not link:
                return jsonify({"error": "Please paste the Drive link."}), 400
            pipeline.submit_compilation(stage_id, link, notes)
        elif stage["stage_key"] == "highlights":
            clips = data.get("clips") or []
            if not clips or not any((c.get("url") or "").strip() for c in clips):
                return jsonify({"error": "Please paste at least one clip link."}), 400
            pipeline.submit_highlights(stage_id, clips, notes)
        else:
            return jsonify({"error": "This stage doesn't accept submissions."}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/pipeline-stages/<int:stage_id>/mark-received", methods=["POST"])
@login_required
def mark_pipeline_stage_received(stage_id):
    forbidden = _admin_only()
    if forbidden:
        return forbidden
    stage = db.row_to_dict(db.query_one("SELECT * FROM pipeline_stages WHERE id = ?", (stage_id,)))
    if not stage:
        return jsonify({"error": "Not found"}), 404
    try:
        if stage["stage_key"] == "compilation":
            pipeline.mark_compilation_received(stage_id)
        elif stage["stage_key"] == "highlights":
            pipeline.mark_highlights_received(stage_id)
        else:
            return jsonify({"error": "This stage isn't received/submitted."}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/pipeline-stages/<int:stage_id>/decide-audio", methods=["POST"])
@login_required
def decide_pipeline_audio(stage_id):
    """Filler-video's dynamic branch (Part 23): after Review Content
    approves, yes/no on sending it to a musician."""
    forbidden = _admin_only()
    if forbidden:
        return forbidden
    data = request.get_json(force=True) or {}
    try:
        pipeline.decide_filler_audio(stage_id, bool(data.get("wants_audio")))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/outputs/<int:output_id>/filler-film-candidates")
@login_required
def filler_film_candidates(output_id):
    """Other Filler-video shoots not yet scheduled — offered as "bunch with
    this one too" options (Part 23, Filler-only)."""
    return jsonify({"candidates": pipeline.unscheduled_filler_film_candidates(exclude_output_id=output_id)})


@bp.route("/pipeline-stages/<int:stage_id>/capture-highlights", methods=["POST"])
@login_required
def capture_pipeline_highlights(stage_id):
    forbidden = _admin_only()
    if forbidden:
        return forbidden
    data = request.get_json(force=True)
    candidates = data.get("candidates") or []
    if not candidates:
        return jsonify({"error": "Please add at least one candidate clip."}), 400
    try:
        pipeline.capture_highlight_candidates(stage_id, candidates, data.get("deadline") or None)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


# ---------------------------------------------------------------------- notes / inspiration / assets / comments
@bp.route("/campaigns/<int:campaign_id>/inspiration", methods=["POST"])
@login_required
def add_inspiration(campaign_id):
    if not content_module.user_can_edit_campaign(g.user, campaign_id):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Missing URL"}), 400
    link_id = content_module.add_inspiration_link(campaign_id, url, data.get("label", ""))
    return jsonify({"id": link_id})


@bp.route("/inspiration/<int:link_id>", methods=["DELETE"])
@login_required
def delete_inspiration(link_id):
    db.execute("DELETE FROM inspiration_links WHERE id = ?", (link_id,))
    return jsonify({"ok": True})


@bp.route("/campaigns/<int:campaign_id>/comments", methods=["POST"])
@login_required
def add_comment(campaign_id):
    data = request.get_json(force=True)
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "Empty comment"}), 400
    comment_id = content_module.add_comment(campaign_id, g.user["id"], body)
    return jsonify({"id": comment_id, "author_name": g.user["name"]})


@bp.route("/campaigns/<int:campaign_id>/assets", methods=["POST"])
@login_required
def upload_asset(campaign_id):
    if not content_module.user_can_edit_campaign(g.user, campaign_id):
        return jsonify({"error": "Forbidden"}), 403
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file"}), 400
    filename = secure_filename(file.filename)
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    dest = os.path.join(current_app.config["UPLOAD_FOLDER"], unique_name)
    file.save(dest)
    asset_id = db.execute(
        "INSERT INTO assets (campaign_id, filename, stored_path, uploaded_by) VALUES (?,?,?,?)",
        (campaign_id, filename, unique_name, g.user["id"]),
    )
    return jsonify({"id": asset_id, "filename": filename})


# ---------------------------------------------------------------------- ideas bank
@bp.route("/ideas", methods=["POST"])
@login_required
def create_idea():
    data = request.get_json(force=True)
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Please enter an idea."}), 400
    idea_id = db.execute(
        "INSERT INTO content_ideas (title, notes, links, content_type_id, created_by) VALUES (?,?,?,?,?)",
        (title, data.get("notes", ""), data.get("links", ""), data.get("content_type_id"), g.user["id"]),
    )
    if data.get("content_type_id"):
        idea = db.row_to_dict(db.query_one("SELECT * FROM content_ideas WHERE id = ?", (idea_id,)))
        content_module._backfill_idea_into_placeholder_slot(idea)
    return jsonify({"id": idea_id})


IDEA_EDITABLE_FIELDS = {"title", "notes", "links", "content_type_id"}


@bp.route("/ideas/<int:idea_id>", methods=["PATCH"])
@login_required
def update_idea(idea_id):
    idea = db.query_one("SELECT id FROM content_ideas WHERE id = ?", (idea_id,))
    if not idea:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(force=True)
    fields = {k: v for k, v in data.items() if k in IDEA_EDITABLE_FIELDS}
    if not fields:
        return jsonify({"ok": True})
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE content_ideas SET {set_clause} WHERE id = ?", (*fields.values(), idea_id))
    if "content_type_id" in fields and fields["content_type_id"]:
        fresh_idea = db.row_to_dict(db.query_one("SELECT * FROM content_ideas WHERE id = ?", (idea_id,)))
        content_module._backfill_idea_into_placeholder_slot(fresh_idea)
    return jsonify({"ok": True})


@bp.route("/ideas/<int:idea_id>", methods=["DELETE"])
@login_required
def delete_idea(idea_id):
    db.execute("DELETE FROM content_ideas WHERE id = ?", (idea_id,))
    return jsonify({"ok": True})


@bp.route("/ideas/<int:idea_id>/schedule", methods=["POST"])
@login_required
def schedule_idea(idea_id):
    forbidden = _admin_only()
    if forbidden:
        return forbidden
    idea = db.row_to_dict(db.query_one("SELECT * FROM content_ideas WHERE id = ?", (idea_id,)))
    if not idea:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(force=True)
    content_type_id = data.get("content_type_id") or idea.get("content_type_id")
    publish_date = data.get("publish_date")
    if not content_type_id or not publish_date:
        return jsonify({"error": "Choose a content type and date."}), 400

    campaign_id = content_module.create_campaign(
        title=idea["title"],
        publish_date_iso=publish_date,
        content_type_id=content_type_id,
        notes=content_module.idea_notes_with_links(idea),
        owner_id=g.user["id"],
        created_by=g.user["id"],
        source_idea_id=idea_id,
    )
    db.execute("UPDATE content_ideas SET scheduled_campaign_id = ? WHERE id = ?", (campaign_id, idea_id))
    return jsonify({"campaign_id": campaign_id})


# ---------------------------------------------------------------------- dashboard widgets
@bp.route("/warnings")
@login_required
def warnings():
    return jsonify({
        "warnings": analytics.warnings_for_week(),
        "suggestions": [dict(s) for s in analytics.suggest_filler()],
    })
