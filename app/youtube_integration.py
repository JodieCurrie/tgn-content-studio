"""
YouTube Data API v3 — read-only channel stats + recent uploads for the
Social Growth panel (Sept). Piggybacks on the same Google OAuth connection
already used for Calendar/Drive (see app/google_integration.py) — no
separate Google Cloud project or credentials, just the youtube.readonly
scope added to that one client, plus enabling "YouTube Data API v3" on
the same project.

YouTube's public API has no "shares" or "saves" metric at all (the
Instagram/TikTok-style engagement breakdown doesn't exist here) — this
module only ever returns what YouTube actually reports: views, likes,
comments, subscribers, and video count.
"""
import urllib.parse

from . import google_integration as g

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"


def get_channel_stats():
    """The connected Google account's own YouTube channel — title,
    subscriber count, lifetime view count, video count. Returns None if
    the connected account has no YouTube channel at all (a plain Google
    account with no channel ever created)."""
    data = g.authed_get(f"{YOUTUBE_API}/channels?part=snippet,statistics&mine=true")
    items = data.get("items") or []
    if not items:
        return None
    ch = items[0]
    stats = ch.get("statistics", {})
    return {
        "channel_id": ch["id"],
        "title": ch["snippet"]["title"],
        "thumbnail_url": ch["snippet"].get("thumbnails", {}).get("default", {}).get("url"),
        "subscriber_count": int(stats.get("subscriberCount", 0)),
        "view_count": int(stats.get("viewCount", 0)),
        "video_count": int(stats.get("videoCount", 0)),
    }


def get_recent_videos(limit=5):
    """The channel's `limit` most recent uploads, each with its own view/
    like/comment counts. Three API calls total: one to find the channel's
    uploads playlist, one to list its most recent items, one batched call
    for every listed video's statistics."""
    channel_data = g.authed_get(f"{YOUTUBE_API}/channels?part=contentDetails&mine=true")
    items = channel_data.get("items") or []
    if not items:
        return []
    uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    playlist_data = g.authed_get(
        f"{YOUTUBE_API}/playlistItems?part=snippet&playlistId={urllib.parse.quote(uploads_playlist_id)}"
        f"&maxResults={int(limit)}"
    )
    playlist_items = playlist_data.get("items") or []
    if not playlist_items:
        return []

    video_ids = [pi["snippet"]["resourceId"]["videoId"] for pi in playlist_items]
    ids_param = urllib.parse.quote(",".join(video_ids))
    videos_data = g.authed_get(f"{YOUTUBE_API}/videos?part=statistics&id={ids_param}")
    stats_by_id = {v["id"]: v.get("statistics", {}) for v in videos_data.get("items") or []}

    videos = []
    for pi in playlist_items:
        video_id = pi["snippet"]["resourceId"]["videoId"]
        stats = stats_by_id.get(video_id, {})
        videos.append({
            "video_id": video_id,
            "title": pi["snippet"]["title"],
            "published_at": pi["snippet"]["publishedAt"],
            "thumbnail_url": pi["snippet"].get("thumbnails", {}).get("medium", {}).get("url"),
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "view_count": int(stats.get("viewCount", 0)),
            "like_count": int(stats.get("likeCount", 0)),
            "comment_count": int(stats.get("commentCount", 0)),
        })
    return videos
