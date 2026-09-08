"""
Seeds reference data (roles, content types, platforms, task templates,
creation options, scheduling rules) and a small set of demo users + content
ideas. Safe to re-run: it checks for existing rows before inserting.

Run with:  python -m scripts.seed
"""
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash

from app import create_app, db as dbmod
from app import scheduling
from app import pipeline
from app import task_engine
from app import content as content_module


DEFAULT_PASSWORD = "TGNstudio2026!"

ROLES = [
    dict(key="admin", label="Admin / Content Director", is_admin=1, can_manage_all_content=1, can_manage_settings=1, color="#B08BD6"),
    dict(key="production", label="Production", is_admin=0, can_manage_all_content=0, can_manage_settings=0, color="#75AAC9"),
    dict(key="editor", label="Final Edit / Review", is_admin=0, can_manage_all_content=0, can_manage_settings=0, color="#E0A857"),
    dict(key="music", label="Music / Audio", is_admin=0, can_manage_all_content=0, can_manage_settings=0, color="#7FC8A9"),
    dict(key="member", label="Team Member", is_admin=0, can_manage_all_content=0, can_manage_settings=0, color="#9FA5C9"),
]

PLATFORMS = [
    ("instagram", "Instagram"),
    ("tiktok", "TikTok"),
    ("facebook", "Facebook"),
    ("threads", "Threads"),
    ("youtube", "YouTube"),
    ("youtube_shorts", "YouTube Shorts"),
    ("website", "TGN Website (Blog)"),
    ("podcast", "Podcast (Spotify/Apple)"),
]

# key, label, color, category, is_filler, req_video, req_music, req_jodie, lead_days, platform_keys, is_campaign_type
# category: 'targeted' | 'filler' | 'monthly' (Part 7/8/9 — the three big
# creation buckets Jodie asked for, replacing the old flat type list).
CONTENT_TYPES = [
    # ---- Targeted Campaign (Part 7): 5 types sharing one biweekly cycle ----
    ("targeted_short", "Targeted Video — Short", "#C895D5", "targeted", 0, 1, 1, 1, 10, ["instagram", "tiktok", "facebook", "threads"], 1),
    ("targeted_long", "Targeted Video — YouTube", "#F09756", "targeted", 0, 1, 1, 1, 10, ["youtube"], 0),
    ("highlight_1", "Highlight / Snippet 1", "#FEBD5A", "targeted", 0, 1, 0, 1, 3, ["instagram", "facebook", "threads"], 0),
    ("highlight_2", "Highlight / Snippet 2", "#F5A742", "targeted", 0, 1, 0, 1, 3, ["instagram", "facebook", "threads"], 0),
    # The full long-form video, cut to portrait and reposted across the other
    # platforms once the highlight reels are done — the "5th targeted type"
    # Jodie asked us to name; renameable any time from Admin > Content Types.
    ("targeted_full_repost", "Full YouTube — Portrait Repost", "#E08A45", "targeted", 0, 1, 0, 1, 3, ["instagram", "facebook", "threads", "tiktok"], 0),

    # ---- Filler Post (Part 8): 9 subtypes under one "Filler Post" menu option ----
    ("tiktok_style", "TikTok Style Reel", "#A3C0B6", "filler", 1, 1, 0, 1, 4, ["tiktok"], 0),
    ("interview", "Interview / Studio", "#FFDAC1", "filler", 1, 1, 0, 1, 7, ["youtube", "instagram"], 0),
    ("carousel", "Carousel Post", "#FD738E", "filler", 1, 1, 0, 1, 3, ["instagram", "facebook"], 0),
    ("normal_post", "Normal / Static Post", "#9FA5C9", "filler", 1, 0, 0, 1, 2, ["instagram", "facebook", "threads"], 0),
    ("moving_scripture", "Moving Scripture", "#CC6DA6", "filler", 1, 1, 1, 1, 4, ["instagram", "facebook"], 0),
    ("quick_reel", "Quick Reel", "#B5EAD7", "filler", 1, 1, 0, 1, 3, ["instagram", "tiktok"], 0),
    ("scripture_expansion", "Scripture Expansion", "#C7CEEA", "filler", 1, 1, 0, 1, 4, ["instagram", "facebook"], 0),
    # These two are never auto-scheduled unless a matching idea already
    # exists AND that idea includes a link to the relevant source video —
    # that idea-matching logic is part of the (not-yet-built) Ideas overhaul,
    # so for now they're simply left off every scheduling rule.
    ("preaching_teaching", "Preaching / Teaching", "#E2F0CB", "filler", 1, 1, 0, 1, 3, ["instagram", "facebook"], 0),
    ("podcast_additional_highlight", "Podcast Additional Highlight Snippet", "#97D2FB", "filler", 1, 1, 0, 1, 3, ["instagram", "facebook", "threads"], 0),

    # ---- Monthly Campaign (Part 9): 7 types, lighter-weight recurring content ----
    ("podcast_episode", "Podcast Episode", "#7FC8A9", "monthly", 0, 1, 1, 1, 10, ["youtube", "podcast"], 0),
    ("podcast_highlight", "Podcast Highlight Snippet / Question", "#6FB89A", "monthly", 0, 1, 0, 1, 3, ["instagram", "facebook", "threads"], 0),
    # Not auto-scheduled yet (future functionality, per Jodie) — exist as
    # pickable types only, for now.
    ("course", "Course / Educational", "#FFD3B4", "monthly", 0, 1, 0, 1, 14, ["youtube", "website"], 0),
    ("course_highlight", "Course Highlight Snippet", "#FFC199", "monthly", 0, 1, 0, 1, 3, ["instagram", "facebook"], 0),
    ("blog", "Blog Post", "#FEC4D5", "monthly", 0, 0, 0, 1, 7, ["website"], 0),
    ("blog_video", "Blog Post Video", "#F7A8C4", "monthly", 0, 1, 0, 1, 7, ["youtube", "instagram"], 0),
    ("testimony", "Testimony", "#75AAC9", "monthly", 0, 1, 0, 1, 10, ["instagram", "youtube", "facebook"], 0),
    # Reinstated (Sept, per Jodie's Ideas-tab request) as a 4th opt-in
    # Monthly pipeline type, alongside podcast_episode/testimony/course.
    ("bible_study", "Bible Study", "#B7A6D6", "monthly", 0, 1, 0, 1, 10, ["youtube", "instagram", "facebook"], 0),

    # Custom Events (Part 19) are scheduling blocks (team unavailable, a
    # holiday, a busy period) rather than actual content, so they get their
    # own category — never counted as filler content or targeted for the
    # filler-cascade drag behaviour.
    ("custom", "Custom Event", "#D9D9D9", "custom", 1, 0, 0, 1, 5, [], 0),
]

