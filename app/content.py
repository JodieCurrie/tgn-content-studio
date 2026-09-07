"""
Campaign/output business logic — creation, detail assembly, updates.
Kept separate from routes/content.py (the blueprint) so scheduling.py can
call create_campaign_from_rule() without importing Flask request handling.
"""
from datetime import date, timedelta

from . import db
from . import task_engine
from . import scheduling
from . import pipeline


# Fixed display order/labels/icons for the 4 content-type categories (Part
# 7/8/9's category restructuring) — shared by Admin > Content Types (grouped
# table) and the Calendar page's color key (grouped, click-to-expand legend),
# so both group and order categories the same way instead of each guessing.
CONTENT_TYPE_CATEGORIES = [
    ("targeted", "Targeted", "🎯"),
    ("filler", "Filler", "🧩"),
    ("monthly", "Monthly", "📅"),
    ("custom", "Custom", "✨"),
]


def group_content_types_by_category(types):
    """types: a list of dicts each having a 'category_key'. Returns an
    ordered list of {key, label, icon, types} groups (skipping any category
    with zero members), in CONTENT_TYPE_CATEGORIES order — any type whose
    category_key doesn't match one of the 4 known categories falls into a
    trailing 'Other' group rather than silently vanishing.

    Deliberately named 'types', not 'items': Jinja's dot-access on a dict
    falls back to a real attribute/method before the dict key, so a group
    dict with an 'items' key would render dict.items (a bound method)
    instead of the list whenever a template used group.items."""
    by_key = {}
    for t in types:
        by_key.setdefault(t.get("category_key"), []).append(t)
    groups = []
    for key, label, icon in CONTENT_TYPE_CATEGORIES:
        members = by_key.pop(key, [])
        if members:
            groups.append({"key": key, "label": label, "icon": icon, "types": members})
    for key, members in by_key.items():
        if members:
            groups.append({"key": key or "other", "label": (key or "Other").capitalize(), "icon": "📁", "types": members})
    return groups


def get_content_type(content_type_id):
    return db.row_to_dict(db.query_one("SELECT * FROM content_types WHERE id = ?", (content_type_id,)))


def get_content_type_by_key(key):
    return db.row_to_dict(db.query_one("SELECT * FROM content_types WHERE key = ?", (key,)))


def create_campaign_from_rule(rule, publish_date_iso):
    """Called by scheduling.materialize_rule() to turn one occurrence of a
    rule into a real Campaign (+ its outputs + its tasks). Ideas overhaul
    (Sept): before falling back to a generic placeholder title, check the
    Ideas bank for the oldest unscheduled idea tagged with this exact
    content type — if one exists, use it to fill this slot automatically
    (title/notes/links) instead of Jodie having to schedule it by hand."""
    ct = get_content_type(rule["content_type_id"])
    idea = _oldest_matching_idea(ct)
    title = idea["title"] if idea else (rule["default_title"] or ct["label"])
    notes = idea_notes_with_links(idea) if idea else ""

    campaign_id = db.execute(
        """INSERT INTO campaigns
           (title, notes, publish_date, status, primary_content_type_id,
            scheduling_rule_id, schedule_origin, source_idea_id, created_at, updated_at)
           VALUES (?, ?, ?, 'planned', ?, ?, 'rule', ?, datetime('now'), datetime('now'))""",
        (title, notes, publish_date_iso, ct["id"], rule["id"], idea["id"] if idea else None),
    )
    if idea:
        db.execute("UPDATE content_ideas SET scheduled_campaign_id = ? WHERE id = ?", (campaign_id, idea["id"]))

    add_output(campaign_id, ct["id"], publish_date_iso)
    task_engine.generate_tasks_for_campaign(campaign_id)
    _spawn_paired_and_followup_content(campaign_id, ct, publish_date_iso)

    return campaign_id


# Two Filler subtypes are never auto-scheduled by a recurring rule at all —
# there's nothing to shoot until a specific source clip exists — so they're
# deliberately left off every scheduling_rule (see scripts/seed.py
# CONTENT_TYPES). They can only ever reach the calendar via a matching idea
# that also carries a link to that source video/episode.
IDEA_REQUIRES_LINK_TYPE_KEYS = ("preaching_teaching", "podcast_additional_highlight")


