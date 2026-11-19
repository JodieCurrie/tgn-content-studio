"""
Final-video posting queue (Nov 2026, per Jodie).

The loop this module supports:

  1. Jodie (or the team) uploads the FINISHED video/image for a scheduled
     post from the Posting page — straight from a phone if she likes — and
     says which calendar post it is. The file goes to Google Drive (see
     routes/posting.py and google_integration.start_resumable_upload).
  2. Uploads by an admin (Jodie) are approved automatically — she made it
     herself. Uploads by anyone else wait for an admin's OK, and an admin
     push goes out. Designs follow the same rule (see attach_design).
  3. At the post's slot (12:16pm for a day's first post, 8:14pm for its
     second — see slot_time_for) the push-reminder sweep
     (push.send_post_reminders) says "time to post" (or, if something's
     missing, offers Delay 10 min / Reschedule). One tap shares the video +
     caption into Instagram/TikTok/etc. via the phone's own share sheet — no
     Instagram/TikTok API connection exists, and TikTok won't let an
     unaudited app publish — and the post is registered as posted when the
     share sheet completes (with an Undo).
  4. A post that wasn't approved/posted once its day has passed is silently
     moved — no notification (reschedule_missed): filler posts swap into the
     next blank filler slot; a Targeted/Monthly campaign and its highlight
     pair move to the same weekday the following week with the whole
     calendar re-flowing around them (scheduling.apply_drag "shift_rule").

Nothing here talks to Instagram/TikTok/Facebook/YouTube directly.
"""
from datetime import date, datetime, timedelta

from . import db
from . import content as content_module
from . import scheduling

# Platforms a post actually goes out on today. Website (blog) and podcast
# outputs are deliberately left out for now (Jodie, Nov 2026: the website
# and podcast aren't built yet) — they stay on the calendar but never show
# up in the posting queue or trigger reminders.
POSTABLE_PLATFORM_KEYS = ("instagram", "tiktok", "facebook", "youtube", "youtube_shorts", "threads")

SLOT_FIRST_POST = (12, 16)   # the day's first Instagram/TikTok post
SLOT_SECOND_POST = (20, 14)  # the day's second
SLOT_EXTRA_POST = (16, 30)   # a third+ post on a busy day
DELAY_MINUTES = 10

FINAL_FOLDER_NAME = "TGN Final Videos"
FINAL_FOLDER_STATE_KEY = "final_videos_folder_id"
LIVE_FROM_STATE_KEY = "posting_queue_live_from"

IMAGE_MIME_PREFIX = "image/"

FORMAT_LABELS = {
    "reel": "Reel / short video",
    "post": "Photo post",
    "carousel": "Carousel",
    "story": "Story",
    "youtube": "YouTube video",
}
# How each format shows up on each platform — shown on the review page so
# she knows what she's posting it as when the share sheet opens.
FORMAT_HINTS = {
    "reel": {
        "instagram": "Instagram → choose “Reel”",
        "tiktok": "TikTok → regular video post",
        "facebook": "Facebook → choose “Reel”",
        "youtube_shorts": "YouTube → a Short (vertical, under 3 min)",
        "youtube": "YouTube → upload as a regular video",
        "threads": "Threads → attach as a video",
    },
    "post": {
        "instagram": "Instagram → choose “Post”",
        "tiktok": "TikTok → photo post",
        "facebook": "Facebook → photo post",
        "youtube_shorts": "YouTube → not used for photos",
        "youtube": "YouTube → community post",
        "threads": "Threads → attach as a photo",
    },
    "carousel": {
        "instagram": "Instagram → “Post”, add all the slides",
        "tiktok": "TikTok → photo post with several images",
        "facebook": "Facebook → multi-photo post",
        "youtube_shorts": "YouTube → not used for carousels",
        "youtube": "YouTube → community post",
        "threads": "Threads → attach all the slides",
    },
    "story": {
        "instagram": "Instagram → choose “Story” (disappears after 24h)",
        "tiktok": "TikTok → choose “Story”",
        "facebook": "Facebook → choose “Story”",
        "youtube_shorts": "YouTube → a Short",
        "youtube": "YouTube → regular video",
        "threads": "Threads → regular post",
    },
    "youtube": {
        "instagram": "Instagram → choose “Reel” (or trim to a clip)",
        "tiktok": "TikTok → regular video post",
        "facebook": "Facebook → regular video post",
        "youtube_shorts": "YouTube → a Short",
        "youtube": "YouTube → upload as a regular video (needs the cover image)",
        "threads": "Threads → attach as a video",
    },
}
_LONG_FORM_TYPE_KEYS = ("targeted_long", "targeted_full_repost", "course", "bible_study", "testimony", "podcast_episode")