# Single-type options map straight through (unchanged); Filler Post and
# Monthly Campaign use pick_subtype=True so the create-content modal shows a
# second "which type?" dropdown instead of creating every listed type at
# once (Part 8/9's two-level picker).
# key, label, icon, type_keys, pick_subtype
CREATION_OPTIONS = [
    ("targeted_campaign", "Targeted Campaign", "🎯", ["targeted_short", "targeted_long"], False),
    ("filler_post", "Filler Post", "🧩", [
        "tiktok_style", "interview", "carousel", "normal_post", "moving_scripture",
        "quick_reel", "scripture_expansion", "preaching_teaching", "podcast_additional_highlight",
    ], True),
    ("monthly_campaign", "Monthly Campaign", "📅", [
        "podcast_episode", "podcast_highlight", "course", "course_highlight", "blog", "blog_video", "testimony",
        "bible_study",
    ], True),
    ("custom", "Custom Event", "✨", ["custom"], False),
]

# The full Targeted Video production pipeline (Part 14-18): Script
# Development -> Concept Hashout -> Film/Record -> Edit -> Review -> Audio
# -> Final Compilation, in that exact order (Jodie's spec — Concept Hashout
# comes after the first script draft). Script Development / Concept
# Hashout / Film-Record are ONE SHOOT shared by both Short and Long (see
# app/pipeline.py SHOOT_STAGE_KEYS) — they're only ever attached to
# targeted_short's own template row, never duplicated onto targeted_long's;
# task_engine.py skips them for any non-targeted_short output. Concept
# Hashout and Film/Record aren't plain checklist items like the rest —
# completing Script Development (both scripts) unlocks a "schedule this
# meeting" action instead, and confirming the meeting happened (or
# capturing the post-shoot editor hand-off, for Film/Record) is what
# actually completes those two stages.
# (role_key, task_name, offset_days_before, stage_key)
PIPELINE_SHOOT_TEMPLATES = [
    ("admin", "Write script", 21, "script"),
    ("admin", "Write YouTube script", 21, "script"),
    ("admin", "Concept hashout meeting", 17, "concept"),
    ("production", "Film / record", 12, "film"),
]

# Production-level templates branch per output from there. Short keeps the
# full Edit -> Review -> Audio -> Review -> Compilation loop (a musician is
# involved); Long has no separate audio/compilation step at all — the
# editor adds generic audio as part of editing, so Review Edit is the last
# checkpoint before Select Highlight Reels. Review Edit/Review Audio are
# approve/reject gates, not plain checklist items (see app/pipeline.py
# REVIEW_STAGE_KEYS); Audio needs an explicit assign-and-send action before
# it's a plain checklist item; Compilation/Highlights finish via an admin
# "Mark as received" click, not a checkbox (see SUBMISSION_STAGE_KEYS).
PIPELINE_PRODUCTION_TEMPLATES = {
    "targeted_short": [
        ("production", "Edit & export", 7, "edit"),
        ("admin", "Review edit", 5, "review_edit"),
        ("music", "Add/record audio", 3, "audio"),
        ("admin", "Review audio", 1, "review_audio"),
        ("editor", "Final compilation & polish", 0, "compilation"),
    ],
    "targeted_long": [
        ("production", "Edit & export", 7, "edit"),
        ("admin", "Review edit", 3, "review_edit"),
        ("admin", "Select highlight reels", 0, "highlights"),
    ],
}

