"""
Recurring scheduling engine.

A SchedulingRule describes a *pattern* ("targeted campaign every 14 days
from this Wednesday", "testimony on the last Tuesday of the month"), not a
list of dates. materialize_rule() turns that pattern into real, editable
Campaign rows out to a rolling horizon. Nothing about a materialized
Campaign is fake or read-only — once created it's a normal row a user can
edit like any other; the rule is only consulted again when generating the
*next* not-yet-created occurrence, or when the user explicitly asks to
shift the rule itself.

Drag-and-drop resolution (Part 12 of the brief) is the other half: moving
a rule-linked campaign must distinguish "change this one date" from
"change the recurring rule from here on", and must know which other
campaigns *depend on* the one being moved (e.g. a highlight snippet
scheduled N days after its parent campaign).
"""
from datetime import date, timedelta
import calendar as pycal

from . import db


def _add_days(iso_date, n):
    d = date.fromisoformat(iso_date)
    return (d + timedelta(days=n)).isoformat()


def _weekday_name(idx):
    return ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][idx]


def nth_weekday_of_month(year, month, weekday, n):
    """1-based nth occurrence of `weekday` (Mon=0) in year/month."""
    first_of_month = date(year, month, 1)
    first_weekday = first_of_month.weekday()
    delta = (weekday - first_weekday) % 7
    day_num = 1 + delta + (n - 1) * 7
    last_day = pycal.monthrange(year, month)[1]
    if day_num > last_day:
        return None
    return date(year, month, day_num)


def last_weekday_of_month(year, month, weekday):
    last_day = pycal.monthrange(year, month)[1]
    d = date(year, month, last_day)
    delta = (d.weekday() - weekday) % 7
    return d - timedelta(days=delta)