def _oldest_matching_idea(ct):
    if ct["category_key"] not in ("monthly", "filler"):
        return None
    idea = db.row_to_dict(db.query_one(
        """SELECT * FROM content_ideas WHERE content_type_id = ? AND scheduled_campaign_id IS NULL
           ORDER BY created_at ASC LIMIT 1""",
        (ct["id"],),
    ))
    if not idea:
        return None
    if ct["key"] in IDEA_REQUIRES_LINK_TYPE_KEYS and not (idea["links"] or "").strip():
        return None
    return idea


def idea_notes_with_links(idea):
    notes = idea["notes"] or ""
    links = (idea["links"] or "").strip()
    if not links:
        return notes
    links_block = "Links from the idea:\n" + links
    return f"{notes}\n\n{links_block}" if notes else links_block


def _spawn_paired_and_followup_content(campaign_id, ct, publish_date_iso, actor_id=None):
    """The auto-pairing/follow-up rules that apply regardless of whether the
    campaign came from a recurring rule or a manual quick-create — kept in
    one place so both create_campaign_from_rule() and create_campaign() stay
    in sync (Part 7/9/11)."""
    # Targeted Video: Short auto-spawns its YouTube companion as a second
    # output on the SAME campaign (Part 7), then two highlight/snippet
    # follow-ups and the portrait "full episode" repost as dependent
    # campaigns the following week (Part 7's 5th targeted type).
    if ct["key"] == "targeted_short":
        long_ct = get_content_type_by_key("targeted_long")
        if long_ct and not db.query_one(
            "SELECT id FROM content_outputs WHERE campaign_id = ? AND content_type_id = ?",
            (campaign_id, long_ct["id"]),
        ):
            add_output(campaign_id, long_ct["id"], publish_date_iso)
            task_engine.generate_tasks_for_campaign(campaign_id, only_new_output_type=long_ct["id"])
        _spawn_targeted_followups(campaign_id, publish_date_iso, actor_id)

    # Blog Post pairs with Blog Post Video the same day — same pattern as
    # Targeted Short+YouTube, but with no highlight-snippet follow-ups (Part 9).
    if ct["key"] == "blog":
        video_ct = get_content_type_by_key("blog_video")
        if video_ct and not db.query_one(
            "SELECT id FROM content_outputs WHERE campaign_id = ? AND content_type_id = ?",
            (campaign_id, video_ct["id"]),
        ):
            add_output(campaign_id, video_ct["id"], publish_date_iso)
            task_engine.generate_tasks_for_campaign(campaign_id, only_new_output_type=video_ct["id"])

    # Podcast Episode gets 3 Highlight/Question posts per 3-month cycle: one
    # the same day as the episode, the other two on the 2nd Thursday of each
    # of the following two months (Part 9).
    if ct["key"] == "podcast_episode":
        _spawn_podcast_highlight_followups(campaign_id, publish_date_iso, actor_id)


def _spawn_targeted_followups(parent_campaign_id, parent_publish_iso, actor_id=None):
    parent_date = date.fromisoformat(parent_publish_iso)
    parent_title = db.query_one("SELECT title FROM campaigns WHERE id = ?", (parent_campaign_id,))["title"]
    # Highlight/Snippet 1 & 2 land on the Monday and Friday of the following
    # week (offsets +5 and +9 days from a Wednesday anchor); the portrait
    # "full episode" repost lands the Monday after that — before the next
    # targeted campaign begins on a 14-day cycle (offset +11).
    followups = (
        (5, "highlight_1", "Highlight/Snippet 1"),
        (9, "highlight_2", "Highlight/Snippet 2"),
        (11, "targeted_full_repost", "Full YouTube — Portrait Repost"),
    )
    for offset, type_key, label in followups:
        ct = get_content_type_by_key(type_key)
        if not ct:
            continue
        follow_date = (parent_date + timedelta(days=offset)).isoformat()
        dep_id = db.execute(
            """INSERT INTO campaigns
               (title, publish_date, status, primary_content_type_id,
                schedule_origin, depends_on_campaign_id, dependency_offset_days,
                created_at, updated_at)
               VALUES (?, ?, 'planned', ?, 'dependent', ?, ?, datetime('now'), datetime('now'))""",
            (f"{parent_title} — {label}", follow_date, ct["id"], parent_campaign_id, offset),
        )
        add_output(dep_id, ct["id"], follow_date)
        task_engine.generate_tasks_for_campaign(dep_id)
        db.execute(
            "INSERT INTO activity_log (campaign_id, actor_id, message) VALUES (?, ?, ?)",
            (dep_id, actor_id, f"Auto-created as a follow-up to '{parent_title}'."),
        )


