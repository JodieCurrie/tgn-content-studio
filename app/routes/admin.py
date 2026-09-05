import secrets

from flask import Blueprint, render_template, request, redirect, url_for, flash, g, session
from werkzeug.security import generate_password_hash, check_password_hash

from .. import db
from ..auth import login_required, admin_required
from .. import google_integration

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("")
@admin_required
def home():
    counts = {
        "content_types": db.query_one("SELECT COUNT(*) c FROM content_types WHERE archived=0")["c"],
        "rules": db.query_one("SELECT COUNT(*) c FROM scheduling_rules WHERE active=1")["c"],
        "users": db.query_one("SELECT COUNT(*) c FROM users WHERE active=1")["c"],
    }
    return render_template("admin/home.html", counts=counts)


# ---------------------------------------------------------------------- content types
@bp.route("/content-types")
@admin_required
def content_types():
    types = db.rows_to_list(db.query("SELECT * FROM content_types ORDER BY archived, sort_order"))
    platforms = db.rows_to_list(db.query("SELECT * FROM platforms ORDER BY sort_order"))
    for t in types:
        t["default_platform_ids"] = db.from_json(t["default_platform_ids"], [])
    return render_template("admin/content_types.html", types=types, platforms=platforms)


@bp.route("/content-types/new", methods=["POST"])
@admin_required
def content_type_new():
    _save_content_type(None)
    return redirect(url_for("admin.content_types"))


@bp.route("/content-types/<int:type_id>/edit", methods=["POST"])
@admin_required
def content_type_edit(type_id):
    _save_content_type(type_id)
    return redirect(url_for("admin.content_types"))


def _save_content_type(type_id):
    f = request.form
    platform_ids = [int(p) for p in request.form.getlist("platforms")]
    fields = dict(
        key=f["key"].strip().lower().replace(" ", "_"),
        label=f["label"].strip(),
        color=f.get("color", "#9FA5C9"),
        description=f.get("description", ""),
        is_filler=1 if f.get("is_filler") else 0,
        requires_videographer=1 if f.get("requires_videographer") else 0,
        requires_musician=1 if f.get("requires_musician") else 0,
        requires_jodie=1 if f.get("requires_jodie") else 0,
        default_lead_time_days=int(f.get("default_lead_time_days") or 5),
        default_platform_ids=db.to_json(platform_ids),
    )
    if type_id:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE content_types SET {set_clause} WHERE id = ?", (*fields.values(), type_id))
        flash(f"Updated '{fields['label']}'.", "success")
    else:
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        db.execute(f"INSERT INTO content_types ({cols}) VALUES ({placeholders})", tuple(fields.values()))
        flash(f"Added new content type '{fields['label']}'.", "success")


@bp.route("/content-types/<int:type_id>/archive", methods=["POST"])
@admin_required
def content_type_archive(type_id):
    db.execute("UPDATE content_types SET archived = 1 - archived WHERE id = ?", (type_id,))
    return redirect(url_for("admin.content_types"))


# ---------------------------------------------------------------------- task templates
@bp.route("/task-templates")
@admin_required
def task_templates():
    types = db.rows_to_list(db.query("SELECT * FROM content_types WHERE archived=0 ORDER BY sort_order"))
    for t in types:
        t["templates"] = db.rows_to_list(
            db.query("SELECT * FROM task_templates WHERE content_type_id = ? ORDER BY sort_order", (t["id"],))
        )
    roles = db.rows_to_list(db.query("SELECT key, label FROM roles ORDER BY is_admin DESC, label"))
    return render_template("admin/task_templates.html", types=types, roles=roles)


@bp.route("/task-templates/new", methods=["POST"])
@admin_required
def task_template_new():
    f = request.form
    db.execute(
        "INSERT INTO task_templates (content_type_id, role_key, task_name, offset_days_before, sort_order) VALUES (?,?,?,?,?)",
        (int(f["content_type_id"]), f["role_key"], f["task_name"], int(f.get("offset_days_before") or 0), 99),
    )
    flash("Task template added.", "success")
    return redirect(url_for("admin.task_templates"))


