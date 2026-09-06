"""
Targeted Video production pipeline (Part 14-18+): every "shoot" — one Short
+ one YouTube/Long output on the same campaign — moves through two tiers of
stages.

SHOOT-level stages are shared ONCE per campaign, not duplicated per output,
since Short and Long come from the same shoot:

    Script Development -> Concept Hashout -> Film/Record

  * Script Development — two plain tasks ("Write script" and "Write
    YouTube script", both tagged stage_key='script'); the stage only
    completes once both are done. The panel also points at the campaign's
    two script fields (`script` / `script_youtube`), which already exist.
  * Concept Hashout — once unlocked, shows a "Schedule this meeting" action
    (date/time/participants) instead of a checklist, exactly like before.
    Once the scheduled end time is in the past and unconfirmed, it becomes
    a pending confirmation (pending_confirmations_for_admin) that prompts
    on the next admin login. "Yes" completes the stage; "No" reopens the
    same form pre-filled, to reschedule (same Calendar event, PATCHed).
  * Film/Record — identical meeting mechanics, but confirming "yes"
    chains into capturing the post-shoot hand-off: pick the editor (a real
    user) + a deadline (see confirm_film_with_delivery). That hand-off
    assigns EVERY sibling output's Edit task at once (and, for the Short
    sibling, silently pre-assigns Final Compilation too) — one shoot, one
    editor conversation, even though Short and Long branch from here.

PRODUCTION-level stages branch per output from there:

    targeted_short:  Edit -> Review Edit -> Audio -> Review Audio -> Final Compilation
    targeted_long:   Edit -> Review Edit -> Select Highlight Reels

  * Edit — a plain task, assigned via the Film/Record hand-off above.
    "Submit for review" is just completing it normally.
  * Review Edit / Review Audio — an approve/reject gate (approve_review /
    reject_review), not a plain checklist: rejecting resets the stage it
    reviews back to not_started (with Jodie's notes appended and emailed
    to the assignee), looping until approved.
  * Audio (Short only) — unlocks once Review Edit approves. Needs an
    explicit "assign & send" action (assign_and_notify) to pick the
    musician and a deadline before it's a plain checklist item like Edit.
  * Final Compilation (Short only) — unlocks once Review Audio approves,
    auto-assigned to the same editor from the Film/Record hand-off, and
    notified by email once it unlocks. The editor "submits" a Drive link
    (submit_compilation) rather than checking a box; Jodie's explicit
    "Mark as received" (mark_compilation_received) is what actually
    finishes it — the app can't detect a real file download.
  * Select Highlight Reels (Long only) — unlocks once Review Edit approves
    (Long has no separate audio/compilation stage — the editor adds
    generic audio as part of editing). Jodie captures 2-3 candidate
    timestamp ranges (capture_highlight_candidates), which hands the task
    to the same editor; they submit cut clips (submit_highlights); Jodie's
    "Mark as received" (mark_highlights_received) finishes it AND feeds
    the clips directly into the existing highlight_1/highlight_2
    follow-up campaigns' own tasks.

A stage has no independent status of its own: it's derived from the
statuses of the tasks tagged with that stage_key (see task_templates /
tasks.stage_key, and task_engine.py which copies the tag when it generates
a task from a template). Shared shoot-stage tasks are tagged with
campaign_id + stage_key and output_id NULL; production-stage tasks keep
the existing output_id + stage_key tagging. `pipeline_stages` rows hold
what tasks can't: meeting scheduling, hand-off/review/submission
bookkeeping, and the one-time Calendar/Meet/Drive side effects.
"""
from datetime import date, datetime, timedelta, timezone

from . import db
from . import google_integration as gcal
from . import email_integration

# ---------------------------------------------------------------------------
# Stage catalog
# ---------------------------------------------------------------------------
SHOOT_STAGE_KEYS = ("script", "concept", "film")
MEETING_STAGE_KEYS = ("concept", "film")
REVIEW_STAGE_KEYS = ("review_edit", "review_audio")
REOPENS_STAGE = {"review_edit": "edit", "review_audio": "audio"}
SUBMISSION_STAGE_KEYS = ("compilation", "highlights")

PRODUCTION_STAGES_BY_TYPE = {
    "targeted_short": ["edit", "review_edit", "audio", "review_audio", "compilation"],
    "targeted_long": ["edit", "review_edit", "highlights"],
}

STAGE_BY_KEY = {
    "script": {"key": "script", "label": "Script Development"},
    "concept": {"key": "concept", "label": "Concept Hashout", "is_meeting": True, "meet": True},
    "film": {"key": "film", "label": "Film/Record", "is_meeting": True, "meet": False},
    "edit": {"key": "edit", "label": "Edit"},
    "review_edit": {"key": "review_edit", "label": "Review Edit"},
    "audio": {"key": "audio", "label": "Audio Creation"},
    "review_audio": {"key": "review_audio", "label": "Review Audio"},
    "compilation": {"key": "compilation", "label": "Final Compilation"},
    "highlights": {"key": "highlights", "label": "Select Highlight Reels"},
}

# The two output types this pipeline applies to — every other content type
# keeps its plain flat task list, untouched.
PIPELINE_CONTENT_TYPE_KEYS = ("targeted_short", "targeted_long")

# Google Calendar colorId for pipeline meetings/deadlines — "8" is Google's
# "Graphite" (a neutral grey), visually distinct from the light-grey
# (#D9D9D9) used by the separate "Custom Event" content type.
MEETING_CALENDAR_COLOR_ID = "8"


def is_pipeline_content_type_id(content_type_id):
    row = db.query_one("SELECT key FROM content_types WHERE id = ?", (content_type_id,))
    return bool(row) and row["key"] in PIPELINE_CONTENT_TYPE_KEYS


