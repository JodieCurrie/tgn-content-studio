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
# review_design (Filler's design workflow, Part 23) reuses the exact same
# approve/reject-loop mechanics as review_edit/review_audio — reopening
# design_post on a reject, same emails, same UI.
REVIEW_STAGE_KEYS = ("review_edit", "review_audio", "review_design")
REOPENS_STAGE = {"review_edit": "edit", "review_audio": "audio", "review_design": "design_post"}
SUBMISSION_STAGE_KEYS = ("compilation", "highlights")

PRODUCTION_STAGES_BY_TYPE = {
    "targeted_short": ["edit", "review_edit", "audio", "review_audio", "compilation"],
    "targeted_long": ["edit", "review_edit", "highlights"],
    # Monthly Campaign pipeline (opt-in, Part 21-22): unlike Targeted Video,
    # this is a single-output pipeline — no shared "shoot" tier, since a
    # Monthly campaign only ever has one output. Every stage row here lives
    # at the PRODUCTION tier (output_id set, campaign_id NULL) even though
    # "develop_concept"/"film" are shoot-like in what they do — there's no
    # sibling output to share them with, so there's nothing to gain from a
    # second tier. Opt-in per campaign via start_output_pipeline() — the
    # content type keeps its plain flat task list by default (see
    # MONTHLY_PIPELINE_TYPE_KEYS / task_engine.py).
    "podcast_episode": ["develop_concept", "film", "edit", "review_edit", "highlights"],
    "testimony": ["develop_concept", "film", "edit", "review_edit"],
    "course": ["develop_concept", "film", "edit", "review_edit", "highlights"],

    # Filler "video" group (Part 23, opt-in like Monthly): Develop Concept ->
    # Film/Record -> Edit -> Review Content, statically declared here —
    # ending at review_edit deliberately, NOT audio/review_audio. Whether a
    # musician gets involved at all is a per-post decision Jodie makes only
    # AFTER Review Content approves (decide_filler_audio), not something
    # every filler video goes through — so those two stages get inserted
    # dynamically into pipeline_stages only when she says yes, rather than
    # existing from the start like every other stage set in this dict.
    "tiktok_style": ["develop_concept", "film", "edit", "review_edit"],
    "interview": ["develop_concept", "film", "edit", "review_edit"],
    "preaching_teaching": ["develop_concept", "film", "edit", "review_edit"],

    # Filler "design" group (Part 23, opt-in): Develop Concept -> Design Post
    # -> Review Design. No film/audio at all — a design hand-off (notes +
    # inspiration links) takes the place of Edit, via assign_and_notify.
    "carousel": ["develop_concept", "design_post", "review_design"],
    "normal_post": ["develop_concept", "design_post", "review_design"],
    "moving_scripture": ["develop_concept", "design_post", "review_design"],
    "quick_reel": ["develop_concept", "design_post", "review_design"],
    "scripture_expansion": ["develop_concept", "design_post", "review_design"],
}

STAGE_BY_KEY = {
    "script": {"key": "script", "label": "Script Development"},
    "concept": {"key": "concept", "label": "Concept Hashout", "is_meeting": True, "meet": True},
    # Monthly's first stage (Part 21) — a plain task like Script Development
    # was for Targeted, not a meeting; "Concept Hashout" above stays
    # Targeted-only, unchanged.
    "develop_concept": {"key": "develop_concept", "label": "Develop Concept"},
    "film": {"key": "film", "label": "Film/Record", "is_meeting": True, "meet": False},
    "edit": {"key": "edit", "label": "Edit"},
    "review_edit": {"key": "review_edit", "label": "Review Edit"},
    "audio": {"key": "audio", "label": "Audio Creation"},
    "review_audio": {"key": "review_audio", "label": "Review Audio"},
    "compilation": {"key": "compilation", "label": "Final Compilation"},
    "highlights": {"key": "highlights", "label": "Select Highlight Reels"},
    # Filler design group only (Part 23) — design_post is a plain hand-off
    # task (like Edit), review_design is an approve/reject gate (like
    # Review Edit), reusing all the same mechanics under new labels.
    "design_post": {"key": "design_post", "label": "Design Post"},
    "review_design": {"key": "review_design", "label": "Review Design"},
}