# Monthly Campaign pipeline templates (Part 21) — OPT-IN per campaign, unlike
# the two tables above which are always-on for every Targeted output. These
# rows exist in task_templates from the start, but task_engine.py skips
# creating the actual tasks from them until pipeline.start_output_pipeline()
# has been called for that specific campaign (see the
# monthly_pipeline_started check there) — until then, that content type's
# existing flat TASK_TEMPLATES list below still runs as normal. No shared
# "shoot" tier here (a Monthly campaign only ever has one output), so
# develop_concept/film live at the same per-output level as edit onward —
# see app/pipeline.py PRODUCTION_STAGES_BY_TYPE.
MONTHLY_PIPELINE_TEMPLATES = {
    "podcast_episode": [
        ("admin", "Develop concept", 30, "develop_concept"),
        ("production", "Film / record", 20, "film"),
        ("production", "Edit & export", 10, "edit"),
        ("admin", "Review edit", 5, "review_edit"),
        ("admin", "Select highlight reels", 3, "highlights"),
    ],
    "testimony": [
        ("admin", "Develop concept", 30, "develop_concept"),
        ("production", "Film / record", 20, "film"),
        ("production", "Edit & export", 10, "edit"),
        ("admin", "Review edit", 5, "review_edit"),
    ],
    "bible_study": [
        ("admin", "Develop concept", 30, "develop_concept"),
        ("production", "Film / record", 20, "film"),
        ("production", "Edit & export", 10, "edit"),
        ("admin", "Review edit", 5, "review_edit"),
    ],
    "course": [
        ("admin", "Develop concept", 30, "develop_concept"),
        ("production", "Film / record", 20, "film"),
        ("production", "Edit & export", 10, "edit"),
        ("admin", "Review edit", 5, "review_edit"),
        ("admin", "Select highlight reels", 3, "highlights"),
    ],
}

# Filler's opt-in staged pipelines (Part 23) — the same additive mechanism as
# MONTHLY_PIPELINE_TEMPLATES above (seeded alongside each type's single flat
# "Create X" task, only ever used once pipeline.start_output_pipeline() has
# run for that specific output). "video" group ends at review_edit — Audio /
# Review Audio are seeded here too (so their tasks exist to materialize
# later) but only ever get created once pipeline.decide_filler_audio() adds
# their pipeline_stages rows (see task_engine.py's dynamic-stage guard).
# "design" group has no film/audio at all — Design Post takes Edit's place.
_FILLER_VIDEO_TEMPLATES = [
    ("admin", "Develop concept", 15, "develop_concept"),
    ("production", "Film / record", 10, "film"),
    ("production", "Edit & export", 5, "edit"),
    ("admin", "Review content", 3, "review_edit"),
    ("music", "Add/record audio", 2, "audio"),
    ("admin", "Review audio", 1, "review_audio"),
]
_FILLER_DESIGN_TEMPLATES = [
    ("admin", "Develop concept", 10, "develop_concept"),
    ("production", "Design post", 4, "design_post"),
    ("admin", "Review design", 2, "review_design"),
]
FILLER_PIPELINE_TEMPLATES = {
    "tiktok_style": _FILLER_VIDEO_TEMPLATES,
    "interview": _FILLER_VIDEO_TEMPLATES,
    "preaching_teaching": _FILLER_VIDEO_TEMPLATES,
    "carousel": _FILLER_DESIGN_TEMPLATES,
    "normal_post": _FILLER_DESIGN_TEMPLATES,
    "moving_scripture": _FILLER_DESIGN_TEMPLATES,
    "quick_reel": _FILLER_DESIGN_TEMPLATES,
    "scripture_expansion": _FILLER_DESIGN_TEMPLATES,
}

