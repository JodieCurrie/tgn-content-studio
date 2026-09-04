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

    platform_ids = data.get("platform_ids") or None
    assigned_user_id = data.get("assigned_user_id") or None
    owner_id = data.get("owner_id") or g.user["id"]

    campaign_id = content_module.create_campaign(
        title=title,
        publish_date_iso=publish_date,
        content_type_id=type_ids[0],
        platform_ids=platform_ids,
        owner_id=owner_id,
        assigned_user_id=assigned_user_id,
        concept=data.get("concept", ""),
        created_by=g.user["id"],
        extra_output_type_ids=type_ids[1:],
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
    allowed = {"status", "assigned_user_id", "title", "publish_date"}
    fields = {k: v for k, v in data.items() if k in allowed}
    if fields:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE content_outputs SET {set_clause} WHERE id = ?", (*fields.values(), output_id))
    if "platform_ids" in data:
        db.execute("DELETE FROM output_platforms WHERE output_id = ?", (output_id,))
        for pid in data["platform_ids"]:
            db.execute("INSERT INTO output_platforms (output_id, platform_id) VALUES (?, ?)", (output_id, pid))
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
    if fields:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        db.execute(
            f"UPDATE tasks SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            (*fields.values(), task_id),
        )
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
        "INSERT INTO content_ideas (title, notes, created_by) VALUES (?,?,?)",
        (title, data.get("notes", ""), g.user["id"]),
    )
    return jsonify({"id": idea_id})


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
    content_type_id = data.get("content_type_id")
    publish_date = data.get("publish_date")
    if not content_type_id or not publish_date:
        return jsonify({"error": "Choose a content type and date."}), 400

    campaign_id = content_module.create_campaign(
        title=idea["title"],
        publish_date_iso=publish_date,
        content_type_id=content_type_id,
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
