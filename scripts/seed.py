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

# key, label, color, is_filler, req_video, req_music, req_jodie, lead_days, platform_keys, is_campaign_type
CONTENT_TYPES = [
    ("targeted_short", "Targeted Video — Short", "#C895D5", 0, 1, 1, 1, 10, ["instagram", "tiktok", "facebook", "threads"], 1),
    ("targeted_long", "Targeted Video — YouTube", "#F09756", 0, 1, 1, 1, 10, ["youtube"], 0),
    ("highlight", "Highlight / Snippet", "#FEBD5A", 0, 1, 0, 1, 3, ["instagram", "facebook", "threads"], 0),
    ("tiktok_style", "TikTok-Style Video", "#A3C0B6", 1, 1, 0, 1, 4, ["tiktok"], 0),
    ("carousel", "Carousel", "#FD738E", 1, 1, 0, 1, 3, ["instagram", "facebook"], 0),
    ("normal_post", "Normal / Static Post", "#9FA5C9", 1, 0, 0, 1, 2, ["instagram", "facebook", "threads"], 0),
    ("moving_scripture", "Moving Scripture", "#CC6DA6", 1, 1, 1, 1, 4, ["instagram", "facebook"], 0),
    ("podcast_question", "Podcast Question", "#97D2FB", 1, 1, 0, 1, 3, ["instagram", "facebook", "threads"], 0),
    ("testimony", "Testimony", "#75AAC9", 0, 1, 0, 1, 10, ["instagram", "youtube", "facebook"], 0),
    ("blog", "Blog Post", "#FEC4D5", 0, 0, 0, 1, 7, ["website"], 0),
    ("podcast_episode", "Podcast Episode", "#7FC8A9", 0, 1, 1, 1, 10, ["youtube", "podcast"], 0),
    ("quick_reel", "Quick Reel", "#B5EAD7", 1, 1, 0, 1, 3, ["instagram", "tiktok"], 0),
    ("interview", "Interview / Studio", "#FFDAC1", 1, 1, 0, 1, 7, ["youtube", "instagram"], 0),
    ("bible_study", "Bible Study", "#C7CEEA", 1, 1, 0, 1, 5, ["youtube", "instagram"], 0),
    ("preaching_snippet", "Preaching / Teaching Snippet", "#E2F0CB", 1, 1, 0, 1, 3, ["instagram", "facebook"], 0),
    ("course", "Course / Educational", "#FFD3B4", 0, 1, 0, 1, 14, ["youtube", "website"], 0),
    ("custom", "Custom", "#D9D9D9", 1, 0, 0, 1, 5, [], 0),
]

CREATION_OPTIONS = [
    ("targeted_campaign", "Targeted Campaign", "🎯", ["targeted_short", "targeted_long"]),
    ("podcast", "Podcast Episode", "🎙️", ["podcast_episode"]),
    ("testimony", "Testimony", "🙌", ["testimony"]),
    ("blog", "Blog", "📝", ["blog"]),
    ("carousel", "Carousel", "🎠", ["carousel"]),
    ("normal_post", "Normal Post", "📱", ["normal_post"]),
    ("moving_scripture", "Moving Scripture", "✝️", ["moving_scripture"]),
    ("quick_reel", "Quick Reel", "⚡", ["quick_reel"]),
    ("interview", "Interview / Studio", "🎬", ["interview"]),
    ("tiktok_style", "TikTok-Style Video", "🎵", ["tiktok_style"]),
    ("preaching_snippet", "Preaching / Teaching Snippet", "📖", ["preaching_snippet"]),
    ("bible_study", "Bible Study", "📚", ["bible_study"]),
    ("course", "Course / Educational", "🎓", ["course"]),
    ("custom", "Custom", "✨", ["custom"]),
]