def _spawn_podcast_highlight_followups(parent_campaign_id, parent_publish_iso, actor_id=None):
    hl_ct = get_content_type_by_key("podcast_highlight")
    if not hl_ct:
        return
    parent_date = date.fromisoformat(parent_publish_iso)
    parent_title = db.query_one("SELECT title FROM campaigns WHERE id = ?", (parent_campaign_id,))["title"]

    targets = [(parent_date, "same day as the episode")]
    y, m = parent_date.year, parent_date.month
    for i in (1, 2):
        y2, m2 = scheduling.add_months_ym(y, m, i)
        occ = scheduling.nth_weekday_of_month(y2, m2, 3, 2)  # 2nd Thursday (Thu=3, Mon=0 convention)
        if occ:
            targets.append((occ, f"2nd Thursday, {i} month(s) after the episode"))

    for idx, (occ_date, note) in enumerate(targets, start=1):
        follow_date = occ_date.isoformat()
        dep_id = db.execute(
            """INSERT INTO campaigns
               (title, publish_date, status, primary_content_type_id,
                schedule_origin, depends_on_campaign_id, dependency_offset_days,
                created_at, updated_at)
               VALUES (?, ?, 'planned', ?, 'dependent', ?, ?, datetime('now'), datetime('now'))""",
            (f"{parent_title} — Highlight/Question {idx}", follow_date, hl_ct["id"], parent_campaign_id,
             (occ_date - parent_date).days),
        )
        add_output(dep_id, hl_ct["id"], follow_date)
        task_engine.generate_tasks_for_campaign(dep_id)
        db.execute(
            "INSERT INTO activity_log (campaign_id, actor_id, message) VALUES (?, ?, ?)",
            (dep_id, actor_id, f"Auto-created — one of 3 highlight/question posts for this podcast cycle ({note})."),
        )


def add_output(campaign_id, content_type_id, publish_date_iso, title=None, platform_ids=None, assigned_user_id=None):
    ct = get_content_type(content_type_id)
    if platform_ids is None:
        platform_ids = db.from_json(ct["default_platform_ids"], [])
    output_id = db.execute(
        """INSERT INTO content_outputs (campaign_id, content_type_id, title, publish_date, status, assigned_user_id)
           VALUES (?, ?, ?, ?, 'idea', ?)""",
        (campaign_id, content_type_id, title, publish_date_iso, assigned_user_id),
    )
    for pid in platform_ids:
        db.execute(
            "INSERT OR IGNORE INTO output_platforms (output_id, platform_id) VALUES (?, ?)",
            (output_id, pid),
        )
    return output_id


def create_campaign(*, title, publish_date_iso, content_type_id, platform_ids=None, owner_id=None,
                     assigned_user_id=None, concept="", notes="", created_by=None, source_idea_id=None,
                     extra_output_type_ids=None):
    """Used by the quick "+ Create Content" flow and the Ideas -> Schedule flow."""
    ct = get_content_type(content_type_id)
    campaign_id = db.execute(
        """INSERT INTO campaigns
           (title, concept, notes, publish_date, status, owner_id, primary_content_type_id,
            schedule_origin, source_idea_id, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'planned', ?, ?, 'manual', ?, ?, datetime('now'), datetime('now'))""",
        (title, concept, notes, publish_date_iso, owner_id, ct["id"], source_idea_id, created_by),
    )
    add_output(campaign_id, ct["id"], publish_date_iso, platform_ids=platform_ids, assigned_user_id=assigned_user_id)
    for extra_id in (extra_output_type_ids or []):
        add_output(campaign_id, extra_id, publish_date_iso, assigned_user_id=assigned_user_id)
    task_engine.generate_tasks_for_campaign(campaign_id)

    # Same auto-pairing/follow-up behaviour as rule-generated campaigns (Part
    # 7/9/11), so a manually-created Targeted Video, Blog Post or Podcast
    # Episode gets its companion output(s) too, not just the recurring ones.
    _spawn_paired_and_followup_content(campaign_id, ct, publish_date_iso, actor_id=created_by)

    return campaign_id


