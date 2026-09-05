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
    ("targeted_full_repost", "Full Episode — Portrait Repost", "#E08A45", "targeted", 0, 1, 0, 1, 3, ["instagram", "facebook", "threads", "tiktok"], 0),

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
    ], True),
    ("custom", "Custom Event", "✨", ["custom"], False),
]

# task templates: content_type_key -> [(role_key, task_name, offset_days_before)]
# NOTE: these are still the simple/lightweight task lists from before the
# full production-pipeline rebuild (Part 14-18: Script Development ->
# Concept Hashout -> Film/Record -> Edit -> Review -> Audio -> Final
# Compilation, with real Calendar/Meet invites and Drive uploads) — that
# automated pipeline is its own separate, not-yet-started piece of work.
# These templates just make sure every content type has *something*
# actionable on the calendar today.
TASK_TEMPLATES = {
    # ---- Targeted ----
    "targeted_short": [
        ("admin", "Develop concept", 10), ("admin", "Write script", 9),
        ("production", "Film / record", 7), ("music", "Source/create audio", 6),
        ("production", "Edit short version & hand off to final editor", 4),
        ("editor", "Final pass & polish", 2), ("admin", "Approve edit", 1),
        ("admin", "Schedule & publish", 0),
    ],
    "targeted_long": [
        ("production", "Edit long version & hand off to final editor", 4),
        ("editor", "Final pass & polish", 2), ("admin", "Approve long edit", 1),
    ],
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
    "tiktok_style": [
        ("production", "Film/create clip", 3), ("production", "Edit & export", 1),
        ("admin", "Caption & schedule", 0),
    ],
    "interview": [
        ("admin", "Book guest & prep questions", 6), ("production", "Film interview", 4),
        ("production", "Edit & export", 1),
    ],
    "carousel": [
        ("production", "Design carousel slides", 2), ("admin", "Write copy & schedule", 0),
    ],
    "normal_post": [
        ("admin", "Write copy", 1), ("admin", "Schedule", 0),
    ],
    "moving_scripture": [
        ("production", "Film & edit", 3), ("music", "Add background audio", 1),
        ("admin", "Caption & schedule", 0),
    ],
    "quick_reel": [
        ("production", "Film & edit", 2), ("admin", "Caption & schedule", 0),
    ],
    "scripture_expansion": [
        ("production", "Film & edit", 2), ("admin", "Caption & schedule", 0),
    ],
    "preaching_teaching": [
        ("production", "Cut & edit clip", 2), ("admin", "Caption & schedule", 0),
    ],
    "podcast_additional_highlight": [
        ("admin", "Pick moment & write copy", 2), ("production", "Create graphic/clip", 1),
    ],

    # ---- Monthly ----
    "podcast_episode": [
        ("admin", "Prep questions/outline", 9), ("production", "Record episode", 7),
        ("production", "Edit & export", 4), ("music", "Intro/outro audio", 3),
        ("admin", "Review edit", 3), ("admin", "Schedule & publish", 0),
    ],
    "podcast_highlight": [
        ("admin", "Pick question & write copy", 2), ("production", "Create graphic/clip", 1),
    ],
    "course": [
        ("admin", "Outline course content", 12), ("production", "Film & edit lessons", 8),
        ("admin", "Review all lessons", 3),
    ],
    "course_highlight": [
        ("production", "Cut highlight from source lesson", 2), ("admin", "Approve & schedule", 0),
    ],
    "blog": [
        ("admin", "Write draft", 6), ("admin", "Review & edit", 2), ("admin", "Publish to website", 0),
    ],
    "blog_video": [
        ("production", "Edit companion video", 4), ("admin", "Approve & schedule", 1),
    ],
    "testimony": [
        ("admin", "Reach out & coordinate", 9), ("production", "Film testimony", 7),
        ("production", "Edit & export", 4), ("admin", "Review footage", 3),
        ("admin", "Schedule & publish", 0),
    ],

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
    app = create_app()
    with app.app_context():
        _seed_roles()
        _seed_platforms()
        type_ids = _seed_content_types()
        _seed_task_templates(type_ids)
        _seed_creation_options(type_ids)
        user_ids = _seed_users()
        _seed_scheduling_rules(type_ids)
        _seed_ideas(user_ids)
        created = scheduling.materialize_all_active_rules()
        print(f"Materialized {len(created)} rule-generated campaigns.")
        _showcase_first_campaign()
        dbmod.get_db().commit()
    print("Seed complete.")


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
DEPRECATED_CONTENT_TYPE_KEYS = ["highlight", "podcast_question", "bible_study", "preaching_snippet"]


def _archive_deprecated_content_types():
    for key in DEPRECATED_CONTENT_TYPE_KEYS:
        dbmod.execute("UPDATE content_types SET archived = 1 WHERE key = ? AND archived = 0", (key,))


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
                "INSERT INTO task_templates (content_type_id, role_key, task_name, offset_days_before, sort_order) VALUES (?,?,?,?,?)",
                (ct_id, role_key, name, offset, order),
            )


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
