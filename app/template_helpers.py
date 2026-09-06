from datetime import date, datetime
from flask import g


def register(app):
    @app.context_processor
    def inject_globals():
        return {
            "current_user": getattr(g, "user", None),
            "today": date.today(),
            "pending_pipeline_confirmations": getattr(g, "pending_pipeline_confirmations", []),
        }

    @app.template_filter("friendly_date")
    def friendly_date(value):
        if not value:
            return ""
        if isinstance(value, str):
            value = date.fromisoformat(value[:10])
        return value.strftime("%a %-d %b")

    @app.template_filter("friendly_date_long")
    def friendly_date_long(value):
        if not value:
            return ""
        if isinstance(value, str):
            value = date.fromisoformat(value[:10])
        return value.strftime("%A, %-d %B %Y")

    @app.template_filter("friendly_datetime")
    def friendly_datetime(value):
        """Formats a pipeline meeting's ISO datetime (stored UTC — see the
        note in pipeline.py about this app having no timezone
        infrastructure) as e.g. 'Wed 15 Oct, 2:30pm UTC'."""
        if not value:
            return ""
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        formatted = value.strftime("%a %-d %b, %-I:%M%p")
        return f"{formatted[:-2]}{formatted[-2:].lower()} UTC"

    @app.template_filter("initials")
    def initials(name):
        if not name:
            return "?"
        parts = name.strip().split()
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    @app.template_filter("status_label")
    def status_label(value):
        return (value or "").replace("_", " ").title()