def _content_type_key(content_type_id):
    row = db.query_one("SELECT key FROM content_types WHERE id = ?", (content_type_id,))
    return row["key"] if row else None


# ---------------------------------------------------------------------------
# Setup — idempotent, safe to call any number of times
# ---------------------------------------------------------------------------
def ensure_pipeline_for_output(output_id):
    """Creates the campaign's 3 shared shoot-stage rows (once per campaign)
    and this output's own production-stage rows for its type. Call whenever
    an output's tasks are (re)generated."""
    output = db.row_to_dict(db.query_one("SELECT * FROM content_outputs WHERE id = ?", (output_id,)))
    if not output or not is_pipeline_content_type_id(output["content_type_id"]):
        return
    _ensure_shoot_stages(output["campaign_id"])
    ct_key = _content_type_key(output["content_type_id"])
    for i, stage_key in enumerate(PRODUCTION_STAGES_BY_TYPE.get(ct_key, [])):
        existing = db.query_one(
            "SELECT id FROM pipeline_stages WHERE output_id = ? AND stage_key = ?", (output_id, stage_key)
        )
        if not existing:
            db.execute(
                "INSERT INTO pipeline_stages (output_id, stage_key, sort_order) VALUES (?, ?, ?)",
                (output_id, stage_key, i),
            )


def _ensure_shoot_stages(campaign_id):
    existing = db.query_one("SELECT id FROM pipeline_stages WHERE campaign_id = ? LIMIT 1", (campaign_id,))
    if existing:
        return
    for i, stage_key in enumerate(SHOOT_STAGE_KEYS):
        db.execute(
            "INSERT INTO pipeline_stages (campaign_id, stage_key, sort_order) VALUES (?, ?, ?)",
            (campaign_id, stage_key, i),
        )


# ---------------------------------------------------------------------------
# Status views
# ---------------------------------------------------------------------------
def get_shoot_stages_with_status(campaign_id):
    """The 3 shared shoot-level stages for this campaign (script/concept/
    film), each annotated the same way get_stages_with_status always has.
    Returns [] for a campaign with no pipeline outputs. Render this ONCE
    per campaign, above the per-output trackers."""
    rows = db.rows_to_list(
        db.query("SELECT * FROM pipeline_stages WHERE campaign_id = ? ORDER BY sort_order", (campaign_id,))
    )
    if not rows:
        return []
    all_tasks = db.rows_to_list(
        db.query(
            "SELECT * FROM tasks WHERE campaign_id = ? AND output_id IS NULL AND stage_key IS NOT NULL ORDER BY due_date, id",
            (campaign_id,),
        )
    )
    tasks_by_stage = {}
    for t in all_tasks:
        tasks_by_stage.setdefault(t["stage_key"], []).append(t)

    now_iso = datetime.utcnow().isoformat()
    result = []
    previous_complete = True  # Script Development is always unlocked
    for row in rows:
        tasks = tasks_by_stage.get(row["stage_key"], [])
        status = _derive_status(tasks)
        unlocked = previous_complete
        is_meeting_stage = row["stage_key"] in MEETING_STAGE_KEYS
        participant_ids = db.from_json(row.get("participant_user_ids"), [])
        entry = {
            **row,
            "label": STAGE_BY_KEY[row["stage_key"]]["label"],
            "status": status,
            "unlocked": unlocked,
            "tasks": tasks,
            "is_meeting_stage": is_meeting_stage,
            "participant_ids": participant_ids,
            "participant_names": names_for_user_ids(participant_ids),
        }
        if is_meeting_stage:
            has_meeting = bool(row.get("meeting_start"))
            confirmed = bool(row.get("meeting_confirmed_at"))
            meeting_passed = bool(row.get("meeting_end")) and row["meeting_end"] < now_iso
            entry["needs_scheduling"] = unlocked and status != "complete" and not has_meeting
            entry["needs_confirmation"] = unlocked and has_meeting and meeting_passed and not confirmed
            entry["scheduled_upcoming"] = unlocked and has_meeting and not meeting_passed and not confirmed and status != "complete"
            if row["stage_key"] == "film":
                entry["recipient_name"] = _name_for_user_id(row.get("delivery_recipient_user_id"))
        result.append(entry)
        previous_complete = status == "complete"
    return result


def get_stages_with_status(output_id):
    """This output's own production-level stages (Edit onward), each
    annotated with derived status/unlocked plus whatever UI state its kind
    needs (review/assignment/submission). The first stage only unlocks once
    the campaign's shared shoot stages (script/concept/film) are all
    complete. Returns [] for a non-pipeline output."""
    output = db.row_to_dict(db.query_one("SELECT * FROM content_outputs WHERE id = ?", (output_id,)))
    if not output:
        return []
    shoot = get_shoot_stages_with_status(output["campaign_id"])
    shoot_complete = bool(shoot) and shoot[-1]["status"] == "complete"

    rows = db.rows_to_list(
        db.query("SELECT * FROM pipeline_stages WHERE output_id = ? ORDER BY sort_order", (output_id,))
    )
    if not rows:
        return []
    all_tasks = db.rows_to_list(
        db.query("SELECT * FROM tasks WHERE output_id = ? AND stage_key IS NOT NULL ORDER BY due_date, id", (output_id,))
    )
    tasks_by_stage = {}
    for t in all_tasks:
        tasks_by_stage.setdefault(t["stage_key"], []).append(t)

    result = []
    previous_complete = shoot_complete
    for row in rows:
        stage_key = row["stage_key"]
        tasks = tasks_by_stage.get(stage_key, [])
        status = _derive_status(tasks)
        unlocked = previous_complete
        entry = {
            **row,
            "label": STAGE_BY_KEY[stage_key]["label"],
            "status": status,
            "unlocked": unlocked,
            "tasks": tasks,
            "is_review_stage": stage_key in REVIEW_STAGE_KEYS,
            "is_submission_stage": stage_key in SUBMISSION_STAGE_KEYS,
        }
        if stage_key in REVIEW_STAGE_KEYS:
            entry["needs_review"] = unlocked and status != "complete"
        if stage_key == "audio":
            entry["needs_assignment"] = unlocked and status != "complete" and not row.get("delivery_recipient_user_id")
            entry["recipient_name"] = _name_for_user_id(row.get("delivery_recipient_user_id"))
        if stage_key == "highlights":
            entry["needs_capture"] = unlocked and status != "complete" and not row.get("highlight_candidates")
            entry["highlight_candidates"] = db.from_json(row.get("highlight_candidates"), [])
            entry["needs_submission"] = (
                unlocked and status != "complete" and bool(row.get("highlight_candidates")) and not row.get("submission_link")
            )
            entry["has_submission"] = unlocked and status != "complete" and bool(row.get("submission_link"))
            entry["submitted_clips"] = db.from_json(row.get("submission_link"), [])
        if stage_key == "compilation":
            entry["needs_submission"] = unlocked and status != "complete" and not row.get("submission_link")
            entry["has_submission"] = unlocked and status != "complete" and bool(row.get("submission_link"))
        result.append(entry)
        previous_complete = status == "complete"
    return result