# task templates: content_type_key -> [(role_key, task_name, offset_days_before)]
# targeted_short/targeted_long are deliberately absent here — they're seeded
# from PIPELINE_SHOOT_TEMPLATES/PIPELINE_PRODUCTION_TEMPLATES instead (see
# _migrate_targeted_video_pipeline).
TASK_TEMPLATES = {
    # ---- Targeted (highlights/repost only — the two main videos are the
    # 7-stage pipeline above) ----
    "highlight_1": [
        ("production", "Cut highlight from source video", 2), ("production", "Export", 1),
        ("admin", "Approve & schedule", 0),
    ],
    "highlight_2": [
        ("production", "Cut highlight from source video", 2), ("production", "Export", 1),
        ("admin", "Approve & schedule", 0),
    ],
    "targeted_full_repost": [
        ("production", "Convert to portrait & export", 1), ("admin", "Caption & schedule", 0),
    ],

    # ---- Filler ----
    # 8 of the 9 subtypes are opt-in pipeline-eligible (Part 23) — their
    # default is now a SINGLE "Create X" task assigned to Jodie, due ~2
    # weeks before publish; reassigning it away from her starts the staged
    # workflow (see app/pipeline.py after_task_reassignment /
    # FILLER_PIPELINE_TEMPLATES above). podcast_additional_highlight is
    # deliberately left as its own small flat checklist — no pipeline.
    "tiktok_style": [("admin", "Create TikTok Style Reel", 14)],
    "interview": [("admin", "Create Interview / Studio", 14)],
    "carousel": [("admin", "Create Carousel Post", 14)],
    "normal_post": [("admin", "Create Normal / Static Post", 14)],
    "moving_scripture": [("admin", "Create Moving Scripture", 14)],
    "quick_reel": [("admin", "Create Quick Reel", 14)],
    "scripture_expansion": [("admin", "Create Scripture Expansion", 14)],
    "preaching_teaching": [("admin", "Create Preaching / Teaching", 14)],
    "podcast_additional_highlight": [
        ("admin", "Pick moment & write copy", 2), ("production", "Create graphic/clip", 1),
    ],

    # ---- Monthly ----
    # podcast_episode/testimony/course are opt-in pipeline-eligible too (Part
    # 21/23) — same single "Create X" default as Filler above.
    "podcast_episode": [("admin", "Create Podcast Episode", 14)],
    "podcast_highlight": [
        ("admin", "Pick question & write copy", 2), ("production", "Create graphic/clip", 1),
    ],
    "course": [("admin", "Create Course / Educational", 14)],
    "course_highlight": [
        ("production", "Cut highlight from source lesson", 2), ("admin", "Approve & schedule", 0),
    ],
    "blog": [
        ("admin", "Write draft", 6), ("admin", "Review & edit", 2), ("admin", "Publish to website", 0),
    ],
    "blog_video": [
        ("production", "Edit companion video", 4), ("admin", "Approve & schedule", 1),
    ],
    "testimony": [("admin", "Create Testimony", 14)],
    "bible_study": [("admin", "Create Bible Study", 14)],

    "custom": [
        ("admin", "Plan & schedule", 2),
    ],
}

DEMO_USERS = [
    dict(name="Jodie Currie", email="jodiecurrie3.jc@gmail.com", role="admin"),
    dict(name="Lizzy", email="lizzy@demo.tgncontent.local", role="production"),
    dict(name="Gabe", email="gabe@demo.tgncontent.local", role="editor"),
]

# Straight from Jodie's own "Potential Videos" idea list (Sheet2 of her live
# calendar) — real unscheduled ideas, not invented ones.
DEMO_IDEAS = [
    "Beating my Chest",
    "Where are you",
    "Sweet Magnolias faith",
    "Ecclesiastes",
    "Where are you going?",
    "Life after Death",
    "Mercy rewrote my life",
    "Mount Sinai vs Mount Zion",
]


def next_weekday(from_date, weekday):
    """Next date on/after from_date matching Python weekday (Mon=0)."""
    days_ahead = (weekday - from_date.weekday()) % 7
    return from_date + timedelta(days=days_ahead)


def seed():
    """CLI entry point (`python scripts/seed.py`): builds its own app +
    app context, then runs the actual seeding logic. Kept separate from
    seed_data() below so that logic can also run *inside* an app context
    that already exists (e.g. a running request) without recursively
    creating a second Flask app."""
    app = create_app()
    with app.app_context():
        seed_data()
    print("Seed complete.")


def seed_data():
    """The actual seed/migration steps. Safe to call repeatedly (every
    step checks for existing rows first) and safe to call from inside an
    already-running app — e.g. Admin → "Sync pipeline & reference data" —
    for hosts where a Shell tab isn't available (Render's free plan has no
    Shell/one-off-job access) to still be able to pick up a new deploy's
    task-template/pipeline changes without needing shell access."""
    _seed_roles()
    _seed_platforms()
    type_ids = _seed_content_types()
    _seed_task_templates(type_ids)
    _migrate_targeted_video_pipeline(type_ids)
    _migrate_opt_in_flat_tasks(type_ids)
    _migrate_full_repost_label(type_ids)
    _migrate_reinstate_bible_study(type_ids)
    _seed_opt_in_pipeline_templates(type_ids)
    _seed_creation_options(type_ids)
    user_ids = _seed_users()
    _seed_scheduling_rules(type_ids)
    _seed_ideas(user_ids)
    created = scheduling.materialize_all_active_rules()
    print(f"Materialized {len(created)} rule-generated campaigns.")
    filler_created = content_module.fill_weekly_filler_gaps()
    print(f"Auto-filled {len(filler_created)} filler gap-day campaign(s) toward the 4-day-a-week minimum.")
    _showcase_first_campaign()
    dbmod.get_db().commit()