# The two output types the ALWAYS-ON Targeted Video pipeline applies to —
# every other content type keeps its plain flat task list unless/until it
# opts into its own pipeline (see MONTHLY_PIPELINE_TYPE_KEYS below).
PIPELINE_CONTENT_TYPE_KEYS = ("targeted_short", "targeted_long")

# Monthly Campaign types that CAN opt into a staged production pipeline
# (Part 21), on a per-campaign basis, via start_output_pipeline(). Part 23
# extends the same opt-in mechanism to 8 Filler subtypes, split into a
# "video" group (its own Film/Record + optional Audio) and a "design" group
# (a Design Post hand-off instead). Every opt-in-eligible type keeps its
# plain flat checklist by default — a single "Create X" task assigned to
# Jodie (see scripts/seed.py TASK_TEMPLATES) — and only switches over to the
# staged pipeline when that task gets reassigned away from her (see
# after_task_reassignment), which replaced the old explicit "Start
# production workflow" button for both Monthly and Filler.
MONTHLY_PIPELINE_TYPE_KEYS = ("podcast_episode", "testimony", "course")
FILLER_VIDEO_PIPELINE_TYPE_KEYS = ("tiktok_style", "interview", "preaching_teaching")
FILLER_DESIGN_PIPELINE_TYPE_KEYS = ("carousel", "normal_post", "moving_scripture", "quick_reel", "scripture_expansion")
FILLER_PIPELINE_TYPE_KEYS = FILLER_VIDEO_PIPELINE_TYPE_KEYS + FILLER_DESIGN_PIPELINE_TYPE_KEYS
OPT_IN_PIPELINE_TYPE_KEYS = MONTHLY_PIPELINE_TYPE_KEYS + FILLER_PIPELINE_TYPE_KEYS

# Which follow-up content type a monthly pipeline's delivered highlight
# clips get fed into once "Mark as received" fires (mirrors Targeted's
# highlight_1/highlight_2, see _feed_highlight_clips_into_followups) — keyed
# by the PRIMARY type, since that's what mark_highlights_received has on
# hand. Testimony has no highlight follow-up type at all, so it's absent
# here (and has no "highlights" stage to begin with).
MONTHLY_HIGHLIGHT_FOLLOWUP_TYPE = {
    "podcast_episode": "podcast_highlight",
    "course": "course_highlight",
}

# Google Calendar colorId for pipeline meetings/deadlines — "8" is Google's
# "Graphite" (a neutral grey), visually distinct from the light-grey
# (#D9D9D9) used by the separate "Custom Event" content type.
MEETING_CALENDAR_COLOR_ID = "8"


def is_pipeline_content_type_id(content_type_id):
    row = db.query_one("SELECT key FROM content_types WHERE id = ?", (content_type_id,))
    return bool(row) and row["key"] in PIPELINE_CONTENT_TYPE_KEYS


def is_opt_in_pipeline_eligible(content_type_id):
    row = db.query_one("SELECT key FROM content_types WHERE id = ?", (content_type_id,))
    return bool(row) and row["key"] in OPT_IN_PIPELINE_TYPE_KEYS


def is_filler_video_pipeline_eligible(content_type_id):
    row = db.query_one("SELECT key FROM content_types WHERE id = ?", (content_type_id,))
    return bool(row) and row["key"] in FILLER_VIDEO_PIPELINE_TYPE_KEYS


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


