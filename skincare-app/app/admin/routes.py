import re
import secrets
import string
import uuid

from flask import (
    Blueprint, render_template, request, redirect, url_for, session, flash,
    current_app
)

from app.extensions import get_anon_client, get_service_client
from app.utils import admin_login_required, is_valid_email

admin_bp = Blueprint("admin", __name__, template_folder="../templates/admin")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or uuid.uuid4().hex[:8]


def _upload_image(file_storage, bucket: str) -> str | None:
    """Upload an image file to Supabase Storage and return its public URL."""
    if not file_storage or not file_storage.filename:
        return None
    db = get_service_client()
    ext = file_storage.filename.rsplit(".", 1)[-1].lower()
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


# ---------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------
@admin_bp.route("/login", methods=["GET"])
def login():
    return render_template("admin/login.html")


@admin_bp.route("/login", methods=["POST"])
def login_post():
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")

    if not is_valid_email(email) or not password:
        flash("Enter a valid email and password.", "error")
        return redirect(url_for("admin.login"))

    anon = get_anon_client()
    try:
        auth_response = anon.auth.sign_in_with_password(
            {"email": email, "password": password}
        )
    except Exception:
        flash("Invalid admin credentials.", "error")
        return redirect(url_for("admin.login"))

    user = getattr(auth_response, "user", None)
    if not user:
        flash("Invalid admin credentials.", "error")
        return redirect(url_for("admin.login"))

    db = get_service_client()
    profile = db.table("app_users").select("*").eq("id", user.id).maybe_single().execute()
    role = (profile.data or {}).get("role") if profile else None

    if role != "admin":
        flash("This account doesn't have admin access.", "error")
        return redirect(url_for("admin.login"))

    session.clear()
    session["admin_id"] = user.id
    session["admin_email"] = email
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


# ---------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------
@admin_bp.route("/")
@admin_login_required
def dashboard():
    db = get_service_client()
    products_count = len((db.table("products").select("id").execute().data) or [])
    packages_count = len((db.table("packages").select("id").execute().data) or [])
    orders = (
        db.table("orders").select("*").order("created_at", desc=True).limit(5).execute().data
        or []
    )
    pending_orders = len(
        [o for o in (db.table("orders").select("status").execute().data or [])
         if o.get("status") == "pending"]
    )
    codes_available = len(
        [c for c in (db.table("invitation_codes").select("use_count,max_uses").execute().data or [])
         if c.get("use_count", 0) < c.get("max_uses", 1)]
    )
    return render_template(
        "admin/dashboard.html",
        products_count=products_count,
        packages_count=packages_count,
        pending_orders=pending_orders,
        codes_available=codes_available,
        recent_orders=orders,
    )


# ---------------------------------------------------------------------
# Invitation codes
# ---------------------------------------------------------------------
@admin_bp.route("/invitation-codes")
@admin_login_required
def invitation_codes():
    db = get_service_client()
    codes = (
        db.table("invitation_codes").select("*").order("created_at", desc=True).execute().data
        or []
    )
    return render_template("admin/invitation_codes.html", codes=codes)


@admin_bp.route("/invitation-codes/create", methods=["POST"])
@admin_login_required
def create_invitation_code():
    db = get_service_client()
    assigned_email = request.form.get("assigned_email", "").strip() or None
    max_uses = int(request.form.get("max_uses", 1) or 1)
    custom_code = request.form.get("code", "").strip().upper()

    code = custom_code or "".join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8)
    )

    db.table("invitation_codes").insert({
        "code": code,
        "assigned_email": assigned_email,
        "max_uses": max_uses,
        "created_by": session.get("admin_id"),
    }).execute()

    flash(f"Invitation code {code} created.", "success")
    return redirect(url_for("admin.invitation_codes"))


@admin_bp.route("/invitation-codes/<code_id>/delete", methods=["POST"])
@admin_login_required
def delete_invitation_code(code_id):
    db = get_service_client()
    db.table("invitation_codes").delete().eq("id", code_id).execute()
    flash("Invitation code removed.", "success")
    return redirect(url_for("admin.invitation_codes"))


# ---------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------
@admin_bp.route("/products")
@admin_login_required
def products():
    db = get_service_client()
    items = db.table("products").select("*, inventory(quantity_on_hand)").order(
        "created_at", desc=True
    ).execute().data or []
    for item in items:
        inv = item.get("inventory")
        if isinstance(inv, list):
            item["stock"] = inv[0]["quantity_on_hand"] if inv else 0
        elif isinstance(inv, dict):
            item["stock"] = inv.get("quantity_on_hand", 0)
        else:
            item["stock"] = 0
    return render_template("admin/products.html", products=items)


