from flask import (
    Blueprint, render_template, request, redirect, url_for, session, flash
)

from app.extensions import get_anon_client, get_service_client
from app.utils import is_valid_email

auth_bp = Blueprint("auth", __name__)


def _check_invitation_code(code: str, email: str):
    """Look up an invitation code and confirm it can be used by this email.

    Returns (invitation_row, error_message). invitation_row is None on failure.
    """
    if not code or not code.strip():
        return None, "Please enter your invitation code."

    db = get_service_client()
    result = (
        db.table("invitation_codes")
        .select("*")
        .eq("code", code.strip().upper())
        .maybe_single()
        .execute()
    )
    row = result.data if result else None

    if not row:
        return None, "That invitation code isn't recognized."

    if row.get("use_count", 0) >= row.get("max_uses", 1):
        return None, "This invitation code has already been used."

    if row.get("expires_at"):
        from datetime import datetime, timezone
        try:
            expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            if expires < datetime.now(timezone.utc):
                return None, "This invitation code has expired."
        except ValueError:
            pass

    assigned_email = (row.get("assigned_email") or "").lower().strip()
    if assigned_email and assigned_email != email.lower().strip():
        return None, "This invitation code is assigned to a different account."

    return row, None


def _consume_invitation_code(row: dict, model_id: str, email: str):
    db = get_service_client()
    new_use_count = row.get("use_count", 0) + 1
    db.table("invitation_codes").update({
        "use_count": new_use_count,
        "is_used": new_use_count >= row.get("max_uses", 1),
        "assigned_model_id": model_id,
        "assigned_email": row.get("assigned_email") or email,
    }).eq("id", row["id"]).execute()


@auth_bp.route("/", methods=["GET"])
def welcome():
    next_url = request.args.get("next", "")
    return render_template("welcome.html", next_url=next_url)


@auth_bp.route("/login", methods=["POST"])
def login():
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    next_url = request.form.get("next_url") or url_for("main.packages")

    if not is_valid_email(email):
        flash("Please enter a valid Gmail address.", "error")
        return redirect(url_for("auth.welcome"))

    if not password:
        flash("Please enter your password.", "error")
        return redirect(url_for("auth.welcome"))

    anon = get_anon_client()
    try:
        auth_response = anon.auth.sign_in_with_password(
            {"email": email, "password": password}
        )
    except Exception:
        flash("We couldn't sign you in. Check your email and password.", "error")
        return redirect(url_for("auth.welcome"))

    user = getattr(auth_response, "user", None)
    if not user:
        flash("We couldn't sign you in. Check your email and password.", "error")
        return redirect(url_for("auth.welcome"))

    db = get_service_client()
    model_row = (
        db.table("models").select("*").eq("user_id", user.id).maybe_single().execute()
    )
    model = model_row.data if model_row else None

    if not model:
        flash("No model profile is linked to this account yet. Contact your admin.", "error")
        return redirect(url_for("auth.welcome"))

    session.clear()
    session["model_id"] = model["id"]
    session["model_name"] = model.get("display_name", email)
    session["model_email"] = email

    flash(f"Welcome back, {model.get('display_name', 'there')}.", "success")
    return redirect(next_url)


@auth_bp.route("/register", methods=["POST"])
def register():
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    display_name = request.form.get("display_name", "").strip() or email.split("@")[0]
    invite_code = request.form.get("invitation_code", "").strip()

    if not is_valid_email(email):
        flash("Please enter a valid Gmail address.", "error")
        return redirect(url_for("auth.welcome"))

    if not password or len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("auth.welcome"))

    invite_row, err = _check_invitation_code(invite_code, email)
    if err:
        flash(err, "error")
        return redirect(url_for("auth.welcome"))

    anon = get_anon_client()
    try:
        auth_response = anon.auth.sign_up({"email": email, "password": password})
    except Exception as exc:
        flash(f"We couldn't create your account: {exc}", "error")
        return redirect(url_for("auth.welcome"))

    user = getattr(auth_response, "user", None)
    if not user:
        flash("We couldn't create your account. Please try again.", "error")
        return redirect(url_for("auth.welcome"))

    db = get_service_client()
    db.table("app_users").upsert({
        "id": user.id, "email": email, "role": "model", "full_name": display_name,
    }).execute()

    model_insert = db.table("models").insert({
        "user_id": user.id, "display_name": display_name, "status": "active",
    }).execute()
    model = model_insert.data[0] if model_insert.data else None

    if not model:
        flash("Account created, but we couldn't finish setup. Contact your admin.", "error")
        return redirect(url_for("auth.welcome"))

    _consume_invitation_code(invite_row, model["id"], email)

    session.clear()
    session["model_id"] = model["id"]
    session["model_name"] = display_name
    session["model_email"] = email

    flash(f"Welcome, {display_name}. Your account is ready.", "success")
    return redirect(url_for("main.packages"))

@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("forgot_password.html")
    email = request.form.get("email", "").strip()
    if not is_valid_email(email):
        flash("Please enter a valid email address.", "error")
        return redirect(url_for("auth.forgot_password"))
    # Use your existing Supabase anonymous client.
    anon = get_anon_client()
    try:
        # Use the actual public URL of your deployed Flask application.
        redirect_url = url_for(
            "auth.reset_password",
            _external=True
        )
        anon.auth.reset_password_for_email(
            email,
            options={"redirect_to": redirect_url}
        )
    except Exception:
        # Avoid exposing account details or internal errors.
        pass
    # Use the same message whether or not the email exists.
    flash(
        "If an account exists for that email, a password recovery link will be sent.",
        "success"
    )
    return redirect(url_for("auth.welcome"))
@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if request.method == "GET":
        return render_template("reset_password.html")
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")
    if len(password) < 8:
        flash("Your password must be at least 8 characters.", "error")
        return redirect(url_for("auth.reset_password"))
    if password != confirm_password:
        flash("Your passwords do not match.", "error")
        return redirect(url_for("auth.reset_password"))
    # Do not update the password using the service-role client.
    # The recovery session must first be validated.
    flash(
        "Password recovery needs to be verified before your password can be changed.",
        "error"
    )
    return redirect(url_for("auth.welcome"))


@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("You've been signed out.", "success")
    return redirect(url_for("auth.welcome"))
