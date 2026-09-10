"""
Social Growth panel (Sept) — home-dashboard tracker for follower/view
growth and recent posts across Jodie's channels.

Direct-API data today comes only from YouTube (see app/youtube_integration.py),
piggybacking on the Google account already connected for Calendar/Drive.
Instagram, Facebook, Threads, TikTok, and the podcast feed have no public
API a small ministry account can use the same way (see PLATFORM_META) —
those show as "not connected yet" placeholders on the panel rather than
fake numbers, until/unless a future direct-API or aggregator integration
is built for them.

No background scheduler exists on this app's Render plan, so — same
pattern as scheduling.ensure_horizon_rolled_forward() — ensure_synced() is
called cheaply on every logged-in request (see app/auth.py) and only does
real work once every SYNC_REFRESH_HOURS.
"""
from datetime import datetime, timedelta

from . import db
from . import google_integration

SYNC_REFRESH_HOURS = 24

# Every platform Jodie posts to as Jodie Currie / That's Good News, shown
# on the panel in this order. `connected` says whether this app currently
# has any live data source for it — see the module docstring for why the
# others don't yet.
PLATFORM_META = {
    "youtube": {"label": "YouTube", "icon": "▶️", "connected": True},
    "instagram": {"label": "Instagram", "icon": "📸", "connected": False},
    "tiktok": {"label": "TikTok", "icon": "🎵", "connected": False},
    "facebook": {"label": "Facebook", "icon": "👍", "connected": False},
    "threads": {"label": "Threads", "icon": "🧵", "connected": False},
    "podcast": {"label": "Podcast", "icon": "🎙️", "connected": False},
}

CONNECTED_PLATFORMS = tuple(key for key, meta in PLATFORM_META.items() if meta["connected"])


def _latest_snapshot(platform):
    return db.row_to_dict(
        db.query_one(
            "SELECT * FROM social_snapshots WHERE platform = ? ORDER BY captured_at DESC LIMIT 1",
            (platform,),
        )
    )


def _snapshot_before(platform, cutoff_dt):
    """The most recent snapshot for `platform` captured at or before
    `cutoff_dt` — used to compute a "vs about a week ago" delta without
    needing an exact 7-day-old row to exist."""
    return db.row_to_dict(
        db.query_one(
            "SELECT * FROM social_snapshots WHERE platform = ? AND captured_at <= ? ORDER BY captured_at DESC LIMIT 1",
            (platform, cutoff_dt.isoformat()),
        )
    )


def sync_youtube():
    """Pulls current channel stats + recent videos from YouTube and
    records one new snapshot row. Safe to call even when Google isn't
    connected yet, or the connected account has no YouTube channel, or the
    YouTube Data API isn't enabled on the Google Cloud project yet — any
    of those quietly no-op rather than breaking the page that triggered
    this (see ensure_synced)."""
    from . import youtube_integration  # local import: keeps this module importable without it during tests

    stats = youtube_integration.get_channel_stats()
    if not stats:
        return None

    videos = youtube_integration.get_recent_videos(limit=5)
    extra = {
        "video_count": stats["video_count"],
        "title": stats["title"],
        "thumbnail_url": stats["thumbnail_url"],
        "recent_videos": videos,
    }
    db.execute(
        "INSERT INTO social_snapshots (platform, followers, total_views, extra) VALUES ('youtube', ?, ?, ?)",
        (stats["subscriber_count"], stats["view_count"], db.to_json(extra)),
    )
    return stats


def sync_all():
    """Runs every platform's sync that this app currently knows how to do.
    Each platform's failure (not connected, API error, quota, ...) is
    caught on its own so one broken integration never blocks another."""
    synced = []
    if google_integration.is_configured():
        try:
            if sync_youtube() is not None:
                synced.append("youtube")
        except (google_integration.GoogleNotConnected, RuntimeError):
            pass  # no connected account yet, or a transient API error — try again next sync
    return synced


def ensure_synced():
    """Cheap on every request; only re-syncs once SYNC_REFRESH_HOURS has
    passed since the last real sync — same "no worker process, so check on
    request instead" pattern as scheduling.ensure_horizon_rolled_forward()."""
    row = db.query_one("SELECT value FROM app_state WHERE key = 'social_stats_synced_at'")
    if row:
        try:
            last_synced = datetime.fromisoformat(row["value"])
            if (datetime.utcnow() - last_synced).total_seconds() < SYNC_REFRESH_HOURS * 3600:
                return
        except ValueError:
            pass  # malformed value — treat as never synced, fall through and re-sync

    sync_all()

    now_iso = datetime.utcnow().isoformat()
    db.execute(
        """INSERT INTO app_state (key, value, updated_at) VALUES ('social_stats_synced_at', ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')""",
        (now_iso,),
    )


def _platform_card(platform):
    meta = PLATFORM_META[platform]
    if platform not in CONNECTED_PLATFORMS:
        return {"key": platform, "label": meta["label"], "icon": meta["icon"], "connected": False}

    latest = _latest_snapshot(platform)
    if not latest:
        return {"key": platform, "label": meta["label"], "icon": meta["icon"], "connected": True, "has_data": False}

    week_ago = datetime.utcnow() - timedelta(days=7)
    prior = _snapshot_before(platform, week_ago)

    followers_delta = None
    views_delta = None
    if prior:
        if latest["followers"] is not None and prior["followers"] is not None:
            followers_delta = latest["followers"] - prior["followers"]
        if latest["total_views"] is not None and prior["total_views"] is not None:
            views_delta = latest["total_views"] - prior["total_views"]

    extra = db.from_json(latest["extra"], default={})
    return {
        "key": platform,
        "label": meta["label"],
        "icon": meta["icon"],
        "connected": True,
        "has_data": True,
        "followers": latest["followers"],
        "followers_delta": followers_delta,
        "total_views": latest["total_views"],
        "views_delta": views_delta,
        "video_count": extra.get("video_count"),
        "recent_videos": extra.get("recent_videos", []),
        "captured_at": latest["captured_at"],
    }


def home_panel():
    """Everything app/templates/home.html's Social Growth section needs.
    google_connected distinguishes "no Google account connected at all"
    (show a one-time setup prompt) from "connected, just no data synced
    yet" (has_data False on the youtube card, e.g. right after connecting,
    before the next sync). `platforms` is the ordered list for the card
    grid; `by_key` is the same cards keyed by platform so the template can
    reach a specific one (e.g. YouTube's recent videos) without filtering."""
    cards = [_platform_card(key) for key in PLATFORM_META]
    return {
        "google_connected": bool(google_integration.get_connection()),
        "platforms": cards,
        "by_key": {c["key"]: c for c in cards},
    }
