from flask import Blueprint, render_template

from .. import db
from ..auth import login_required

bp = Blueprint("ideas", __name__, url_prefix="/ideas")


@bp.route("")
@login_required
def index():
    ideas = db.rows_to_list(
        db.query(
            """SELECT i.*, u.name AS created_by_name, ct.label AS type_label, ct.color AS type_color,
                      ct.category_key AS type_category
               FROM content_ideas i
               LEFT JOIN users u ON u.id = i.created_by
               LEFT JOIN content_types ct ON ct.id = i.content_type_id
               WHERE i.scheduled_campaign_id IS NULL ORDER BY i.created_at DESC"""
        )
    )
    scheduled = db.rows_to_list(
        db.query(
            """SELECT i.*, c.title AS campaign_title, c.publish_date FROM content_ideas i
               JOIN campaigns c ON c.id = i.scheduled_campaign_id
               ORDER BY i.created_at DESC LIMIT 15"""
        )
    )
    content_types = db.rows_to_list(db.query("SELECT * FROM content_types WHERE archived = 0 ORDER BY sort_order"))
    # Monthly/Filler each get their own "what type of post" dropdown once an
    # idea is tagged with that category (Ideas overhaul, Sept); Targeted
    # Campaign ideas need no sub-type — one output always spawns the rest.
    monthly_types = [t for t in content_types if t["category_key"] == "monthly"]
    filler_types = [t for t in content_types if t["category_key"] == "filler"]
    targeted_types = [t for t in content_types if t["category_key"] == "targeted" and t["is_campaign_type"]]
    return render_template(
        "ideas.html", ideas=ideas, scheduled=scheduled, content_types=content_types,
        monthly_types=monthly_types, filler_types=filler_types, targeted_types=targeted_types,
    )
