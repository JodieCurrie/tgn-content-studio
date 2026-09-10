"""
Google integration — Calendar/Meet invites + Drive file storage.

Deliberately built on the Python standard library only (urllib + json),
not the google-api-python-client / google-auth packages. Two reasons:

1. This app targets zero client-side build tooling and a minimal server
   dependency footprint (see README "Tech stack, and why") — the same
   reasoning that kept the whole app on Flask + SQLite.
2. It keeps every HTTP call here plain, inspectable JSON-over-HTTPS
   against Google's documented REST endpoints, rather than depending on
   a large generated client library this codebase's sandbox couldn't
   install/test locally before shipping.

This module only ever holds an OAuth *access*/*refresh* token pair that
one admin granted (stored in the `integration_credentials` table) — the
`client_id`/`client_secret` themselves live in environment variables
(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET), never in the database or in
code. See app/routes/admin.py for the /admin/google/connect and
/admin/google/callback routes that perform the one-time authorization.
"""
import json
import os
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta

from . import db

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"

# drive.file (not full drive access) = the app can only see/manage files
# it creates itself — least-privilege for a shared TGN Drive folder.
# youtube.readonly (Sept, Social Growth panel) = read-only channel stats and
# recent uploads for whichever YouTube channel is on this same Google
# account — no ability to upload, edit, or delete anything on the channel.
SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/youtube.readonly",
]


class GoogleNotConnected(Exception):
    """Raised when a Calendar/Drive action is attempted before an admin
    has completed the one-time Google authorization."""
    pass


def _client_id():
    return os.environ.get("GOOGLE_CLIENT_ID")


def _client_secret():
    return os.environ.get("GOOGLE_CLIENT_SECRET")


def _redirect_uri():
    # Must match, character-for-character, an "Authorized redirect URI"
    # configured on the Google Cloud OAuth client.
    return os.environ.get("GOOGLE_REDIRECT_URI")


def is_configured():
    """The environment variables are set — doesn't mean an account is
    connected yet, just that the app *could* start that flow."""
    return bool(_client_id() and _client_secret() and _redirect_uri())


def build_auth_url(state):
    params = {
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",   # required to get a refresh_token
        "prompt": "consent",        # force refresh_token even on re-auth
        "state": state,
        "include_granted_scopes": "true",
    }
    return f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


def _post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google request to {url} failed ({e.code}): {body}") from e


def exchange_code_for_tokens(code):
    return _post_form(TOKEN_ENDPOINT, {
        "code": code,
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "redirect_uri": _redirect_uri(),
        "grant_type": "authorization_code",
    })


def _fetch_userinfo(access_token):
    req = urllib.request.Request(USERINFO_ENDPOINT)
    req.add_header("Authorization", f"Bearer {access_token}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def save_new_connection(tokens, connected_by_user_id):
    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token")
    expires_at = (datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 3600))).isoformat()
    email = None
    try:
        email = _fetch_userinfo(access_token).get("email")
    except Exception:
        pass  # non-fatal — connection still works without a display email

    existing = db.query_one("SELECT * FROM integration_credentials WHERE provider = 'google'")
    if existing and not refresh_token:
        # Google only returns a refresh_token on first consent (or when
        # prompt=consent forces re-issue); if for some reason we didn't
        # get one, keep the previously stored one rather than losing it.
        refresh_token = existing["refresh_token"]

    if existing:
        db.execute(
            """UPDATE integration_credentials
               SET account_email=?, access_token=?, refresh_token=?, token_expires_at=?,
                   scope=?, connected_by=?, updated_at=datetime('now')
               WHERE provider='google'""",
            (email, access_token, refresh_token, expires_at, tokens.get("scope", ""), connected_by_user_id),
        )
    else:
        db.execute(
            """INSERT INTO integration_credentials
               (provider, account_email, access_token, refresh_token, token_expires_at, scope, connected_by)
               VALUES ('google', ?, ?, ?, ?, ?, ?)""",
            (email, access_token, refresh_token, expires_at, tokens.get("scope", ""), connected_by_user_id),
        )


def get_connection():
    row = db.query_one("SELECT * FROM integration_credentials WHERE provider = 'google'")
    return db.row_to_dict(row)


def disconnect():
    db.execute("DELETE FROM integration_credentials WHERE provider = 'google'")


def get_valid_access_token():
    """Returns a live access token, refreshing it first if it's expired
    or close to it. Raises GoogleNotConnected if no admin has connected
    a Google account yet."""
    conn = get_connection()
    if not conn or not conn.get("refresh_token"):
        raise GoogleNotConnected("No Google account has been connected yet (Admin → Integrations).")

    expires_at = conn.get("token_expires_at")
    still_valid = False
    if expires_at:
        try:
            still_valid = datetime.fromisoformat(expires_at) - timedelta(minutes=2) > datetime.utcnow()
        except ValueError:
            still_valid = False

    if still_valid and conn.get("access_token"):
        return conn["access_token"]

    tokens = _post_form(TOKEN_ENDPOINT, {
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "refresh_token": conn["refresh_token"],
        "grant_type": "refresh_token",
    })
    access_token = tokens["access_token"]
    expires_at_new = (datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 3600))).isoformat()
    db.execute(
        "UPDATE integration_credentials SET access_token=?, token_expires_at=?, updated_at=datetime('now') WHERE provider='google'",
        (access_token, expires_at_new),
    )
    return access_token


