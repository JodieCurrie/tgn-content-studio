"""
Automatic task generation (Part 20): every content type carries a set of
configurable TaskTemplates ("Videographer: Film, Edit, Export, Submit").
When a campaign (and its outputs) is created, matching templates fan out
into real Task rows with due dates computed from the publish date and each
template's lead time — separate from the publish date itself (Part 21).
"""
from datetime import date, timedelta

from . import db
from . import pipeline


def generate_tasks_for_campaign(campaign_id, only_new_output_type=None):
    campaign = db.row_to_dict(db.query_one("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)))
    if not campaign:
        return

    outputs = db.rows_to_list(
        db.query("SELECT * FROM content_outputs WHERE campaign_id = ?", (campaign_id,))
    )
    if only_new_output_type is not None:
        outputs = [o for o in outputs if o["content_type_id"] == only_new_output_type]

    default_assignees = _default_assignees_by_role()
    targeted_short_row = db.query_one("SELECT id FROM content_types WHERE key = 'targeted_short'")
    targeted_short_id = targeted_short_row["id"] if targeted_short_row else None

    for output in outputs:
        # Targeted Video — Short/YouTube get their two-tier production
        # pipeline set up alongside their tasks (Part 14-18+); every other
        # content type is a no-op here.
        pipeline.ensure_pipeline_for_output(output["id"])

        # An opt-in pipeline type (Monthly's podcast_episode/testimony/
        # course, or Filler's 8 video/design subtypes, Part 21/23) keeps its
        # plain flat "Create X" checklist by default — its staged-pipeline
        # templates only get created once that ONE output has opted in via
        # pipeline.start_output_pipeline() (now triggered by reassigning the
        # flat task, see pipeline.after_task_reassignment), which is what
        # actually creates this output's pipeline_stages rows. Until then,
        # skip them here so they don't appear alongside the flat list.
        output_pipeline_started = bool(
            db.query_one("SELECT id FROM pipeline_stages WHERE output_id = ? LIMIT 1", (output["id"],))
        )

        templates = db.rows_to_list(
            db.query(
                "SELECT * FROM task_templates WHERE content_type_id = ? ORDER BY sort_order",
                (output["content_type_id"],),
            )
        )
        # SHOOT_STAGE_KEYS ("script", "concept", "film") is only a *shared,
        # campaign-level* tier for the always-on Targeted Video pipeline
        # (targeted_short/targeted_long) — a Monthly pipeline output reuses
        # the "film" key too, but as its own per-output stage (Monthly has
        # no shoot tier to share, see pipeline.py PRODUCTION_STAGES_BY_TYPE).
        # Only treat stage_key membership in SHOOT_STAGE_KEYS as "shared"
        # for genuinely Targeted-pipeline outputs, or Monthly's own "film"
        # template/task gets silently skipped/misfiled as campaign-scoped.
        is_targeted_pipeline_output = pipeline.is_pipeline_content_type_id(output["content_type_id"])

        for tpl in templates:
            # Shoot-level stages (script/concept/film) are shared once per
            # campaign — they only ever live on targeted_short's templates
            # (see seed.py PIPELINE_SHOOT_TEMPLATES), and a targeted_long
            # sibling must NOT get its own duplicate copies of them.
            if (
                is_targeted_pipeline_output
                and tpl["stage_key"] in pipeline.SHOOT_STAGE_KEYS
                and output["content_type_id"] != targeted_short_id
            ):
                continue

            if pipeline.is_opt_in_pipeline_eligible(output["content_type_id"]):
                if output_pipeline_started:
                    # The staged pipeline has taken over for this output —
                    # never regenerate its old flat (stage_key IS NULL)
                    # checklist templates, or start_output_pipeline()'s
                    # one-time cleanup of untouched flat tasks would just be
                    # immediately undone by this very call.
                    if not tpl["stage_key"]:
                        continue
                    # Some stages are added dynamically well after the
                    # pipeline starts (Filler-video's optional Audio/Review
                    # Audio, only inserted by pipeline.decide_filler_audio
                    # once Jodie says yes) — only materialize a staged
                    # template's task once ITS OWN pipeline_stages row
                    # actually exists, or a task would appear (and get
                    # auto-assigned) for a stage nobody has activated yet.
                    if not db.query_one(
                        "SELECT id FROM pipeline_stages WHERE output_id = ? AND stage_key = ?",
                        (output["id"], tpl["stage_key"]),
                    ):
                        continue
                else:
                    # Not opted in yet — keep showing the flat checklist,
                    # not the staged templates.
                    if tpl["stage_key"]:
                        continue

            # Scope the dedup check by stage_key, not just task_name. Without
            # this, a leftover pre-pipeline task (stage_key IS NULL) that's
            # already in_progress/complete — and so never gets cleaned up —
            # can share a name with a new pipeline-stage template (e.g. both
            # eras call a task "Film / record"). A bare name match would then
            # skip creating the real stage-tagged task forever, leaving that
            # stage permanently empty and locking the whole pipeline behind
            # it. Matching on (task_name, stage_key) together keeps a legacy
            # untagged task and a new staged task as distinct tasks. Shared
            # shoot-stage tasks are also deduped campaign-wide (output_id IS
            # NULL), since only one copy should ever exist per campaign.
            is_shared_shoot_tpl = is_targeted_pipeline_output and tpl["stage_key"] in pipeline.SHOOT_STAGE_KEYS
            if is_shared_shoot_tpl:
                exists = db.query_one(
                    "SELECT id FROM tasks WHERE campaign_id = ? AND output_id IS NULL AND task_name = ? AND stage_key = ?",
                    (campaign_id, tpl["task_name"], tpl["stage_key"]),
                )
            elif tpl["stage_key"]:
                exists = db.query_one(
                    "SELECT id FROM tasks WHERE output_id = ? AND task_name = ? AND stage_key = ?",
                    (output["id"], tpl["task_name"], tpl["stage_key"]),
                )
            else:
                exists = db.query_one(
                    "SELECT id FROM tasks WHERE output_id = ? AND task_name = ? AND stage_key IS NULL",
                    (output["id"], tpl["task_name"]),
                )
            if exists:
                continue
            publish_date = date.fromisoformat(output["publish_date"])
            due = (publish_date - timedelta(days=tpl["offset_days_before"])).isoformat()
            task_output_id = None if is_shared_shoot_tpl else output["id"]
            db.execute(
                """INSERT INTO tasks (campaign_id, output_id, task_name, role_key, assigned_user_id,
                                       due_date, status, created_from_template, stage_key)
                   VALUES (?, ?, ?, ?, ?, ?, 'not_started', 1, ?)""",
                (
                    campaign_id,
                    task_output_id,
                    tpl["task_name"],
                    tpl["role_key"],
                    default_assignees.get(tpl["role_key"]),
                    due,
                    tpl["stage_key"],
                ),
            )


def _default_assignees_by_role():
    """If exactly one active user holds a role, auto-assign new tasks to
    them; otherwise leave unassigned so a human picks."""
    rows = db.query(
        """SELECT r.key AS role_key, u.id AS user_id, COUNT(*) OVER (PARTITION BY r.key) AS cnt
           FROM users u JOIN roles r ON r.id = u.role_id WHERE u.active = 1"""
    )
    result = {}
    seen_multi = set()
    for r in rows:
        if r["cnt"] == 1:
            result[r["role_key"]] = r["user_id"]
        else:
            seen_multi.add(r["role_key"])
    for k in seen_multi:
        result.pop(k, None)
    return result


def regenerate_tasks_for_output(output_id):
    output = db.row_to_dict(db.query_one("SELECT * FROM content_outputs WHERE id = ?", (output_id,)))
    if output:
        generate_tasks_for_campaign(output["campaign_id"], only_new_output_type=output["content_type_id"])
