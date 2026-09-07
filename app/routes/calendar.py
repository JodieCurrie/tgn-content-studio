import calendar as pycal
from datetime import date, timedelta

from flask import Blueprint, render_template, request, g

from .. import db
from ..auth import login_required
from .. import content as content_module
from .. import analytics
from .. import pipeline

bp = Blueprint("calendar", __name__)

WEEKDAY_HEADERS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
INITIAL_WEEKS = 14   # how many weeks render before the user has to scroll (~3 months)
WEEKS_PER_FRAGMENT = 4  # weeks fetched per infinite-scroll batch

# A repeating 3-color pastel rotation across the day cells themselves (like
# Jodie's Excel calendar), keyed by calendar month so the same month always
# gets the same color every year (Jan/Apr/Jul/Oct share one, Feb/May/Aug/Nov
# another, Mar/Jun/Sep/Dec the third). The month-name rail stays neutral —
# only the day blocks (the 1st through the last day of that month) are
# tinted. Soft, low-saturation washes (not the earlier, punchier version) so
# adjacent months read as a gentle variation rather than a bold color swap.
# Sampled directly from the exact swatches Jodie sent: peach/orange, mint
# green, blue, lavender — a 4-color rotation (same colors as before, just
# reordered), so it repeats every 4 years rather than a fixed 3-month cadence.
MONTH_TINT_COLORS = ["#fff3ed", "#f5fcf2", "#f5fbff", "#f9f3fe"]  # orange, green, blue, purple


@bp.app_template_global()
def month_tint_color(month):
    """Every day cell is tinted by its own real calendar month (not by
    whichever month the week's rail label happens to be grouped under), so
    the color always fills a complete month edge-to-edge with no blank or
    mismatched days at a week that spans two months."""
    return MONTH_TINT_COLORS[(month - 1) % len(MONTH_TINT_COLORS)]


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


def _segment_weeks(weeks, continues_year=None, continues_month=None):
    """Group consecutive weeks under whichever month owns each one, so the
    month name can be rendered once, spanning those rows in a narrow side
    rail (rotated text, like Jodie's original Excel calendar) — instead of a
    full-width divider row that forced each month to re-render its own
    boundary week and duplicate it.

    `continues_year`/`continues_month` identify the month the *previously
    rendered* batch of weeks ended on (the infinite-scroll fragment endpoint
    is a fresh call each time, so without this a month split across two
    fetches would otherwise get its name rendered twice in a row — once at
    the bottom of one batch and again at the top of the next). When the
    first segment here is that same month, it's marked `continuation` so the
    template renders an unlabeled rail block that just extends the strip."""
    segments = []
    for week in weeks:
        year, month = _week_owner_month(week)
        if segments and (segments[-1]["year"], segments[-1]["month"]) == (year, month):
            segments[-1]["weeks"].append(week)
        else:
            continuation = (
                not segments and year == continues_year and month == continues_month
            )
            segments.append({
                "year": year, "month": month, "month_name": pycal.month_name[month], "weeks": [week],
                "continuation": continuation,
            })
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


def _meetings_by_day(start, end):
    """Scheduled pipeline meetings (Concept Hashout / Film-Record — shared
    once per shoot/campaign) falling in this date range, keyed by date —
    rendered as a small grey pill alongside the normal content cards (see
    render_day in _calendar_macros.html and calendar_week.html)."""
    rows = db.rows_to_list(
        db.query(
            """SELECT ps.id AS stage_id, ps.stage_key, ps.meeting_start, ps.meeting_end,
                      ps.calendar_link, ps.meet_link,
                      c.id AS campaign_id, c.title AS campaign_title
               FROM pipeline_stages ps
               JOIN campaigns c ON c.id = ps.campaign_id
               WHERE ps.meeting_start IS NOT NULL
                 AND date(ps.meeting_start) BETWEEN ? AND ?""",
            (start.isoformat(), end.isoformat()),
        )
    )
    by_day = {}
    for r in rows:
        r["label"] = pipeline.STAGE_BY_KEY[r["stage_key"]]["label"]
        by_day.setdefault(r["meeting_start"][:10], []).append(r)
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
    meetings_by_day = _meetings_by_day(weeks[0][0], weeks[-1][-1])
    next_from = weeks[-1][0] + timedelta(days=7)

    legend_types = db.rows_to_list(
        db.query("SELECT key, label, color, category_key FROM content_types WHERE archived = 0 ORDER BY sort_order")
    )
    legend_groups = content_module.group_content_types_by_category(legend_types)

    return render_template(
        "calendar_month.html",
        segments=segments, by_day=by_day, meetings_by_day=meetings_by_day, today=today,
        jump_year=year, jump_month=month,
        next_from=next_from.isoformat(),
        cont_year=segments[-1]["year"], cont_month=segments[-1]["month"],
        weekday_headers=WEEKDAY_HEADERS,
        legend_groups=legend_groups,
    )


