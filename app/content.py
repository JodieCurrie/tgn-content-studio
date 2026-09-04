"""
Campaign/output business logic — creation, detail assembly, updates.
Kept separate from routes/content.py (the blueprint) so scheduling.py can
call create_campaign_from_rule() without importing Flask request handling.
"""
from datetime import date, timedelta

from . import db
from . import task_engine


def get_content_type(content_type_id):
    return db.row_to_dict(db.query_one("SELECT * FROM content_types WHERE id = ?", (content_type_id,)))


def get_content_type_by_key(key):
    return db.row_to_dict(db.query_one("SELECT * FROM content_types WHERE key = ?", (key,)))


def create_campaign_from_rule(rule, publish_date_iso):
    """Called by scheduling.materialize_rule() to turn one occurrence of a
    rule into a real Campaign (+ its outputs + its tasks)."""
    ct = get_content_type(rule["content_type_id"])
    title = rule["default_title"] or ct["label"]

    campaign_id = db.execute(
        """INSERT INTO campaigns
           (title, publish_date, status, primary_content_type_id,
            scheduling_rule_id, schedule_origin, created_at, updated_at)
           VALUES (?, ?, 'planned', ?, ?, 'rule', datetime('now'), datetime('now'))""",
        (title, publish_date_iso, ct["id"], rule["id"]),
    )

    add_output(campaign_id, ct["id"], publish_date_iso)
    task_engine.generate_tasks_for_campaign(campaign_id)

    # A targeted campaign automatically spawns its long-form companion output
    # (Part 10: short + long from the same shared topic) and, a week later,
    # a dependent highlight-snippet follow-up pair (Part 11).
    if ct["key"] == "targeted_short":
        long_ct = get_content_type_by_key("targeted_long")
        if long_ct:
            add_output(campaign_id, long_ct["id"], publish_date_iso)
            task_engine.generate_tasks_for_campaign(campaign_id, only_new_output_type=long_ct["id"])
        _spawn_highlight_followups(campaign_id, publish_date_iso)

    if ct["key"] == "testimony":
        _spawn_blog_followup(campaign_id, publish_date_iso)

    return campaign_id


def _spawn_blog_followup(parent_campaign_id, parent_publish_iso, actor_id=None):
    """Blog is locked one day after Testimony in the observed pattern
    (Tue testimony -> Wed blog, same end-of-month week)."""
    blog_ct = get_content_type_by_key("blog")
    if not blog_ct:
        return
    follow_date = (date.fromisoformat(parent_publish_iso) + timedelta(days=1)).isoformat()
    parent_title = db.query_one("SELECT title FROM campaigns WHERE id = ?", (parent_campaign_id,))["title"]
    dep_id = db.execute(
        """INSERT INTO campaigns
           (title, publish_date, status, primary_content_type_id,
            schedule_origin, depends_on_campaign_id, dependency_offset_days,
            created_at, updated_at)
           VALUES (?, ?, 'planned', ?, 'dependent', ?, 1, datetime('now'), datetime('now'))""",
        (f"End of month blog — {parent_title}", follow_date, blog_ct["id"], parent_campaign_id),
    )
    add_output(dep_id, blog_ct["id"], follow_date)
    task_engine.generate_tasks_for_campaign(dep_id)
    db.execute(
        "INSERT INTO activity_log (campaign_id, actor_id, message) VALUES (?, ?, ?)",
        (dep_id, actor_id, f"Auto-created alongside '{parent_title}' (monthly testimony + blog pairing)."),
    )


