"""
Thin database layer over sqlite3.

Why raw sqlite3 and not an ORM: this deployment target has no SQLAlchemy
available at build time, and SQLite is a completely legitimate production
choice at TGN's scale (a handful of users, a content calendar, low write
volume) as long as the host gives it a persistent disk — see README.md
"Database choice" for the reasoning and the Postgres upgrade path.

Every function here returns plain dicts (via sqlite3.Row) so the rest of
the app never touches a cursor directly.
"""
import sqlite3
import os
import json
from pathlib import Path
from flask import g, current_app

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_db():
    if "db" not in g:
        db_path = current_app.config["DATABASE_PATH"]
        g.db = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(db_path):
    """Create the schema if the database file doesn't exist yet or is empty."""
    first_time = not os.path.exists(db_path) or os.path.getsize(db_path) == 0
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())
    _rebuild_pipeline_stages_v2_if_needed(conn)
    _run_migrations(conn)
    conn.commit()
    conn.close()
    return first_time


# New columns added to already-existing tables after the app went live can't
# just be added to the CREATE TABLE statements above — `CREATE TABLE IF NOT
# EXISTS` is a no-op once the table already exists in production, so a real
# migration step is needed here. Each entry is safe to run repeatedly (it
# checks the column doesn't already exist first) and never touches existing
# rows/data — it only adds a new, defaulted column alongside them.
_COLUMN_MIGRATIONS = [
    ("content_types", "category_key", "TEXT NOT NULL DEFAULT 'targeted'"),
    ("creation_options", "pick_subtype", "INTEGER NOT NULL DEFAULT 0"),
    ("scheduling_rules", "interval_months", "INTEGER"),
    ("task_templates", "stage_key", "TEXT"),
    ("tasks", "stage_key", "TEXT"),
    ("content_outputs", "drive_folder_id", "TEXT"),
    ("content_outputs", "drive_folder_link", "TEXT"),
    ("pipeline_stages", "meeting_start", "TEXT"),
    ("pipeline_stages", "meeting_end", "TEXT"),
    ("pipeline_stages", "participant_user_ids", "TEXT"),
    ("pipeline_stages", "meeting_confirmed_at", "TEXT"),
    ("pipeline_stages", "delivery_link", "TEXT"),
    ("pipeline_stages", "delivery_deadline", "TEXT"),
    ("pipeline_stages", "delivery_email_sent_at", "TEXT"),
    ("pipeline_stages", "delivery_recipient_user_id", "INTEGER REFERENCES users(id)"),
    ("pipeline_stages", "review_decision", "TEXT"),
    ("pipeline_stages", "review_notes", "TEXT"),
    ("pipeline_stages", "reviewed_at", "TEXT"),
    ("pipeline_stages", "submission_link", "TEXT"),
    ("pipeline_stages", "submission_notes", "TEXT"),
    ("pipeline_stages", "submitted_at", "TEXT"),
    ("pipeline_stages", "highlight_candidates", "TEXT"),
    ("pipeline_stages", "audio_decision", "TEXT"),
    ("campaigns", "drive_folder_id", "TEXT"),
    ("campaigns", "drive_folder_link", "TEXT"),
    ("campaigns", "script_youtube", "TEXT NOT NULL DEFAULT ''"),
]


def _column_exists(conn, table, column):
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})").fetchall())


# Pipeline v2 (Part 14-18+) reshapes `pipeline_stages`: Script Development /
# Concept Hashout / Film-Record become shared per-campaign rows (output_id
# NULL, campaign_id set) instead of duplicated per output, which also means
# `output_id` has to become nullable and a CHECK/second UNIQUE constraint
# need adding. SQLite can't relax a NOT NULL column or add a CHECK via
# ALTER TABLE, so — only if an *old-shaped* table is found (no `campaign_id`
# column yet) — this renames it aside, lets the schema.sql executescript
# above (which already ran) stand up a fresh new-shaped table, best-effort
# carries over any real scheduled-meeting data for script/concept/film (one
# row per campaign+stage, preferring whichever old per-output row actually
# has a meeting_start), and drops the old table. Old edit/review/audio/
# compilation rows are intentionally NOT carried over — that stage set
# changed shape too (review_edit/review_audio didn't exist before), and
# fresh rows are recreated the next time tasks are (re)generated. Safe to
# run repeatedly: a no-op once `campaign_id` exists.
def _rebuild_pipeline_stages_v2_if_needed(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "pipeline_stages" not in tables or _column_exists(conn, "pipeline_stages", "campaign_id"):
        return

    old_rows = [dict(r) for r in conn.execute("SELECT * FROM pipeline_stages").fetchall()]
    conn.execute("ALTER TABLE pipeline_stages RENAME TO pipeline_stages_old_v1")
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())  # CREATE TABLE IF NOT EXISTS now actually creates the new-shaped table

    SHOOT_KEYS = ("script", "concept", "film")
    output_to_campaign = {
        r[0]: r[1] for r in conn.execute("SELECT id, campaign_id FROM content_outputs").fetchall()
    }
    best_by_campaign_stage = {}
    for row in old_rows:
        if row.get("stage_key") not in SHOOT_KEYS:
            continue  # edit/review/audio/compilation: superseded shape, don't carry over
        campaign_id = output_to_campaign.get(row.get("output_id"))
        if not campaign_id:
            continue
        key = (campaign_id, row["stage_key"])
        current = best_by_campaign_stage.get(key)
        if current is None or (not current.get("meeting_start") and row.get("meeting_start")):
            best_by_campaign_stage[key] = row

    for (campaign_id, stage_key), row in best_by_campaign_stage.items():
        conn.execute(
            """INSERT INTO pipeline_stages
               (campaign_id, stage_key, sort_order, calendar_event_id, calendar_link, meet_link,
                activated_at, meeting_start, meeting_end, participant_user_ids, meeting_confirmed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                campaign_id, stage_key, SHOOT_KEYS.index(stage_key),
                row.get("calendar_event_id"), row.get("calendar_link"), row.get("meet_link"),
                row.get("activated_at"), row.get("meeting_start"), row.get("meeting_end"),
                row.get("participant_user_ids"), row.get("meeting_confirmed_at"),
            ),
        )
    conn.execute("DROP TABLE pipeline_stages_old_v1")


def _run_migrations(conn):
    for table, column, coldef in _COLUMN_MIGRATIONS:
        if not _column_exists(conn, table, column):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")


def register(app):
    app.teardown_appcontext(close_db)


def row_to_dict(row):
    if row is None:
        return None
    return dict(row)


def rows_to_list(rows):
    return [dict(r) for r in rows]


def query(sql, params=()):
    return get_db().execute(sql, params).fetchall()


def query_one(sql, params=()):
    return get_db().execute(sql, params).fetchone()


def execute(sql, params=()):
    db = get_db()
    cur = db.execute(sql, params)
    db.commit()
    return cur.lastrowid


def executemany(sql, seq_of_params):
    db = get_db()
    db.executemany(sql, seq_of_params)
    db.commit()


def to_json(value):
    return json.dumps(value)


def from_json(value, default=None):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