@bp.route("/calendar/month-fragment")
@login_required
def month_fragment():
    """Returns the next batch of weeks (grouped into month segments) for
    calendar.js to append as the user scrolls further down. `from` must be
    an ISO date that falls on a Sunday — calendar.js always hands back
    exactly the date this view last reported as `next_from`, so the flow of
    weeks never skips or repeats one. `cont_year`/`cont_month` (also handed
    back by the previous batch) identify the month the rail strip currently
    ends on, so a month split across this fetch boundary doesn't get its
    name rendered a second time right under itself."""
    from_str = request.args.get("from")
    if not from_str:
        return "", 400
    try:
        start_sunday = date.fromisoformat(from_str)
    except ValueError:
        return "", 400
    if start_sunday.weekday() != 6:  # Python Sunday = 6
        return "", 400

    cont_year = request.args.get("cont_year", type=int)
    cont_month = request.args.get("cont_month", type=int)

    weeks = _continuous_weeks(start_sunday, WEEKS_PER_FRAGMENT)
    segments = _segment_weeks(weeks, continues_year=cont_year, continues_month=cont_month)
    by_day = _outputs_by_day(weeks[0][0], weeks[-1][-1])
    meetings_by_day = _meetings_by_day(weeks[0][0], weeks[-1][-1])

    return render_template(
        "partials/calendar_month_fragment.html",
        segments=segments, by_day=by_day, meetings_by_day=meetings_by_day, today=date.today(),
    )


WEEK_VIEW_SPAN_DAYS = 14  # Part 23: a 2-week snippet, not just the current week