@admin_bp.route("/products/create", methods=["POST"])
@admin_login_required
def create_product():
    db = get_service_client()
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "").strip()
    price = float(request.form.get("retail_price", 0) or 0)
    quantity = int(request.form.get("quantity", 0) or 0)
    image_url = _upload_image(
        request.files.get("image"), current_app.config["SUPABASE_PRODUCT_BUCKET"]
    )

    if not name:
        flash("Product name is required.", "error")
        return redirect(url_for("admin.products"))

    product = db.table("products").insert({
        "name": name, "slug": _slugify(name), "description": description,
        "category": category, "retail_price": price, "image_url": image_url,
    }).execute().data
    if product:
        db.table("inventory").insert({
            "product_id": product[0]["id"], "quantity_on_hand": quantity,
        }).execute()

    flash(f'"{name}" added to the catalog.', "success")
    return redirect(url_for("admin.products"))


@admin_bp.route("/products/<product_id>/delete", methods=["POST"])
@admin_login_required
def delete_product(product_id):
    db = get_service_client()
    db.table("products").delete().eq("id", product_id).execute()
    flash("Product deleted.", "success")
    return redirect(url_for("admin.products"))


@admin_bp.route("/products/<product_id>/inventory", methods=["POST"])
@admin_login_required
def update_inventory(product_id):
    db = get_service_client()
    quantity = int(request.form.get("quantity", 0) or 0)
    db.table("inventory").update({"quantity_on_hand": quantity}).eq(
        "product_id", product_id
    ).execute()
    flash("Inventory updated.", "success")
    return redirect(url_for("admin.products"))


# ---------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------
@admin_bp.route("/packages")
@admin_login_required
def packages():
    db = get_service_client()
    items = db.table("packages").select(
        "*, package_items(quantity, products(name))"
    ).order("created_at", desc=True).execute().data or []
    all_products = db.table("products").select("id, name").eq("is_active", True).execute().data or []
    return render_template("admin/packages.html", packages=items, all_products=all_products)


@admin_bp.route("/packages/create", methods=["POST"])
@admin_login_required
def create_package():
    db = get_service_client()
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    original_price = float(request.form.get("original_price", 0) or 0)
    model_price = float(request.form.get("model_price", 0) or 0)
    product_ids = request.form.getlist("product_ids")
    cover_image_url = _upload_image(
        request.files.get("cover_image"), current_app.config["SUPABASE_BANNER_BUCKET"]
    )

    if not name:
        flash("Package name is required.", "error")
        return redirect(url_for("admin.packages"))

    package = db.table("packages").insert({
        "name": name, "slug": _slugify(name), "description": description,
        "original_price": original_price, "model_price": model_price,
        "cover_image_url": cover_image_url,
    }).execute().data

    if package and product_ids:
        db.table("package_items").insert([
            {"package_id": package[0]["id"], "product_id": pid, "quantity": 1}
            for pid in product_ids
        ]).execute()

    flash(f'Package "{name}" created.', "success")
    return redirect(url_for("admin.packages"))

@admin_bp.route("/packages/<package_id>/delete", methods=["POST"])
@admin_login_required
def delete_package(package_id):
    db = get_service_client()
    db.table("packages").delete().eq("id", package_id).execute()
    flash("Package deleted.", "success")
    return redirect(url_for("admin.packages"))
    
@admin_bp.route("/packages/<package_id>/toggle-active", methods=["POST"])
@admin_login_required
def toggle_package_active(package_id):
    db = get_service_client()
    current = db.table("packages").select("is_active").eq("id", package_id).maybe_single().execute()
    is_active = (current.data or {}).get("is_active", True)
    db.table("packages").update({"is_active": not is_active}).eq("id", package_id).execute()
    flash("Package deactivated." if is_active else "Package reactivated.", "success")
    return redirect(url_for("admin.packages"))


# ---------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------
@admin_bp.route("/orders")
@admin_login_required
def orders():
    db = get_service_client()
    items = db.table("orders").select(
        "*, models(display_name), order_items(*)"
    ).order("created_at", desc=True).execute().data or []
    return render_template("admin/orders.html", orders=items)


@admin_bp.route("/orders/<order_id>/status", methods=["POST"])
@admin_login_required
def update_order_status(order_id):
    db = get_service_client()
    new_status = request.form.get("status")
    db.table("orders").update({"status": new_status}).eq("id", order_id).execute()
    flash("Order status updated.", "success")
    return redirect(url_for("admin.orders"))