def start_output_pipeline(output_id, actor_id=None):
    """The opt-in trigger for a Monthly Campaign (Part 21): switches ONE
    campaign's output from its plain flat checklist over to the staged
    Develop Concept -> Film/Record -> Edit -> Review Edit -> [Select
    Highlight Reels] pipeline. Safe to call only once per output — raises if
    the type isn't eligible or the pipeline's already running. Clears out
    whichever flat checklist tasks nobody has touched yet (same
    not_started-only safety rule used when Targeted's pipeline first
    replaced its own flat list — see scripts/seed.py
    _migrate_targeted_video_pipeline) and lays down the new stage rows +
    tasks in their place."""
    output = db.row_to_dict(db.query_one("SELECT * FROM content_outputs WHERE id = ?", (output_id,)))
    if not output:
        raise ValueError("No such content output.")
    if not is_opt_in_pipeline_eligible(output["content_type_id"]):
        raise ValueError("This content type doesn't have an opt-in production pipeline.")
    if db.query_one("SELECT id FROM pipeline_stages WHERE output_id = ? LIMIT 1", (output_id,)):
        raise ValueError("The production workflow is already running for this content.")

    ct_key = _content_type_key(output["content_type_id"])
    stage_keys = PRODUCTION_STAGES_BY_TYPE.get(ct_key, [])
    for i, stage_key in enumerate(stage_keys):
        db.execute(
            "INSERT INTO pipeline_stages (output_id, stage_key, sort_order) VALUES (?, ?, ?)",
            (output_id, stage_key, i),
        )

    stale = db.rows_to_list(
        db.query(
            """SELECT id FROM tasks WHERE output_id = ? AND created_from_template = 1
               AND status = 'not_started' AND stage_key IS NULL""",
            (output_id,),
        )
    )
    for t in stale:
        db.execute("DELETE FROM tasks WHERE id = ?", (t["id"],))

    from . import task_engine
    task_engine.generate_tasks_for_campaign(output["campaign_id"], only_new_output_type=output["content_type_id"])

    _log(output["campaign_id"], f"Started the staged production workflow for {_output_type_label(output)}.")
    return get_stages_with_status(output_id)