def get_campaign_detail(campaign_id):
    campaign = db.row_to_dict(
        db.query_one(
            """SELECT c.*, ct.label AS primary_type_label, ct.color AS primary_type_color,
                      u.name AS owner_name
               FROM campaigns c
               LEFT JOIN content_types ct ON ct.id = c.primary_content_type_id
               LEFT JOIN users u ON u.id = c.owner_id
               WHERE c.id = ?""",
            (campaign_id,),
        )
    )
    if not campaign:
        return None

    outputs = db.rows_to_list(
        db.query(
            """SELECT o.*, ct.label AS type_label, ct.color AS type_color, ct.key AS type_key,
                      u.name AS assigned_name
               FROM content_outputs o
               JOIN content_types ct ON ct.id = o.content_type_id
               LEFT JOIN users u ON u.id = o.assigned_user_id
               WHERE o.campaign_id = ? ORDER BY o.publish_date, o.id""",
            (campaign_id,),
        )
    )
    for o in outputs:
        plats = db.rows_to_list(
            db.query(
                """SELECT p.key, p.label FROM output_platforms op
                   JOIN platforms p ON p.id = op.platform_id WHERE op.output_id = ?""",
                (o["id"],),
            )
        )
        o["platforms"] = plats
        # Targeted Video — Short/YouTube: the 7-stage production pipeline,
        # each stage with its own tasks (rendered separately from the
        # campaign's plain task list below). [] for every other type, unless
        # an opt-in type (Monthly/Filler) has started its own staged
        # pipeline — see is_opt_in_eligible, used by the panel to hint that
        # reassigning the flat "Create X" task is what starts it.
        o["pipeline_stages"] = pipeline.get_stages_with_status(o["id"])
        o["is_opt_in_eligible"] = pipeline.is_opt_in_pipeline_eligible(o["content_type_id"])

    tasks = db.rows_to_list(
        db.query(
            """SELECT t.*, u.name AS assigned_name FROM tasks t
               LEFT JOIN users u ON u.id = t.assigned_user_id
               WHERE t.campaign_id = ? ORDER BY t.due_date IS NULL, t.due_date, t.id""",
            (campaign_id,),
        )
    )
    inspiration = db.rows_to_list(
        db.query("SELECT * FROM inspiration_links WHERE campaign_id = ? ORDER BY id", (campaign_id,))
    )
    assets = db.rows_to_list(
        db.query("SELECT a.*, u.name AS uploaded_by_name FROM assets a LEFT JOIN users u ON u.id = a.uploaded_by WHERE a.campaign_id = ? ORDER BY a.id", (campaign_id,))
    )
    comments = db.rows_to_list(
        db.query(
            """SELECT c.*, u.name AS author_name FROM comments c
               LEFT JOIN users u ON u.id = c.author_id WHERE c.campaign_id = ? ORDER BY c.created_at""",
            (campaign_id,),
        )
    )
    activity = db.rows_to_list(
        db.query(
            """SELECT a.*, u.name AS actor_name FROM activity_log a
               LEFT JOIN users u ON u.id = a.actor_id WHERE a.campaign_id = ? ORDER BY a.created_at DESC LIMIT 20""",
            (campaign_id,),
        )
    )
    # Shared shoot-level tracker (Script/Concept/Film) — one per campaign,
    # rendered once above the per-output pipeline trackers. [] for a
    # campaign with no Targeted Video outputs.
    campaign["shoot_stages"] = pipeline.get_shoot_stages_with_status(campaign_id)

    dependents = db.rows_to_list(
        db.query(
            "SELECT id, title, publish_date, status FROM campaigns WHERE depends_on_campaign_id = ? ORDER BY publish_date",
            (campaign_id,),
        )
    )
    parent = None
    if campaign.get("depends_on_campaign_id"):
        parent = db.row_to_dict(
            db.query_one("SELECT id, title, publish_date FROM campaigns WHERE id = ?", (campaign["depends_on_campaign_id"],))
        )

    campaign["outputs"] = outputs
    # Pipeline-stage tasks are rendered under their own output's stage
    # tracker instead (see o["pipeline_stages"] above) — keep this flat
    # list to the tasks that aren't part of a pipeline.
    campaign["tasks"] = [t for t in tasks if not t.get("stage_key")]
    campaign["inspiration"] = inspiration
    campaign["assets"] = assets
    campaign["comments"] = comments
    campaign["activity"] = activity
    campaign["dependents"] = dependents
    campaign["parent_campaign"] = parent
    return campaign