def _showcase_first_campaign():
    """Fleshes out the very first upcoming targeted campaign with a real
    topic + concept + script, so the demo isn't just empty placeholder
    titles — mirrors how Jodie's own sheet has real topics up close and
    generic labels further out."""
    first = dbmod.query_one(
        "SELECT id FROM campaigns WHERE title = 'New Targeted Campaign' ORDER BY publish_date LIMIT 1"
    )
    if not first:
        return
    campaign_id = first["id"]
    title = "Does God really forgive everything?"
    dbmod.execute(
        """UPDATE campaigns SET title = ?,
               concept = 'A targeted campaign about the depth of God''s forgiveness — for anyone carrying guilt that feels too big for grace.',
               bible_references = '1 John 1:9, Psalm 103:12, Isaiah 1:18',
               notes = 'Anchor topic for this cycle — see brief example.'
           WHERE id = ?""",
        (title, campaign_id),
    )
    for row in dbmod.query("SELECT id FROM campaigns WHERE depends_on_campaign_id = ?", (campaign_id,)):
        dbmod.execute("UPDATE campaigns SET title = ? WHERE id = ?", (f"{title} — Highlight Snippet", row["id"]))


def _seed_roles():
    for r in ROLES:
        existing = dbmod.query_one("SELECT id FROM roles WHERE key = ?", (r["key"],))
        if existing:
            continue
        dbmod.execute(
            "INSERT INTO roles (key, label, is_admin, can_manage_all_content, can_manage_settings, color) VALUES (?,?,?,?,?,?)",
            (r["key"], r["label"], r["is_admin"], r["can_manage_all_content"], r["can_manage_settings"], r["color"]),
        )


def _seed_platforms():
    for i, (key, label) in enumerate(PLATFORMS):
        existing = dbmod.query_one("SELECT id FROM platforms WHERE key = ?", (key,))
        if existing:
            continue
        dbmod.execute("INSERT INTO platforms (key, label, sort_order) VALUES (?,?,?)", (key, label, i))


def _platform_ids(keys):
    ids = []
    for k in keys:
        row = dbmod.query_one("SELECT id FROM platforms WHERE key = ?", (k,))
        if row:
            ids.append(row["id"])
    return ids