# ---------------------------------------------------------------- reading
def _platform_keys_by_output(output_ids):
    if not output_ids:
        return {}
    placeholders = ",".join("?" * len(output_ids))
    rows = db.rows_to_list(db.query(
        f"""SELECT op.output_id, p.key FROM output_platforms op
            JOIN platforms p ON p.id = op.platform_id
            WHERE op.output_id IN ({placeholders})""",
        tuple(output_ids),
    ))
    out = {}
    for r in rows:
        out.setdefault(r["output_id"], []).append(r["key"])
    return out


def _is_postable(platform_keys):
    return any(k in POSTABLE_PLATFORM_KEYS for k in platform_keys)


def _is_counted(platform_keys):
    return any(k in content_module.COUNTED_PLATFORM_KEYS for k in platform_keys)


def state_of(output):
    """posted > approved > awaiting (uploaded, needs her OK) > needs_video."""
    if output.get("posted_at"):
        return "posted"
    if output.get("approval_status") == "approved":
        return "approved"
    if output.get("final_media_drive_id"):
        return "awaiting"
    return "needs_video"


STATE_LABELS = {
    "needs_video": "Needs final video",
    "awaiting": "Waiting for your approval",
    "approved": "Approved — ready to post",
    "posted": "Posted",
}


def list_outputs(start, end, only_unposted=False):
    """Every postable (non-custom, Instagram/TikTok/Facebook/YouTube…)
    output with a publish_date in [start, end], richest-first for the queue
    page — each row gets platform_keys, state and state_label."""
    rows = db.rows_to_list(db.query(
        """SELECT o.*, c.title AS campaign_title, c.source_idea_id AS source_idea_id,
                  c.depends_on_campaign_id AS depends_on_campaign_id,
                  ct.label AS type_label, ct.color AS type_color,
                  ct.key AS type_key, ct.category_key AS category_key, u.name AS assigned_name
           FROM content_outputs o
           JOIN campaigns c ON c.id = o.campaign_id
           JOIN content_types ct ON ct.id = o.content_type_id
           LEFT JOIN users u ON u.id = o.assigned_user_id
           WHERE o.publish_date BETWEEN ? AND ? AND ct.category_key != 'custom'
           ORDER BY o.publish_date, o.sort_order, o.id""",
        (start.isoformat(), end.isoformat()),
    ))
    platforms = _platform_keys_by_output([r["id"] for r in rows])
    blank_titles = _blank_titles()
    result = []
    for r in rows:
        keys = platforms.get(r["id"], [])
        if not _is_postable(keys):
            continue
        r["platform_keys"] = keys
        r["state"] = state_of(r)
        r["state_label"] = STATE_LABELS[r["state"]]
        r["is_blank"] = _is_blank(r, blank_titles)
        if r["is_blank"] and r["state"] == "needs_video":
            r["state_label"] = "Empty slot — needs an idea"
        r["format"] = format_for(r)
        r["format_label"] = FORMAT_LABELS[r["format"]]
        r["display_title"] = r.get("title") or r["campaign_title"]
        if only_unposted and r["state"] == "posted":
            continue
        result.append(r)
    return result


def get_output(output_id):
    row = db.row_to_dict(db.query_one(
        """SELECT o.*, c.title AS campaign_title, c.source_idea_id AS source_idea_id,
                  c.depends_on_campaign_id AS depends_on_campaign_id,
                  ct.label AS type_label, ct.color AS type_color,
                  ct.key AS type_key, ct.category_key AS category_key
           FROM content_outputs o
           JOIN campaigns c ON c.id = o.campaign_id
           JOIN content_types ct ON ct.id = o.content_type_id
           WHERE o.id = ?""",
        (output_id,),
    ))
    if not row:
        return None
    row["platform_keys"] = _platform_keys_by_output([output_id]).get(output_id, [])
    row["state"] = state_of(row)
    row["state_label"] = STATE_LABELS[row["state"]]
    row["is_blank"] = _is_blank(row, _blank_titles())
    if row["is_blank"] and row["state"] == "needs_video":
        row["state_label"] = "Empty slot — needs an idea"
    row["format"] = format_for(row)
    row["format_label"] = FORMAT_LABELS[row["format"]]
    row["display_title"] = row.get("title") or row["campaign_title"]
    return row


