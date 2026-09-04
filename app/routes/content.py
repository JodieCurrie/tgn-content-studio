from flask import Blueprint, render_template

from .. import db
from ..auth import login_required, admin_required

bp = Blueprint("content", __name__, url_prefix="/content")


@bp.route("/create-modal")
@login_required
def create_modal():
    """Renders the '+ Create Content' quick-create modal as an HTML
    fragment, fetched by JS and injected into the page — Part 7 wants this
    to feel instant, not like navigating to a giant form."""
    options = db.rows_to_list(
        db.query("SELECT * FROM creation_options WHERE archived = 0 ORDER BY sort_order")
    )
    for o in options:
        o["output_type_ids"] = db.from_json(o["output_type_ids"], [])
    content_types = db.rows_to_list(db.query("SELECT * FROM content_types WHERE archived = 0 ORDER BY sort_order"))
    ct_by_id = {c["id"]: c for c in content_types}
    for o in options:
        o["types"] = [ct_by_id[i] for i in o["output_type_ids"] if i in ct_by_id]
    platforms = db.rows_to_list(db.query("SELECT * FROM platforms ORDER BY sort_order"))
    users = db.rows_to_list(
        db.query(
            """SELECT u.id, u.name, r.key AS role_key FROM users u
               JOIN roles r ON r.id = u.role_id WHERE u.active = 1 ORDER BY u.name"""
        )
    )
    return render_template(
        "partials/create_modal.html", options=options, platforms=platforms, users=users,
    )
