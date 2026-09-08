"""
Content balance tracking + smart scheduling warnings/filler suggestions
(Parts 27-29). These are advisory only — nothing here blocks or auto-edits
anything; they just compute numbers and hand back plain-language notes.

Sept: Jodie's posting-frequency target is measured in DAYS a week, not raw
post count — two posts on the same day (usually a second-platform cut of
the same content) count as one posting day. See
content.MIN_POSTING_DAYS_PER_WEEK, which content.fill_weekly_filler_gaps()
actually schedules extra Filler posts against (run wherever the recurring
horizon gets materialized). suggest_filler() below is only what's left to
say for a week that pass hasn't caught up to yet.
"""
from datetime import date, timedelta

from . import db
from . import content as content_module
from . import pipeline

VIDEO_TYPE_KEYS = {
    "targeted_short", "targeted_long", "highlight_1", "highlight_2", "targeted_full_repost",
    "moving_scripture", "tiktok_style", "quick_reel", "scripture_expansion",
}


def week_bounds(anchor=None):
    anchor = anchor or date.today()
    monday = anchor - timedelta(days=anchor.weekday())
    return monday, monday + timedelta(days=6)


def outputs_in_range(start, end):
    return db.rows_to_list(
        db.query(
            """SELECT o.*, ct.key AS type_key, ct.label AS type_label, ct.color AS type_color,
                      ct.is_filler AS is_filler
               FROM content_outputs o JOIN content_types ct ON ct.id = o.content_type_id
               WHERE o.publish_date BETWEEN ? AND ?
               ORDER BY o.publish_date""",
            (start.isoformat(), end.isoformat()),
        )
    )


def content_mix(start, end):
    rows = outputs_in_range(start, end)
    mix = {}
    for r in rows:
        mix.setdefault(r["type_label"], {"count": 0, "color": r["type_color"]})
        mix[r["type_label"]]["count"] += 1
    return {"total": len(rows), "by_type": mix, "outputs": rows}


def warnings_for_week(anchor=None):
    start, end = week_bounds(anchor)
    days = content_module.posting_days_in_range(start, end)
    day_count = len(days)
    target = content_module.MIN_POSTING_DAYS_PER_WEEK
    warnings = []

    if day_count < target:
        warnings.append({
            "level": "warning",
            "text": f"Only {day_count} posting day{'s' if day_count != 1 else ''} scheduled this week — "
                    f"your minimum is {target} days a week. Run a data sync from Admin to auto-fill the rest with filler.",
        })
    else:
        warnings.append({
            "level": "success",
            "text": f"{day_count} posting days scheduled this week — your {target}-day minimum is met.",
        })

    # consecutive same-family (video) warning, in publish-date order
    rows = outputs_in_range(start, end)
    streak = 0
    max_streak = 0
    for r in rows:
        if r["type_key"] in VIDEO_TYPE_KEYS:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    if max_streak >= 4:
        warnings.append({
            "level": "warning",
            "text": f"You currently have {max_streak} video posts in a row this week — consider breaking it up with a carousel or static post.",
        })

    # testimony / first-Tuesday sanity check, informational only
    testimony_rule = db.query_one("SELECT sr.* FROM scheduling_rules sr JOIN content_types ct ON ct.id = sr.content_type_id WHERE ct.key = 'testimony' AND sr.active = 1")
    if testimony_rule:
        from . import scheduling
        first_tue = scheduling.nth_weekday_of_month(anchor.year if anchor else date.today().year,
                                                      anchor.month if anchor else date.today().month, 1, 1)
        has_testimony_this_month = first_tue and db.query_one(
            """SELECT id FROM campaigns WHERE primary_content_type_id = (SELECT id FROM content_types WHERE key='testimony')
               AND publish_date = ?""",
            (first_tue.isoformat(),),
        )
        if has_testimony_this_month:
            warnings.append({"level": "success", "text": f"Testimony scheduled for the first Tuesday of the month ({first_tue.strftime('%-d %b')})."})

    return warnings