def uploadable_outputs(today=None):
    """The "What is this?" dropdown on the upload form: unposted postable
    outputs from a few days back through the next three weeks, soonest
    first, those still missing a final video flagged so they sort to the
    top of the list."""
    today = today or date.today()
    rows = list_outputs(today - timedelta(days=3), today + timedelta(days=21), only_unposted=True)
    rows.sort(key=lambda r: (r["state"] != "needs_video", r["publish_date"], r["sort_order"], r["id"]))
    return rows


def _blank_titles():
    """Titles a still-untouched placeholder carries — a content type's own
    label (a filler day with no idea yet) or a scheduling rule's default
    title ('New Targeted Campaign' …). Mirrors content._soonest_blank_filler_slot."""
    titles = {r["label"] for r in db.query("SELECT label FROM content_types")}
    titles |= {r["default_title"] for r in db.query("SELECT default_title FROM scheduling_rules") if r["default_title"]}
    return titles


def _is_blank(output, blank_titles):
    """True for a slot nobody has put an idea/content into yet."""
    return (
        not output.get("source_idea_id")
        and not output.get("final_media_drive_id")
        and (output.get("campaign_title") or "") in blank_titles
    )


def format_for(output):
    """reel / post / carousel / story / youtube — her saved choice if any,
    else a sensible default from the content type and the uploaded file."""
    saved = (output.get("post_format") or "").strip()
    if saved in FORMAT_LABELS:
        return saved
    key = output.get("type_key") or ""
    if key == "carousel":
        return "carousel"
    if key == "normal_post" or (output.get("final_media_mime") or "").startswith(IMAGE_MIME_PREFIX):
        return "post"
    if key in _LONG_FORM_TYPE_KEYS:
        return "youtube"
    return "reel"


def format_hints(output, platform_keys=None):
    keys = platform_keys if platform_keys is not None else output.get("platform_keys", [])
    table = FORMAT_HINTS.get(format_for(output), FORMAT_HINTS["reel"])
    return [table[k] for k in keys if k in table]


def suggest_caption(output):
    """A starting caption she can edit. Uses, in order: the caption already
    written on the campaign, the idea's body copy, the concept or title —
    then scripture references and a closing line + hashtags."""
    c = db.row_to_dict(db.query_one(
        "SELECT title, caption, concept, bible_references, source_idea_id FROM campaigns WHERE id = ?",
        (output["campaign_id"],),
    )) or {}
    idea = None
    if c.get("source_idea_id"):
        idea = db.row_to_dict(db.query_one(
            "SELECT title, body_content, notes FROM content_ideas WHERE id = ?", (c["source_idea_id"],)
        ))
    title = (c.get("title") or output.get("campaign_title") or "").strip()
    if (c.get("caption") or "").strip():
        return c["caption"].strip()
    body = ((idea or {}).get("body_content") or "").strip() or (c.get("concept") or "").strip()
    parts = []
    if body:
        parts.append(body)
    elif title and title not in _blank_titles():
        parts.append(title)
    refs = (c.get("bible_references") or "").strip()
    if refs:
        parts.append(f"📖 {refs}")
    parts.append("That's Good News — follow along and share this with someone who needs it today.")
    tags = ["#ThatsGoodNews", "#Faith", "#Jesus"]
    if refs or (output.get("type_key") or "").startswith(("moving_scripture", "scripture")):
        tags += ["#Scripture", "#BibleVerse"]
    parts.append(" ".join(tags))
    return "\n\n".join(parts)


# ------------------------------------------------------------------ slots
def slot_time_for(output):
    """(hour, minute) this output should go out at. A day's first counted
    (Instagram/TikTok) post → 12:16pm, its second → 8:14pm, a third+ →
    4:30pm. A long-form/YouTube-only post isn't part of the two-a-day rhythm,
    so it just takes its platform's own best-time window."""
    from . import best_time as best_time_module
    keys = output.get("platform_keys")
    if keys is None:
        keys = _platform_keys_by_output([output["id"]]).get(output["id"], [])
    if not _is_counted(keys):
        hour, minute, _ = best_time_module.best_time_candidates_for_platforms(keys)[0]
        return hour, minute
    same_day = db.rows_to_list(db.query(
        """SELECT DISTINCT o.id FROM content_outputs o
           JOIN output_platforms op ON op.output_id = o.id
           JOIN platforms p ON p.id = op.platform_id
           WHERE o.publish_date = ? AND p.key IN ({})
           ORDER BY o.sort_order, o.id""".format(",".join("?" * len(content_module.COUNTED_PLATFORM_KEYS))),
        (output["publish_date"], *content_module.COUNTED_PLATFORM_KEYS),
    ))
    ids = [r["id"] for r in same_day]
    idx = ids.index(output["id"]) if output["id"] in ids else 0
    if idx == 0:
        return SLOT_FIRST_POST
    if idx == 1:
        return SLOT_SECOND_POST
    return SLOT_EXTRA_POST


