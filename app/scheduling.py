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


def second_last_weekday_of_month(year, month, weekday):
    """The occurrence of `weekday` one week before the last one in the month
    (e.g. Blog Post = second-last Thursday of every month). Always still
    falls inside the same month — the last weekday of a month is never
    earlier than day (last_day - 6), and every month has at least 28 days,
    so subtracting 7 days can't cross back into the previous month."""
    return last_weekday_of_month(year, month, weekday) - timedelta(days=7)


def add_months_ym(year, month, delta):
    total = (year * 12 + (month - 1)) + delta
    return total // 12, total % 12 + 1


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

    if rule["rule_type"] == "monthly_second_last_weekday":
        y, m = after_date.year, after_date.month
        for _ in range(14):
            occ = second_last_weekday_of_month(y, m, rule["weekday"])
            if occ > after_date:
                return occ
            m += 1
            if m > 12:
                m = 1
                y += 1
        return None

    if rule["rule_type"] == "every_n_months_nth_weekday":
        # Occurrences only fall in months that are a whole number of
        # `interval_months` cycles away from the anchor month (e.g. the
        # Podcast Episode's "every 3rd month") — not every month like
        # 'monthly_nth_weekday' above.
        interval = rule["interval_months"] or 3
        anchor = date.fromisoformat(rule["anchor_date"])
        y, m = anchor.year, anchor.month
        occ = nth_weekday_of_month(y, m, rule["weekday"], rule["nth"] or 1)
        for _ in range(60):  # generous cap: 60 cycles is decades out
            if occ and occ > after_date:
                return occ
            y, m = add_months_ym(y, m, interval)
            occ = nth_weekday_of_month(y, m, rule["weekday"], rule["nth"] or 1)
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


# Sept: materialize_rule()'s horizon_end is always "whenever this ran" plus
# the rule's horizon_weeks — so a rule with a 10-16 week horizon quietly
# stops producing new campaigns once that many weeks have passed since the
# last time someone actually re-ran it (materialize_all_active_rules /
# fill_weekly_filler_gaps only fire from seed_data() or the Admin "Sync
# pipeline & reference data" button — nothing re-runs them on its own).
# Jodie caught Monthly/Filler content stopping in November because of
# exactly this: the horizon was last rolled forward whenever that button (or
# a deploy's seed_data()) last ran, and every week since then that nobody
# clicked it, the visible window got one week shorter without her noticing.
#
# HORIZON_REFRESH_HOURS keeps it rolling on its own, the same "no
# scheduler/worker process, so check cheaply on request instead" pattern as
# app/auth.py's pipeline-confirmation check: app_state.horizon_synced_at
# records the last time this actually ran (not every request — most calls
# short-circuit on that one cheap SELECT), and once it's stale the full
# rule + filler-gap materialization runs and the timestamp is bumped. Safe
# to call on every request: both underlying functions are idempotent.
HORIZON_REFRESH_HOURS = 20


def ensure_horizon_rolled_forward():
    from datetime import datetime
    row = db.query_one("SELECT value FROM app_state WHERE key = 'horizon_synced_at'")
    if row:
        try:
            last_synced = datetime.fromisoformat(row["value"])
            if (datetime.utcnow() - last_synced).total_seconds() < HORIZON_REFRESH_HOURS * 3600:
                return
        except ValueError:
            pass  # malformed value — treat as never synced, fall through and re-sync

    materialize_all_active_rules()
    from . import content as content_module
    content_module.fill_weekly_filler_gaps()

    now_iso = datetime.utcnow().isoformat()
    db.execute(
        """INSERT INTO app_state (key, value, updated_at) VALUES ('horizon_synced_at', ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')""",
        (now_iso,),
    )


# ---------------------------------------------------------------------------
# Drag-and-drop resolution
# ---------------------------------------------------------------------------