CAMPAIGN_TEXT_FIELDS = {
    "title", "concept", "script", "script_youtube", "bible_references", "caption", "notes",
    "notes_to_videographer", "notes_to_musician", "status", "owner_id",
}


def update_campaign_fields(campaign_id, fields):
    allowed = {k: v for k, v in fields.items() if k in CAMPAIGN_TEXT_FIELDS}
    if not allowed:
        return
    campaign = db.row_to_dict(db.query_one("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)))
    set_clause = ", ".join(f"{k} = ?" for k in allowed)
    params = list(allowed.values()) + [campaign_id]
    db.execute(f"UPDATE campaigns SET {set_clause}, updated_at = datetime('now') WHERE id = ?", params)
    if "title" in allowed and campaign and campaign.get("source_idea_id"):
        _maybe_release_idea_back_to_pool(campaign["source_idea_id"], campaign_id, allowed["title"])
    for field_name, task_name in SCRIPT_FIELD_AUTO_COMPLETE_TASKS.items():
        if field_name in allowed and (allowed[field_name] or "").strip():
            _auto_complete_shoot_task(campaign_id, "script", task_name)


# Sept redesign ("uploads auto-advance the task, no manual status click"):
# filling in a script field is itself the deliverable for that task — Jodie
# shouldn't also have to separately check it off. The checkbox stays too,
# as a manual fallback (e.g. the outline lives somewhere else entirely).
SCRIPT_FIELD_AUTO_COMPLETE_TASKS = {
    "script": "Write script",
    "script_youtube": "Write YouTube script",
}


def _auto_complete_shoot_task(campaign_id, stage_key, task_name):
    task = db.row_to_dict(db.query_one(
        "SELECT * FROM tasks WHERE campaign_id = ? AND output_id IS NULL AND stage_key = ? AND task_name = ?",
        (campaign_id, stage_key, task_name),
    ))
    if not task or task["status"] == "complete":
        return
    db.execute("UPDATE tasks SET status = 'complete', updated_at = datetime('now') WHERE id = ?", (task["id"],))
    pipeline.after_task_status_change(task["id"])


def _maybe_release_idea_back_to_pool(idea_id, campaign_id, new_title):
    """Ideas overhaul (Sept): a campaign that was auto-filled from a
    matching idea (see _oldest_matching_idea) keeps its source_idea_id as
    long as Jodie leaves that title alone. The moment she retitles it
    herself — swapping in different content for the slot — the idea is no
    longer "used" here: unlink it from this campaign and drop it back into
    the unscheduled pool so a later slot can pick it up automatically
    instead of it being silently lost."""
    idea = db.row_to_dict(db.query_one("SELECT * FROM content_ideas WHERE id = ?", (idea_id,)))
    if not idea or idea["title"] == new_title:
        return
    db.execute("UPDATE campaigns SET source_idea_id = NULL WHERE id = ?", (campaign_id,))
    db.execute("UPDATE content_ideas SET scheduled_campaign_id = NULL WHERE id = ?", (idea_id,))


def delete_campaign(campaign_id):
    db.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))


def add_inspiration_link(campaign_id, url, label=""):
    return db.execute(
        "INSERT INTO inspiration_links (campaign_id, url, label) VALUES (?, ?, ?)",
        (campaign_id, url, label),
    )


def user_can_edit_campaign(user, campaign_id):
    if user is None:
        return False
    if user["role_is_admin"] or user["role_can_manage_all_content"]:
        return True
    assigned = db.query_one(
        """SELECT 1 FROM content_outputs WHERE campaign_id = ? AND assigned_user_id = ?
           UNION SELECT 1 FROM tasks WHERE campaign_id = ? AND assigned_user_id = ? LIMIT 1""",
        (campaign_id, user["id"], campaign_id, user["id"]),
    )
    return assigned is not None


def add_comment(campaign_id, author_id, body):
    return db.execute(
        "INSERT INTO comments (campaign_id, author_id, body) VALUES (?, ?, ?)",
        (campaign_id, author_id, body),
    )
