import re
from functools import wraps

from flask import session, redirect, url_for, flash, request


def model_login_required(view):
    """Require a logged-in model session; otherwise bounce to the welcome page."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("model_id"):
            flash("Please sign in to continue.", "error")
            return redirect(url_for("auth.welcome", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_login_required(view):
    """Require a logged-in admin session; otherwise bounce to the admin login."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            flash("Please sign in as an admin to continue.", "error")
            return redirect(url_for("admin.login"))
        return view(*args, **kwargs)

    return wrapped


def is_valid_email(value: str) -> bool:
    if not value:
        return False
    return re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value.strip()) is not None


def to_currency(value) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"
