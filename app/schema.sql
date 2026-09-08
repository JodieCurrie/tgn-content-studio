-- TGN Content Studio — database schema (SQLite for local/dev; portable ANSI SQL,
-- documented Postgres-equivalent types in README for the production swap).
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Roles & Users
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS roles (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    key                     TEXT NOT NULL UNIQUE,      -- 'admin','production','music','member'
    label                   TEXT NOT NULL,
    is_admin                INTEGER NOT NULL DEFAULT 0,-- full access: users, content types, all content
    can_manage_all_content  INTEGER NOT NULL DEFAULT 0,-- sees & edits every campaign, not just assigned
    can_manage_settings     INTEGER NOT NULL DEFAULT 0,-- content types / task templates / scheduling rules
    color                   TEXT NOT NULL DEFAULT '#9FA5C9'
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    role_id         INTEGER NOT NULL REFERENCES roles(id),
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Platforms & Content Types (the colour-coded key)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS platforms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS content_types (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    key                     TEXT NOT NULL UNIQUE,
    label                   TEXT NOT NULL,
    color                   TEXT NOT NULL,             -- hex, matches (or extends) the Excel legend
    description             TEXT NOT NULL DEFAULT '',
    is_filler               INTEGER NOT NULL DEFAULT 1,
    requires_videographer   INTEGER NOT NULL DEFAULT 0,
    requires_musician       INTEGER NOT NULL DEFAULT 0,
    requires_jodie          INTEGER NOT NULL DEFAULT 1,
    default_lead_time_days  INTEGER NOT NULL DEFAULT 5, -- how far before publish work should start
    default_platform_ids    TEXT NOT NULL DEFAULT '[]', -- JSON array of platform ids
    is_campaign_type        INTEGER NOT NULL DEFAULT 0, -- true = spawns multiple outputs (e.g. Targeted Campaign)
    category_key            TEXT NOT NULL DEFAULT 'targeted', -- 'targeted' | 'filler' | 'monthly' — the 3 big buckets
    sort_order              INTEGER NOT NULL DEFAULT 0,
    archived                INTEGER NOT NULL DEFAULT 0
);

-- "+ Create Content" menu options (Part 6). Configurable: each option maps
-- to one or more content_types that get created as outputs on the new
-- campaign. Keeping this separate from content_types lets a single quick
-- pick like "Targeted Campaign" fan out into several coloured outputs.
-- When pick_subtype = 1 (Filler Post / Monthly Campaign), output_type_ids is
-- the MENU of choices offered in a second dropdown rather than a fixed set
-- of outputs to create all at once — the UI submits the one the user picked
-- as an override (see quick_create in routes/api.py).
CREATE TABLE IF NOT EXISTS creation_options (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    key             TEXT NOT NULL UNIQUE,
    label           TEXT NOT NULL,
    icon            TEXT NOT NULL DEFAULT '✨',
    output_type_ids TEXT NOT NULL DEFAULT '[]', -- JSON array of content_type ids
    pick_subtype    INTEGER NOT NULL DEFAULT 0, -- 1 = show a second "which type?" dropdown at creation time
    sort_order      INTEGER NOT NULL DEFAULT 0,
    archived        INTEGER NOT NULL DEFAULT 0
);

-- ---------------------------------------------------------------------------
-- Scheduling rules (recurring generators)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scheduling_rules (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    label               TEXT NOT NULL,
    content_type_id     INTEGER NOT NULL REFERENCES content_types(id),
    rule_type           TEXT NOT NULL,      -- 'biweekly' | 'monthly_nth_weekday' | 'monthly_last_weekday'
                                             -- | 'monthly_second_last_weekday' | 'every_n_months_nth_weekday'
    weekday             INTEGER NOT NULL,   -- 0=Mon .. 6=Sun (Python convention)
    interval_days        INTEGER,           -- for 'biweekly'
    nth                  INTEGER,           -- for 'monthly_nth_weekday' / 'every_n_months_nth_weekday' (1..4)
    interval_months       INTEGER,          -- for 'every_n_months_nth_weekday' (e.g. 3 = every 3rd month)
    anchor_date          TEXT NOT NULL,     -- ISO date; the current effective anchor / most recent occurrence
    active               INTEGER NOT NULL DEFAULT 1,
    horizon_weeks        INTEGER NOT NULL DEFAULT 10, -- how far ahead to materialize campaigns
    default_title        TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Content ideas bank (unscheduled)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS content_ideas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    notes           TEXT NOT NULL DEFAULT '',
    links           TEXT NOT NULL DEFAULT '',           -- newline-separated URLs
    content_type_id INTEGER REFERENCES content_types(id),-- the specific type Jodie tagged it as (nullable = untyped)
    created_by      INTEGER REFERENCES users(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    scheduled_campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL -- set once converted
);

-- ---------------------------------------------------------------------------
-- Campaigns (the shared idea/topic) — everything scheduled is a Campaign,
-- even a single filler post, so idea -> outputs -> tasks stays one shape.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS campaigns (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    title                   TEXT NOT NULL,
    concept                 TEXT NOT NULL DEFAULT '',
    script                  TEXT NOT NULL DEFAULT '',
    bible_references        TEXT NOT NULL DEFAULT '',
    caption                 TEXT NOT NULL DEFAULT '',
    notes                   TEXT NOT NULL DEFAULT '',
    notes_to_videographer   TEXT NOT NULL DEFAULT '',
    notes_to_musician       TEXT NOT NULL DEFAULT '',
    publish_date            TEXT NOT NULL,          -- ISO date, primary/anchor publish date
    status                  TEXT NOT NULL DEFAULT 'idea',
    owner_id                INTEGER REFERENCES users(id),
    primary_content_type_id INTEGER REFERENCES content_types(id),

    -- scheduling provenance — drives the drag-and-drop resolution logic
    scheduling_rule_id      INTEGER REFERENCES scheduling_rules(id) ON DELETE SET NULL,
    schedule_origin         TEXT NOT NULL DEFAULT 'manual', -- 'manual' | 'rule' | 'dependent'
    is_rule_exception       INTEGER NOT NULL DEFAULT 0,     -- broken off its rule ("this one only")
    depends_on_campaign_id  INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
    dependency_offset_days  INTEGER,                        -- offset from parent's publish_date at creation

    source_idea_id          INTEGER REFERENCES content_ideas(id) ON DELETE SET NULL,
    created_by              INTEGER REFERENCES users(id),
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now')),

    -- Targeted Video production pipeline v2 (Part 14-18+): Script
    -- Development / Concept Hashout / Film-Record are shared once per
    -- shoot (per campaign) rather than duplicated across the Short and
    -- YouTube outputs — see app/pipeline.py SHOOT_STAGE_KEYS. One Drive
    -- folder per shoot (covers both outputs' files); script_youtube is
    -- the second, YouTube-specific script Script Development captures
    -- alongside the existing `script` field.
    drive_folder_id         TEXT,
    drive_folder_link       TEXT,
    script_youtube          TEXT NOT NULL DEFAULT ''
);

-- ---------------------------------------------------------------------------
-- Content outputs (platform-specific deliverables within a campaign)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS content_outputs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id         INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    content_type_id     INTEGER NOT NULL REFERENCES content_types(id),
    title               TEXT,                     -- defaults to campaign title if null
    publish_date        TEXT NOT NULL,             -- may differ slightly from campaign anchor
    status               TEXT NOT NULL DEFAULT 'idea',
    assigned_user_id     INTEGER REFERENCES users(id),
    sort_order           INTEGER NOT NULL DEFAULT 0,
    -- One Drive folder per video (Targeted Video production pipeline, Part
    -- 14-18) — created automatically once the pipeline reaches Concept
    -- Hashout; every stage's files live in this one folder. NULL for output
    -- types that don't use the pipeline.
    drive_folder_id      TEXT,
    drive_folder_link    TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS output_platforms (
    output_id   INTEGER NOT NULL REFERENCES content_outputs(id) ON DELETE CASCADE,
    platform_id INTEGER NOT NULL REFERENCES platforms(id),
    PRIMARY KEY (output_id, platform_id)
);

-- ---------------------------------------------------------------------------
-- Tasks (production work, separate from publish date)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_templates (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    content_type_id     INTEGER NOT NULL REFERENCES content_types(id) ON DELETE CASCADE,
    role_key            TEXT NOT NULL,             -- which role this task is typically for
    task_name           TEXT NOT NULL,
    offset_days_before  INTEGER NOT NULL DEFAULT 3,-- due date = publish_date - offset
    sort_order          INTEGER NOT NULL DEFAULT 0,
    -- Which of the 7 Targeted Video pipeline stages this task belongs to
    -- (see app/pipeline.py PIPELINE_STAGES) — NULL for every other content
    -- type, which just keeps the plain flat task list they've always had.
    stage_key           TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id     INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    output_id       INTEGER REFERENCES content_outputs(id) ON DELETE CASCADE,
    task_name       TEXT NOT NULL,
    role_key        TEXT NOT NULL DEFAULT 'member',
    assigned_user_id INTEGER REFERENCES users(id),
    due_date        TEXT,
    status          TEXT NOT NULL DEFAULT 'not_started',
    priority        TEXT NOT NULL DEFAULT 'normal',
    notes           TEXT NOT NULL DEFAULT '',
    created_from_template INTEGER NOT NULL DEFAULT 0,
    stage_key       TEXT,       -- copied from the template that created it; see pipeline.py
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Targeted Video production pipeline v2 (Part 14-18+): two tiers of stages.
--
--   Shoot-level  (script -> concept -> film): ONE set per campaign, shared
--   by both the Short and YouTube/Long outputs, since they come from the
--   same shoot. These rows have campaign_id set and output_id NULL.
--
--   Production-level (edit -> review_edit -> [audio -> review_audio ->
--   compilation] for Short / [highlights] for Long): one set PER OUTPUT,
--   since editing/review/audio/highlights genuinely differ between the two
--   deliverables. These rows have output_id set and campaign_id NULL.
--
-- A stage's actual status is still derived from its tasks' statuses (see
-- pipeline.py) — shared shoot-stage tasks are tagged with campaign_id and
-- stage_key but output_id NULL; production-stage tasks keep the existing
-- output_id + stage_key tagging. This table only holds what tasks can't:
-- one-time Calendar/Meet/Drive side effects, meeting scheduling, hand-off
-- (recipient/deadline) capture, review decisions, and submission links —
-- kept here so each only ever fires/records once.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pipeline_stages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id         INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
    output_id           INTEGER REFERENCES content_outputs(id) ON DELETE CASCADE,
    stage_key           TEXT NOT NULL,
    sort_order          INTEGER NOT NULL,
    calendar_event_id   TEXT,       -- Concept Hashout + Film/Record only
    calendar_link       TEXT,
    meet_link           TEXT,       -- Concept Hashout only
    activated_at        TEXT,       -- when this stage's side effects fired (unlocked)

    -- Real meeting scheduling (Concept Hashout / Film-Record only): a stage
    -- isn't auto-scheduled at a fixed time anymore — a human picks a real
    -- date/time/participant list, which can be revised (reschedule keeps the
    -- same row + the same Calendar event, just updates these).
    meeting_start           TEXT,       -- ISO datetime
    meeting_end             TEXT,       -- ISO datetime
    participant_user_ids    TEXT,       -- JSON array of user ids
    meeting_confirmed_at    TEXT,       -- set once an admin confirms "yes, it happened"

    -- Hand-off / delivery capture — generic across Film/Record (hands the
    -- shoot off to the editor) and Audio Creation (hands the approved edit
    -- off to the musician): a real user account now, not free-text.
    delivery_recipient_user_id INTEGER REFERENCES users(id),
    delivery_link               TEXT,       -- optional extra link/note
    delivery_deadline           TEXT,       -- ISO date
    delivery_email_sent_at      TEXT,

    -- Review decisions (review_edit / review_audio only): approve/reject
    -- gate. A reject reopens the stage it reviews (see pipeline.py
    -- REOPENS_STAGE) rather than advancing.
    review_decision      TEXT,       -- 'approved' | 'rejected'
    review_notes         TEXT,
    reviewed_at          TEXT,

    -- Submission capture (compilation / highlights only): the assignee
    -- pastes a Drive link when their work is ready; an admin then clicks
    -- "Mark as received" to actually finish the stage, since the app can't
    -- detect a real file download. `submission_link` holds a single URL for
    -- compilation, or a JSON list of {label, url} for highlights.
    submission_link       TEXT,
    submission_notes      TEXT,
    submitted_at          TEXT,

    -- Highlights only: Jodie's captured candidate timestamp ranges, JSON
    -- list of {label, start, end, notes}.
    highlight_candidates  TEXT,

    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK ((campaign_id IS NOT NULL AND output_id IS NULL) OR (campaign_id IS NULL AND output_id IS NOT NULL)),
    UNIQUE (campaign_id, stage_key),
    UNIQUE (output_id, stage_key)
);

-- ---------------------------------------------------------------------------
-- Notes / Inspiration links / Assets — polymorphic onto a campaign
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS inspiration_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    url         TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS assets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id     INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    filename        TEXT NOT NULL,
    stored_path     TEXT NOT NULL,
    uploaded_by     INTEGER REFERENCES users(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS comments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id     INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    author_id       INTEGER REFERENCES users(id),
    body            TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Activity log — transparency for anything the scheduler changes automatically
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS activity_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id     INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
    actor_id        INTEGER REFERENCES users(id),   -- null = system/automation
    message         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Integration credentials — one row per connected external account
-- (currently just 'google', authorized once by an admin; used for
-- Calendar/Meet invites and Drive file storage). Never holds the
-- client_id/client_secret themselves — those stay in environment
-- variables — only the per-connection tokens this app was granted.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS integration_credentials (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider        TEXT NOT NULL UNIQUE,      -- 'google'
    account_email   TEXT,                      -- the Google account that authorized this
    access_token    TEXT,
    refresh_token   TEXT,
    token_expires_at TEXT,                     -- ISO datetime
    scope           TEXT NOT NULL DEFAULT '',
    connected_by    INTEGER REFERENCES users(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- App state — tiny generic key/value table for background-ish bookkeeping
-- that isn't tied to any one user or record. First use (Sept): remembering
-- when the recurring-schedule/filler-gap rolling horizon was last extended,
-- since this app has no scheduler/worker process (same reasoning as the
-- pipeline-confirmation check in app/auth.py) — see
-- scheduling.ensure_horizon_rolled_forward().
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_state (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_campaigns_publish_date ON campaigns(publish_date);
CREATE INDEX IF NOT EXISTS idx_outputs_campaign ON content_outputs(campaign_id);
CREATE INDEX IF NOT EXISTS idx_outputs_publish_date ON content_outputs(publish_date);
CREATE INDEX IF NOT EXISTS idx_tasks_campaign ON tasks(campaign_id);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
