import functools
from flask import Blueprint, render_template, request, redirect, url_for, session, g, flash
from werkzeug.security import check_password_hash, generate_password_hash

from . import db
from . import pipeline
from . import scheduling

bp = Blueprint("auth", __name__)


@bp.before_app_request
def load_logged_in_user():
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
        g.pending_pipeline_confirmations = []
    else:
        g.user = db.row_to_dict(
            db.query_one(
                """SELECT u.*, r.key AS role_key, r.label AS role_label,
                          r.is_admin AS role_is_admin,
                          r.can_manage_all_content AS role_can_manage_all_content,
                          r.can_manage_settings AS role_can_manage_settings,
                          r.color AS role_color
                   FROM users u JOIN roles r ON r.id = u.role_id
                   WHERE u.id = ? AND u.active = 1""",
                (user_id,),
            )
        )
        # "Did this meeting happen, or does it need rescheduling?" prompts
        # only for admins (Part 14-18 pipeline redesign) — checked on every
        # request rather than via a background job, since this app has no
        # scheduler/worker process; the query is cheap and the table tiny.
        if g.user and g.user["role_is_admin"]:
            g.pending_pipeline_confirmations = pipeline.pending_confirmations_for_admin()
        else:
            g.pending_pipeline_confirmations = []
        # Sept: keeps the recurring-schedule/filler-gap rolling horizon
        # extending on its own instead of quietly stalling weeks after
        # whoever last clicked Admin > "Sync pipeline & reference data" —
        # see scheduling.ensure_horizon_rolled_forward for why. Cheap on
        # every request but for the one day it actually re-syncs.
        if g.user:
            scheduling.ensure_horizon_rolled_forward()


def login_required(view):
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(**kwargs)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        if not g.user["role_is_admin"]:
            flash("That area is limited to admins.", "error")
            return redirect(url_for("dashboard.home"))
        return view(**kwargs)
    return wrapped


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for("dashboard.home"))

    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db.row_to_dict(
            db.query_one("SELECT * FROM users WHERE lower(email) = ? AND active = 1", (email,))
        )
        if user is None or not check_password_hash(user["password_hash"], password):
            error = "Incorrect email or password."
        else:
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            next_url = request.args.get("next") or url_for("dashboard.home")
            return redirect(next_url)

    return render_template("login.html", error=error)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/account", methods=["GET", "POST"])
def account():
    if g.user is None:
        return redirect(url_for("auth.login", next=request.path))
    error = None
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not check_password_hash(g.user["password_hash"], current):
            error = "Current password is incorrect."
        elif len(new) < 8:
            error = "New password should be at least 8 characters."
        elif new != confirm:
            error = "New password and confirmation don't match."
        else:
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new), g.user["id"]))
            flash("Password updated.", "success")
            return redirect(url_for("dashboard.home"))
    return render_template("account.html", error=error)