def _derive_status(tasks):
    if not tasks:
        return "not_started"
    if all(t["status"] == "complete" for t in tasks):
        return "complete"
    if any(t["status"] != "not_started" for t in tasks):
        return "in_progress"
    return "not_started"


def can_advance_task(task):
    """Gating check for PATCH /api/tasks/<id>: may this task's status be
    changed away from 'not_started'? Non-pipeline tasks (stage_key is NULL)
    are never gated. Script Development stays a plain checklist item.
    Concept/Film (meetings), Review Edit/Audio (approve-reject), and
    Compilation/Highlights (submit + admin mark-received) can NEVER be
    flipped by hand — each has its own dedicated action. Edit and Audio are
    plain checklist items once unlocked/assigned."""
    stage_key = task.get("stage_key")
    if not stage_key or stage_key not in STAGE_BY_KEY:
        return True
    if stage_key == "script":
        return True
    if stage_key in MEETING_STAGE_KEYS or stage_key in REVIEW_STAGE_KEYS or stage_key in SUBMISSION_STAGE_KEYS:
        return False
    stages = get_stages_with_status(task["output_id"])
    entry = next((s for s in stages if s["stage_key"] == stage_key), None)
    return entry["unlocked"] if entry else True


# ---------------------------------------------------------------------------
# Cascading — fires after any pipeline task's status is saved
# ---------------------------------------------------------------------------
def after_task_status_change(task_id):
    task = db.row_to_dict(db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,)))
    if not task or not task["stage_key"]:
        return
    if task["output_id"] is None:
        _after_shoot_task_change(task["campaign_id"], task["stage_key"])
    else:
        _after_production_task_change(task["output_id"], task["stage_key"])


def _after_shoot_task_change(campaign_id, stage_key):
    stages = get_shoot_stages_with_status(campaign_id)
    idx = next((i for i, s in enumerate(stages) if s["stage_key"] == stage_key), None)
    if idx is None or stages[idx]["status"] != "complete":
        return
    if idx + 1 < len(stages):
        _on_shoot_stage_unlocked(campaign_id, stages[idx + 1])
    else:
        # Film/Record just completed — cascade unlock bookkeeping to every
        # sibling output's first production stage. (confirm_film_with_delivery
        # already assigned Edit/Compilation synchronously; this just marks
        # the one-time "unlocked" bookkeeping, idempotent via activated_at.)
        outputs = db.rows_to_list(db.query("SELECT id FROM content_outputs WHERE campaign_id = ?", (campaign_id,)))
        for o in outputs:
            prod_stages = get_stages_with_status(o["id"])
            if prod_stages:
                _on_stage_unlocked(prod_stages[0])


