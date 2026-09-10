import os
from pathlib import Path
from flask import Flask, send_from_directory, current_app

from . import db as db_module


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)

    instance_dir = Path(app.instance_path)
    instance_dir.mkdir(parents=True, exist_ok=True)
    # UPLOAD_FOLDER defaults to the (ephemeral) instance dir, same as
    # DATABASE_PATH below, but — like DATABASE_PATH — can be pointed at a
    # Render persistent disk via env var so uploaded files survive deploys
    # and restarts too, not just the database.
    uploads_dir = Path(os.environ.get("UPLOAD_FOLDER", str(instance_dir / "uploads")))
    uploads_dir.mkdir(parents=True, exist_ok=True)

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
        DATABASE_PATH=os.environ.get(
            "DATABASE_PATH", str(instance_dir / "tgn.db")
        ),
        UPLOAD_FOLDER=str(uploads_dir),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,  # 25MB per upload
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

    if test_config:
        app.config.update(test_config)

    # DATABASE_PATH may point outside the instance dir (e.g. at a Render
    # persistent disk's mount path via env var) — make sure that directory
    # actually exists before sqlite3 tries to open a file in it.
    Path(app.config["DATABASE_PATH"]).parent.mkdir(parents=True, exist_ok=True)

    db_module.register(app)
    db_module.init_db(app.config["DATABASE_PATH"])

    from . import auth
    from .routes import calendar, content, tasks, dashboard, admin, api, ideas

    app.register_blueprint(auth.bp)
    app.register_blueprint(calendar.bp)
    app.register_blueprint(content.bp)
    app.register_blueprint(tasks.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(ideas.bp)

    from . import template_helpers
    template_helpers.register(app)

    @app.route("/health")
    def health():
        return {"status": "ok"}

    @app.route("/uploads/<path:filename>")
    def uploaded_file(filename):
        from .auth import login_required
        return login_required(
            lambda: send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)
        )()

    return app