@bp.route("/calendar/week")
@login_required
def week_view():
    today = date.today()
    anchor_str = request.args.get("date")
    anchor = date.fromisoformat(anchor_str) if anchor_str else today
    start = anchor - timedelta(days=(anchor.weekday() + 1) % 7)  # Sunday-first, matches the month calendar
    end = start + timedelta(days=WEEK_VIEW_SPAN_DAYS - 1)
    days = [start + timedelta(days=i) for i in range(WEEK_VIEW_SPAN_DAYS)]
    by_day = _outputs_by_day(start, end)
    meetings_by_day = _meetings_by_day(start, end)
    # A flat "Meetings this week" list (same data as meetings_by_day,
    # flattened + sorted) so every participant sees it when they open the
    # week, the same way "Tasks due this week" already isn't scoped to any
    # one viewer — this is what satisfies "listed for everyone involved".
    meetings_this_week = sorted(
        (m for day_meetings in meetings_by_day.values() for m in day_meetings),
        key=lambda m: m["meeting_start"],
    )
    for m in meetings_this_week:
        m["participant_names"] = pipeline.names_for_user_ids(
            db.from_json(
                db.query_one("SELECT participant_user_ids FROM pipeline_stages WHERE id = ?", (m["stage_id"],))["participant_user_ids"],
                [],
            )
        )

    # Part 23: "Tasks due" is now scoped to whoever's actually looking at
    # it — their own tasks in this 2-week window, not every task ever due
    # (which is what a "my tasks" list used to mean here) and not everyone
    # else's. Meetings above stay unscoped, since a shared shoot involves
    # more than one person by nature.
    tasks_this_week = db.rows_to_list(
        db.query(
            """SELECT t.*, c.title AS campaign_title, u.name AS assigned_name
               FROM tasks t JOIN campaigns c ON c.id = t.campaign_id
               LEFT JOIN users u ON u.id = t.assigned_user_id
               WHERE t.due_date BETWEEN ? AND ? AND t.assigned_user_id = ?
               ORDER BY t.due_date""",
            (start.isoformat(), end.isoformat(), g.user["id"]),
        )
    )

    prev_week = (start - timedelta(days=WEEK_VIEW_SPAN_DAYS)).isoformat()
    next_week = (start + timedelta(days=WEEK_VIEW_SPAN_DAYS)).isoformat()

    return render_template(
        "calendar_week.html", days=days, by_day=by_day, meetings_by_day=meetings_by_day,
        meetings_this_week=meetings_this_week, start=start, end=end,
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


# ---------------------------------------------------------------------- pipeline: meeting-stage modals (campaign-scoped for Targeted's shared shoot, output-scoped for a Monthly pipeline's own "film" — see app/pipeline.py PRODUCTION_STAGES_BY_TYPE)
def _meeting_stage_for_campaign(campaign_id, stage_key):
    """Resolves a meeting stage's row whichever way it's scoped: Targeted's
    shared concept/film live on the campaign itself; a Monthly pipeline has
    no shared shoot tier, so its own "film" lives on its single output."""
    return db.row_to_dict(
        db.query_one(
            """SELECT * FROM pipeline_stages WHERE stage_key = ? AND (
                   campaign_id = ?
                   OR output_id IN (SELECT id FROM content_outputs WHERE campaign_id = ?)
               )""",
            (stage_key, campaign_id, campaign_id),
        )
    )


@bp.route("/campaigns/<int:campaign_id>/pipeline/<stage_key>/schedule-modal")
@login_required
def pipeline_schedule_modal(campaign_id, stage_key):
    """Fragment for the 'Schedule this meeting' modal — also reused, with
    the stage's current values pre-filled, for a reschedule."""
    if stage_key not in pipeline.MEETING_STAGE_KEYS:
        return "<div class='panel-empty'>Not found.</div>", 404
    campaign = db.row_to_dict(db.query_one("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)))
    stage = _meeting_stage_for_campaign(campaign_id, stage_key)
    if not campaign or not stage:
        return "<div class='panel-empty'>Not found.</div>", 404
    users = db.rows_to_list(db.query("SELECT id, name FROM users WHERE active = 1 ORDER BY name"))
    participant_ids = db.from_json(stage.get("participant_user_ids"), [])
    # Bunching multiple filming sessions into one meeting (Part 23, Filler
    # video only): offer any of THIS output's Filler-video siblings whose
    # own Film/Record hasn't been scheduled yet.
    bunch_candidates = []
    if stage_key == "film" and stage.get("output_id"):
        output = db.row_to_dict(db.query_one("SELECT content_type_id FROM content_outputs WHERE id = ?", (stage["output_id"],)))
        if output and pipeline.is_filler_video_pipeline_eligible(output["content_type_id"]):
            bunch_candidates = pipeline.unscheduled_filler_film_candidates(exclude_output_id=stage["output_id"])
    return render_template(
        "partials/schedule_meeting_modal.html",
        campaign=campaign, stage=stage, stage_key=stage_key,
        stage_label=pipeline.STAGE_BY_KEY[stage_key]["label"],
        users=users, participant_ids=participant_ids, bunch_candidates=bunch_candidates,
    )


@bp.route("/pipeline-stages/<int:stage_id>/confirm-modal")
@login_required
def pipeline_confirm_modal(stage_id):
    """Fragment for the 'Did this happen, or does it need rescheduling?'
    modal — auto-opened on an admin's login (see base.html) for any stage
    pending confirmation, and reusable on demand from the panel too."""
    stage = db.row_to_dict(
        db.query_one(
            """SELECT ps.*, COALESCE(c1.title, c2.title) AS campaign_title
               FROM pipeline_stages ps
               LEFT JOIN campaigns c1 ON c1.id = ps.campaign_id
               LEFT JOIN content_outputs o ON o.id = ps.output_id
               LEFT JOIN campaigns c2 ON c2.id = o.campaign_id
               WHERE ps.id = ?""",
            (stage_id,),
        )
    )
    if not stage:
        return "<div class='panel-empty'>Not found.</div>", 404
    return render_template(
        "partials/confirm_meeting_modal.html",
        stage=stage, stage_label=pipeline.STAGE_BY_KEY[stage["stage_key"]]["label"],
    )


@bp.route("/pipeline-stages/<int:stage_id>/delivery-modal")
@login_required
def pipeline_delivery_modal(stage_id):
    """Fragment for the editor hand-off form shown right after confirming
    Film/Record happened — recipient is a real user now, picked from a
    dropdown rather than typed in as free text. For a Monthly pipeline's
    output-scoped "film" (no sibling outputs to hand off at once), the
    deadline field is pre-filled with a ~10-day-out suggestion (Part 21);
    Targeted's shared shoot leaves it blank, as before."""
    stage = db.row_to_dict(
        db.query_one(
            """SELECT ps.*, COALESCE(c1.title, c2.title) AS campaign_title, o.publish_date AS output_publish_date
               FROM pipeline_stages ps
               LEFT JOIN campaigns c1 ON c1.id = ps.campaign_id
               LEFT JOIN content_outputs o ON o.id = ps.output_id
               LEFT JOIN campaigns c2 ON c2.id = o.campaign_id
               WHERE ps.id = ?""",
            (stage_id,),
        )
    )
    if not stage or stage["stage_key"] != "film":
        return "<div class='panel-empty'>Not found.</div>", 404
    users = db.rows_to_list(db.query("SELECT id, name FROM users WHERE active = 1 ORDER BY name"))
    default_deadline = None
    if stage.get("output_id"):
        suggested = date.today() + timedelta(days=10)
        if stage.get("output_publish_date"):
            suggested = min(suggested, date.fromisoformat(stage["output_publish_date"]) - timedelta(days=1))
        default_deadline = suggested.isoformat()
    return render_template(
        "partials/video_delivery_modal.html", stage=stage, users=users, default_deadline=default_deadline,
    )


# ---------------------------------------------------------------------- pipeline: production-level modals (output-scoped)
@bp.route("/pipeline-stages/<int:stage_id>/assign-modal")
@login_required
def pipeline_assign_modal(stage_id):
    """Fragment for Audio Creation's 'Assign & send' form — pick the
    musician + a deadline, defaulting to 5 days out (or the publish date
    minus 5, whichever is sooner, with a warning if that's tight)."""
    stage = db.row_to_dict(
        db.query_one(
            """SELECT ps.*, o.publish_date, c.title AS campaign_title, c.drive_folder_link
               FROM pipeline_stages ps JOIN content_outputs o ON o.id = ps.output_id
               JOIN campaigns c ON c.id = o.campaign_id WHERE ps.id = ?""",
            (stage_id,),
        )
    )
    if not stage or stage["stage_key"] not in ("audio", "design_post"):
        return "<div class='panel-empty'>Not found.</div>", 404
    users = db.rows_to_list(db.query("SELECT id, name FROM users WHERE active = 1 ORDER BY name"))
    publish = date.fromisoformat(stage["publish_date"])
    default_deadline = min(date.today() + timedelta(days=5), publish - timedelta(days=5))
    tight_deadline = default_deadline < date.today() + timedelta(days=5)
    return render_template(
        "partials/assign_and_notify_modal.html",
        stage=stage, users=users, default_deadline=default_deadline.isoformat(), tight_deadline=tight_deadline,
        stage_label=pipeline.STAGE_BY_KEY[stage["stage_key"]]["label"],
    )


@bp.route("/pipeline-stages/<int:stage_id>/review-modal")
@login_required
def pipeline_review_modal(stage_id):
    """Fragment for Review Edit/Review Audio's approve-or-reject form."""
    stage = db.row_to_dict(
        db.query_one(
            """SELECT ps.*, c.title AS campaign_title, c.drive_folder_link
               FROM pipeline_stages ps JOIN content_outputs o ON o.id = ps.output_id
               JOIN campaigns c ON c.id = o.campaign_id WHERE ps.id = ?""",
            (stage_id,),
        )
    )
    if not stage or stage["stage_key"] not in pipeline.REVIEW_STAGE_KEYS:
        return "<div class='panel-empty'>Not found.</div>", 404
    return render_template(
        "partials/review_decision_modal.html", stage=stage, stage_label=pipeline.STAGE_BY_KEY[stage["stage_key"]]["label"],
    )


@bp.route("/pipeline-stages/<int:stage_id>/submission-modal")
@login_required
def pipeline_submission_modal(stage_id):
    """Fragment for Final Compilation's / Highlights' assignee-facing
    "paste your finished link(s)" form."""
    stage = db.row_to_dict(
        db.query_one(
            """SELECT ps.*, c.title AS campaign_title
               FROM pipeline_stages ps JOIN content_outputs o ON o.id = ps.output_id
               JOIN campaigns c ON c.id = o.campaign_id WHERE ps.id = ?""",
            (stage_id,),
        )
    )
    if not stage or stage["stage_key"] not in pipeline.SUBMISSION_STAGE_KEYS:
        return "<div class='panel-empty'>Not found.</div>", 404
    candidates = db.from_json(stage.get("highlight_candidates"), []) if stage["stage_key"] == "highlights" else []
    return render_template(
        "partials/submission_modal.html", stage=stage, stage_label=pipeline.STAGE_BY_KEY[stage["stage_key"]]["label"],
        candidates=candidates,
    )


@bp.route("/pipeline-stages/<int:stage_id>/highlights-modal")
@login_required
def pipeline_highlights_modal(stage_id):
    """Fragment for Jodie's 'Select Highlight Reels' candidate-timestamp
    capture form."""
    stage = db.row_to_dict(
        db.query_one(
            """SELECT ps.*, c.title AS campaign_title
               FROM pipeline_stages ps JOIN content_outputs o ON o.id = ps.output_id
               JOIN campaigns c ON c.id = o.campaign_id WHERE ps.id = ?""",
            (stage_id,),
        )
    )
    if not stage or stage["stage_key"] != "highlights":
        return "<div class='panel-empty'>Not found.</div>", 404
    return render_template("partials/highlight_candidates_modal.html", stage=stage)