def _on_shoot_stage_unlocked(campaign_id, stage_row):
    if stage_row.get("activated_at"):
        return
    db.execute("UPDATE pipeline_stages SET activated_at = datetime('now') WHERE id = ?", (stage_row["id"],))
    if stage_row["stage_key"] == "concept":
        campaign = db.row_to_dict(db.query_one("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)))
        if campaign and not campaign.get("drive_folder_id"):
            _create_drive_folder_for_campaign(campaign)


def _after_production_task_change(output_id, stage_key):
    stages = get_stages_with_status(output_id)
    idx = next((i for i, s in enumerate(stages) if s["stage_key"] == stage_key), None)
    if idx is None:
        return
    if stages[idx]["status"] == "complete" and idx + 1 < len(stages):
        _on_stage_unlocked(stages[idx + 1])


def _on_stage_unlocked(stage_row):
    if stage_row.get("activated_at"):
        return
    db.execute("UPDATE pipeline_stages SET activated_at = datetime('now') WHERE id = ?", (stage_row["id"],))
    if stage_row["stage_key"] == "compilation":
        _notify_compilation_ready(stage_row)


def _notify_compilation_ready(stage_row):
    output = db.row_to_dict(db.query_one("SELECT * FROM content_outputs WHERE id = ?", (stage_row["output_id"],)))
    task = db.query_one("SELECT assigned_user_id FROM tasks WHERE output_id = ? AND stage_key = 'compilation'", (output["id"],))
    if not task or not task["assigned_user_id"]:
        return
    assignee = db.query_one("SELECT name, email FROM users WHERE id = ?", (task["assigned_user_id"],))
    if not assignee or not assignee["email"]:
        return
    campaign = _campaign_title_and_owner(output)
    title = campaign["title"] if campaign else _output_type_label(output)
    drive_link = campaign.get("drive_folder_link") if campaign else None
    try:
        email_integration.send_email(
            assignee["email"],
            f"Time for final compilation: {title}",
            f"Hi {assignee['name']},\n\nThe audio's approved — time to put together the final compilation for \u201c{title}\u201d.\n"
            f"Files: {drive_link or '(ask Jodie for the folder link)'}\n\nThanks!\n",
        )
        _log(output["campaign_id"], f"Notified {assignee['name']} that Final Compilation is ready.")
    except email_integration.EmailNotConfigured:
        pass
    except Exception as e:
        _log(output["campaign_id"], f"Final Compilation unlocked, but notifying {assignee['name']} failed: {e}")


# ---------------------------------------------------------------------------
# Meeting scheduling (Concept Hashout / Film-Record) — campaign-scoped
# ---------------------------------------------------------------------------
def schedule_meeting(campaign_id, stage_key, start_iso, end_iso, participant_ids):
    """Creates the meeting the first time, or updates it in place on a
    reschedule (same DB row, same Calendar event — PATCHed rather than
    duplicated). Returns the campaign's refreshed shoot-stage list."""
    if stage_key not in MEETING_STAGE_KEYS:
        raise ValueError(f"'{stage_key}' isn't a schedulable pipeline stage.")
    stage = db.row_to_dict(
        db.query_one("SELECT * FROM pipeline_stages WHERE campaign_id = ? AND stage_key = ?", (campaign_id, stage_key))
    )
    if not stage:
        raise ValueError("No such pipeline stage for this campaign.")

    campaign = db.row_to_dict(db.query_one("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)))
    config = STAGE_BY_KEY[stage_key]
    label = config["label"]
    start_dt = _parse_dt(start_iso)
    end_dt = _parse_dt(end_iso)
    title = f"{label}: {campaign['title']}" if campaign else label

    db.execute(
        """UPDATE pipeline_stages
           SET meeting_start = ?, meeting_end = ?, participant_user_ids = ?, meeting_confirmed_at = NULL
           WHERE id = ?""",
        (start_dt.isoformat(), end_dt.isoformat(), db.to_json(list(participant_ids or [])), stage["id"]),
    )

    emails = _emails_for_user_ids(participant_ids)
    try:
        if stage.get("calendar_event_id"):
            gcal.update_calendar_event(
                stage["calendar_event_id"], summary=title, start_dt=start_dt, end_dt=end_dt, attendee_emails=emails,
            )
            _log(campaign_id, f"Rescheduled \u201c{title}\u201d on Google Calendar.")
        else:
            event = gcal.create_calendar_event(
                summary=title,
                description=f"Auto-created by TGN Content Studio for the \u201c{campaign['title']}\u201d pipeline." if campaign else "",
                start_dt=start_dt, end_dt=end_dt, attendee_emails=emails,
                create_meet_link=bool(config.get("meet")), color_id=MEETING_CALENDAR_COLOR_ID,
            )
            meet_link = None
            if config.get("meet"):
                for entry in (event.get("conferenceData", {}) or {}).get("entryPoints", []) or []:
                    if entry.get("entryPointType") == "video":
                        meet_link = entry.get("uri")
                        break
            db.execute(
                "UPDATE pipeline_stages SET calendar_event_id = ?, calendar_link = ?, meet_link = ? WHERE id = ?",
                (event.get("id"), event.get("htmlLink"), meet_link, stage["id"]),
            )
            _log(campaign_id, f"Scheduled \u201c{title}\u201d on Google Calendar.")
    except gcal.GoogleNotConnected:
        _log(campaign_id, f"Saved the meeting time for \u201c{title}\u201d, but Google isn't connected yet (Admin → Integrations) so no Calendar event was created.")
    except Exception as e:
        _log(campaign_id, f"Saved the meeting time for \u201c{title}\u201d, but the Calendar event couldn't be saved: {e}")

    return get_shoot_stages_with_status(campaign_id)


def pending_confirmations_for_admin():
    """Meeting stages (concept/film — always campaign-scoped now) whose
    scheduled end time has passed with nobody having said whether it
    happened — these prompt an admin on their next login."""
    rows = db.rows_to_list(
        db.query(
            """SELECT ps.*, c.title AS campaign_title
               FROM pipeline_stages ps
               JOIN campaigns c ON c.id = ps.campaign_id
               WHERE ps.meeting_end IS NOT NULL AND datetime(ps.meeting_end) < datetime('now')
                 AND ps.meeting_confirmed_at IS NULL
               ORDER BY ps.meeting_end"""
        )
    )
    for r in rows:
        r["label"] = STAGE_BY_KEY[r["stage_key"]]["label"]
    return rows


def confirm_stage_happened(stage_id):
    """'Yes, it happened' for Concept Hashout. (Film/Record's "yes" goes
    through confirm_film_with_delivery instead.) Marks the shared stage's
    task(s) complete, unlocking Film/Record for the whole shoot."""
    stage = _get_stage_row_by_id(stage_id)
    if not stage:
        raise ValueError("No such pipeline stage.")
    db.execute("UPDATE pipeline_stages SET meeting_confirmed_at = datetime('now') WHERE id = ?", (stage_id,))
    _complete_shared_stage_tasks(stage["campaign_id"], stage["stage_key"])
    return get_shoot_stages_with_status(stage["campaign_id"])


def reschedule_stage(stage_id, start_iso, end_iso, participant_ids):
    """'No, it needs rescheduling' — reopens scheduling with a new
    time/participant list for the same (campaign-scoped) stage/event."""
    stage = _get_stage_row_by_id(stage_id)
    if not stage:
        raise ValueError("No such pipeline stage.")
    return schedule_meeting(stage["campaign_id"], stage["stage_key"], start_iso, end_iso, participant_ids)


def confirm_film_with_delivery(stage_id, recipient_user_id, deadline_iso, note=""):
    """'Yes, Film/Record happened' + the post-shoot hand-off, captured in
    the same flow: assigns EVERY sibling pipeline output's Edit task to the
    chosen editor (a real user account) with the given deadline — and, for
    the Short sibling, silently pre-assigns Final Compilation too, since
    that's the same editor merging in the audio later. Creates one
    external-only Calendar deadline invite for the editor (not shown in the
    app's own calendar views — Jodie's calendar keeps showing only the
    publish date) and sends one hand-off email pointing at the shared Drive
    folder. Completes Film/Record, unlocking Edit for both outputs."""
    stage = _get_stage_row_by_id(stage_id)
    if not stage or stage["stage_key"] != "film":
        raise ValueError("This isn't the Film/Record stage.")
    campaign_id = stage["campaign_id"]
    campaign = db.row_to_dict(db.query_one("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)))
    recipient = db.row_to_dict(db.query_one("SELECT * FROM users WHERE id = ?", (recipient_user_id,)))
    if not recipient:
        raise ValueError("No such user.")

    db.execute(
        """UPDATE pipeline_stages
           SET meeting_confirmed_at = datetime('now'), delivery_recipient_user_id = ?, delivery_deadline = ?
           WHERE id = ?""",
        (recipient_user_id, deadline_iso, stage_id),
    )

    outputs = db.rows_to_list(
        db.query(
            """SELECT o.*, ct.key AS ct_key FROM content_outputs o
               JOIN content_types ct ON ct.id = o.content_type_id
               WHERE o.campaign_id = ? AND ct.key IN ('targeted_short', 'targeted_long')""",
            (campaign_id,),
        )
    )
    for output in outputs:
        _assign_task(output["id"], "edit", recipient_user_id, deadline_iso, note)
        if output["ct_key"] == "targeted_short":
            comp_task = db.query_one("SELECT id FROM tasks WHERE output_id = ? AND stage_key = 'compilation'", (output["id"],))
            if comp_task:
                db.execute("UPDATE tasks SET assigned_user_id = ? WHERE id = ?", (recipient_user_id, comp_task["id"]))

    title = campaign["title"] if campaign else "the shoot"
    drive_link = campaign.get("drive_folder_link") if campaign else None
    if recipient.get("email"):
        try:
            start_dt, end_dt = _deadline_event_window(deadline_iso)
            gcal.create_calendar_event(
                summary=f"Deadline: Edit — {title}",
                description=f"Auto-created by TGN Content Studio. Raw footage: {drive_link or '(ask Jodie for the folder link)'}",
                start_dt=start_dt, end_dt=end_dt, attendee_emails=[recipient["email"]],
                create_meet_link=False, color_id=MEETING_CALENDAR_COLOR_ID,
            )
        except gcal.GoogleNotConnected:
            _log(campaign_id, f"Assigned the edit to {recipient['name']} — Google isn't connected yet, so no deadline invite was created.")
        except Exception as e:
            _log(campaign_id, f"Assigned the edit to {recipient['name']}, but the deadline invite couldn't be created: {e}")

    try:
        if recipient.get("email"):
            body = (
                f"Hi {recipient['name']},\n\nThe shoot for \u201c{title}\u201d is done — here's the shared Drive folder "
                f"with the raw footage:\n{drive_link or '(ask Jodie for the folder link)'}\n\n"
                f"Please have your edit ready by {deadline_iso}.\n"
            )
            if note:
                body += f"\n{note}\n"
            email_integration.send_email(recipient["email"], f"Time to edit: {title}", body)
            db.execute("UPDATE pipeline_stages SET delivery_email_sent_at = datetime('now') WHERE id = ?", (stage_id,))
        _log(campaign_id, f"Sent the edit hand-off to {recipient['name']} ({recipient.get('email') or 'no email on file'}).")
    except email_integration.EmailNotConfigured:
        _log(campaign_id, f"Recorded the edit hand-off to {recipient['name']} — email isn't configured yet, so let them know directly.")
    except Exception as e:
        _log(campaign_id, f"Recorded the edit hand-off to {recipient['name']}, but the email failed to send ({e}).")

    _complete_shared_stage_tasks(campaign_id, "film")
    return get_shoot_stages_with_status(campaign_id)


def _assign_task(output_id, stage_key, recipient_user_id, deadline_iso, note=""):
    task = db.query_one("SELECT id, notes FROM tasks WHERE output_id = ? AND stage_key = ?", (output_id, stage_key))
    if not task:
        return
    new_notes = task["notes"] or ""
    if note:
        new_notes = (new_notes + "\n" if new_notes else "") + note
    db.execute(
        "UPDATE tasks SET assigned_user_id = ?, due_date = ?, notes = ?, updated_at = datetime('now') WHERE id = ?",
        (recipient_user_id, deadline_iso, new_notes, task["id"]),
    )


# ---------------------------------------------------------------------------
# Generic hand-off (Audio's assign step; Highlights' capture-assign step)
# ---------------------------------------------------------------------------
def assign_and_notify(stage_id, recipient_user_id, deadline_iso, note="", create_calendar_invite=True):
    stage = _get_stage_row_by_id(stage_id)
    if not stage or stage["stage_key"] not in ("audio", "highlights"):
        raise ValueError("This stage doesn't use assign-and-notify.")
    output = db.row_to_dict(db.query_one("SELECT * FROM content_outputs WHERE id = ?", (stage["output_id"],)))
    recipient = db.row_to_dict(db.query_one("SELECT * FROM users WHERE id = ?", (recipient_user_id,)))
    if not recipient:
        raise ValueError("No such user.")

    db.execute(
        "UPDATE pipeline_stages SET delivery_recipient_user_id = ?, delivery_deadline = ? WHERE id = ?",
        (recipient_user_id, deadline_iso, stage_id),
    )
    _assign_task(output["id"], stage["stage_key"], recipient_user_id, deadline_iso, note)

    campaign = _campaign_title_and_owner(output)
    title = campaign["title"] if campaign else _output_type_label(output)
    drive_link = campaign.get("drive_folder_link") if campaign else None
    label = STAGE_BY_KEY[stage["stage_key"]]["label"]

    if create_calendar_invite and recipient.get("email"):
        try:
            start_dt, end_dt = _deadline_event_window(deadline_iso)
            gcal.create_calendar_event(
                summary=f"Deadline: {label} — {title}",
                description=f"Auto-created by TGN Content Studio. Files: {drive_link or '(ask Jodie for the folder link)'}",
                start_dt=start_dt, end_dt=end_dt, attendee_emails=[recipient["email"]],
                create_meet_link=False, color_id=MEETING_CALENDAR_COLOR_ID,
            )
        except gcal.GoogleNotConnected:
            _log(output["campaign_id"], f"Assigned {label} to {recipient['name']} — Google isn't connected yet, so no deadline invite was created.")
        except Exception as e:
            _log(output["campaign_id"], f"Assigned {label} to {recipient['name']}, but the deadline invite couldn't be created: {e}")

    try:
        if recipient.get("email"):
            body = f"Hi {recipient['name']},\n\n{label} is ready for \u201c{title}\u201d.\n"
            if drive_link:
                body += f"Files: {drive_link}\n"
            body += f"Please have it ready by {deadline_iso}.\n"
            if note:
                body += f"\n{note}\n"
            email_integration.send_email(recipient["email"], f"{label}: {title}", body)
            db.execute("UPDATE pipeline_stages SET delivery_email_sent_at = datetime('now') WHERE id = ?", (stage_id,))
        _log(output["campaign_id"], f"Assigned {label} to {recipient['name']} ({recipient.get('email') or 'no email on file'}), due {deadline_iso}.")
    except email_integration.EmailNotConfigured:
        _log(output["campaign_id"], f"Assigned {label} to {recipient['name']} — email isn't configured yet, so let them know directly.")
    except Exception as e:
        _log(output["campaign_id"], f"Assigned {label} to {recipient['name']}, but the email failed to send ({e}).")

    return get_stages_with_status(output["id"])


# ---------------------------------------------------------------------------
# Review Edit / Review Audio — approve/reject
# ---------------------------------------------------------------------------
def approve_review(stage_id, notes=""):
    stage = _get_stage_row_by_id(stage_id)
    if not stage or stage["stage_key"] not in REVIEW_STAGE_KEYS:
        raise ValueError("This isn't a review stage.")
    db.execute(
        "UPDATE pipeline_stages SET review_decision = 'approved', review_notes = ?, reviewed_at = datetime('now') WHERE id = ?",
        (notes, stage_id),
    )
    _complete_stage_tasks(stage["output_id"], stage["stage_key"])
    return get_stages_with_status(stage["output_id"])


def reject_review(stage_id, notes):
    stage = _get_stage_row_by_id(stage_id)
    if not stage or stage["stage_key"] not in REVIEW_STAGE_KEYS:
        raise ValueError("This isn't a review stage.")
    reopens = REOPENS_STAGE[stage["stage_key"]]
    db.execute(
        "UPDATE pipeline_stages SET review_decision = 'rejected', review_notes = ?, reviewed_at = datetime('now') WHERE id = ?",
        (notes, stage_id),
    )
    output = db.row_to_dict(db.query_one("SELECT * FROM content_outputs WHERE id = ?", (stage["output_id"],)))
    task = db.query_one("SELECT id, notes, assigned_user_id FROM tasks WHERE output_id = ? AND stage_key = ?", (output["id"], reopens))
    if task:
        new_notes = task["notes"] or ""
        new_notes = (new_notes + "\n" if new_notes else "") + f"[Revision requested] {notes}"
        db.execute(
            "UPDATE tasks SET status = 'not_started', notes = ?, updated_at = datetime('now') WHERE id = ?",
            (new_notes, task["id"]),
        )
        if task["assigned_user_id"]:
            assignee = db.query_one("SELECT name, email FROM users WHERE id = ?", (task["assigned_user_id"],))
            if assignee and assignee["email"]:
                campaign = _campaign_title_and_owner(output)
                title = campaign["title"] if campaign else _output_type_label(output)
                try:
                    email_integration.send_email(
                        assignee["email"], f"Changes requested: {title}",
                        f"Hi {assignee['name']},\n\nJodie requested changes on {STAGE_BY_KEY[reopens]['label']} for \u201c{title}\u201d:\n\n{notes}\n\nPlease revise and resubmit.\n",
                    )
                except email_integration.EmailNotConfigured:
                    pass
                except Exception:
                    pass
    _log(output["campaign_id"], f"Requested changes on {STAGE_BY_KEY[stage['stage_key']]['label']}: {notes}")
    return get_stages_with_status(output["id"])


# ---------------------------------------------------------------------------
# Final Compilation (Short only)
# ---------------------------------------------------------------------------
def submit_compilation(stage_id, link, notes=""):
    stage = _get_stage_row_by_id(stage_id)
    if not stage or stage["stage_key"] != "compilation":
        raise ValueError("This isn't the Final Compilation stage.")
    db.execute(
        "UPDATE pipeline_stages SET submission_link = ?, submission_notes = ?, submitted_at = datetime('now') WHERE id = ?",
        (link, notes, stage_id),
    )
    _notify_admins_of_submission(stage, link, notes)
    return get_stages_with_status(stage["output_id"])


def mark_compilation_received(stage_id):
    stage = _get_stage_row_by_id(stage_id)
    if not stage or stage["stage_key"] != "compilation":
        raise ValueError("This isn't the Final Compilation stage.")
    _complete_stage_tasks(stage["output_id"], "compilation")
    return get_stages_with_status(stage["output_id"])


# ---------------------------------------------------------------------------
# Select Highlight Reels (Long only)
# ---------------------------------------------------------------------------
def capture_highlight_candidates(stage_id, candidates, deadline_iso=None):
    """Jodie's step: records 2-3 candidate timestamp ranges and hands the
    Highlights task off to the same editor who did the Edit, via
    assign_and_notify (no calendar invite — this isn't a hard deadline in
    the way edit/audio hand-offs are)."""
    stage = _get_stage_row_by_id(stage_id)
    if not stage or stage["stage_key"] != "highlights":
        raise ValueError("This isn't the Highlights stage.")
    db.execute("UPDATE pipeline_stages SET highlight_candidates = ? WHERE id = ?", (db.to_json(candidates), stage_id))
    output = db.row_to_dict(db.query_one("SELECT * FROM content_outputs WHERE id = ?", (stage["output_id"],)))
    edit_task = db.query_one("SELECT assigned_user_id FROM tasks WHERE output_id = ? AND stage_key = 'edit'", (output["id"],))
    recipient_user_id = edit_task["assigned_user_id"] if edit_task else None
    if not recipient_user_id:
        raise ValueError("No editor is assigned yet to hand these off to.")
    if not deadline_iso:
        deadline_iso = (date.fromisoformat(output["publish_date"]) - timedelta(days=2)).isoformat()
    note = "Candidate clips:\n" + "\n".join(
        f"- {c.get('label', '')}: {c.get('start', '')}\u2013{c.get('end', '')} {c.get('notes', '')}".strip() for c in candidates
    )
    return assign_and_notify(stage_id, recipient_user_id, deadline_iso, note=note, create_calendar_invite=False)


def submit_highlights(stage_id, clips, notes=""):
    stage = _get_stage_row_by_id(stage_id)
    if not stage or stage["stage_key"] != "highlights":
        raise ValueError("This isn't the Highlights stage.")
    db.execute(
        "UPDATE pipeline_stages SET submission_link = ?, submission_notes = ?, submitted_at = datetime('now') WHERE id = ?",
        (db.to_json(clips), notes, stage_id),
    )
    _notify_admins_of_submission(stage, ", ".join(c.get("url", "") for c in clips if c.get("url")), notes)
    return get_stages_with_status(stage["output_id"])


def mark_highlights_received(stage_id):
    stage = _get_stage_row_by_id(stage_id)
    if not stage or stage["stage_key"] != "highlights":
        raise ValueError("This isn't the Highlights stage.")
    clips = db.from_json(stage.get("submission_link"), [])
    output = db.row_to_dict(db.query_one("SELECT * FROM content_outputs WHERE id = ?", (stage["output_id"],)))
    _feed_highlight_clips_into_followups(output["campaign_id"], clips)
    _complete_stage_tasks(stage["output_id"], "highlights")
    return get_stages_with_status(stage["output_id"])


def _feed_highlight_clips_into_followups(campaign_id, clips):
    """Feeds delivered highlight clips directly into the existing
    highlight_1/highlight_2 follow-up campaigns (auto-spawned 5/9 days out
    by content.py's _spawn_targeted_followups): the clip's link is appended
    onto that campaign's own "Cut highlight from source video" task, so
    those two posts show up with their raw footage already attached."""
    followups = db.rows_to_list(
        db.query(
            """SELECT c.id, ct.key AS ct_key FROM campaigns c
               JOIN content_types ct ON ct.id = c.primary_content_type_id
               WHERE c.depends_on_campaign_id = ? AND ct.key IN ('highlight_1', 'highlight_2')""",
            (campaign_id,),
        )
    )
    by_key = {f["ct_key"]: f for f in followups}
    ordered = [by_key.get("highlight_1"), by_key.get("highlight_2")]
    for clip, followup in zip(clips, ordered):
        if not followup or not clip.get("url"):
            continue
        task = db.query_one(
            "SELECT id, notes FROM tasks WHERE campaign_id = ? AND task_name = 'Cut highlight from source video'",
            (followup["id"],),
        )
        if task:
            label = clip.get("label") or "Source clip"
            new_notes = task["notes"] or ""
            new_notes = (new_notes + "\n" if new_notes else "") + f"{label}: {clip['url']}"
            db.execute("UPDATE tasks SET notes = ? WHERE id = ?", (new_notes, task["id"]))
        db.execute(
            "INSERT INTO activity_log (campaign_id, actor_id, message) VALUES (?, NULL, ?)",
            (followup["id"], f"Source clip attached: {clip.get('url')}"),
        )


def _notify_admins_of_submission(stage, link_summary, notes):
    output = db.row_to_dict(db.query_one("SELECT * FROM content_outputs WHERE id = ?", (stage["output_id"],)))
    campaign = _campaign_title_and_owner(output)
    title = campaign["title"] if campaign else _output_type_label(output)
    label = STAGE_BY_KEY[stage["stage_key"]]["label"]
    admins = db.rows_to_list(
        db.query(
            "SELECT u.email, u.name FROM users u JOIN roles r ON r.id = u.role_id WHERE r.is_admin = 1 AND u.active = 1 AND u.email IS NOT NULL"
        )
    )
    body = f"{label} is ready for \u201c{title}\u201d:\n{link_summary}\n"
    if notes:
        body += f"\nNotes: {notes}\n"
    for a in admins:
        try:
            email_integration.send_email(a["email"], f"Ready for review: {label} — {title}", body)
        except (email_integration.EmailNotConfigured, Exception):
            pass
    _log(output["campaign_id"], f"{label} submitted for \u201c{title}\u201d — {link_summary}")


def _complete_stage_tasks(output_id, stage_key):
    tasks = db.rows_to_list(db.query("SELECT id FROM tasks WHERE output_id = ? AND stage_key = ?", (output_id, stage_key)))
    for t in tasks:
        db.execute("UPDATE tasks SET status = 'complete', updated_at = datetime('now') WHERE id = ?", (t["id"],))
        after_task_status_change(t["id"])


def _complete_shared_stage_tasks(campaign_id, stage_key):
    tasks = db.rows_to_list(
        db.query("SELECT id FROM tasks WHERE campaign_id = ? AND output_id IS NULL AND stage_key = ?", (campaign_id, stage_key))
    )
    for t in tasks:
        db.execute("UPDATE tasks SET status = 'complete', updated_at = datetime('now') WHERE id = ?", (t["id"],))
        after_task_status_change(t["id"])


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _get_stage_row_by_id(stage_id):
    return db.row_to_dict(db.query_one("SELECT * FROM pipeline_stages WHERE id = ?", (stage_id,)))


def _parse_dt(iso_str):
    """Parses a naive 'YYYY-MM-DDTHH:MM' (an HTML datetime-local input) or
    an already offset-aware ISO string, and returns a UTC-aware datetime.
    No timezone infrastructure exists anywhere in this app — every meeting
    time entered in the scheduling modal is treated as UTC, the same
    simplification used everywhere else Google Calendar is touched."""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _deadline_event_window(deadline_iso):
    """A short morning reminder block for a plain deadline *date* (as
    opposed to _parse_dt, which expects a full date+time)."""
    if "T" in deadline_iso:
        start_dt = _parse_dt(deadline_iso)
    else:
        d = date.fromisoformat(deadline_iso)
        start_dt = datetime(d.year, d.month, d.day, 9, 0, tzinfo=timezone.utc)
    return start_dt, start_dt + timedelta(minutes=30)


def _emails_for_user_ids(user_ids):
    user_ids = list(user_ids or [])
    if not user_ids:
        return []
    placeholders = ",".join("?" for _ in user_ids)
    rows = db.rows_to_list(
        db.query(f"SELECT email FROM users WHERE id IN ({placeholders}) AND email IS NOT NULL", tuple(user_ids))
    )
    return sorted(r["email"] for r in rows)


def names_for_user_ids(user_ids):
    user_ids = list(user_ids or [])
    if not user_ids:
        return []
    placeholders = ",".join("?" for _ in user_ids)
    rows = db.rows_to_list(db.query(f"SELECT id, name FROM users WHERE id IN ({placeholders})", tuple(user_ids)))
    by_id = {r["id"]: r["name"] for r in rows}
    return [by_id[i] for i in user_ids if i in by_id]


def _name_for_user_id(user_id):
    if not user_id:
        return None
    row = db.query_one("SELECT name FROM users WHERE id = ?", (user_id,))
    return row["name"] if row else None


def _campaign_title_and_owner(output):
    return db.row_to_dict(db.query_one("SELECT * FROM campaigns WHERE id = ?", (output["campaign_id"],)))


def _log(campaign_id, message):
    db.execute(
        "INSERT INTO activity_log (campaign_id, actor_id, message) VALUES (?, NULL, ?)",
        (campaign_id, message),
    )


def _create_drive_folder_for_campaign(campaign):
    """One Drive folder per SHOOT (campaign), covering both the Short and
    Long outputs' files — fired once when Concept Hashout unlocks (i.e.
    once Script Development is done)."""
    folder_name = f"{campaign['title']} — Production"
    try:
        emails = _attendee_emails_for_campaign(campaign["id"])
        result = gcal.create_drive_folder(folder_name, share_with_emails=emails)
        db.execute(
            "UPDATE campaigns SET drive_folder_id = ?, drive_folder_link = ? WHERE id = ?",
            (result.get("id"), result.get("webViewLink"), campaign["id"]),
        )
        _log(campaign["id"], f"Created Drive folder for \u201c{folder_name}\u201d.")
    except gcal.GoogleNotConnected:
        _log(campaign["id"], "Skipped Drive folder creation — Google isn't connected yet (Admin → Integrations).")
    except Exception as e:
        _log(campaign["id"], f"Drive folder creation failed: {e}")


def _attendee_emails_for_campaign(campaign_id):
    """Owner + anyone already assigned a task on this campaign — used for
    sharing the shoot's Drive folder, created before any meeting
    participants or hand-off recipients have been chosen."""
    emails = set()
    row = db.query_one("SELECT owner_id FROM campaigns WHERE id = ?", (campaign_id,))
    if row and row["owner_id"]:
        u = db.query_one("SELECT email FROM users WHERE id = ?", (row["owner_id"],))
        if u and u["email"]:
            emails.add(u["email"])
    assignees = db.rows_to_list(
        db.query(
            """SELECT DISTINCT u.email FROM tasks t JOIN users u ON u.id = t.assigned_user_id
               WHERE t.campaign_id = ? AND u.email IS NOT NULL""",
            (campaign_id,),
        )
    )
    for a in assignees:
        emails.add(a["email"])
    return sorted(emails)


def _output_type_label(output):
    row = db.query_one("SELECT label FROM content_types WHERE id = ?", (output["content_type_id"],))
    return row["label"] if row else "Video"