def get_drag_options(campaign_id):
    """What choices should the UI offer for dragging this campaign?"""
    c = db.row_to_dict(
        db.query_one(
            """SELECT c.*, ct.category_key FROM campaigns c
               LEFT JOIN content_types ct ON ct.id = c.primary_content_type_id
               WHERE c.id = ?""",
            (campaign_id,),
        )
    )
    if not c:
        return None

    if c["schedule_origin"] == "rule" and c["scheduling_rule_id"]:
        rule = db.row_to_dict(
            db.query_one("SELECT rule_type FROM scheduling_rules WHERE id = ?", (c["scheduling_rule_id"],))
        )
        dependents = db.query(
            "SELECT id, title, publish_date FROM campaigns WHERE depends_on_campaign_id = ?",
            (campaign_id,),
        )
        choices = [
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
        ]
        # "Push forward" (Part 10) only makes sense for the monthly-style
        # recurring content, not the biweekly targeted campaign rhythm.
        if rule and rule["rule_type"] != "biweekly":
            choices.append({
                "key": "push_forward",
                "label": "Push this content type forward",
                "description": "Skip this occurrence — it moves to the next available slot, and every later "
                                "occurrence shifts forward one slot too. The schedule keeps going, nothing breaks.",
            })
        return {
            "needs_choice": True,
            "schedule_origin": "rule",
            "dependents": db.rows_to_list(dependents),
            "choices": choices,
        }

    if c["category_key"] == "filler":
        later_filler = db.query_one(
            """SELECT 1 FROM campaigns c2 JOIN content_types ct2 ON ct2.id = c2.primary_content_type_id
               WHERE ct2.category_key = 'filler' AND c2.publish_date > ? AND c2.id != ? LIMIT 1""",
            (c["publish_date"], campaign_id),
        )
        if later_filler:
            return {
                "needs_choice": True,
                "schedule_origin": "manual",
                "dependents": [],
                "choices": [
                    {
                        "key": "only",
                        "label": "Move this post only",
                        "description": "Just this filler post moves. Everything else stays where it is.",
                    },
                    {
                        "key": "push_all_filler_forward",
                        "label": "Move this and push all following filler posts forward",
                        "description": "Every filler post scheduled after this one shifts by the same number of days.",
                    },
                ],
            }

    return {"needs_choice": False, "schedule_origin": c["schedule_origin"]}