def _seed_content_types():
    ids = {}
    for i, (key, label, color, category, is_filler, rv, rm, rj, lead, plat_keys, is_campaign) in enumerate(CONTENT_TYPES):
        existing = dbmod.query_one("SELECT id FROM content_types WHERE key = ?", (key,))
        if existing:
            ids[key] = existing["id"]
            # keep the category current on a re-run (e.g. after this update
            # ships) without touching anything else about an existing type
            dbmod.execute("UPDATE content_types SET category_key = ? WHERE id = ?", (category, existing["id"]))
            continue
        plat_ids = _platform_ids(plat_keys)
        new_id = dbmod.execute(
            """INSERT INTO content_types
               (key, label, color, description, is_filler, requires_videographer, requires_musician,
                requires_jodie, default_lead_time_days, default_platform_ids, is_campaign_type, category_key, sort_order)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, label, color, "", is_filler, rv, rm, rj, lead, dbmod.to_json(plat_ids), is_campaign, category, i),
        )
        ids[key] = new_id
    _archive_deprecated_content_types()
    return ids


# Content types from the old flat list that Jodie's category restructuring
# (Part 7/8/9) replaced or split up. They're archived, never deleted — any
# already-scheduled content still using one keeps working, it just drops out
# of the "+ Create Content" menu and admin lists going forward.
DEPRECATED_CONTENT_TYPE_KEYS = ["highlight", "podcast_question", "preaching_snippet"]


def _archive_deprecated_content_types():
    for key in DEPRECATED_CONTENT_TYPE_KEYS:
        dbmod.execute("UPDATE content_types SET archived = 1 WHERE key = ? AND archived = 0", (key,))


# One-off rename (Sept): "Full Episode — Portrait Repost" -> "Full YouTube —
# Portrait Repost". _seed_content_types() only sets a label on INSERT, never
# on an existing row (so a label Jodie has since customized from Admin >
# Content Types is never clobbered on redeploy) — only rename it here if it
# still has exactly the old default label.
def _migrate_full_repost_label(type_ids):
    ct_id = type_ids.get("targeted_full_repost")
    if not ct_id:
        return
    dbmod.execute(
        "UPDATE content_types SET label = ? WHERE id = ? AND label = ?",
        ("Full YouTube — Portrait Repost", ct_id, "Full Episode — Portrait Repost"),
    )


# "bible_study" was archived long ago under the old flat type list, before
# Jodie's category restructuring (it used to be in DEPRECATED_CONTENT_TYPE_KEYS
# above). It's now a first-class row in CONTENT_TYPES again (Sept, per her
# Ideas-tab request) — _seed_content_types() only relabels/category-updates
# an existing row, it never un-archives one, so a leftover archived row from
# an old install needs this explicit nudge back to active.
def _migrate_reinstate_bible_study(type_ids):
    ct_id = type_ids.get("bible_study")
    if not ct_id:
        return
    dbmod.execute("UPDATE content_types SET archived = 0 WHERE id = ?", (ct_id,))


def _seed_task_templates(type_ids):
    for ct_key, tasks in TASK_TEMPLATES.items():
        ct_id = type_ids.get(ct_key)
        if not ct_id:
            continue
        existing = dbmod.query_one("SELECT id FROM task_templates WHERE content_type_id = ?", (ct_id,))
        if existing:
            continue
        for order, (role_key, name, offset) in enumerate(tasks):
            dbmod.execute(
                "INSERT INTO task_templates (content_type_id, role_key, task_name, offset_days_before, sort_order, stage_key) VALUES (?,?,?,?,?,NULL)",
                (ct_id, role_key, name, offset, order),
            )


def _seed_opt_in_pipeline_templates(type_ids):
    """Additively seeds the opt-in staged-pipeline templates — Monthly's 3
    (Part 21) and Filler's 8 (Part 23) — alongside each type's existing flat
    TASK_TEMPLATES ("Create X") — NOT a delete-and-replace like
    _migrate_targeted_video_pipeline, since the flat list stays the default
    and these only ever get used once a specific output's pipeline has
    started (pipeline.start_output_pipeline(), now triggered by reassigning
    the flat task — see pipeline.after_task_reassignment). Fingerprinted per
    type on a 'develop_concept'-tagged template existing, so this is a no-op
    once already seeded but safely re-fires (e.g. via Admin's "Sync" button)
    for anyone upgrading."""
    all_templates = dict(MONTHLY_PIPELINE_TEMPLATES)
    all_templates.update(FILLER_PIPELINE_TEMPLATES)
    for ct_key, templates in all_templates.items():
        ct_id = type_ids.get(ct_key)
        if not ct_id:
            continue
        already_seeded = dbmod.query_one(
            "SELECT id FROM task_templates WHERE content_type_id = ? AND stage_key = 'develop_concept'", (ct_id,)
        )
        if already_seeded:
            continue
        for order, (role_key, name, offset, stage_key) in enumerate(templates):
            dbmod.execute(
                """INSERT INTO task_templates
                   (content_type_id, role_key, task_name, offset_days_before, sort_order, stage_key)
                   VALUES (?,?,?,?,?,?)""",
                (ct_id, role_key, name, offset, 100 + order, stage_key),
            )


def _migrate_targeted_video_pipeline(type_ids):
    """One-time migration (Part 14-18+): swaps each targeted_short/
    targeted_long task list for the current two-tier pipeline templates
    (shoot templates on targeted_short only; production templates per
    type — see PIPELINE_SHOOT_TEMPLATES/PIPELINE_PRODUCTION_TEMPLATES), and
    retrofits it onto every already-scheduled output of those two types.
    Only ever touches tasks nobody has started yet (status='not_started')
    — anything already in progress or done is left exactly as-is; it just
    won't show up grouped under a stage in the new pipeline view.

    Fingerprinted on a 'review_edit'-tagged template existing, since that's
    unique to this shape (earlier shapes used a single 'review' key, or no
    stage_key at all) — so this safely re-fires for anyone upgrading from
    an older shape, but is a no-op once already migrated. `pipeline_stages`
    itself is rebuilt separately and automatically at app startup (see
    app/db.py _rebuild_pipeline_stages_v2_if_needed) — this function only
    handles task_templates/tasks."""
    for ct_key in pipeline.PIPELINE_CONTENT_TYPE_KEYS:
        ct_id = type_ids.get(ct_key)
        if not ct_id:
            continue

        already_migrated = dbmod.query_one(
            "SELECT id FROM task_templates WHERE content_type_id = ? AND stage_key = 'review_edit'", (ct_id,)
        )
        if not already_migrated:
            dbmod.execute("DELETE FROM task_templates WHERE content_type_id = ?", (ct_id,))
            templates = list(PIPELINE_PRODUCTION_TEMPLATES.get(ct_key, []))
            if ct_key == "targeted_short":
                templates = list(PIPELINE_SHOOT_TEMPLATES) + templates
            for order, (role_key, name, offset, stage_key) in enumerate(templates):
                dbmod.execute(
                    """INSERT INTO task_templates
                       (content_type_id, role_key, task_name, offset_days_before, sort_order, stage_key)
                       VALUES (?,?,?,?,?,?)""",
                    (ct_id, role_key, name, offset, order, stage_key),
                )

        outputs = dbmod.rows_to_list(
            dbmod.query("SELECT * FROM content_outputs WHERE content_type_id = ?", (ct_id,))
        )
        for output in outputs:
            pipeline.ensure_pipeline_for_output(output["id"])
            stale = dbmod.rows_to_list(dbmod.query(
                """SELECT id FROM tasks WHERE output_id = ? AND created_from_template = 1
                   AND status = 'not_started' AND stage_key IS NULL""",
                (output["id"],),
            ))
            if stale:
                for t in stale:
                    dbmod.execute("DELETE FROM tasks WHERE id = ?", (t["id"],))
            # Old-shaped stage-tagged tasks that no longer match any current
            # template (e.g. the old single 'review' stage, or an old
            # per-output 'script'/'concept'/'film' task now superseded by
            # the shared campaign-level ones) are cleared the same
            # not_started-only way, so generate_tasks_for_campaign can lay
            # down the current set cleanly.
            stale_stage_tagged = dbmod.rows_to_list(dbmod.query(
                """SELECT id FROM tasks WHERE output_id = ? AND created_from_template = 1
                   AND status = 'not_started' AND stage_key IS NOT NULL
                   AND stage_key NOT IN (SELECT stage_key FROM task_templates WHERE content_type_id = ?)""",
                (output["id"], ct_id),
            ))
            for t in stale_stage_tagged:
                dbmod.execute("DELETE FROM tasks WHERE id = ?", (t["id"],))
            if stale or stale_stage_tagged:
                task_engine.generate_tasks_for_campaign(output["campaign_id"], only_new_output_type=ct_id)


def _migrate_opt_in_flat_tasks(type_ids):
    """One-time migration (Part 23): retrofits the new single "Create X"
    flat-task shape onto every opt-in-eligible type's ALREADY-SEEDED
    task_templates and already-scheduled outputs — needed because
    _seed_task_templates() no-ops once any template row exists for a type,
    so a live database (Jodie's) would otherwise keep its old multi-task
    flat checklist forever. Fingerprinted per type on there being exactly
    one flat (stage_key IS NULL) template whose name starts with "Create "
    — a no-op once already migrated, safe to re-run. Only ever touches
    outputs whose staged pipeline hasn't started yet and whose flat tasks
    are still untouched (status='not_started'), same safety rule as
    _migrate_targeted_video_pipeline."""
    for ct_key in pipeline.OPT_IN_PIPELINE_TYPE_KEYS:
        ct_id = type_ids.get(ct_key)
        if not ct_id:
            continue
        new_template = TASK_TEMPLATES.get(ct_key)
        if not new_template or len(new_template) != 1:
            continue
        already_migrated = dbmod.query_one(
            """SELECT id FROM task_templates WHERE content_type_id = ? AND stage_key IS NULL
               AND task_name = ?""",
            (ct_id, new_template[0][1]),
        )
        flat_count = dbmod.query_one(
            "SELECT COUNT(*) AS n FROM task_templates WHERE content_type_id = ? AND stage_key IS NULL", (ct_id,)
        )
        if already_migrated and flat_count and flat_count["n"] == 1:
            continue

        dbmod.execute("DELETE FROM task_templates WHERE content_type_id = ? AND stage_key IS NULL", (ct_id,))
        role_key, name, offset = new_template[0]
        dbmod.execute(
            """INSERT INTO task_templates (content_type_id, role_key, task_name, offset_days_before, sort_order, stage_key)
               VALUES (?,?,?,?,0,NULL)""",
            (ct_id, role_key, name, offset),
        )

        outputs = dbmod.rows_to_list(
            dbmod.query("SELECT * FROM content_outputs WHERE content_type_id = ?", (ct_id,))
        )
        for output in outputs:
            if dbmod.query_one("SELECT id FROM pipeline_stages WHERE output_id = ? LIMIT 1", (output["id"],)):
                continue  # already running its staged pipeline — leave it alone
            stale = dbmod.rows_to_list(dbmod.query(
                """SELECT id FROM tasks WHERE output_id = ? AND created_from_template = 1
                   AND status = 'not_started' AND stage_key IS NULL""",
                (output["id"],),
            ))
            for t in stale:
                dbmod.execute("DELETE FROM tasks WHERE id = ?", (t["id"],))
            if stale:
                task_engine.generate_tasks_for_campaign(output["campaign_id"], only_new_output_type=ct_id)


def _seed_creation_options(type_ids):
    for i, (key, label, icon, type_keys, pick_subtype) in enumerate(CREATION_OPTIONS):
        existing = dbmod.query_one("SELECT id FROM creation_options WHERE key = ?", (key,))
        if existing:
            continue
        output_ids = [type_ids[k] for k in type_keys if k in type_ids]
        dbmod.execute(
            "INSERT INTO creation_options (key, label, icon, output_type_ids, pick_subtype, sort_order) VALUES (?,?,?,?,?,?)",
            (key, label, icon, dbmod.to_json(output_ids), 1 if pick_subtype else 0, i),
        )
    _archive_deprecated_creation_options()


# These single-type "+ Create Content" menu entries are now folded into the
# "Filler Post" / "Monthly Campaign" two-step picker (Part 8/9) — archived
# rather than deleted so nothing that referenced them historically breaks.
DEPRECATED_CREATION_OPTION_KEYS = [
    "podcast", "testimony", "blog", "carousel", "normal_post", "moving_scripture",
    "quick_reel", "interview", "tiktok_style", "preaching_snippet", "bible_study", "course",
]


def _archive_deprecated_creation_options():
    for key in DEPRECATED_CREATION_OPTION_KEYS:
        dbmod.execute("UPDATE creation_options SET archived = 1 WHERE key = ? AND archived = 0", (key,))


def _seed_users():
    ids = {}
    for u in DEMO_USERS:
        existing = dbmod.query_one("SELECT id FROM users WHERE email = ?", (u["email"],))
        if existing:
            ids[u["role"]] = existing["id"]
            continue
        role_row = dbmod.query_one("SELECT id FROM roles WHERE key = ?", (u["role"],))
        new_id = dbmod.execute(
            "INSERT INTO users (name, email, password_hash, role_id, active) VALUES (?,?,?,?,1)",
            (u["name"], u["email"], generate_password_hash(DEFAULT_PASSWORD), role_row["id"]),
        )
        ids[u["role"]] = new_id
    return ids


def _seed_scheduling_rules(type_ids):
    today = date.today()

    def _roll_to_next_valid_month(compute_fn, y, m):
        for _ in range(14):
            occ = compute_fn(y, m)
            if occ and occ >= today:
                return occ
            m += 1
            if m > 12:
                m = 1
                y += 1
        return None

    def _upsert_rule(content_type_key, label, rule_type, weekday, anchor, horizon_weeks,
                      default_title, nth=None, interval_days=None, interval_months=None):
        """Insert a scheduling rule, or correct an existing one for the same
        content type in place if Jodie's spec changed its cadence (e.g.
        Testimony moving from 'last Tuesday' to 'first Tuesday') — updating
        rather than skip-if-exists so a corrected rule actually takes effect
        on a re-run, without losing the row's history/id."""
        ct_id = type_ids.get(content_type_key)
        if not ct_id:
            return
        existing = dbmod.query_one("SELECT id FROM scheduling_rules WHERE content_type_id = ?", (ct_id,))
        params = (label, rule_type, weekday, interval_days, nth, interval_months, anchor.isoformat(),
                  horizon_weeks, default_title)
        if existing:
            dbmod.execute(
                """UPDATE scheduling_rules SET label=?, rule_type=?, weekday=?, interval_days=?, nth=?,
                       interval_months=?, anchor_date=?, horizon_weeks=?, default_title=? WHERE id=?""",
                params + (existing["id"],),
            )
        else:
            dbmod.execute(
                """INSERT INTO scheduling_rules
                   (label, rule_type, weekday, interval_days, nth, interval_months, anchor_date,
                    horizon_weeks, default_title, content_type_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                params + (ct_id,),
            )

    # Targeted Campaign — unchanged biweekly Wednesday rhythm.
    anchor = next_weekday(today, 2)  # Wednesday
    _upsert_rule("targeted_short", "Targeted Campaign — biweekly Wednesday", "biweekly", 2,
                 anchor, 12, "New Targeted Campaign", interval_days=14)

    # Testimony — first Tuesday of every month.
    anchor = _roll_to_next_valid_month(lambda y, m: scheduling.nth_weekday_of_month(y, m, 1, 1), today.year, today.month)
    _upsert_rule("testimony", "Testimony — first Tuesday of the month", "monthly_nth_weekday", 1,
                 anchor, 16, "Monthly Testimony", nth=1)

    # Blog Post — second-last Thursday of every month (Blog Post Video pairs
    # automatically the same day — see content._spawn_paired_and_followup_content).
    anchor = _roll_to_next_valid_month(lambda y, m: scheduling.second_last_weekday_of_month(y, m, 3), today.year, today.month)
    _upsert_rule("blog", "Blog Post — second-last Thursday of the month", "monthly_second_last_weekday", 3,
                 anchor, 16, "Monthly Blog Post")

    # Podcast Episode — second Thursday of every third month. 3 Highlight/
    # Question posts per cycle spawn automatically (see
    # content._spawn_podcast_highlight_followups) — no separate rule needed
    # for those, they follow the episode.
    anchor = _roll_to_next_valid_month(lambda y, m: scheduling.nth_weekday_of_month(y, m, 3, 2), today.year, today.month)
    _upsert_rule("podcast_episode", "Podcast Episode — every 3rd month (2nd Thursday)", "every_n_months_nth_weekday",
                 3, anchor, 60, "Monthly Podcast Episode", nth=2, interval_months=3)


def _seed_ideas(user_ids):
    admin_id = user_ids.get("admin")
    for title in DEMO_IDEAS:
        existing = dbmod.query_one("SELECT id FROM content_ideas WHERE title = ?", (title,))
        if existing:
            continue
        dbmod.execute(
            "INSERT INTO content_ideas (title, created_by) VALUES (?, ?)", (title, admin_id)
        )


if __name__ == "__main__":
    seed()