def _spawn_highlight_followups(parent_campaign_id, parent_publish_iso, actor_id=None):
    highlight_ct = get_content_type_by_key("highlight")
    if not highlight_ct:
        return
    parent_date = date.fromisoformat(parent_publish_iso)
    # mirrors the observed pattern: highlight snippets land on the Monday and
    # Friday of the following week (offsets +5 and +9 days from a Wednesday)
    for offset, label in ((5, "Highlight Snippet (Mon follow-up)"), (9, "Highlight Snippet (Fri follow-up)")):
        follow_date = (parent_date + timedelta(days=offset)).isoformat()
        parent_title = db.query_one("SELECT title FROM campaigns WHERE id = ?", (parent_campaign_id,))["title"]
        dep_id = db.execute(
            """INSERT INTO campaigns
               (title, publish_date, status, primary_content_type_id,
                schedule_origin, depends_on_campaign_id, dependency_offset_days,
                created_at, updated_at)
               VALUES (?, ?, 'planned', ?, 'dependent', ?, ?, datetime('now'), datetime('now'))""",
            (f"{parent_title} — Highlight Snippet", follow_date, highlight_ct["id"], parent_campaign_id, offset),
        )
        add_output(dep_id, highlight_ct["id"], follow_date)
        task_engine.generate_tasks_for_campaign(dep_id)
        db.execute(
            "INSERT INTO activity_log (campaign_id, actor_id, message) VALUES (?, ?, ?)",
            (dep_id, actor_id, f"Auto-created as a follow-up to '{parent_title}'."),
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
                     assigned_user_id=None, concept="", created_by=None, source_idea_id=None,
                     extra_output_type_ids=None):
    """Used by the quick "+ Create Content" flow and the Ideas -> Schedule flow."""
    ct = get_content_type(content_type_id)
    campaign_id = db.execute(
        """INSERT INTO campaigns
           (title, concept, publish_date, status, owner_id, primary_content_type_id,
            schedule_origin, source_idea_id, created_by, created_at, updated_at)
           VALUES (?, ?, ?, 'planned', ?, ?, 'manual', ?, ?, datetime('now'), datetime('now'))""",
        (title, concept, publish_date_iso, owner_id, ct["id"], source_idea_id, created_by),
    )
    add_output(campaign_id, ct["id"], publish_date_iso, platform_ids=platform_ids, assigned_user_id=assigned_user_id)
    for extra_id in (extra_output_type_ids or []):
        add_output(campaign_id, extra_id, publish_date_iso, assigned_user_id=assigned_user_id)
    task_engine.generate_tasks_for_campaign(campaign_id)

    # Same auto-follow-up behaviour as rule-generated campaigns, so a
    # manually-created targeted campaign or testimony gets its highlight
    # snippets / end-of-month blog too (Part 11), not just the recurring ones.
    if ct["key"] == "targeted_short" and not extra_output_type_ids:
        long_ct = get_content_type_by_key("targeted_long")
        if long_ct:
            add_output(campaign_id, long_ct["id"], publish_date_iso, assigned_user_id=assigned_user_id)
            task_engine.generate_tasks_for_campaign(campaign_id, only_new_output_type=long_ct["id"])
    if ct["key"] == "targeted_short":
        _spawn_highlight_followups(campaign_id, publish_date_iso, actor_id=created_by)
    if ct["key"] == "testimony":
        _spawn_blog_followup(campaign_id, publish_date_iso, actor_id=created_by)

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
    campaign["tasks"] = tasks
    campaign["inspiration"] = inspiration
    campaign["assets"] = assets
    campaign["comments"] = comments
    campaign["activity"] = activity
    campaign["dependents"] = dependents
    campaign["parent_campaign"] = parent
    return campaign


CAMPAIGN_TEXT_FIELDS = {
    "title", "concept", "script", "bible_references", "caption", "notes",
    "notes_to_videographer", "notes_to_musician", "status", "owner_id",
}


def update_campaign_fields(campaign_id, fields):
    allowed = {k: v for k, v in fields.items() if k in CAMPAIGN_TEXT_FIELDS}
    if not allowed:
        return
    set_clause = ", ".join(f"{k} = ?" for k in allowed)
    params = list(allowed.values()) + [campaign_id]
    db.execute(f"UPDATE campaigns SET {set_clause}, updated_at = datetime('now') WHERE id = ?", params)


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
