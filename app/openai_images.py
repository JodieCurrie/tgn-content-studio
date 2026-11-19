"""
Optional in-app design + cover-image generation with OpenAI's image API
(Nov 2026, per Jodie: she prefers ChatGPT-style designs to Canva's).

Important: a ChatGPT *subscription* does not include API access — this
needs an API key from platform.openai.com with its own billing, saved in
Render as OPENAI_API_KEY (OPENAI_IMAGE_MODEL overrides the model name,
default gpt-image-1). Without a key nothing here runs and the app simply
offers "upload your own design" instead.

Generation takes up to a minute, so it runs on a background thread; the
result is uploaded to the shared Drive folder and recorded on the idea /
output, then (for ideas) the admins get the usual "design ready" push.
"""
import base64
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request

from flask import current_app

from . import db
from . import google_integration as google

API_URL = "https://api.openai.com/v1/images/generations"


def configured():
    return bool(os.environ.get("OPENAI_API_KEY"))


def _model():
    return os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")


def generate_png(prompt, size="1024x1024"):
    """Returns PNG bytes. Raises RuntimeError with a readable message."""
    body = json.dumps({"model": _model(), "prompt": prompt, "size": size, "n": 1}).encode()
    req = urllib.request.Request(
        API_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=170) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            pass
        raise RuntimeError(f"OpenAI said {e.code}: {detail or 'request failed'}")
    except Exception as e:  # network/timeouts
        raise RuntimeError(f"Couldn't reach OpenAI: {e}")
    item = (data.get("data") or [{}])[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=60) as r:
            return r.read()
    raise RuntimeError("OpenAI returned no image.")


def _upload_png(png_bytes, name):
    from . import posting as posting_module
    folder_id = google.find_or_create_named_folder(posting_module.FINAL_FOLDER_NAME, posting_module.FINAL_FOLDER_STATE_KEY)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
        tmp.write(png_bytes)
        path = tmp.name
    try:
        return google.upload_file_to_drive(path, name, "image/png", folder_id=folder_id)["id"]
    finally:
        os.unlink(path)


def design_prompt(idea, type_label, feedback=""):
    body = (idea.get("body_content") or "").strip()
    notes = (idea.get("notes") or "").strip()
    parts = [
        f"Design a clean, modern, faith-based social media graphic for a ministry called “That's Good News”. Format: {type_label}.",
        "Warm, hopeful, uncluttered, strong typographic hierarchy, plenty of breathing room. No logos or watermarks, no extra words beyond the copy given.",
        f"Title: {idea['title']}",
    ]
    if body:
        parts.append(f"Use exactly this text on the design (spell it exactly, add nothing else):\n{body}")
    if notes:
        parts.append(f"Notes on the idea: {notes}")
    if feedback:
        parts.append(f"Changes requested on the previous version — apply these: {feedback}")
    return "\n\n".join(parts)


def generate_design_async(idea_id, feedback=""):
    """Kicks off generation for an idea; returns immediately."""
    app = current_app._get_current_object()
    db.execute("UPDATE content_ideas SET design_gen_status = 'generating', design_gen_error = NULL WHERE id = ?", (idea_id,))

    def work():
        with app.app_context():
            try:
                idea = db.row_to_dict(db.query_one("SELECT * FROM content_ideas WHERE id = ?", (idea_id,)))
                ct = db.row_to_dict(db.query_one("SELECT label FROM content_types WHERE id = ?", (idea.get("content_type_id"),))) or {}
                png = generate_png(design_prompt(idea, ct.get("label") or "post", feedback), "1024x1024")
                drive_id = _upload_png(png, f"design-idea-{idea_id}.png")
                db.execute(
                    """UPDATE content_ideas SET design_drive_id = ?, design_mime = 'image/png', design_gen_status = 'none',
                           design_gen_error = NULL, design_approval = 'pending', design_feedback = NULL WHERE id = ?""",
                    (drive_id, idea_id),
                )
                from . import posting as posting_module
                posting_module.notify_design_ready(idea)
            except Exception as e:  # keep the thread from dying silently
                db.execute(
                    "UPDATE content_ideas SET design_gen_status = 'failed', design_gen_error = ? WHERE id = ?",
                    (str(e)[:300], idea_id),
                )

    threading.Thread(target=work, daemon=True).start()


def generate_cover_async(output_id, note=""):
    app = current_app._get_current_object()

    def work():
        with app.app_context():
            try:
                out = db.row_to_dict(db.query_one(
                    """SELECT o.id, o.title, c.title AS campaign_title, c.concept FROM content_outputs o
                       JOIN campaigns c ON c.id = o.campaign_id WHERE o.id = ?""", (output_id,)))
                title = out["title"] or out["campaign_title"]
                prompt = (
                    "A bold, clean YouTube thumbnail / cover image for a faith-based video by “That's Good News”. "
                    f"Video title: {title}. Warm, hopeful colours, large readable title text exactly as given, "
                    "no other words, no logos."
                )
                if note:
                    prompt += f" Extra direction: {note}"
                png = generate_png(prompt, "1536x1024")
                drive_id = _upload_png(png, f"cover-{output_id}.png")
                db.execute("UPDATE content_outputs SET cover_drive_id = ? WHERE id = ?", (drive_id, output_id))
            except Exception:
                pass

    threading.Thread(target=work, daemon=True).start()
