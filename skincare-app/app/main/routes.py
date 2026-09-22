from flask import (
    Blueprint, render_template, request, redirect, url_for, session, flash,
    current_app
)

from app.extensions import get_service_client
from app.utils import model_login_required, upload_files_to_bucket

main_bp = Blueprint("main", __name__)

FLAT_SHIPPING_FEE = 0.00


@main_bp.route("/packages")
@model_login_required
def packages():
    db = get_service_client()
    result = (
        db.table("packages")
        .select("*, package_items(quantity, products(name, image_url))")
        .eq("is_active", True)
        .order("created_at", desc=False)
        .execute()
    )
    return render_template("packages.html", packages=result.data or [])


@main_bp.route("/checkout/<package_id>", methods=["GET"])
@model_login_required
def checkout(package_id):
    db = get_service_client()
    pkg_result = (
        db.table("packages")
        .select("*, package_items(quantity, products(id, name, image_url, retail_price))")
        .eq("id", package_id)
        .maybe_single()
        .execute()
    )
    package = pkg_result.data if pkg_result else None
    if not package:
        flash("That package is no longer available.", "error")
        return redirect(url_for("main.packages"))

    model_result = (
        db.table("models").select("*").eq("id", session["model_id"]).maybe_single().execute()
    )
    model = model_result.data if model_result else {}

    subtotal = float(package["model_price"])
    total = subtotal + FLAT_SHIPPING_FEE

    return render_template(
        "checkout.html",
        package=package,
        model=model,
        subtotal=subtotal,
        shipping=FLAT_SHIPPING_FEE,
        total=total,
    )


@main_bp.route("/checkout/<package_id>", methods=["POST"])
@model_login_required
def place_order(package_id):
    db = get_service_client()
    pkg_result = (
        db.table("packages")
        .select("*, package_items(quantity, products(id, name, retail_price))")
        .eq("id", package_id)
        .maybe_single()
        .execute()
    )
    package = pkg_result.data if pkg_result else None
    if not package:
        flash("That package is no longer available.", "error")
        return redirect(url_for("main.packages"))

    full_name = request.form.get("full_name", "").strip()
    phone = request.form.get("phone", "").strip()
    address_line1 = request.form.get("address_line1", "").strip()
    address_line2 = request.form.get("address_line2", "").strip()
    city = request.form.get("city", "").strip()
    state = request.form.get("state", "").strip()
    postal_code = request.form.get("postal_code", "").strip()
    country = request.form.get("country", "").strip()
    notes = request.form.get("notes", "").strip()
    gift_card_codes = request.form.get("gift_card_codes", "").strip()
    gift_card_files = request.files.getlist("gift_card_images")

    if not all([full_name, address_line1, city, postal_code, country]):
        flash("Please complete all required delivery fields.", "error")
        return redirect(url_for("main.checkout", package_id=package_id))

    if not gift_card_codes:
        flash("Please enter your Amazon gift card code(s).", "error")
        return redirect(url_for("main.checkout", package_id=package_id))

    valid_files = [f for f in gift_card_files if f and f.filename]
    if not valid_files:
        flash("Please upload at least one photo of your gift card and receipt.", "error")
        return redirect(url_for("main.checkout", package_id=package_id))

    gift_card_image_urls = upload_files_to_bucket(
        valid_files, current_app.config["SUPABASE_PAYMENT_PROOF_BUCKET"]
    )
    if not gift_card_image_urls:
        flash("We couldn't upload your images. Please try again.", "error")
        return redirect(url_for("main.checkout", package_id=package_id))

    subtotal = float(package["model_price"])
    total = subtotal + FLAT_SHIPPING_FEE

    order_insert = db.table("orders").insert({
        "model_id": session["model_id"],
        "package_id": package_id,
        "status": "pending",
        "subtotal": subtotal,
        "shipping_fee": FLAT_SHIPPING_FEE,
        "total": total,
        "payment_method": "amazon_gift_card",
        "gift_card_codes": gift_card_codes,
        "gift_card_image_urls": gift_card_image_urls,
        "delivery_name": full_name,
        "delivery_phone": phone,
        "delivery_address": {
            "line1": address_line1, "line2": address_line2, "city": city,
            "state": state, "postal_code": postal_code, "country": country,
        },
        "notes": notes,
    }).execute()

    order = order_insert.data[0] if order_insert.data else None
    if not order:
        flash("We couldn't place your order. Please try again.", "error")
        return redirect(url_for("main.checkout", package_id=package_id))

    line_items = []
    for item in package.get("package_items", []):
        product = item.get("products") or {}
        qty = item.get("quantity", 1)
        unit_price = float(product.get("retail_price", 0))
        line_items.append({
            "order_id": order["id"],
            "product_id": product.get("id"),
            "product_name": product.get("name", "Item"),
            "unit_price": unit_price,
            "quantity": qty,
            "line_total": round(unit_price * qty, 2),
        })
    if line_items:
        db.table("order_items").insert(line_items).execute()

    return redirect(url_for("main.order_success", order_id=order["id"]))


@main_bp.route("/order/<order_id>/success")
@model_login_required
def order_success(order_id):
    db = get_service_client()
    order_result = (
        db.table("orders")
        .select("*, order_items(*), packages(name, cover_image_url)")
        .eq("id", order_id)
        .maybe_single()
        .execute()
    )
    order = order_result.data if order_result else None
    if not order or order.get("model_id") != session["model_id"]:
        flash("Order not found.", "error")
        return redirect(url_for("main.packages"))

    return render_template("order_success.html", order=order)