def suggest_filler(anchor=None, limit=3):
    """Advisory-only fallback for the dashboard: content.fill_weekly_filler_gaps()
    is what actually schedules gap-filling posts (on data sync), so this
    only has something to say when a week is still short — e.g. before a
    sync has run, or the horizon ran out of weekday slots to use."""
    start, end = week_bounds(anchor)
    days = content_module.posting_days_in_range(start, end)
    if len(days) >= content_module.MIN_POSTING_DAYS_PER_WEEK:
        return []

    rows = outputs_in_range(start, end)
    used_keys = {r["type_key"] for r in rows}
    filler_types = db.rows_to_list(
        db.query("SELECT * FROM content_types WHERE is_filler = 1 AND archived = 0 ORDER BY sort_order")
    )
    filler_types = [ft for ft in filler_types if ft["key"] not in content_module.FILLER_AUTOFILL_EXCLUDED_TYPE_KEYS]
    # prefer filler types not already used this week, and de-prioritise video
    # if there's already a lot of video in the mix
    video_count = sum(1 for r in rows if r["type_key"] in VIDEO_TYPE_KEYS)
    scored = []
    for ft in filler_types:
        score = 0
        if ft["key"] not in used_keys:
            score += 2
        if ft["key"] in VIDEO_TYPE_KEYS and video_count >= 2:
            score -= 3
        scored.append((score, ft))
    scored.sort(key=lambda x: -x[0])
    return [ft for _, ft in scored[:limit]]


def home_summary(user=None):
    start, end = week_bounds()
    rows = outputs_in_range(start, end)
    published = sum(1 for r in rows if r["status"] == "published")
    upcoming = len(rows) - published

    tasks_due = db.rows_to_list(
        db.query(
            """SELECT t.*, c.title AS campaign_title,
                      c.concept AS campaign_concept, c.source_idea_id AS campaign_source_idea_id,
                      c.publish_date AS campaign_publish_date
               FROM tasks t JOIN campaigns c ON c.id = t.campaign_id
               WHERE t.due_date BETWEEN ? AND ? AND t.status NOT IN ('complete')
               ORDER BY t.due_date""",
            (start.isoformat(), end.isoformat()),
        )
    )
    overdue = db.rows_to_list(
        db.query(
            """SELECT t.*, c.title AS campaign_title,
                      c.concept AS campaign_concept, c.source_idea_id AS campaign_source_idea_id,
                      c.publish_date AS campaign_publish_date
               FROM tasks t JOIN campaigns c ON c.id = t.campaign_id
               WHERE t.due_date < ? AND t.status NOT IN ('complete') ORDER BY t.due_date""",
            (date.today().isoformat(),),
        )
    )
    # Sept: same "only what actually needs attention" visibility rule as the
    # Tasks/Week/List views, so the dashboard's counts match what a person
    # actually sees when they click through.
    tasks_due = pipeline.filter_visible_tasks(tasks_due)
    overdue = pipeline.filter_visible_tasks(overdue)
    awaiting_approval = db.rows_to_list(
        db.query(
            """SELECT c.* FROM campaigns c WHERE c.status = 'awaiting_review' ORDER BY c.publish_date"""
        )
    )
    team_load = db.rows_to_list(
        db.query(
            """SELECT u.name, r.label AS role_label, COUNT(t.id) AS due_this_week
               FROM users u JOIN roles r ON r.id = u.role_id
               LEFT JOIN tasks t ON t.assigned_user_id = u.id AND t.due_date BETWEEN ? AND ? AND t.status != 'complete'
               WHERE u.active = 1 GROUP BY u.id ORDER BY r.is_admin DESC, u.name""",
            (start.isoformat(), end.isoformat()),
        )
    )

    return {
        "week_start": start,
        "week_end": end,
        "posts_total": len(rows),
        "posting_days_total": len(content_module.posting_days_in_range(start, end)),
        "posts_published": published,
        "posts_upcoming": upcoming,
        "tasks_due_count": len(tasks_due),
        "tasks_due": tasks_due,
        "overdue": overdue,
        "awaiting_approval": awaiting_approval,
        "team_load": team_load,
        "mix": content_mix(start, end),
        "warnings": warnings_for_week(),
        "suggestions": suggest_filler(),
    }