def apply_drag(campaign_id, new_date_iso, mode, actor_id=None):
    c = db.row_to_dict(db.query_one("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)))
    if not c:
        raise ValueError("Campaign not found")
    category_key = _category_key_for(c["primary_content_type_id"])

    old_date = c["publish_date"]
    delta_days = (date.fromisoformat(new_date_iso) - date.fromisoformat(old_date)).days

    if mode is None:
        mode = "only"
    if c["schedule_origin"] != "rule" and mode not in ("only", "push_all_filler_forward"):
        # rule-only modes requested on a non-rule campaign — fall back safely
        mode = "only"

    if mode == "only":
        _move_campaign(campaign_id, new_date_iso, delta_days)
        if c["schedule_origin"] == "rule":
            db.execute(
                "UPDATE campaigns SET is_rule_exception = 1, schedule_origin = 'manual', scheduling_rule_id = NULL WHERE id = ?",
                (campaign_id,),
            )
        _log(campaign_id, actor_id, f"Moved from {old_date} to {new_date_iso} (this occurrence only).")
        _rebalance_filler_after_move(
            category_key, actor_id, touched_dates=[old_date, new_date_iso],
            reason=" after this campaign's schedule changed",
        )

    elif mode == "with_dependents":
        pre_dates = _collect_move_dates(campaign_id)  # this campaign + all dependents, before moving
        _move_campaign(campaign_id, new_date_iso, delta_days)
        _shift_dependents(campaign_id, delta_days, actor_id)
        if c["schedule_origin"] == "rule":
            db.execute(
                "UPDATE campaigns SET is_rule_exception = 1, schedule_origin = 'manual', scheduling_rule_id = NULL WHERE id = ?",
                (campaign_id,),
            )
        _log(campaign_id, actor_id, f"Moved from {old_date} to {new_date_iso} along with its dependent content.")
        touched = pre_dates + [_add_days(d, delta_days) for d in pre_dates]
        _rebalance_filler_after_move(
            category_key, actor_id, touched_dates=touched,
            reason=" after this campaign's schedule changed",
        )

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
        earliest = min(date.fromisoformat(old_date), date.fromisoformat(new_date_iso))
        _rebalance_filler_after_move(
            category_key, actor_id, wide_from=earliest, reason=" after the recurring schedule shifted",
        )

    elif mode == "push_forward":
        # The dropped date isn't used here — pushing forward means "skip this
        # slot", not "move to wherever I dropped it"; see push_content_type_forward.
        push_content_type_forward(campaign_id, actor_id=actor_id)

    elif mode == "push_all_filler_forward":
        _move_campaign(campaign_id, new_date_iso, delta_days)
        later = db.query(
            """SELECT c2.id, c2.publish_date FROM campaigns c2
               JOIN content_types ct2 ON ct2.id = c2.primary_content_type_id
               WHERE ct2.category_key = 'filler' AND c2.publish_date > ? AND c2.id != ?
               ORDER BY c2.publish_date""",
            (old_date, campaign_id),
        )
        for row in later:
            row_new_date = _add_days(row["publish_date"], delta_days)
            _move_campaign(row["id"], row_new_date, delta_days)
        _log(
            campaign_id, actor_id,
            f"Moved from {old_date} to {new_date_iso} and pushed {len(later)} later filler post(s) "
            f"forward by {delta_days:+d} day(s).",
        )

    else:
        raise ValueError(f"Unknown drag mode: {mode}")


def push_content_type_forward(campaign_id, actor_id=None):
    """'Push this content type forward' (Part 10): skip this occurrence —
    hand its slot to the next one, cascade every later occurrence of the
    same rule forward by one slot, and extend the chain with a freshly
    computed date at the end so the horizon doesn't shrink. This is how a
    recurring monthly item (e.g. this month's Testimony) gets skipped
    without breaking the rest of the recurring schedule."""
    c = db.row_to_dict(db.query_one("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)))
    if not c:
        raise ValueError("Campaign not found")
    if c["schedule_origin"] != "rule" or not c["scheduling_rule_id"]:
        raise ValueError("This isn't part of a recurring schedule.")
    category_key = _category_key_for(c["primary_content_type_id"])
    earliest_touched = date.fromisoformat(c["publish_date"])

    rule = db.row_to_dict(db.query_one("SELECT * FROM scheduling_rules WHERE id = ?", (c["scheduling_rule_id"],)))
    chain = db.rows_to_list(
        db.query(
            """SELECT id, publish_date FROM campaigns
               WHERE scheduling_rule_id = ? AND publish_date >= ? AND is_rule_exception = 0
               ORDER BY publish_date""",
            (rule["id"], c["publish_date"]),
        )
    )
    if not chain:
        raise ValueError("No upcoming occurrences to push forward.")

    dates = [row["publish_date"] for row in chain]
    last_date = date.fromisoformat(dates[-1])
    next_new = _next_occurrence(rule, last_date)
    if next_new is None:
        raise ValueError("Couldn't work out the next date in this schedule.")
    new_dates = dates[1:] + [next_new.isoformat()]

    for row, new_date_for_row in zip(chain, new_dates):
        delta = (date.fromisoformat(new_date_for_row) - date.fromisoformat(row["publish_date"])).days
        _move_campaign(row["id"], new_date_for_row, delta)
        _shift_dependents(row["id"], delta, actor_id)

    _log(
        campaign_id, actor_id,
        f"Pushed forward — skipped this slot in the '{rule['label']}' schedule; "
        f"every later occurrence moved forward one slot to keep the rhythm going.",
    )
    _rebalance_filler_after_move(
        category_key, actor_id, wide_from=earliest_touched, reason=" after the schedule was pushed forward",
    )
    return {"new_date": new_dates[0]}


def _category_key_for(content_type_id):
    row = db.query_one("SELECT category_key FROM content_types WHERE id = ?", (content_type_id,))
    return row["category_key"] if row else None


def _collect_move_dates(campaign_id, _seen=None):
    """This campaign's current publish_date plus every recursive dependent's,
    read BEFORE any of them are moved — used so a "with dependents" drag can
    tell the filler rebalancer every week that's about to be vacated/entered,
    not just the one campaign the user actually dragged."""
    _seen = _seen if _seen is not None else set()
    if campaign_id in _seen:
        return []
    _seen.add(campaign_id)
    row = db.query_one("SELECT publish_date FROM campaigns WHERE id = ?", (campaign_id,))
    dates = [row["publish_date"]] if row else []
    dependents = db.query("SELECT id FROM campaigns WHERE depends_on_campaign_id = ?", (campaign_id,))
    for dep in dependents:
        dates.extend(_collect_move_dates(dep["id"], _seen))
    return dates


def move_campaign_to_date(campaign_id, new_date_iso):
    """Public wrapper around _move_campaign for callers outside this module
    (content.rebalance_filler_for_weeks) that need to relocate a campaign
    without going through the drag-mode/rule-exception machinery — used to
    relocate an existing Filler post rather than dragging it."""
    c = db.row_to_dict(db.query_one("SELECT publish_date FROM campaigns WHERE id = ?", (campaign_id,)))
    if not c:
        raise ValueError("Campaign not found")
    delta_days = (date.fromisoformat(new_date_iso) - date.fromisoformat(c["publish_date"])).days
    _move_campaign(campaign_id, new_date_iso, delta_days)
    return delta_days


def _rebalance_filler_after_move(category_key, actor_id, touched_dates=None, wide_from=None, reason=""):
    """Sept, per Jodie: moving a Targeted (or Monthly) campaign off/onto a
    day can push a week under the 4-post minimum, or it can drop a week that
    was already under the minimum into range — either way the *existing*
    pool of Filler posts should be reshuffled to cover it automatically,
    without waiting for the next horizon sync. Deliberately does nothing
    when the campaign that moved is Filler itself — a filler post dragged on
    its own only ever affects other filler through the explicit
    "push_all_filler_forward" opt-in, never automatically."""
    if category_key == "filler":
        return
    if not touched_dates and wide_from is None:
        return
    from . import content as content_module  # local import to avoid the top-level cycle
    today = date.today()
    if wide_from is not None:
        # 52 weeks forward from the earliest touched date itself, not from
        # today — a shifted rule can already sit far in the future, and the
        # sweep still needs to cover the year *following that point*.
        horizon_end = wide_from + timedelta(days=7 * 52)
        weeks = content_module.weeks_between(wide_from, horizon_end)
    else:
        weeks = [content_module.week_start_for(d) for d in touched_dates]
    content_module.rebalance_filler_for_weeks(weeks, today=today, actor_id=actor_id, reason=reason)


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
