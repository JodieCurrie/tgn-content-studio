from datetime import date, timedelta

from flask import Blueprint, render_template, request, g

from .. import db
from .. import pipeline
from ..auth import login_required

bp = Blueprint("tasks", __name__, url_prefix="/tasks")

# campaign_concept/campaign_source_idea_id/campaign_publish_date feed
# pipeline.filter_visible_tasks()'s admin-only declutter rule (Sept);
# content_type_label is the second pill on each row (an output's own type,
# falling back to the campaign's primary type for a campaign-level task).
TASK_SELECT_FIELDS = """
    t.*, c.title AS campaign_title, c.id AS campaign_id, u.name AS assigned_name,
    c.concept AS campaign_concept, c.source_idea_id AS campaign_source_idea_id,
    c.publish_date AS campaign_publish_date,
    COALESCE(oct.label, cct.label) AS content_type_label
"""
TASK_JOINS = """
    FROM tasks t
    JOIN campaigns c ON c.id = t.campaign_id
    LEFT JOIN users u ON u.id = t.assigned_user_id
    LEFT JOIN content_outputs o ON o.id = t.output_id
    LEFT JOIN content_types oct ON oct.id = o.content_type_id
    LEFT JOIN content_types cct ON cct.id = c.primary_content_type_id
"""


def _bucket_meeting_entries(entries, today_iso, week_end_iso, overdue, this_week, later):
    for entry in entries:
        due = entry.get("due_date")
        if due and due < today_iso:
            overdue.append(entry)
        elif due and due <= week_end_iso:
            this_week.append(entry)
        else:
            later.append(entry)
    overdue.sort(key=lambda t: t.get("due_date") or "")
    this_week.sort(key=lambda t: t.get("due_date") or "")
    later.sort(key=lambda t: (t.get("due_date") is None, t.get("due_date") or ""))


@bp.route("")
@login_required
def my_tasks():
    """Part 23 (Sept redesign): each person logs in and sees only what
    actually needs their attention right now — a stage-tagged task is
    hidden while its stage is still locked, and Jodie's own tasks are
    further decluttered to campaigns that actually have real content behind
    them within the next few months (see pipeline.filter_visible_tasks).
    Admins can flip a filter to see everyone's."""
    scope = request.args.get("scope", "mine")
    if scope == "all" and g.user["role_is_admin"]:
        user_filter = ""
        params = ()
    else:
        user_filter = "AND t.assigned_user_id = ?"
        params = (g.user["id"],)

    today = date.today()
    week_end = today + timedelta(days=7)

    overdue = db.rows_to_list(db.query(
        f"""SELECT {TASK_SELECT_FIELDS} {TASK_JOINS}
            WHERE t.due_date < ? AND t.status != 'complete' {user_filter}
            ORDER BY t.due_date""",
        (today.isoformat(), *params),
    ))
    this_week = db.rows_to_list(db.query(
        f"""SELECT {TASK_SELECT_FIELDS} {TASK_JOINS}
            WHERE t.due_date BETWEEN ? AND ? AND t.status != 'complete' {user_filter}
            ORDER BY t.due_date""",
        (today.isoformat(), week_end.isoformat(), *params),
    ))
    later = db.rows_to_list(db.query(
        f"""SELECT {TASK_SELECT_FIELDS} {TASK_JOINS}
            WHERE (t.due_date > ? OR t.due_date IS NULL) AND t.status != 'complete' {user_filter}
            ORDER BY t.due_date IS NULL, t.due_date LIMIT 40""",
        (week_end.isoformat(), *params),
    ))
    # "Recently completed" deliberately skips the visibility filter — a
    # task someone already finished shouldn't vanish from their own recent
    # history just because a later stage locked back up or the campaign's
    # since drifted past the declutter window.
    done_recently = db.rows_to_list(db.query(
        f"""SELECT {TASK_SELECT_FIELDS} {TASK_JOINS}
            WHERE t.status = 'complete' {user_filter}
            ORDER BY t.updated_at DESC LIMIT 10""",
        params,
    ))

    overdue = pipeline.filter_visible_tasks(overdue)
    this_week = pipeline.filter_visible_tasks(this_week)
    later = pipeline.filter_visible_tasks(later)

    if scope != "all":
        # A videographer/musician added as a Concept Hashout / Film-Record
        # participant sees a read-only entry for it here (Sept) — merged
        # only into someone's own list, not the shared "Everyone" view.
        entries = pipeline.meeting_task_entries_for_user(g.user["id"])
        _bucket_meeting_entries(entries, today.isoformat(), week_end.isoformat(), overdue, this_week, later)

    return render_template(
        "tasks.html", overdue=overdue, this_week=this_week, later=later,
        done_recently=done_recently, scope=scope,
    )