def _next_occurrence(rule, after_date):
    """Return the next occurrence strictly after `after_date` (a date object)."""
    if rule["rule_type"] == "biweekly":
        anchor = date.fromisoformat(rule["anchor_date"])
        interval = rule["interval_days"] or 14
        occ = anchor
        # walk forward from the anchor until we're past after_date
        if occ <= after_date:
            steps = ((after_date - occ).days // interval) + 1
            occ = occ + timedelta(days=interval * steps)
        return occ

    if rule["rule_type"] == "monthly_nth_weekday":
        y, m = after_date.year, after_date.month
        for _ in range(14):
            occ = nth_weekday_of_month(y, m, rule["weekday"], rule["nth"] or 1)
            if occ and occ > after_date:
                return occ
            m += 1
            if m > 12:
                m = 1
                y += 1
        return None

    if rule["rule_type"] == "monthly_last_weekday":
        y, m = after_date.year, after_date.month
        for _ in range(14):
            occ = last_weekday_of_month(y, m, rule["weekday"])
            if occ > after_date:
                return occ
            m += 1
            if m > 12:
                m = 1
                y += 1
        return None

    return None


def generate_occurrences(rule, start_date, end_date):
    """All occurrence dates for a rule within [start_date, end_date] inclusive."""
    occurrences = []
    cursor = start_date - timedelta(days=1)
    while True:
        nxt = _next_occurrence(rule, cursor)
        if nxt is None or nxt > end_date:
            break
        occurrences.append(nxt)
        cursor = nxt
    return occurrences


def materialize_rule(rule_id, today=None):
    """Ensure Campaign rows exist for every not-yet-created occurrence of a
    rule within its horizon. Safe to call repeatedly (idempotent)."""
    from . import content as content_module  # local import to avoid cycle

    rule = db.row_to_dict(db.query_one("SELECT * FROM scheduling_rules WHERE id = ?", (rule_id,)))
    if not rule or not rule["active"]:
        return []

    today = today or date.today()
    horizon_end = today + timedelta(days=7 * (rule["horizon_weeks"] or 10))
    anchor = date.fromisoformat(rule["anchor_date"])
    start = min(anchor, today)

    occurrences = generate_occurrences(rule, start, horizon_end)
    # always include the anchor date itself if it falls in range and isn't covered
    if anchor not in occurrences and start <= anchor <= horizon_end:
        occurrences.insert(0, anchor)
        occurrences.sort()

    existing_dates = {
        r["publish_date"]
        for r in db.query(
            "SELECT publish_date FROM campaigns WHERE scheduling_rule_id = ?", (rule_id,)
        )
    }

    created = []
    for occ in occurrences:
        iso = occ.isoformat()
        if iso in existing_dates:
            continue
        campaign_id = content_module.create_campaign_from_rule(rule, iso)
        created.append(campaign_id)
    return created


def materialize_all_active_rules():
    rule_ids = [r["id"] for r in db.query("SELECT id FROM scheduling_rules WHERE active = 1")]
    total = []
    for rid in rule_ids:
        total.extend(materialize_rule(rid))
    return total


# ---------------------------------------------------------------------------
# Drag-and-drop resolution
# ---------------------------------------------------------------------------

def get_drag_options(campaign_id):
    """What choices should the UI offer for dragging this campaign?"""
    c = db.row_to_dict(db.query_one("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)))
    if not c:
        return None
    if c["schedule_origin"] != "rule" or not c["scheduling_rule_id"]:
        return {"needs_choice": False, "schedule_origin": c["schedule_origin"]}

    dependents = db.query(
        "SELECT id, title, publish_date FROM campaigns WHERE depends_on_campaign_id = ?",
        (campaign_id,),
    )
    return {
        "needs_choice": True,
        "schedule_origin": "rule",
        "dependents": db.rows_to_list(dependents),
        "choices": [
            {
                "key": "only",
                "label": "Move this campaign only",
                "description": "Breaks it off the recurring rule. Future occurrences stay on schedule.",
            },
            {
                "key": "with_dependents",
                "label": "Move this campaign and its dependent outputs",
                "description": "Highlight snippets / follow-ups tied to it move by the same amount.",
            },
            {
                "key": "shift_rule",
                "label": "Shift the recurring schedule from here onward",
                "description": "This and every future occurrence not already customised move to the new rhythm.",
            },
        ],
    }


def apply_drag(campaign_id, new_date_iso, mode, actor_id=None):
    c = db.row_to_dict(db.query_one("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)))
    if not c:
        raise ValueError("Campaign not found")

    old_date = c["publish_date"]
    delta_days = (date.fromisoformat(new_date_iso) - date.fromisoformat(old_date)).days

    if c["schedule_origin"] != "rule" or mode is None:
        mode = "only"

    if mode == "only":
        _move_campaign(campaign_id, new_date_iso, delta_days)
        if c["schedule_origin"] == "rule":
            db.execute(
                "UPDATE campaigns SET is_rule_exception = 1, schedule_origin = 'manual', scheduling_rule_id = NULL WHERE id = ?",
                (campaign_id,),
            )
        _log(campaign_id, actor_id, f"Moved from {old_date} to {new_date_iso} (this occurrence only).")

    elif mode == "with_dependents":
        _move_campaign(campaign_id, new_date_iso, delta_days)
        _shift_dependents(campaign_id, delta_days, actor_id)
        if c["schedule_origin"] == "rule":
            db.execute(
                "UPDATE campaigns SET is_rule_exception = 1, schedule_origin = 'manual', scheduling_rule_id = NULL WHERE id = ?",
                (campaign_id,),
            )
        _log(campaign_id, actor_id, f"Moved from {old_date} to {new_date_iso} along with its dependent content.")

    elif mode == "shift_rule":
        _move_campaign(campaign_id, new_date_iso, delta_days)
        _shift_dependents(campaign_id, delta_days, actor_id)
        if c["scheduling_rule_id"]:
            rule = db.row_to_dict(
                db.query_one("SELECT * FROM scheduling_rules WHERE id = ?", (c["scheduling_rule_id"],))
            )
            db.execute(
                "UPDATE scheduling_rules SET anchor_date = ? WHERE id = ?",
                (new_date_iso, rule["id"]),
            )
            # remove future non-exception occurrences so they regenerate on the new rhythm
            db.execute(
                """DELETE FROM campaigns WHERE scheduling_rule_id = ? AND publish_date > ?
                   AND is_rule_exception = 0 AND id != ?""",
                (rule["id"], new_date_iso, campaign_id),
            )
            materialize_rule(rule["id"])
        _log(
            campaign_id,
            actor_id,
            f"Shifted the recurring schedule: from {old_date} onward now anchors to {new_date_iso}.",
        )
    else:
        raise ValueError(f"Unknown drag mode: {mode}")


def _move_campaign(campaign_id, new_date_iso, delta_days):
    db.execute(
        "UPDATE campaigns SET publish_date = ?, updated_at = datetime('now') WHERE id = ?",
        (new_date_iso, campaign_id),
    )
    outputs = db.query("SELECT id, publish_date FROM content_outputs WHERE campaign_id = ?", (campaign_id,))
    for o in outputs:
        new_out_date = _add_days(o["publish_date"], delta_days)
        db.execute("UPDATE content_outputs SET publish_date = ? WHERE id = ?", (new_out_date, o["id"]))
    # shift task due dates by the same delta so lead time relative to publish is preserved
    tasks = db.query("SELECT id, due_date FROM tasks WHERE campaign_id = ? AND due_date IS NOT NULL", (campaign_id,))
    for t in tasks:
        db.execute(
            "UPDATE tasks SET due_date = ? WHERE id = ?",
            (_add_days(t["due_date"], delta_days), t["id"]),
        )


def _shift_dependents(campaign_id, delta_days, actor_id, _seen=None):
    _seen = _seen or set()
    if campaign_id in _seen:
        return
    _seen.add(campaign_id)
    dependents = db.query(
        "SELECT id, publish_date FROM campaigns WHERE depends_on_campaign_id = ?", (campaign_id,)
    )
    for dep in dependents:
        new_date = _add_days(dep["publish_date"], delta_days)
        _move_campaign(dep["id"], new_date, delta_days)
        _log(dep["id"], actor_id, f"Shifted automatically because the campaign it follows moved ({delta_days:+d} days).")
        _shift_dependents(dep["id"], delta_days, actor_id, _seen)


def _log(campaign_id, actor_id, message):
    db.execute(
        "INSERT INTO activity_log (campaign_id, actor_id, message) VALUES (?, ?, ?)",
        (campaign_id, actor_id, message),
    )
