import calendar as pycal
from datetime import date, timedelta

from flask import Blueprint, render_template, request, g

from .. import db
from ..auth import login_required
from .. import content as content_module
from .. import analytics

bp = Blueprint("calendar", __name__)

WEEKDAY_HEADERS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
INITIAL_WEEKS = 14   # how many weeks render before the user has to scroll (~3 months)
WEEKS_PER_FRAGMENT = 4  # weeks fetched per infinite-scroll batch


def _sunday_on_or_before(d):
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _continuous_weeks(start_sunday, count):
    """`count` consecutive 7-day weeks starting at `start_sunday` — a flat,
    non-overlapping sequence (no per-month padding), which is what makes the
    weeks bleed into each other with no duplicated/repeated week at a month
    boundary (Part 6, refined per Jodie's feedback: one continuous flow, not
    a self-contained grid per month)."""
    weeks = []
    cursor = start_sunday
    for _ in range(count):
        weeks.append([cursor + timedelta(days=i) for i in range(7)])
        cursor += timedelta(days=7)
    return weeks


def _week_owner_month(week):
    """Which (year, month) a week 'belongs to', by majority of its 7 days —
    used only to decide where the vertical month label sits, never to drop
    or duplicate any day."""
    counts = {}
    for d in week:
        key = (d.year, d.month)
        counts[key] = counts.get(key, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _segment_weeks(weeks):
    """Group consecutive weeks under whichever month owns each one, so the
    month name can be rendered once, spanning those rows in a narrow side
    rail (rotated text, like Jodie's original Excel calendar) — instead of a
    full-width divider row that forced each month to re-render its own
    boundary week and duplicate it."""
    segments = []
    for week in weeks:
        year, month = _week_owner_month(week)
        if segments and (segments[-1]["year"], segments[-1]["month"]) == (year, month):
            segments[-1]["weeks"].append(week)
        else:
            segments.append({"year": year, "month": month, "month_name": pycal.month_name[month], "weeks": [week]})
    return segments


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
    """The calendar (Part 2 of the brief, refined per Jodie's feedback): one
    continuous, Sunday-first flow of weeks — never a repeated/duplicated
    week at a month boundary. Renders a starting window; calendar.js extends
    it further as the user scrolls, fetching more from
    /calendar/month-fragment, with no fixed cap on how far ahead that can go.
    The month name is shown once per group of weeks in a vertical side rail,
    like Jodie's original Excel calendar, instead of a full-width divider."""
    today = date.today()
    year = request.args.get("year", type=int) or today.year
    month = request.args.get("month", type=int) or today.month

    start_sunday = _sunday_on_or_before(date(year, month, 1))
    weeks = _continuous_weeks(start_sunday, INITIAL_WEEKS)
    segments = _segment_weeks(weeks)
    by_day = _outputs_by_day(weeks[0][0], weeks[-1][-1])
    next_from = weeks[-1][0] + timedelta(days=7)

    legend = db.rows_to_list(
        db.query("SELECT key, label, color FROM content_types WHERE archived = 0 ORDER BY sort_order")
    )

    return render_template(
        "calendar_month.html",
        segments=segments, by_day=by_day, today=today,
        jump_year=year, jump_month=month,
        next_from=next_from.isoformat(),
        weekday_headers=WEEKDAY_HEADERS,
        legend=legend,
    )


@bp.route("/calendar/month-fragment")
@login_required
def month_fragment():
    """Returns the next batch of weeks (grouped into month segments) for
    calendar.js to append as the user scrolls further down. `from` must be
    an ISO date that falls on a Sunday — calendar.js always hands back
    exactly the date this view last reported as `next_from`, so the flow of
    weeks never skips or repeats one."""
    from_str = request.args.get("from")
    if not from_str:
        return "", 400
    try:
        start_sunday = date.fromisoformat(from_str)
    except ValueError:
        return "", 400
    if start_sunday.weekday() != 6:  # Python Sunday = 6
        return "", 400

    weeks = _continuous_weeks(start_sunday, WEEKS_PER_FRAGMENT)
    segments = _segment_weeks(weeks)
    by_day = _outputs_by_day(weeks[0][0], weeks[-1][-1])

    return render_template(
        "partials/calendar_month_fragment.html",
        segments=segments, by_day=by_day, today=date.today(),
    )


@bp.route("/calendar/week")
@login_required
def week_view():
    today = date.today()
    anchor_str = request.args.get("date")
    anchor = date.fromisoformat(anchor_str) if anchor_str else today
    start = anchor - timedelta(days=(anchor.weekday() + 1) % 7)  # Sunday-first, matches the month calendar
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