# --------------------------------------------------------------- actions
def local_today():
    """Today's date in the app's local timezone (South Africa)."""
    from . import push as push_module
    return date.fromisoformat(push_module.local_today_iso())


def attach_final_media(output_id, drive_id, name, mime, caption=None, approve_now=False, post_format=None):
    """Stores the uploaded file on the output. approve_now=True is for
    uploads by an admin (her own work needs no second sign-off); anyone
    else's upload waits as 'awaiting'. A caption is always filled in — her
    own if she wrote one, else a suggested one she can edit."""
    db.execute(
        """UPDATE content_outputs SET final_media_drive_id = ?, final_media_name = ?, final_media_mime = ?,
               final_uploaded_at = datetime('now'), approval_status = ?, approved_at = ?
           WHERE id = ?""",
        (drive_id, name, mime, "approved" if approve_now else "awaiting",
         datetime.utcnow().isoformat(timespec="seconds") if approve_now else None, output_id),
    )
    if post_format in FORMAT_LABELS:
        db.execute("UPDATE content_outputs SET post_format = ? WHERE id = ?", (post_format, output_id))
    if caption is None or not caption.strip():
        existing = db.query_one("SELECT post_caption FROM content_outputs WHERE id = ?", (output_id,))
        if not (existing and (existing["post_caption"] or "").strip()):
            out = get_output(output_id)
            caption = suggest_caption(out) if out else ""
        else:
            caption = existing["post_caption"]
    db.execute("UPDATE content_outputs SET post_caption = ? WHERE id = ?", (caption, output_id))


def notify_awaiting_approval(output_id, uploader_name=None):
    """Someone other than an admin submitted a final video — tell the admins."""
    from . import push as push_module
    out = get_output(output_id)
    if not out:
        return
    who = uploader_name or "Someone"
    push_module.send_to_users(
        push_module._admin_user_ids(),
        f"{who} submitted a final video",
        f"“{out['display_title']}” is waiting for your approval before it can post.",
        url=f"/post/{output_id}", tag=f"tgn-approve-{output_id}",
    )


def approve(output_id, caption=None, platform_ids=None):
    if caption is not None:
        db.execute("UPDATE content_outputs SET post_caption = ? WHERE id = ?", (caption, output_id))
    if platform_ids is not None:
        db.execute("DELETE FROM output_platforms WHERE output_id = ?", (output_id,))
        for pid in platform_ids:
            db.execute("INSERT INTO output_platforms (output_id, platform_id) VALUES (?, ?)", (output_id, pid))
    db.execute(
        "UPDATE content_outputs SET approval_status = 'approved', approved_at = datetime('now') WHERE id = ?",
        (output_id,),
    )


def mark_posted(output_id):
    """It went out. Also flips the output's calendar status to 'published'
    (bypassing pipeline.mark_output_published's "everything else must
    already be done" check — a post that's actually out is out)."""
    from . import pipeline
    output = db.row_to_dict(db.query_one("SELECT campaign_id FROM content_outputs WHERE id = ?", (output_id,)))
    if not output:
        raise ValueError("No such post.")
    db.execute(
        "UPDATE content_outputs SET posted_at = datetime('now'), approval_status = 'approved', status = 'published' WHERE id = ?",
        (output_id,),
    )
    pipeline.sync_campaign_status(output["campaign_id"])


def unmark_posted(output_id):
    db.execute("UPDATE content_outputs SET posted_at = NULL, status = 'scheduled' WHERE id = ?", (output_id,))


# ------------------------------------------------------------- reschedule
def _live_from(today):
    """The day the queue went live. Posts dated before it are never
    auto-moved — nothing before the queue existed was tracked as posted or
    not, so treating all of that history as 'missed' would shuffle months
    of already-published content around."""
    row = db.query_one("SELECT value FROM app_state WHERE key = ?", (LIVE_FROM_STATE_KEY,))
    if row:
        return date.fromisoformat(row["value"])
    db.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, datetime('now'))",
        (LIVE_FROM_STATE_KEY, today.isoformat()),
    )
    return today


