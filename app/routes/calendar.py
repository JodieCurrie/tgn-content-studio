import calendar as pycal
from datetime import date, timedelta

from flask import Blueprint, render_template, request, g

from .. import db
from ..auth import login_required
from .. import content as content_module
from .. import analytics

bp = Blueprint("calendar", __name__)


def _month_grid(year, month):
    """Weeks (lists of 7 dates) covering the full calendar month, Mon-first,
    including the leading/trailing days from neighbouring months so weeks
    stay whole — this is what makes months visually flow into each other."""
    first = date(year, month, 1)
    start = first - timedelta(days=first.weekday())
    last_day = pycal.monthrange(year, month)[1]
    last = date(year, month, last_day)
    end = last + timedelta(days=(6 - last.weekday()))

    weeks = []
    cursor = start
    while cursor <= end:
        week = [cursor + timedelta(days=i) for i in range(7)]
        weeks.append(week)
        cursor += timedelta(days=7)
    return weeks, start, end


def _outputs_by_day(start, end):
    rows = db.rows_to_list(
        db.query(
            """SELECT o.id AS output_id, o.publish_date, o.status, o.title AS output_title,
                      o.campaign_id, c.title AS campaign_title, c.schedule_origin, c.is_rule_exception,
                      c.depends_on_campaign_id,
                      ct.key AS type_key, ct.label AS type_label, ct.color AS type_color,
                      u.name AS assigned_name, u.id AS assigned_user_id
               FROM content_outputs o
               JOIN campaigns c ON c.id = o.campaign_id
               JOIN content_types ct ON ct.id = o.content_type_id
               LEFT JOIN users u ON u.id = o.assigned_user_id
               WHERE o.publish_date BETWEEN ? AND ?
               ORDER BY o.publish_date, o.sort_order, o.id""",
            (start.isoformat(), end.isoformat()),
        )
    )
    by_day = {}
    for r in rows:
        by_day.setdefault(r["publish_date"], []).append(r)
    return by_day


@bp.route("/")
@bp.route("/calendar")
@login_required
def month_view():
    today = date.today()
    year = request.args.get("year", type=int) or today.year
    month = request.args.get("month", type=int) or today.month
    weeks, start, end = _month_grid(year, month)
    by_day = _outputs_by_day(start, end)

    prev_month = month - 1 or 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    legend = db.rows_to_list(
        db.query("SELECT key, label, color FROM content_types WHERE archived = 0 ORDER BY sort_order")
    )

    return render_template(
        "calendar_month.html",
        weeks=weeks, by_day=by_day, year=year, month=month,
        month_name=pycal.month_name[month], today=today,
        prev_month=prev_month, prev_year=prev_year,
        next_month=next_month, next_year=next_year,
        legend=legend,
    )


@bp.route("/calendar/week")
@login_required
def week_view():
    today = date.today()
    anchor_str = request.args.get("date")
    anchor = date.fromisoformat(anchor_str) if anchor_str else today
    start = anchor - timedelta(days=anchor.weekday())
    end = start + timedelta(days=6)
    days = [start + timedelta(days=i) for i in range(7)]
    by_day = _outputs_by_day(start, end)

    tasks_this_week = db.rows_to_list(
        db.query(
            """SELECT t.*, c.title AS campaign_title, u.name AS assigned_name
               FROM tasks t JOIN campaigns c ON c.id = t.campaign_id
               LEFT JOIN users u ON u.id = t.assigned_user_id
               WHERE t.due_date BETWEEN ? AND ? ORDER BY t.due_date""",
            (start.isoformat(), end.isoformat()),
        )
    )

    prev_week = (start - timedelta(days=7)).isoformat()
    next_week = (start + timedelta(days=7)).isoformat()

    return render_template(
        "calendar_week.html", days=days, by_day=by_day, start=start, end=end,
        today=today, tasks_this_week=tasks_this_week,
        prev_week=prev_week, next_week=next_week,
    )


