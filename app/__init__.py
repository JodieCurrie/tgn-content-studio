import os
from pathlib import Path
from flask import Flask, send_from_directory, current_app, request

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
    from .routes import calendar, content, tasks, dashboard, admin, api, ideas, cron

    app.register_blueprint(auth.bp)
    app.register_blueprint(calendar.bp)
    app.register_blueprint(content.bp)
    app.register_blueprint(tasks.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(ideas.bp)
    app.register_blueprint(cron.bp)

    from . import template_helpers
    template_helpers.register(app)

    # Oct 2026, per Jodie: changes (a drag, an edit, a new idea) sometimes
    # weren't showing up right away — the page would show the old state for
    # a while, then catch up on its own. Nothing in this app caches
    # anything server-side, which points at the browser (or something
    # sitting in front of it, like a CDN on a custom domain) caching a GET
    # response by default since nothing here ever said not to. Static
    # assets are left alone (they're served from send_from_directory below
    # and by Flask's own static handler, and genuinely benefit from some
    # cache life); every page, every /api/ response and every
    # /calendar/*-fragment response now explicitly says not to cache, so
    # what's on screen always matches what's actually in the database.
    @app.after_request
    def _no_cache_dynamic_responses(response):
        if not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.route("/health")
    def health():
        return {"status": "ok"}

    @app.route("/uploads/<path:filename>")
    def uploaded_file(filename):
        from .auth import login_required
        return login_required(
            lambda: send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)
        )()

    # Served at the site root (not /static/service-worker.js) on purpose:
    # a service worker's default control scope is limited to the URL
    # directory it's served from, and the whole app needs to be in scope
    # for push notifications to work from any page, not just /static/.
    @app.route("/service-worker.js")
    def service_worker():
        static_dir = Path(app.root_path) / "static" / "js"
        response = send_from_directory(static_dir, "service-worker.js")
        response.headers["Content-Type"] = "application/javascript"
        response.headers["Cache-Control"] = "no-cache"
        return response

    return app
