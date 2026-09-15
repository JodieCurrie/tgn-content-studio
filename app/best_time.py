"""
Rule-of-thumb "best time to post" guidance per platform (Sept).

This is what makes a push reminder say "best time to post: 11:00 AM"
instead of just "you have a post today." The windows below are the kind
of general best-practice guidance any platform's own creator resources
publish (late-morning/evening peaks for short-form video, lunchtime and
early-afternoon for feed posts, morning commute for podcasts) — NOT
per-account analytics. TGN has no Instagram/TikTok/Facebook API
connection (only YouTube, via app/social_stats.py, and that's view/
subscriber counts, not posting-time engagement data), so there's no real
audience-activity data to compute this from yet. If that ever changes,
this is the one place to swap in the real numbers.

"Hour" here is local wall-clock time — like everywhere else in this app
(see pipeline.py's _parse_dt docstring), there's no real timezone
conversion infrastructure, just a single LOCAL_TIMEZONE app/push.py
converts against once, right before comparing to "now."
"""

# key -> (hour 0-23, minute, one-line reason shown in the notification)
PLATFORM_BEST_TIMES = {
    "instagram": (11, 0, "Late morning tends to catch the most scroll time."),
    "tiktok": (19, 0, "Evening is TikTok's biggest engagement window."),
    "facebook": (13, 0, "Early afternoon is Facebook's typical peak."),
    "threads": (11, 0, "Same late-morning window as Instagram."),
    "youtube": (15, 0, "Afternoon, ahead of the evening viewing rush."),
    "youtube_shorts": (12, 0, "Midday catches the lunch-break scroll."),
    "website": (9, 0, "Morning, when blog traffic tends to peak."),
    "podcast": (7, 0, "Morning commute is podcasts' biggest window."),
}

DEFAULT_BEST_TIME = (11, 0, "General late-morning posting window.")


def best_time_for_platforms(platform_keys):
    """Picks the EARLIEST recommended hour among an output's platforms —
    post before any of its windows opens rather than after some of them
    have already closed. Falls back to a sane default for a type with no
    platforms attached (or none we have a rule for)."""
    candidates = [PLATFORM_BEST_TIMES[k] for k in platform_keys if k in PLATFORM_BEST_TIMES]
    if not candidates:
        return DEFAULT_BEST_TIME
    return min(candidates, key=lambda t: (t[0], t[1]))


def format_time_label(hour, minute):
    """24h (hour, minute) -> '11:00 AM' / '7:00 PM'."""
    suffix = "AM" if hour < 12 else "PM"
    h12 = hour % 12 or 12
    return f"{h12}:{minute:02d} {suffix}"