def missed_outputs(today=None):
    """Postable outputs whose day has passed (since the queue went live)
    with nothing approved and nothing posted — and that actually hold a
    post (an untouched empty placeholder has nothing to move). Approved-but-
    never-confirmed posts are NOT here: the video exists and may well have
    gone out by hand, so those wait under 'Did these go out?'."""
    today = today or local_today()
    live_from = _live_from(today)
    if live_from >= today:
        return []
    rows = list_outputs(live_from, today - timedelta(days=1), only_unposted=True)
    return [
        r for r in rows
        if r["state"] in ("needs_video", "awaiting") and r["status"] != "published" and not r["is_blank"]
    ]


def _next_free_day(today, counts, after_days=0):
    d = today + timedelta(days=after_days)
    for _ in range(60):
        if content_module._is_posting_weekday(d) and counts.get(d.isoformat(), 0) < content_module.POSTS_PER_DAY_TARGET:
            return d
        d += timedelta(days=1)
    return None


def _root_campaign_id(campaign_id):
    seen = set()
    cid = campaign_id
    while cid not in seen:
        seen.add(cid)
        row = db.query_one("SELECT depends_on_campaign_id FROM campaigns WHERE id = ?", (cid,))
        if not row or not row["depends_on_campaign_id"]:
            return cid
        cid = row["depends_on_campaign_id"]
    return cid


def _following_week_date(current_iso, today):
    """Same weekday one week on: current date + 7 if it's still ahead,
    otherwise the first matching weekday after today."""
    cur = date.fromisoformat(current_iso)
    if cur > today:
        return cur + timedelta(days=7)
    delta = (cur.weekday() - today.weekday()) % 7 or 7
    return today + timedelta(days=delta)


def reschedule_output(output_id, today=None, actor_id=None):
    """Moves one post that isn't going out on its day.

    * Filler: it swaps into the next blank filler slot (the empty
      placeholder is removed — that slot is now held by this post, and the
      empty one it replaces was the one still needing an idea).
    * Targeted / Monthly: the campaign and its highlight/repost pair move to
      the same weekday the following week, and the rest of the calendar
      re-flows around it (rule re-anchored, filler rebalanced to keep the
      two-a-day minimum, Sundays excluded).
    Returns {"ok", "new_date", "message"}."""
    today = today or local_today()
    o = get_output(output_id)
    if not o:
        return {"ok": False, "message": "Couldn't find that post."}
    if o.get("posted_at"):
        return {"ok": False, "message": "That one's already posted."}
    old_date = o["publish_date"]
    campaign_id = o["campaign_id"]
    new_date = None

    if o["category_key"] == "filler":
        # Strictly later than the day it was due (a post still ahead can't
        # "move" into another slot on its own day).
        after = old_date if old_date >= today.isoformat() else None
        slot = content_module._soonest_blank_filler_slot(after_iso=after)
        if slot and slot["id"] != campaign_id and slot["publish_date"] >= today.isoformat():
            new_date = date.fromisoformat(slot["publish_date"])
            content_module.delete_campaign(slot["id"])
        else:
            counts = content_module._output_counts_by_day(today, today + timedelta(days=75))
            new_date = _next_free_day(today, counts, after_days=0 if old_date < today.isoformat() else 1)
        if not new_date:
            return {"ok": False, "message": "No free day found in the next ten weeks."}
        scheduling.move_campaign_to_date(campaign_id, new_date.isoformat())
    else:
        root_id = _root_campaign_id(campaign_id)
        root = db.row_to_dict(db.query_one(
            "SELECT id, publish_date, scheduling_rule_id FROM campaigns WHERE id = ?", (root_id,)
        ))
        # A highlight whose parent already went out moves on its own; if the
        # parent is also unposted the whole pair moves together.
        parent_posted = False
        if root_id != campaign_id:
            parent_posted = bool(db.query_one(
                "SELECT 1 FROM content_outputs WHERE campaign_id = ? AND posted_at IS NOT NULL", (root_id,)
            ))
        move_id = campaign_id if (root_id == campaign_id or parent_posted) else root_id
        move_row = db.row_to_dict(db.query_one(
            "SELECT id, publish_date, scheduling_rule_id FROM campaigns WHERE id = ?", (move_id,)
        ))
        new_date = _following_week_date(move_row["publish_date"], today)
        mode = "shift_rule" if (move_id == root_id and move_row["scheduling_rule_id"]) else "with_dependents"
        scheduling.apply_drag(move_id, new_date.isoformat(), mode, actor_id=actor_id)
        content_module.backfill_unscheduled_ideas()
        if move_id != campaign_id:
            new_date = date.fromisoformat(db.query_one(
                "SELECT publish_date FROM campaigns WHERE id = ?", (campaign_id,))["publish_date"])

    # Whichever way it moved, make sure every posting day still has exactly
    # its two posts (Sundays excluded) — surplus filler slides into the days
    # the move vacated.
    content_module.even_out_daily_counts(today)
    db.execute(
        """UPDATE content_outputs SET reschedule_count = reschedule_count + 1,
               original_publish_date = COALESCE(original_publish_date, ?),
               reminder_sent_at = NULL, reminder_target_time = NULL WHERE id = ?""",
        (old_date, output_id),
    )
    db.execute(
        "INSERT INTO activity_log (campaign_id, actor_id, message) VALUES (?, ?, ?)",
        (campaign_id, actor_id, f"Not posted on {old_date} — rescheduled to {new_date.isoformat()}."),
    )
    return {
        "ok": True, "new_date": new_date.isoformat(), "from": old_date,
        "message": f"Moved to {new_date.strftime('%A %d %b').replace(' 0', ' ')}.",
    }