@bp.route("/calendar/list")
@login_required
def list_view():
    today = date.today()
    horizon = today + timedelta(days=60)
    upcoming = db.rows_to_list(
        db.query(
            """SELECT o.id AS output_id, o.publish_date, o.status, c.id AS campaign_id, c.title,
                      ct.label AS type_label, ct.color AS type_color, u.name AS assigned_name
               FROM content_outputs o JOIN campaigns c ON c.id = o.campaign_id
               JOIN content_types ct ON ct.id = o.content_type_id
               LEFT JOIN users u ON u.id = o.assigned_user_id
               WHERE o.publish_date BETWEEN ? AND ? ORDER BY o.publish_date""",
            (today.isoformat(), horizon.isoformat()),
        )
    )
    overdue_tasks = db.rows_to_list(
        db.query(
            """SELECT t.*, c.title AS campaign_title, u.name AS assigned_name
               FROM tasks t JOIN campaigns c ON c.id = t.campaign_id
               LEFT JOIN users u ON u.id = t.assigned_user_id
               WHERE t.due_date < ? AND t.status != 'complete' ORDER BY t.due_date""",
            (today.isoformat(),),
        )
    )
    upcoming_tasks = db.rows_to_list(
        db.query(
            """SELECT t.*, c.title AS campaign_title, u.name AS assigned_name
               FROM tasks t JOIN campaigns c ON c.id = t.campaign_id
               LEFT JOIN users u ON u.id = t.assigned_user_id
               WHERE t.due_date BETWEEN ? AND ? AND t.status != 'complete' ORDER BY t.due_date""",
            (today.isoformat(), horizon.isoformat()),
        )
    )
    return render_template(
        "calendar_list.html", upcoming=upcoming, overdue_tasks=overdue_tasks,
        upcoming_tasks=upcoming_tasks, today=today,
    )


@bp.route("/calendar/campaigns")
@login_required
def campaign_view():
    """Every campaign that has more than one output, or is part of a
    dependency chain — grouped so you can see a whole topic's spread."""
    campaigns = db.rows_to_list(
        db.query(
            """SELECT c.*, ct.label AS type_label, ct.color AS type_color,
                      (SELECT COUNT(*) FROM content_outputs o WHERE o.campaign_id = c.id) AS output_count
               FROM campaigns c LEFT JOIN content_types ct ON ct.id = c.primary_content_type_id
               WHERE c.depends_on_campaign_id IS NULL
               ORDER BY c.publish_date DESC LIMIT 60"""
        )
    )
    for c in campaigns:
        c["outputs"] = db.rows_to_list(
            db.query(
                """SELECT o.*, ct.label AS type_label, ct.color AS type_color FROM content_outputs o
                   JOIN content_types ct ON ct.id = o.content_type_id WHERE o.campaign_id = ? ORDER BY o.publish_date""",
                (c["id"],),
            )
        )
        c["dependents"] = db.rows_to_list(
            db.query(
                """SELECT c2.*, ct.label AS type_label, ct.color AS type_color FROM campaigns c2
                   JOIN content_types ct ON ct.id = c2.primary_content_type_id
                   WHERE c2.depends_on_campaign_id = ? ORDER BY c2.publish_date""",
                (c["id"],),
            )
        )
    return render_template("calendar_campaigns.html", campaigns=campaigns)


@bp.route("/campaign/<int:campaign_id>/panel")
@login_required
def campaign_panel(campaign_id):
    campaign = content_module.get_campaign_detail(campaign_id)
    if not campaign:
        return "<div class='panel-empty'>Not found.</div>", 404
    users = db.rows_to_list(db.query("SELECT id, name FROM users WHERE active = 1 ORDER BY name"))
    content_types = db.rows_to_list(db.query("SELECT * FROM content_types WHERE archived = 0 ORDER BY sort_order"))
    platforms = db.rows_to_list(db.query("SELECT * FROM platforms ORDER BY sort_order"))
    return render_template(
        "partials/campaign_panel.html", c=campaign, users=users,
        content_types=content_types, platforms=platforms,
    )