def after_task_reassignment(task_id, previous_assigned_user_id):
    """Part 23: reassigning an opt-in type's single default 'Create X' task
    away from whoever held it is now what starts the staged pipeline, for
    both Monthly and Filler — replacing the old explicit 'Start production
    workflow' button. Called from PATCH /api/tasks/<id> with the task's
    assignee BEFORE the update was applied. No-ops for anything that isn't
    exactly this situation: a non-pipeline task, a stage-tagged task
    (already inside a running pipeline), a reassignment back to the same
    person, a non-opt-in-eligible type, or an output whose pipeline is
    already running."""
    task = db.row_to_dict(db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,)))
    if not task or not task["output_id"] or task["stage_key"]:
        return
    if task["assigned_user_id"] == previous_assigned_user_id:
        return
    output = db.row_to_dict(db.query_one("SELECT * FROM content_outputs WHERE id = ?", (task["output_id"],)))
    if not output or not is_opt_in_pipeline_eligible(output["content_type_id"]):
        return
    if db.query_one("SELECT id FROM pipeline_stages WHERE output_id = ? LIMIT 1", (output["id"],)):
        return
    try:
        start_output_pipeline(output["id"])
    except ValueError:
        pass


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
    ct_key = _content_type_key(output["content_type_id"])
    shoot = get_shoot_stages_with_status(output["campaign_id"])
    # A content type with no shared shoot tier at all (e.g. a Monthly
    # pipeline output, which has none) has nothing upstream to wait on, so
    # its first production stage unlocks immediately — only a REAL shoot
    # tier (Targeted) has to actually finish first.
    shoot_complete = (not shoot) or shoot[-1]["status"] == "complete"

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

    now_iso = datetime.utcnow().isoformat()
    result = []
    previous_complete = shoot_complete
    for row in rows:
        stage_key = row["stage_key"]
        tasks = tasks_by_stage.get(stage_key, [])
        status = _derive_status(tasks)
        unlocked = previous_complete
        is_meeting_stage = stage_key in MEETING_STAGE_KEYS
        participant_ids = db.from_json(row.get("participant_user_ids"), [])
        entry = {
            **row,
            "label": STAGE_BY_KEY[stage_key]["label"],
            "status": status,
            "unlocked": unlocked,
            "tasks": tasks,
            "is_review_stage": stage_key in REVIEW_STAGE_KEYS,
            "is_submission_stage": stage_key in SUBMISSION_STAGE_KEYS,
            "is_meeting_stage": is_meeting_stage,
            "participant_ids": participant_ids,
            "participant_names": names_for_user_ids(participant_ids),
        }
        if is_meeting_stage:
            # Only Monthly's per-output "film" stage reaches this branch —
            # Targeted's concept/film live on the shared shoot tier above
            # and are annotated by get_shoot_stages_with_status instead.
            has_meeting = bool(row.get("meeting_start"))
            confirmed = bool(row.get("meeting_confirmed_at"))
            meeting_passed = bool(row.get("meeting_end")) and row["meeting_end"] < now_iso
            entry["needs_scheduling"] = unlocked and status != "complete" and not has_meeting
            entry["needs_confirmation"] = unlocked and has_meeting and meeting_passed and not confirmed
            entry["scheduled_upcoming"] = unlocked and has_meeting and not meeting_passed and not confirmed and status != "complete"
            entry["recipient_name"] = _name_for_user_id(row.get("delivery_recipient_user_id"))
            # "Already recorded" (skip straight to the hand-off) and
            # "bunch with another shoot" are Filler-video-only additions
            # (Part 23) — Targeted/Monthly's own film scheduling is
            # unchanged.
            entry["is_filler_video_film"] = stage_key == "film" and ct_key in FILLER_VIDEO_PIPELINE_TYPE_KEYS
        if stage_key in REVIEW_STAGE_KEYS:
            entry["needs_review"] = unlocked and status != "complete"
            if stage_key == "review_edit" and ct_key in FILLER_VIDEO_PIPELINE_TYPE_KEYS:
                # Filler-video's optional Audio Creation branch: once Review
                # Content approves, Jodie is asked yes/no about sending it to
                # a musician (decide_filler_audio) — audio/review_audio don't
                # exist as stages at all until she says yes.
                entry["needs_audio_decision"] = status == "complete" and not row.get("audio_decision")
        if stage_key in ("audio", "design_post"):
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
    elif stage_row["stage_key"] in REVIEW_STAGE_KEYS:
        _notify_review_ready(stage_row)
    elif stage_row["stage_key"] == "film" and stage_row.get("output_id"):
        # Monthly pipeline outputs have no shared "concept" stage to create
        # the shoot's Drive folder the way Targeted's does — Film/Record
        # unlocking (i.e. Develop Concept just finished) is this pipeline's
        # equivalent moment.
        output = db.row_to_dict(db.query_one("SELECT campaign_id FROM content_outputs WHERE id = ?", (stage_row["output_id"],)))
        campaign = db.row_to_dict(db.query_one("SELECT * FROM campaigns WHERE id = ?", (output["campaign_id"],))) if output else None
        if campaign and not campaign.get("drive_folder_id"):
            _create_drive_folder_for_campaign(campaign)