def reschedule_missed(today=None, notify=False):
    """Quietly moves every missed post (see missed_outputs) — no
    notification, by design. Idempotent, and cheap when nothing's missed.
    `notify` is accepted only for older callers and ignored."""
    today = today or local_today()
    moved = []
    seen_campaigns = set()
    for o in missed_outputs(today):
        if o["campaign_id"] in seen_campaigns:
            continue
        # An earlier move this pass (a pair shifting) may already have
        # carried this one into the future.
        fresh = db.query_one("SELECT publish_date FROM content_outputs WHERE id = ?", (o["id"],))
        if not fresh or fresh["publish_date"] >= today.isoformat():
            continue
        res = reschedule_output(o["id"], today=today)
        seen_campaigns.add(o["campaign_id"])
        if res.get("ok"):
            moved.append({"output_id": o["id"], "title": o["display_title"], "from": res["from"], "to": res["new_date"]})
    return {"moved": moved, "flagged": []}


# ------------------------------------------------------------------ designs
def notify_design_ready(idea):
    """A design was generated (not uploaded by an admin): wait for her review
    and open straight onto the scheduled design on the calendar."""
    from . import push as push_module
    db.execute("UPDATE content_ideas SET design_approval = 'pending' WHERE id = ?", (idea["id"],))
    row = db.row_to_dict(db.query_one("SELECT scheduled_campaign_id FROM content_ideas WHERE id = ?", (idea["id"],))) or {}
    url = f"/calendar?review={row['scheduled_campaign_id']}" if row.get("scheduled_campaign_id") else f"/ideas#idea-{idea['id']}"
    push_module.send_to_users(
        push_module._admin_user_ids(),
        "Design ready for your review",
        f"“{idea['title']}” — open it to approve or ask for changes.",
        url=url, tag=f"tgn-design-{idea['id']}",
    )


def attach_design(idea_id):
    """An approved design that exists as a file goes straight onto the
    Posting page as the final media of its scheduled post (ready to post —
    approving the design already was the approval). Returns the output id,
    or None when there's nothing to attach yet (no file, not scheduled, or
    the post already has its own final media)."""
    idea = db.row_to_dict(db.query_one("SELECT * FROM content_ideas WHERE id = ?", (idea_id,)))
    if not idea or idea.get("design_approval") != "approved" or not idea.get("design_drive_id"):
        return None
    if not idea.get("scheduled_campaign_id"):
        return None
    candidates = db.rows_to_list(db.query(
        "SELECT id, final_media_drive_id FROM content_outputs WHERE campaign_id = ? ORDER BY sort_order, id",
        (idea["scheduled_campaign_id"],),
    ))
    keys = _platform_keys_by_output([c["id"] for c in candidates])
    for c in candidates:
        if not _is_postable(keys.get(c["id"], [])):
            continue
        if c["final_media_drive_id"]:
            return c["id"] if c["final_media_drive_id"] == idea["design_drive_id"] else None
        mime = idea.get("design_mime") or "image/png"
        attach_final_media(c["id"], idea["design_drive_id"], f"design-{idea_id}", mime, approve_now=True)
        return c["id"]
    return None


