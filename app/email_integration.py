"""
Outbound email — used for the one place this app needs to reach someone
outside the team directly: handing off the finished video link to an
external recipient once Film/Record is confirmed done (see pipeline.py).

Deliberately built on the Python standard library only (smtplib +
email.message), the same "no extra client library" reasoning as
google_integration.py. Sends via Gmail's SMTP relay using an app-specific
password (not OAuth) — set as environment variables, never stored in the
database or in code:

    GMAIL_SENDER_EMAIL   the Gmail address to send from
    GMAIL_APP_PASSWORD   a Google Account "App Password" for that address
                         (Google Account -> Security -> 2-Step Verification
                         -> App passwords) — NOT the regular login password

Both are configured directly in the hosting provider's dashboard (see
README), the same way GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET already are.
"""
import os
import smtplib
from email.message import EmailMessage

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


class EmailNotConfigured(Exception):
    """Raised when GMAIL_SENDER_EMAIL / GMAIL_APP_PASSWORD aren't set yet."""
    pass


def is_configured():
    return bool(os.environ.get("GMAIL_SENDER_EMAIL") and os.environ.get("GMAIL_APP_PASSWORD"))


def send_email(to_email, subject, body):
    """Sends a plain-text email. Raises EmailNotConfigured if the env vars
    aren't set, or smtplib.SMTPException/OSError on a real send failure —
    callers (pipeline.py) catch both and log to activity_log rather than
    letting a delivery hiccup block the rest of the workflow."""
    sender = os.environ.get("GMAIL_SENDER_EMAIL")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not sender or not app_password:
        raise EmailNotConfigured(
            "GMAIL_SENDER_EMAIL / GMAIL_APP_PASSWORD aren't set yet (set them in your hosting "
            "provider's environment variables to enable outbound email)."
        )

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
        smtp.starttls()
        smtp.login(sender, app_password)
        smtp.send_message(msg)