# task templates: content_type_key -> [(role_key, task_name, offset_days_before)]
TASK_TEMPLATES = {
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
    "highlight": [
        ("production", "Cut highlight from source video", 2), ("production", "Export", 1),
        ("admin", "Approve & schedule", 0),
    ],
    "tiktok_style": [
        ("production", "Film/create clip", 3), ("production", "Edit & export", 1),
        ("admin", "Caption & schedule", 0),
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
    "podcast_question": [
        ("admin", "Pick question & write copy", 2), ("production", "Create graphic/clip", 1),
    ],
    "testimony": [
        ("admin", "Reach out & coordinate", 9), ("production", "Film testimony", 7),
        ("production", "Edit & export", 4), ("admin", "Review footage", 3),
        ("admin", "Schedule & publish", 0),
    ],
    "blog": [
        ("admin", "Write draft", 6), ("admin", "Review & edit", 2), ("admin", "Publish to website", 0),
    ],
    "podcast_episode": [
        ("admin", "Prep questions/outline", 9), ("production", "Record episode", 7),
        ("production", "Edit & export", 4), ("music", "Intro/outro audio", 3),
        ("admin", "Review edit", 3), ("admin", "Schedule & publish", 0),
    ],
    "quick_reel": [
        ("production", "Film & edit", 2), ("admin", "Caption & schedule", 0),
    ],
    "interview": [
        ("admin", "Book guest & prep questions", 6), ("production", "Film interview", 4),
        ("production", "Edit & export", 1),
    ],
    "bible_study": [
        ("admin", "Write study outline", 4), ("production", "Film/record & edit", 2),
    ],
    "preaching_snippet": [
        ("production", "Cut & edit clip", 2), ("admin", "Caption & schedule", 0),
    ],
    "course": [
        ("admin", "Outline course content", 12), ("production", "Film & edit lessons", 8),
        ("admin", "Review all lessons", 3),
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
    for i, (key, label, color, is_filler, rv, rm, rj, lead, plat_keys, is_campaign) in enumerate(CONTENT_TYPES):
        existing = dbmod.query_one("SELECT id FROM content_types WHERE key = ?", (key,))
        if existing:
            ids[key] = existing["id"]
            continue
        plat_ids = _platform_ids(plat_keys)
        new_id = dbmod.execute(
            """INSERT INTO content_types
               (key, label, color, description, is_filler, requires_videographer, requires_musician,
                requires_jodie, default_lead_time_days, default_platform_ids, is_campaign_type, sort_order)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, label, color, "", is_filler, rv, rm, rj, lead, dbmod.to_json(plat_ids), is_campaign, i),
        )
        ids[key] = new_id
    return ids


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
    for i, (key, label, icon, type_keys) in enumerate(CREATION_OPTIONS):
        existing = dbmod.query_one("SELECT id FROM creation_options WHERE key = ?", (key,))
        if existing:
            continue
        output_ids = [type_ids[k] for k in type_keys if k in type_ids]
        dbmod.execute(
            "INSERT INTO creation_options (key, label, icon, output_type_ids, sort_order) VALUES (?,?,?,?,?)",
            (key, label, icon, dbmod.to_json(output_ids), i),
        )


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

    if not dbmod.query_one("SELECT id FROM scheduling_rules WHERE label LIKE 'Targeted Campaign%'"):
        anchor = next_weekday(today, 2)  # Wednesday
        dbmod.execute(
            """INSERT INTO scheduling_rules
               (label, content_type_id, rule_type, weekday, interval_days, anchor_date, horizon_weeks, default_title)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("Targeted Campaign — biweekly Wednesday", type_ids["targeted_short"], "biweekly", 2, 14,
             anchor.isoformat(), 12, "New Targeted Campaign"),
        )

    if not dbmod.query_one("SELECT id FROM scheduling_rules WHERE label LIKE 'Testimony%'"):
        anchor = scheduling.last_weekday_of_month(today.year, today.month, 1)  # Tuesday
        if anchor < today:
            nm = today.month + 1 if today.month < 12 else 1
            ny = today.year if today.month < 12 else today.year + 1
            anchor = scheduling.last_weekday_of_month(ny, nm, 1)
        dbmod.execute(
            """INSERT INTO scheduling_rules
               (label, content_type_id, rule_type, weekday, anchor_date, horizon_weeks, default_title)
               VALUES (?,?,?,?,?,?,?)""",
            ("Testimony — last Tuesday of the month", type_ids["testimony"], "monthly_last_weekday", 1,
             anchor.isoformat(), 12, "Monthly Testimony"),
        )

    if not dbmod.query_one("SELECT id FROM scheduling_rules WHERE label LIKE 'Podcast Episode%'"):
        anchor = scheduling.nth_weekday_of_month(today.year, today.month, 2, 2)  # 2nd Wednesday
        if not anchor or anchor < today:
            nm = today.month + 1 if today.month < 12 else 1
            ny = today.year if today.month < 12 else today.year + 1
            anchor = scheduling.nth_weekday_of_month(ny, nm, 2, 2)
        dbmod.execute(
            """INSERT INTO scheduling_rules
               (label, content_type_id, rule_type, weekday, nth, anchor_date, horizon_weeks, default_title)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("Podcast Episode — monthly (2nd Wednesday)", type_ids["podcast_episode"], "monthly_nth_weekday", 2, 2,
             anchor.isoformat(), 12, "Monthly Podcast Episode"),
        )


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