def attach_approved_designs():
    """Safety net for designs approved before their post got a slot (or
    scheduled later): called whenever the queue loads and from the sweep."""
    ideas = db.rows_to_list(db.query(
        """SELECT id FROM content_ideas WHERE design_approval = 'approved' AND design_drive_id IS NOT NULL
           AND scheduled_campaign_id IS NOT NULL"""
    ))
    return [i["id"] for i in ideas if attach_design(i["id"])]


# --------------------------------------------------------------- reshuffle
_NON_POSTING_TYPE_PREFIXES = ("podcast", "blog", "course")  # website/podcast aren't built yet


def _campaign_rows_with_counts(date_from, date_to, assume_posted=()):
    """Every non-custom campaign with a publish_date in the range, with how
    many of its outputs count toward the two-a-day (Instagram/TikTok), how
    many are still unposted, and whether any output carries a final video."""
    rows = db.rows_to_list(db.query(
        """SELECT c.id, c.title, c.publish_date, c.depends_on_campaign_id AS dep, c.source_idea_id,
                  c.scheduling_rule_id, ct.key AS type_key, ct.label AS type_label, ct.category_key
           FROM campaigns c JOIN content_types ct ON ct.id = c.primary_content_type_id
           WHERE c.publish_date BETWEEN ? AND ? AND ct.category_key != 'custom'""",
        (date_from.isoformat(), date_to.isoformat()),
    ))
    outs = db.rows_to_list(db.query(
        """SELECT o.id, o.campaign_id, o.status, o.posted_at, o.final_media_drive_id
           FROM content_outputs o JOIN campaigns c ON c.id = o.campaign_id
           WHERE c.publish_date BETWEEN ? AND ?""",
        (date_from.isoformat(), date_to.isoformat()),
    ))
    plats = _platform_keys_by_output([o["id"] for o in outs])
    by_c = {}
    for o in outs:
        by_c.setdefault(o["campaign_id"], []).append(o)
    for r in rows:
        os_ = by_c.get(r["id"], [])
        r["n_counted"] = sum(1 for o in os_ if _is_counted(plats.get(o["id"], [])))
        r["n_postable"] = sum(1 for o in os_ if _is_postable(plats.get(o["id"], [])))
        r["n_unposted"] = sum(1 for o in os_ if not o["posted_at"] and o["status"] != "published"
                              and o["id"] not in assume_posted and _is_postable(plats.get(o["id"], [])))
        r["has_media"] = any(o["final_media_drive_id"] for o in os_)
    return rows


def _is_label_blank(row, labels):
    return row["category_key"] == "filler" and not row["source_idea_id"] and row["title"] in labels


