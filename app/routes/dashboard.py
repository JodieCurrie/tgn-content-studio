from flask import Blueprint, render_template, g

from .. import db
from ..auth import login_required
from .. import analytics
from .. import social_stats

bp = Blueprint("dashboard", __name__)


@bp.route("/home")
@login_required
def home():
    summary = analytics.home_summary(g.user)

    upcoming = db.rows_to_list(
        db.query(
            """SELECT o.id AS output_id, o.publish_date, o.status, c.id AS campaign_id, c.title,
                      ct.label AS type_label, ct.color AS type_color, u.name AS assigned_name
               FROM content_outputs o JOIN campaigns c ON c.id = o.campaign_id
               JOIN content_types ct ON ct.id = o.content_type_id
               LEFT JOIN users u ON u.id = o.assigned_user_id
               WHERE o.publish_date >= date('now') ORDER BY o.publish_date LIMIT 8"""
        )
    )

    social = social_stats.home_panel()

    return render_template("home.html", summary=summary, upcoming=upcoming, social=social)