def _notify_review_ready(stage_row):
    """Section 14/17's "send me an email with a link to the task" — fires
    the moment a Review Edit/Review Audio stage unlocks, for every admin
    (not just whoever happens to be logged in). Previously only Final
    Compilation's unlock sent a heads-up email; reviews unlocked silently."""
    output = db.row_to_dict(db.query_one("SELECT * FROM content_outputs WHERE id = ?", (stage_row["output_id"],)))
    if not output:
        return
    campaign = _campaign_title_and_owner(output)
    title = campaign["title"] if campaign else _output_type_label(output)
    label = STAGE_BY_KEY[stage_row["stage_key"]]["label"]
    admins = db.rows_to_list(
        db.query(
            "SELECT u.email, u.name FROM users u JOIN roles r ON r.id = u.role_id WHERE r.is_admin = 1 AND u.active = 1 AND u.email IS NOT NULL"
        )
    )
    body = f"{label} is ready for your review on “{title}” — open TGN Content Studio to have a look.\n"
    for a in admins:
        try:
            email_integration.send_email(a["email"], f"Ready for review: {label} — {title}", body)
        except (email_integration.EmailNotConfigured, Exception):
            pass
    _log(output["campaign_id"], f"{label} is ready for review on “{title}” — notified the admin(s).")


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
def schedule_meeting(campaign_id, stage_key, start_iso, end_iso, participant_ids, bunch_with_stage_ids=None):
    """Creates the meeting the first time, or updates it in place on a
    reschedule (same DB row, same Calendar event — PATCHed rather than
    duplicated). Returns the refreshed stage list — the shoot's shared list
    for a Targeted campaign-scoped stage, or this output's own list for a
    Monthly/Filler pipeline's output-scoped "film" (no shared shoot tier, so
    its meeting stages live on the single output instead — see
    PRODUCTION_STAGES_BY_TYPE).

    bunch_with_stage_ids (Filler-video only, Part 23): other outputs' own
    Film/Record stages to fold into this SAME session — one calendar event,
    one shoot, covering several filler posts at once — rather than creating
    a separate meeting for each. Each one gets the same time/participants/
    calendar event copied onto its row; see unscheduled_filler_film_candidates
    for how the UI finds candidates to offer."""
    if stage_key not in MEETING_STAGE_KEYS:
        raise ValueError(f"'{stage_key}' isn't a schedulable pipeline stage.")
    stage = db.row_to_dict(
        db.query_one(
            """SELECT * FROM pipeline_stages WHERE stage_key = ? AND (
                   campaign_id = ?
                   OR output_id IN (SELECT id FROM content_outputs WHERE campaign_id = ?)
               )""",
            (stage_key, campaign_id, campaign_id),
        )
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

    if bunch_with_stage_ids:
        primary = _get_stage_row_by_id(stage["id"])
        for sid in bunch_with_stage_ids:
            if sid == stage["id"]:
                continue
            other = _get_stage_row_by_id(sid)
            if not other or other["stage_key"] != "film" or not other.get("output_id"):
                continue
            db.execute(
                """UPDATE pipeline_stages
                   SET meeting_start = ?, meeting_end = ?, participant_user_ids = ?, meeting_confirmed_at = NULL,
                       calendar_event_id = ?, calendar_link = ?, meet_link = ?
                   WHERE id = ?""",
                (
                    primary["meeting_start"], primary["meeting_end"], primary["participant_user_ids"],
                    primary["calendar_event_id"], primary["calendar_link"], primary["meet_link"], sid,
                ),
            )
            other_output = db.row_to_dict(db.query_one("SELECT campaign_id FROM content_outputs WHERE id = ?", (other["output_id"],)))
            if other_output:
                _log(other_output["campaign_id"], f"Bunched into the Film/Record session for \u201c{title}\u201d.")

    if stage.get("output_id"):
        return get_stages_with_status(stage["output_id"])
    return get_shoot_stages_with_status(campaign_id)


def unscheduled_filler_film_candidates(exclude_output_id=None):
    """Other Filler-video outputs whose Film/Record hasn't been scheduled
    yet \u2014 offered as "bunch with this shoot too" options when scheduling one
    of them (Part 23, Filler-only)."""
    placeholders = ",".join("?" for _ in FILLER_VIDEO_PIPELINE_TYPE_KEYS)
    rows = db.rows_to_list(
        db.query(
            f"""SELECT ps.id AS stage_id, ps.output_id, c.id AS campaign_id, c.title AS campaign_title,
                      o.publish_date, ct.label AS type_label
               FROM pipeline_stages ps
               JOIN content_outputs o ON o.id = ps.output_id
               JOIN campaigns c ON c.id = o.campaign_id
               JOIN content_types ct ON ct.id = o.content_type_id
               WHERE ps.stage_key = 'film' AND ps.meeting_start IS NULL AND ps.output_id IS NOT NULL
                 AND ps.output_id != COALESCE(?, -1) AND ct.key IN ({placeholders})
               ORDER BY o.publish_date""",
            (exclude_output_id, *FILLER_VIDEO_PIPELINE_TYPE_KEYS),
        )
    )
    return rows


def pending_confirmations_for_admin():
    """Meeting stages whose scheduled end time has passed with nobody having
    said whether it happened — these prompt an admin on their next login.
    Covers both campaign-scoped meetings (Targeted's shared concept/film)
    and output-scoped ones (a Monthly pipeline's own "film" — it has no
    shared shoot tier, see PRODUCTION_STAGES_BY_TYPE), via a LEFT JOIN
    through either path."""
    rows = db.rows_to_list(
        db.query(
            """SELECT ps.*, COALESCE(c1.title, c2.title) AS campaign_title
               FROM pipeline_stages ps
               LEFT JOIN campaigns c1 ON c1.id = ps.campaign_id
               LEFT JOIN content_outputs o ON o.id = ps.output_id
               LEFT JOIN campaigns c2 ON c2.id = o.campaign_id
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
    the same flow. Two shapes, depending on the stage's scope:

    - Campaign-scoped (Targeted's shared shoot tier): assigns EVERY sibling
      pipeline output's Edit task to the chosen editor, and for the Short
      sibling, silently pre-assigns Final Compilation too, since that's the
      same editor merging in the audio later.
    - Output-scoped (a Monthly pipeline's own "film" — no shared shoot tier,
      only ever this one output): assigns just this output's Edit task.

    Either way: creates one external-only Calendar deadline invite for the
    editor (not shown in the app's own calendar views — Jodie's calendar
    keeps showing only the publish date), sends one hand-off email pointing
    at the shoot's shared Drive folder, and completes Film/Record."""
    stage = _get_stage_row_by_id(stage_id)
    if not stage or stage["stage_key"] != "film":
        raise ValueError("This isn't the Film/Record stage.")
    is_shared_shoot = bool(stage.get("campaign_id"))
    recipient = db.row_to_dict(db.query_one("SELECT * FROM users WHERE id = ?", (recipient_user_id,)))
    if not recipient:
        raise ValueError("No such user.")

    if is_shared_shoot:
        campaign_id = stage["campaign_id"]
        outputs = db.rows_to_list(
            db.query(
                """SELECT o.*, ct.key AS ct_key FROM content_outputs o
                   JOIN content_types ct ON ct.id = o.content_type_id
                   WHERE o.campaign_id = ? AND ct.key IN ('targeted_short', 'targeted_long')""",
                (campaign_id,),
            )
        )
    else:
        this_output = db.row_to_dict(db.query_one("SELECT * FROM content_outputs WHERE id = ?", (stage["output_id"],)))
        campaign_id = this_output["campaign_id"]
        outputs = [this_output]
    campaign = db.row_to_dict(db.query_one("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)))

    db.execute(
        """UPDATE pipeline_stages
           SET meeting_confirmed_at = datetime('now'), delivery_recipient_user_id = ?, delivery_deadline = ?
           WHERE id = ?""",
        (recipient_user_id, deadline_iso, stage_id),
    )

    for output in outputs:
        _assign_task(output["id"], "edit", recipient_user_id, deadline_iso, note)
        if is_shared_shoot and output["ct_key"] == "targeted_short":
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

    if is_shared_shoot:
        _complete_shared_stage_tasks(campaign_id, "film")
        return get_shoot_stages_with_status(campaign_id)
    _complete_stage_tasks(stage["output_id"], "film")
    return get_stages_with_status(stage["output_id"])


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
    if not stage or stage["stage_key"] not in ("audio", "highlights", "design_post"):
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
    _maybe_finish_pipeline_after_review(stage["output_id"], stage["stage_key"])
    return get_stages_with_status(stage["output_id"])


def _maybe_finish_pipeline_after_review(output_id, stage_key):
    """Decides whether an approved review stage is the END of this output's
    pipeline (and so should send the finishing 'Thanks!' email) or whether
    something else still follows:

    - review_edit: finishes Monthly (testimony has nothing after it;
      podcast_episode/course still have Highlights, but that's a separate
      hand-off to the same editor, not a blocker on thanking them for the
      edit itself — matches the pre-Part-23 behavior exactly). Filler-video
      does NOT finish here — decide_filler_audio's yes/no comes next.
    - review_audio: finishes Filler-video when the audio decision was
      "yes" (Targeted Short's review_audio still continues into
      Compilation, so it deliberately does NOT finish here).
    - review_design: always finishes Filler-design — it's the last stage.
    """
    output = db.row_to_dict(db.query_one("SELECT * FROM content_outputs WHERE id = ?", (output_id,)))
    if not output:
        return
    ct_key = _content_type_key(output["content_type_id"])
    if stage_key == "review_edit":
        if ct_key in MONTHLY_PIPELINE_TYPE_KEYS:
            _maybe_notify_pipeline_output_approved(output_id, "review_edit")
    elif stage_key == "review_audio":
        if ct_key in FILLER_VIDEO_PIPELINE_TYPE_KEYS:
            _maybe_notify_pipeline_output_approved(output_id, "review_audio")
    elif stage_key == "review_design":
        _maybe_notify_pipeline_output_approved(output_id, "review_design")


def _maybe_notify_pipeline_output_approved(output_id, source_stage_key):
    """Part 22/23: 'once the video/design is approved... send the creator a
    confirmation email such as "Thanks!"'. source_stage_key is the REVIEW
    stage that just finished things off; REOPENS_STAGE maps it back to the
    task (edit/audio/design_post) whose assignee actually did the work."""
    output = db.row_to_dict(db.query_one("SELECT * FROM content_outputs WHERE id = ?", (output_id,)))
    if not output:
        return
    credited_stage_key = REOPENS_STAGE.get(source_stage_key, source_stage_key)
    task = db.query_one("SELECT assigned_user_id FROM tasks WHERE output_id = ? AND stage_key = ?", (output_id, credited_stage_key))
    if not task or not task["assigned_user_id"]:
        return
    assignee = db.query_one("SELECT name, email FROM users WHERE id = ?", (task["assigned_user_id"],))
    if not assignee or not assignee["email"]:
        return
    campaign = _campaign_title_and_owner(output)
    title = campaign["title"] if campaign else _output_type_label(output)
    try:
        email_integration.send_email(
            assignee["email"], f"Approved: {title}",
            f"Hi {assignee['name']},\n\nYour work on “{title}” is approved and downloaded. Thanks!\n",
        )
    except (email_integration.EmailNotConfigured, Exception):
        pass


def decide_filler_audio(stage_id, wants_audio):
    """Filler-video's dynamic branch point (Part 23): stage_id is the
    OUTPUT's review_edit stage, which is where the yes/no decision (and
    whether it's been made yet) is tracked. 'Yes' inserts fresh audio/
    review_audio pipeline_stages rows after review_edit and materializes
    their tasks (see task_engine.generate_tasks_for_campaign's dynamic-stage
    guard) so Audio picks up right where Targeted Short's does — an explicit
    assign-and-send action. 'No' finishes the pipeline right here."""
    stage = _get_stage_row_by_id(stage_id)
    if not stage or stage["stage_key"] != "review_edit":
        raise ValueError("This isn't the Review Content stage.")
    if stage.get("review_decision") != "approved":
        raise ValueError("Review Content hasn't been approved yet.")
    if stage.get("audio_decision"):
        raise ValueError("That decision has already been made.")
    db.execute(
        "UPDATE pipeline_stages SET audio_decision = ? WHERE id = ?",
        ("yes" if wants_audio else "no", stage_id),
    )
    output = db.row_to_dict(db.query_one("SELECT * FROM content_outputs WHERE id = ?", (stage["output_id"],)))
    if wants_audio:
        existing = db.query_one(
            "SELECT id FROM pipeline_stages WHERE output_id = ? AND stage_key = 'audio'", (output["id"],)
        )
        if not existing:
            max_row = db.query_one("SELECT MAX(sort_order) AS m FROM pipeline_stages WHERE output_id = ?", (output["id"],))
            next_sort = (max_row["m"] if max_row and max_row["m"] is not None else 0) + 1
            db.execute(
                "INSERT INTO pipeline_stages (output_id, stage_key, sort_order) VALUES (?, 'audio', ?)",
                (output["id"], next_sort),
            )
            db.execute(
                "INSERT INTO pipeline_stages (output_id, stage_key, sort_order) VALUES (?, 'review_audio', ?)",
                (output["id"], next_sort + 1),
            )
            from . import task_engine
            task_engine.generate_tasks_for_campaign(output["campaign_id"], only_new_output_type=output["content_type_id"])
        _log(output["campaign_id"], f"Sending audio to a musician for {_output_type_label(output)}.")
    else:
        _maybe_notify_pipeline_output_approved(output["id"], "review_edit")
        _log(output["campaign_id"], f"No audio needed for {_output_type_label(output)} — pipeline finished.")
    return get_stages_with_status(output["id"])


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
    ct_key = _content_type_key(output["content_type_id"])
    _feed_highlight_clips_into_followups(output["campaign_id"], clips, ct_key)
    _complete_stage_tasks(stage["output_id"], "highlights")
    return get_stages_with_status(stage["output_id"])


def _feed_highlight_clips_into_followups(campaign_id, clips, source_ct_key="targeted_long"):
    """Feeds delivered highlight clips directly into existing follow-up
    campaigns so those posts show up with their raw footage already
    attached — Targeted (source_ct_key='targeted_long') has exactly two,
    highlight_1/highlight_2 (auto-spawned by content.py's
    _spawn_targeted_followups), each with a "Cut highlight from source
    video" task by name. A Monthly pipeline (podcast_episode/course) instead
    has however many same-type follow-ups already exist (podcast_episode
    auto-spawns 3 podcast_highlight campaigns; course has none yet, since
    it isn't on a scheduling rule — this just no-ops for it, which is
    expected), attached to whichever task each one has, in publish-date
    order — matched to clips in the order Jodie captured them."""
    if source_ct_key == "targeted_long":
        followups = db.rows_to_list(
            db.query(
                """SELECT c.id, ct.key AS ct_key, c.publish_date FROM campaigns c
                   JOIN content_types ct ON ct.id = c.primary_content_type_id
                   WHERE c.depends_on_campaign_id = ? AND ct.key IN ('highlight_1', 'highlight_2')
                   ORDER BY c.publish_date""",
                (campaign_id,),
            )
        )
        by_key = {f["ct_key"]: f for f in followups}
        ordered = [by_key.get("highlight_1"), by_key.get("highlight_2")]
    else:
        followup_type = MONTHLY_HIGHLIGHT_FOLLOWUP_TYPE.get(source_ct_key)
        if not followup_type:
            return
        ordered = db.rows_to_list(
            db.query(
                """SELECT c.id FROM campaigns c
                   JOIN content_types ct ON ct.id = c.primary_content_type_id
                   WHERE c.depends_on_campaign_id = ? AND ct.key = ?
                   ORDER BY c.publish_date""",
                (campaign_id, followup_type),
            )
        )

    for clip, followup in zip(clips, ordered):
        if not followup or not clip.get("url"):
            continue
        if source_ct_key == "targeted_long":
            task = db.query_one(
                "SELECT id, notes FROM tasks WHERE campaign_id = ? AND task_name = 'Cut highlight from source video'",
                (followup["id"],),
            )
        else:
            # Monthly follow-up types (podcast_highlight/course_highlight)
            # have no task named "Cut highlight from source video" — just
            # attach to whichever task that campaign has first.
            task = db.query_one("SELECT id, notes FROM tasks WHERE campaign_id = ? ORDER BY id LIMIT 1", (followup["id"],))
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