def plan_reshuffle(start, horizon_days=120, assume_posted=()):
    """Works out (without changing anything) where every unposted post from
    before `start` should go: soonest free slot from `start`, Monday–Saturday,
    never more than two Instagram/TikTok posts a day, order kept, posts that
    belong together (a campaign's highlights/repost) kept apart by their
    original spacing. Untouched future filler placeholders give way; anything
    with real content or a rule date stays put."""
    labels = {r["label"] for r in db.query("SELECT label FROM content_types")}
    lookback = start - timedelta(days=60)
    horizon = start + timedelta(days=horizon_days)
    rows = _campaign_rows_with_counts(lookback, horizon, set(assume_posted))
    by_id = {r["id"]: r for r in rows}

    def eligible(r):
        return r["n_unposted"] > 0 and not r["type_key"].startswith(_NON_POSTING_TYPE_PREFIXES) and r["n_postable"] > 0

    # Backlog: unposted content from before `start`, plus the rest of any
    # campaign family (highlights/repost) that has a member in the backlog.
    back_ids = {
        r["id"] for r in rows
        if r["publish_date"] < start.isoformat() and eligible(r) and not _is_label_blank(r, labels)
    }
    def root_of(r):
        return _root_campaign_id(r["id"]) if r["dep"] else None
    family_roots = {root_of(by_id[i]) for i in back_ids if by_id[i]["dep"]}
    for r in rows:
        if r["dep"] and eligible(r) and root_of(r) in family_roots:
            back_ids.add(r["id"])
    backlog = sorted((by_id[i] for i in back_ids), key=lambda r: (r["publish_date"], r["id"]))

    # What each day already holds that must NOT move: everything from `start`
    # on except the backlog and untouched filler placeholders (those are the
    # removable ones).
    fixed, removable = {}, {}
    for r in rows:
        if r["publish_date"] < start.isoformat() or r["id"] in back_ids or not r["n_counted"]:
            continue
        if _is_label_blank(r, labels) and not r["has_media"] and not r["dep"]:
            removable.setdefault(r["publish_date"], []).append(r)
        else:
            fixed[r["publish_date"]] = fixed.get(r["publish_date"], 0) + r["n_counted"]

    placed_count, plan, min_date = {}, [], {}
    for item in backlog:
        n = max(item["n_counted"], 0)
        fam = root_of(item)
        earliest = start
        if fam in min_date:
            earliest = max(earliest, min_date[fam])
        d = earliest
        for _ in range(horizon_days):
            iso = d.isoformat()
            if _is_posting_weekday_(d):
                used = fixed.get(iso, 0) + placed_count.get(iso, 0)
                if n == 0 or used + n <= content_module.POSTS_PER_DAY_TARGET:
                    break
            d += timedelta(days=1)
        else:
            plan.append({"id": item["id"], "title": item["title"], "type": item["type_label"],
                         "from": item["publish_date"], "to": None, "removes": []})
            continue
        iso = d.isoformat()
        placed_count[iso] = placed_count.get(iso, 0) + n
        plan.append({"id": item["id"], "title": item["title"], "type": item["type_label"],
                     "from": item["publish_date"], "to": iso, "removes": [], "n": n})
        if fam is not None:
            gap = 1
            prior = date.fromisoformat(item["publish_date"])
            nxt = [m for m in backlog if m["id"] != item["id"] and root_of(m) == fam and (m["publish_date"], m["id"]) > (item["publish_date"], item["id"])]
            if nxt:
                gap = max(1, (date.fromisoformat(nxt[0]["publish_date"]) - prior).days)
            min_date[fam] = d + timedelta(days=gap)
    # Settle which blanks give way: on each day, whatever the fixed + placed
    # posts leave no room for.
    blanks_to_delete = []
    for iso, plist in removable.items():
        taken = fixed.get(iso, 0) + placed_count.get(iso, 0)
        keep_room = max(0, content_module.POSTS_PER_DAY_TARGET - taken)
        keep = 0
        for b in plist:
            if keep < keep_room:
                keep += b["n_counted"]
            else:
                blanks_to_delete.append(b["id"])
    return {"plan": plan, "delete_blank_ids": blanks_to_delete}


def _is_posting_weekday_(d):
    return content_module._is_posting_weekday(d)


def apply_reshuffle(start, mark_posted_ids=(), actor_id=None):
    """Runs plan_reshuffle for real: marks the given outputs posted, deletes
    the placeholders that give way, moves each backlog campaign (with its
    outputs and task dates), tops any short day back up, lets waiting ideas
    claim blank slots, and starts the posting queue's 'missed' tracking at
    `start`. Returns the plan plus the final per-day counts."""
    for oid in mark_posted_ids:
        if db.query_one("SELECT id FROM content_outputs WHERE id = ?", (oid,)):
            mark_posted(oid)
    result = plan_reshuffle(start, assume_posted=mark_posted_ids)
    for cid in result["delete_blank_ids"]:
        content_module.delete_campaign(cid)
    for p in result["plan"]:
        if not p["to"]:
            continue
        scheduling.move_campaign_to_date(p["id"], p["to"])
        db.execute(
            "UPDATE content_outputs SET reschedule_count = reschedule_count + 1, original_publish_date = COALESCE(original_publish_date, ?), reminder_sent_at = NULL, reminder_target_time = NULL WHERE campaign_id = ?",
            (p["from"], p["id"]),
        )
        db.execute(
            "INSERT INTO activity_log (campaign_id, actor_id, message) VALUES (?, ?, ?)",
            (p["id"], actor_id, f"Reshuffled with the calendar reset: {p['from']} → {p['to']} (not posted on its original day)."),
        )
    content_module.fill_weekly_filler_gaps(start)
    content_module.backfill_unscheduled_ideas()
    db.execute(
        """INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')""",
        (LIVE_FROM_STATE_KEY, start.isoformat()),
    )
    counts = content_module._output_counts_by_day(start, start + timedelta(days=21))
    return {"plan": result["plan"], "deleted_blanks": len(result["delete_blank_ids"]), "counts": counts}
