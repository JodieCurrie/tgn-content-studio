"""
Canva design generation for content ideas (Sept, per Jodie).

The idea bank's 5 post types that have a real, consistent visual shape —
Normal/Static Post, Carousel, Moving Scripture, Quick Reel, Scripture
Expansion — can get a Canva design generated straight from an idea: Jodie
tags the type, writes the body copy, optionally attaches a few reference
images purely as style/mood inspiration (she was explicit these should
never be inserted into the design itself — a fresh design is built that
just *feels* similar), and once both a type and body are present the idea
is marked canva_status='pending'.

There's deliberately no Canva API call inside this Flask app: this app has
its own Canva developer integration to build (real work: registering an
app with Canva, OAuth, and proper Brand Templates with fillable fields per
post type) it doesn't have yet. Instead, Claude picks up pending ideas
itself on a short recurring check (a scheduled task, not code that runs
here) using its own already-connected Canva account, and writes the result
back through the ordinary PATCH /api/ideas/<id> endpoint — same path any
other edit takes. That's "near-instant" (within the check interval)
rather than the moment Jodie hits submit, which is the trade-off she chose
over building the heavier direct integration.

canva_status values:
  none    - not eligible yet (wrong/no type, or no body_content)
  pending - eligible, waiting to be picked up
  ready   - canva_design_link is set
  failed  - a generation attempt gave up (canva_generated_at notes why,
            via the idea's own notes field — there's no separate error
            column; keep this simple until it's needed)
"""
import os
import uuid
from datetime import datetime, timezone

from werkzeug.utils import secure_filename

from . import db

CANVA_DESIGN_TYPE_KEYS = {
    "normal_post", "carousel", "moving_scripture", "quick_reel", "scripture_expansion",
}


def is_canva_eligible_type(type_key):
    return type_key in CANVA_DESIGN_TYPE_KEYS


def next_canva_status(current_status, type_key, body_content, has_link):
    """Whenever an idea is saved (created or edited), work out whether it
    should newly become 'pending'. Never downgrades a 'ready' (or 'failed')
    idea back to 'pending' just because it was edited afterwards —
    regenerating a design once one exists (or once one attempt already
    failed) is a deliberate future action, not something implicit edits
    should trigger on their own."""
    if has_link or current_status in ("ready", "failed"):
        return current_status
    if is_canva_eligible_type(type_key) and (body_content or "").strip():
        return "pending"
    return "none"


def reference_images_for_ideas(idea_ids):
    """{idea_id: [{"id", "filename", "url"}, ...]} for every id in idea_ids,
    one query regardless of how many ideas — used by the Ideas tab list so
    it doesn't run a query per row."""
    idea_ids = [i for i in idea_ids if i]
    if not idea_ids:
        return {}
    placeholders = ",".join("?" for _ in idea_ids)
    rows = db.rows_to_list(db.query(
        f"""SELECT * FROM idea_reference_images WHERE idea_id IN ({placeholders})
            ORDER BY created_at""",
        tuple(idea_ids),
    ))
    by_idea = {}
    for r in rows:
        by_idea.setdefault(r["idea_id"], []).append({
            "id": r["id"], "filename": r["filename"], "url": f"/uploads/{r['stored_path']}",
        })
    return by_idea


def save_reference_image(idea_id, file_storage, uploaded_by, upload_folder):
    filename = secure_filename(file_storage.filename)
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    dest = os.path.join(upload_folder, unique_name)
    file_storage.save(dest)
    image_id = db.execute(
        "INSERT INTO idea_reference_images (idea_id, filename, stored_path, uploaded_by) VALUES (?,?,?,?)",
        (idea_id, filename, unique_name, uploaded_by),
    )
    return {"id": image_id, "filename": filename, "url": f"/uploads/{unique_name}"}


def delete_reference_image(image_id):
    existing = db.query_one("SELECT id FROM idea_reference_images WHERE id = ?", (image_id,))
    if not existing:
        return False
    db.execute("DELETE FROM idea_reference_images WHERE id = ?", (image_id,))
    return True


def mark_ready(idea_id, design_link):
    """What the recurring Canva-generation check calls (via PATCH
    /api/ideas/<id>, not this function directly — see the route) once it's
    built a design for a pending idea."""
    db.execute(
        "UPDATE content_ideas SET canva_status = 'ready', canva_design_link = ?, canva_generated_at = ? WHERE id = ?",
        (design_link, datetime.now(timezone.utc).isoformat(timespec="seconds"), idea_id),
    )