def authed_get(url):
    """Shared low-level GET for any Google JSON API this app talks to —
    used by app/youtube_integration.py so it doesn't need its own token/
    error-handling boilerplate. Raises GoogleNotConnected (via
    get_valid_access_token) or RuntimeError on an HTTP error, same as
    every other call in this module."""
    access_token = get_valid_access_token()
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {access_token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Google request to {url} failed ({e.code}): {e.read().decode('utf-8', errors='replace')}") from e


# ---------------------------------------------------------------------------
# Calendar — used by the production workflow (Concept Hashout, Film/Record,
# etc.) to create real invites with Meet links. Wired up as those workflow
# stages are built; kept here as the shared low-level call.
# ---------------------------------------------------------------------------
def create_calendar_event(summary, description, start_dt, end_dt, attendee_emails, create_meet_link=True, color_id=None):
    """start_dt/end_dt: timezone-aware datetime objects.
    color_id: optional Google Calendar colorId string ("8" = Graphite/grey,
    used for the production-pipeline meetings so they read as distinct,
    neutral "custom" blocks rather than a content-colored event).
    Returns the created event dict (includes 'htmlLink' and, if requested,
    a Meet link under conferenceData)."""
    access_token = get_valid_access_token()
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_dt.isoformat()},
        "end": {"dateTime": end_dt.isoformat()},
        "attendees": [{"email": e} for e in attendee_emails],
        "guestsCanModify": False,
    }
    if color_id:
        body["colorId"] = color_id
    if create_meet_link:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": os.urandom(8).hex(),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
    url = f"{CALENDAR_API}/calendars/primary/events?sendUpdates=all"
    if create_meet_link:
        url += "&conferenceDataVersion=1"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Google Calendar event creation failed ({e.code}): {e.read().decode('utf-8', errors='replace')}") from e


def update_calendar_event(event_id, summary=None, description=None, start_dt=None, end_dt=None, attendee_emails=None):
    """PATCHes an existing event in place (used to reschedule a pipeline
    meeting without leaving a stale duplicate on the calendar). Only the
    fields passed are changed. start_dt/end_dt: timezone-aware datetimes."""
    access_token = get_valid_access_token()
    body = {}
    if summary is not None:
        body["summary"] = summary
    if description is not None:
        body["description"] = description
    if start_dt is not None:
        body["start"] = {"dateTime": start_dt.isoformat()}
    if end_dt is not None:
        body["end"] = {"dateTime": end_dt.isoformat()}
    if attendee_emails is not None:
        body["attendees"] = [{"email": e} for e in attendee_emails]

    url = f"{CALENDAR_API}/calendars/primary/events/{event_id}?sendUpdates=all"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="PATCH")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Google Calendar event update failed ({e.code}): {e.read().decode('utf-8', errors='replace')}") from e


# ---------------------------------------------------------------------------
# Drive — used for high-quality file handling (raw video, edits, audio,
# final compilations) so large files never sit in the app's own database
# or disk. Wired up alongside the file-handling work; kept here as the
# shared low-level call.
# ---------------------------------------------------------------------------
def upload_file_to_drive(local_path, filename, mime_type, folder_id=None, share_with_emails=None):
    access_token = get_valid_access_token()
    metadata = {"name": filename}
    if folder_id:
        metadata["parents"] = [folder_id]

    boundary = "tgnboundary" + os.urandom(8).hex()
    with open(local_path, "rb") as f:
        file_bytes = f.read()

    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--".encode("utf-8")

    url = f"{DRIVE_UPLOAD_API}/files?uploadType=multipart&fields=id,webViewLink,webContentLink"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", f"multipart/related; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Google Drive upload failed ({e.code}): {e.read().decode('utf-8', errors='replace')}") from e

    if share_with_emails:
        for email in share_with_emails:
            _share_drive_file(result["id"], email, access_token)
    return result


def create_drive_folder(name, parent_id=None, share_with_emails=None):
    """Creates a Drive folder (mimeType application/vnd.google-apps.folder).
    Used once per Targeted Video, when its pipeline reaches Concept Hashout
    (see app/pipeline.py) — every later stage's files (script doc, raw
    footage link, edit drafts, audio, final compilation) land in this one
    folder. Returns the created folder dict (includes 'id' and 'webViewLink')."""
    access_token = get_valid_access_token()
    metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        metadata["parents"] = [parent_id]

    url = f"{DRIVE_API}/files?fields=id,webViewLink"
    req = urllib.request.Request(url, data=json.dumps(metadata).encode("utf-8"), method="POST")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Google Drive folder creation failed ({e.code}): {e.read().decode('utf-8', errors='replace')}") from e

    if share_with_emails:
        for email in share_with_emails:
            _share_drive_file(result["id"], email, access_token)
    return result


def _share_drive_file(file_id, email, access_token):
    url = f"{DRIVE_API}/files/{file_id}/permissions?sendNotificationEmail=true"
    body = json.dumps({"type": "user", "role": "writer", "emailAddress": email}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15):
            pass
    except urllib.error.HTTPError:
        pass  # non-fatal — file still uploaded even if one share invite fails
