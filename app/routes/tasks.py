from datetime import date, timedelta

from flask import Blueprint, render_template, request, g

from .. import db
from ..auth import login_required

bp = Blueprint("tasks", __name__, url_prefix="/tasks")


@bp.route("")
@login_required
def my_tasks():
    """Part 23: each person logs in and sees what they need to do. Admins
    can flip a filter to see everyone's."""
    scope = request.args.get("scope", "mine")
    if scope == "all" and g.user["role_is_admin"]:
        user_filter = ""
        params = ()
    else:
        user_filter = "AND t.assigned_user_id = ?"
        params = (g.user["id"],)

    today = date.today()

    overdue = db.rows_to_list(
        db.query(
            f"""SELECT t.*, c.title AS campaign_title, c.id AS campaign_id, u.name AS assigned_name
               FROM tasks t JOIN campaigns c ON c.id = t.campaign_id
               LEFT JOIN users u ON u.id = t.assigned_user_id
               WHERE t.due_date < ? AND t.status != 'complete' {user_filter}
               ORDER BY t.due_date""",
            (today.isoformat(), *params),
        )
    )
    this_week = db.rows_to_list(
        db.query(
            f"""SELECT t.*, c.title AS campaign_title, c.id AS campaign_id, u.name AS assigned_name
               FROM tasks t JOIN campaigns c ON c.id = t.campaign_id
               LEFT JOIN users u ON u.id = t.assigned_user_id
               WHERE t.due_date BETWEEN ? AND ? AND t.status != 'complete' {user_filter}
               ORDER BY t.due_date""",
            (today.isoformat(), (today + timedelta(days=7)).isoformat(), *params),
        )
    )
    later = db.rows_to_list(
        db.query(
            f"""SELECT t.*, c.title AS campaign_title, c.id AS campaign_id, u.name AS assigned_name
               FROM tasks t JOIN campaigns c ON c.id = t.campaign_id
               LEFT JOIN users u ON u.id = t.assigned_user_id
               WHERE (t.due_date > ? OR t.due_date IS NULL) AND t.status != 'complete' {user_filter}
               ORDER BY t.due_date IS NULL, t.due_date LIMIT 40""",
            ((today + timedelta(days=7)).isoformat(), *params),
        )
    )
    done_recently = db.rows_to_list(
        db.query(
            f"""SELECT t.*, c.title AS campaign_title, c.id AS campaign_id, u.name AS assigned_name
               FROM tasks t JOIN campaigns c ON c.id = t.campaign_id
               LEFT JOIN users u ON u.id = t.assigned_user_id
               WHERE t.status = 'complete' {user_filter}
               ORDER BY t.updated_at DESC LIMIT 10""",
            params,
        )
    )

    return render_template(
        "tasks.html", overdue=overdue, this_week=this_week, later=later,
        done_recently=done_recently, scope=scope,
    )
