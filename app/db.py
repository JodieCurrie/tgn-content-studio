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
    conn.execute("PRAGMA foreign_keys = ON")
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    return first_time


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