@bp.route("/task-templates/<int:tpl_id>/delete", methods=["POST"])
@admin_required
def task_template_delete(tpl_id):
    db.execute("DELETE FROM task_templates WHERE id = ?", (tpl_id,))
    return redirect(url_for("admin.task_templates"))


# ---------------------------------------------------------------------- scheduling rules
@bp.route("/scheduling-rules")
@admin_required
def scheduling_rules():
    rules = db.rows_to_list(
        db.query(
            """SELECT sr.*, ct.label AS type_label, ct.color AS type_color
               FROM scheduling_rules sr JOIN content_types ct ON ct.id = sr.content_type_id
               ORDER BY sr.active DESC, sr.label"""
        )
    )
    types = db.rows_to_list(db.query("SELECT id, label FROM content_types WHERE archived=0 ORDER BY sort_order"))
    return render_template("admin/scheduling_rules.html", rules=rules, types=types)


@bp.route("/scheduling-rules/new", methods=["POST"])
@admin_required
def scheduling_rule_new():
    f = request.form
    rule_type = f["rule_type"]
    new_id = db.execute(
        """INSERT INTO scheduling_rules
           (label, content_type_id, rule_type, weekday, interval_days, nth, anchor_date, horizon_weeks, default_title)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            f["label"], int(f["content_type_id"]), rule_type, int(f["weekday"]),
            int(f.get("interval_days") or 14) if rule_type == "biweekly" else None,
            int(f.get("nth") or 1) if rule_type == "monthly_nth_weekday" else None,
            f["anchor_date"], int(f.get("horizon_weeks") or 10), f.get("default_title", ""),
        ),
    )
    from .. import scheduling
    created = scheduling.materialize_rule(new_id)
    flash(f"Rule created — {len(created)} upcoming date(s) generated.", "success")
    return redirect(url_for("admin.scheduling_rules"))


@bp.route("/scheduling-rules/<int:rule_id>/toggle", methods=["POST"])
@admin_required
def scheduling_rule_toggle(rule_id):
    db.execute("UPDATE scheduling_rules SET active = 1 - active WHERE id = ?", (rule_id,))
    return redirect(url_for("admin.scheduling_rules"))


@bp.route("/scheduling-rules/<int:rule_id>/delete", methods=["POST"])
@admin_required
def scheduling_rule_delete(rule_id):
    db.execute("DELETE FROM scheduling_rules WHERE id = ?", (rule_id,))
    return redirect(url_for("admin.scheduling_rules"))


# ---------------------------------------------------------------------- users
@bp.route("/users")
@admin_required
def users():
    all_users = db.rows_to_list(
        db.query(
            """SELECT u.*, r.label AS role_label, r.key AS role_key FROM users u
               JOIN roles r ON r.id = u.role_id ORDER BY u.active DESC, u.name"""
        )
    )
    roles = db.rows_to_list(db.query("SELECT * FROM roles ORDER BY is_admin DESC, label"))
    return render_template("admin/users.html", all_users=all_users, roles=roles)


@bp.route("/users/new", methods=["POST"])
@admin_required
def user_new():
    f = request.form
    existing = db.query_one("SELECT id FROM users WHERE lower(email) = ?", (f["email"].strip().lower(),))
    if existing:
        flash("A user with that email already exists.", "error")
        return redirect(url_for("admin.users"))
    temp_password = f.get("password") or "TGNwelcome1!"
    db.execute(
        "INSERT INTO users (name, email, password_hash, role_id) VALUES (?,?,?,?)",
        (f["name"].strip(), f["email"].strip().lower(), generate_password_hash(temp_password), int(f["role_id"])),
    )
    flash(f"Added {f['name']}. Temporary password: {temp_password} (they should change it after logging in).", "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/toggle-active", methods=["POST"])
@admin_required
def user_toggle_active(user_id):
    if user_id == g.user["id"]:
        flash("You can't deactivate your own account.", "error")
        return redirect(url_for("admin.users"))
    db.execute("UPDATE users SET active = 1 - active WHERE id = ?", (user_id,))
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def user_reset_password(user_id):
    new_password = request.form.get("password") or "TGNwelcome1!"
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), user_id))
    flash(f"Password reset. New temporary password: {new_password}", "success")
    return redirect(url_for("admin.users"))


@bp.route("/creation-options")
@admin_required
def creation_options():
    options = db.rows_to_list(db.query("SELECT * FROM creation_options ORDER BY archived, sort_order"))
    types = db.rows_to_list(db.query("SELECT * FROM content_types WHERE archived=0 ORDER BY sort_order"))
    for o in options:
        o["output_type_ids"] = db.from_json(o["output_type_ids"], [])
    return render_template("admin/creation_options.html", options=options, types=types)


@bp.route("/creation-options/new", methods=["POST"])
@admin_required
def creation_option_new():
    f = request.form
    type_ids = [int(t) for t in request.form.getlist("type_ids")]
    db.execute(
        "INSERT INTO creation_options (key, label, icon, output_type_ids, sort_order) VALUES (?,?,?,?,99)",
        (f["key"].strip().lower().replace(" ", "_"), f["label"].strip(), f.get("icon") or "✨", db.to_json(type_ids)),
    )
    flash("Create-content option added.", "success")
    return redirect(url_for("admin.creation_options"))


@bp.route("/creation-options/<int:option_id>/archive", methods=["POST"])
@admin_required
def creation_option_archive(option_id):
    db.execute("UPDATE creation_options SET archived = 1 - archived WHERE id = ?", (option_id,))
    return redirect(url_for("admin.creation_options"))


# ---------------------------------------------------------------------- integrations (Google)
@bp.route("/integrations")
@admin_required
def integrations():
    connection = google_integration.get_connection()
    return render_template(
        "admin/integrations.html",
        connection=connection,
        configured=google_integration.is_configured(),
    )


@bp.route("/google/connect")
@admin_required
def google_connect():
    if not google_integration.is_configured():
        flash(
            "Google isn't configured on the server yet — GOOGLE_CLIENT_ID, "
            "GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI need to be set as "
            "environment variables first.",
            "error",
        )
        return redirect(url_for("admin.integrations"))
    state = secrets.token_urlsafe(24)
    session["google_oauth_state"] = state
    return redirect(google_integration.build_auth_url(state))


@bp.route("/google/callback")
@admin_required
def google_callback():
    error = request.args.get("error")
    if error:
        flash(f"Google sign-in was cancelled or failed: {error}", "error")
        return redirect(url_for("admin.integrations"))

    expected_state = session.pop("google_oauth_state", None)
    if not expected_state or request.args.get("state") != expected_state:
        flash("That Google sign-in link expired or was invalid — please try connecting again.", "error")
        return redirect(url_for("admin.integrations"))

    code = request.args.get("code")
    if not code:
        flash("Google didn't return an authorization code — please try again.", "error")
        return redirect(url_for("admin.integrations"))

    try:
        tokens = google_integration.exchange_code_for_tokens(code)
        google_integration.save_new_connection(tokens, connected_by_user_id=g.user["id"])
    except Exception as e:
        flash(f"Couldn't finish connecting to Google: {e}", "error")
        return redirect(url_for("admin.integrations"))

    flash("Google account connected — Calendar and Drive are ready to use.", "success")
    return redirect(url_for("admin.integrations"))


@bp.route("/google/disconnect", methods=["POST"])
@admin_required
def google_disconnect():
    google_integration.disconnect()
    flash("Disconnected the Google account. Reconnect any time from here.", "success")
    return redirect(url_for("admin.integrations"))
