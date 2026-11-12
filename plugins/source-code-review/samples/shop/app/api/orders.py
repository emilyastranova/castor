from flask import Blueprint, request

from ..db import session
from app.db.session import query

bp = Blueprint("orders", __name__)


@bp.route("/orders/<order_id>")
def show(order_id):
    # Untrusted input straight into SQL: the review should catch this.
    return query(f"SELECT * FROM orders WHERE id = {order_id}")
