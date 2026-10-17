import re
import uuid
from functools import wraps

from flask import session, redirect, url_for, flash, request, current_app


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


def upload_file_to_bucket(file_storage, bucket: str) -> str | None:
    """Upload a single file to Supabase Storage and return its public URL, or None on failure."""
    if not file_storage or not file_storage.filename:
        return None

    from app.extensions import get_service_client

    db = get_service_client()
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else "jpg"
    path = f"{uuid.uuid4().hex}.{ext}"
    file_bytes = file_storage.read()
    try:
        db.storage.from_(bucket).upload(
            path, file_bytes,
            {"content-type": file_storage.mimetype or "image/jpeg"},
        )
        return db.storage.from_(bucket).get_public_url(path)
    except Exception as exc:
        current_app.logger.warning("Image upload failed: %s", exc)
        return None


def upload_files_to_bucket(file_storages, bucket: str) -> list[str]:
    """Upload multiple files, skipping any that fail, returning the list of public URLs."""
    urls = []
    for f in file_storages or []:
        if f and f.filename:
            url = upload_file_to_bucket(f, bucket)
            if url:
                urls.append(url)
    return urls
