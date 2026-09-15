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

Each platform now carries a short ORDERED LIST of candidate windows
(primary first) instead of a single time, so a reminder can offer
"delay to next best suggested time" — see app/push.py's delay_reminder.
"""

# key -> ordered list of (hour 0-23, minute, one-line reason), earliest-first
PLATFORM_BEST_TIMES = {
    "instagram": [
        (11, 0, "Late morning tends to catch the most scroll time."),
        (13, 0, "Early afternoon is a solid secondary window."),
        (19, 0, "Evening catches the after-work scroll too."),
    ],
    "tiktok": [
        (12, 0, "Midday lunch-break scroll is a good secondary window."),
        (19, 0, "Evening is TikTok's biggest engagement window."),
        (21, 0, "Late evening catches night-owl scrolling."),
    ],
    "facebook": [
        (9, 0, "Morning catches people checking Facebook before work."),
        (13, 0, "Early afternoon is Facebook's typical peak."),
        (20, 0, "Evening is a secondary peak once people are home."),
    ],
    "threads": [
        (11, 0, "Same late-morning window as Instagram."),
        (19, 0, "Evening is a secondary window, mirroring Instagram's."),
    ],
    "youtube": [
        (15, 0, "Afternoon, ahead of the evening viewing rush."),
        (19, 0, "Evening catches the prime-time viewing window."),
    ],
    "youtube_shorts": [
        (12, 0, "Midday catches the lunch-break scroll."),
        (18, 0, "Early evening is a secondary peak."),
    ],
    "website": [
        (9, 0, "Morning, when blog traffic tends to peak."),
        (13, 0, "Early afternoon is a secondary traffic bump."),
    ],
    "podcast": [
        (7, 0, "Morning commute is podcasts' biggest window."),
        (17, 0, "Evening commute is a secondary window."),
    ],
}

DEFAULT_BEST_TIMES = [
    (11, 0, "General late-morning posting window."),
    (15, 0, "General afternoon posting window."),
]


def best_time_candidates_for_platforms(platform_keys):
    """All candidate (hour, minute, reason) windows across an output's
    platforms, earliest-first, de-duplicated by clock time. This is the
    full menu a reminder can step through via "delay to next best time."
    Falls back to a sane generic pair of windows for a type with no
    platforms attached (or none we have rules for)."""
    seen = {}
    for key in platform_keys:
        for hour, minute, reason in PLATFORM_BEST_TIMES.get(key, []):
            seen.setdefault((hour, minute), reason)
    if not seen:
        for hour, minute, reason in DEFAULT_BEST_TIMES:
            seen.setdefault((hour, minute), reason)
    return sorted(((h, m, r) for (h, m), r in seen.items()), key=lambda t: (t[0], t[1]))


def best_time_for_platforms(platform_keys):
    """The single earliest recommended window — post before any of its
    platforms' windows opens rather than after some have already closed.
    Kept as a convenience wrapper around best_time_candidates_for_platforms."""
    return best_time_candidates_for_platforms(platform_keys)[0]


def next_candidate_after(hour, minute, platform_keys):
    """The next candidate window strictly later than (hour, minute), or
    None if that was already the last one today. Used by "delay to next
    best suggested time" — advances through the same ordered list a
    reminder's initial pick came from."""
    for cand_hour, cand_minute, reason in best_time_candidates_for_platforms(platform_keys):
        if (cand_hour, cand_minute) > (hour, minute):
            return (cand_hour, cand_minute, reason)
    return None


def reason_for(hour, minute, platform_keys):
    """Looks up the reason text for a specific (hour, minute) among an
    output's candidates — used when re-displaying a previously-computed
    or delayed-to target whose reason wasn't stored verbatim. Falls back
    to a generic line if the exact slot isn't one of the rule-based ones
    (shouldn't normally happen, since targets always come from this
    module's own candidate lists)."""
    for cand_hour, cand_minute, reason in best_time_candidates_for_platforms(platform_keys):
        if (cand_hour, cand_minute) == (hour, minute):
            return reason
    return "Next best posting window today."


def format_time_label(hour, minute):
    """24h (hour, minute) -> '11:00 AM' / '7:00 PM'."""
    suffix = "AM" if hour < 12 else "PM"
    h12 = hour % 12 or 12
    return f"{h12}:{minute:02d} {suffix}"
