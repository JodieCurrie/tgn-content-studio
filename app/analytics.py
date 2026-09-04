"""
Content balance tracking + smart scheduling warnings/filler suggestions
(Parts 27-29). These are advisory only — nothing here blocks or auto-edits
anything; they just compute numbers and hand back plain-language notes.
"""
from datetime import date, timedelta

from . import db

MIN_POSTS_PER_WEEK = 3
IDEAL_POSTS_PER_WEEK = 4
VIDEO_TYPE_KEYS = {"targeted_short", "targeted_long", "moving_scripture", "tiktok", "highlight", "quick_reel"}


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
    rows = outputs_in_range(start, end)
    total = len(rows)
    warnings = []

    if total < MIN_POSTS_PER_WEEK:
        warnings.append({
            "level": "warning",
            "text": f"Only {total} post{'s' if total != 1 else ''} scheduled this week — your minimum target is {MIN_POSTS_PER_WEEK}.",
        })
    elif total < IDEAL_POSTS_PER_WEEK:
        warnings.append({
            "level": "info",
            "text": f"{total} posts scheduled this week — one more would hit your ideal target of {IDEAL_POSTS_PER_WEEK}.",
        })
    else:
        warnings.append({"level": "success", "text": f"{total} posts scheduled this week — target met."})

    # consecutive same-family (video) warning, in publish-date order
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

    # testimony / last-Tuesday sanity check, informational only
    testimony_rule = db.query_one("SELECT sr.* FROM scheduling_rules sr JOIN content_types ct ON ct.id = sr.content_type_id WHERE ct.key = 'testimony' AND sr.active = 1")
    if testimony_rule:
        from . import scheduling
        last_tue = scheduling.last_weekday_of_month(anchor.year if anchor else date.today().year,
                                                       anchor.month if anchor else date.today().month, 1)
        has_testimony_this_month = db.query_one(
            """SELECT id FROM campaigns WHERE primary_content_type_id = (SELECT id FROM content_types WHERE key='testimony')
               AND publish_date = ?""",
            (last_tue.isoformat(),),
        )
        if has_testimony_this_month:
            warnings.append({"level": "success", "text": f"Testimony scheduled for the last Tuesday of the month ({last_tue.strftime('%-d %b')})."})

    return warnings


def suggest_filler(anchor=None, limit=3):
    start, end = week_bounds(anchor)
    rows = outputs_in_range(start, end)
    total = len(rows)
    if total >= IDEAL_POSTS_PER_WEEK:
        return []

    used_keys = {r["type_key"] for r in rows}
    filler_types = db.rows_to_list(
        db.query("SELECT * FROM content_types WHERE is_filler = 1 AND archived = 0 ORDER BY sort_order")
    )
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
            """SELECT t.*, c.title AS campaign_title FROM tasks t JOIN campaigns c ON c.id = t.campaign_id
               WHERE t.due_date BETWEEN ? AND ? AND t.status NOT IN ('complete')
               ORDER BY t.due_date""",
            (start.isoformat(), end.isoformat()),
        )
    )
    overdue = db.rows_to_list(
        db.query(
            """SELECT t.*, c.title AS campaign_title FROM tasks t JOIN campaigns c ON c.id = t.campaign_id
               WHERE t.due_date < ? AND t.status NOT IN ('complete') ORDER BY t.due_date""",
            (date.today().isoformat(),),
        )
    )
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
