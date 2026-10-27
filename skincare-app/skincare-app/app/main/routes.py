import json

from flask import (
    Blueprint, render_template, request, redirect, url_for, session, flash,
    current_app
)

from app import flutterwave
from app.extensions import get_service_client
from app.utils import model_login_required, is_valid_email

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

    # If we charge in a non-USD currency, show the customer what they'll actually pay.
    charge_note = None
    try:
        currency, subunit = flutterwave.charge_for(total)
        if currency != "USD":
            charge_note = f"{currency} {subunit / 100:,.2f}"
    except flutterwave.FlutterwaveError:
        pass  # misconfiguration is reported when the customer tries to pay

    return render_template(
        "checkout.html",
        package=package,
        model=model,
        subtotal=subtotal,
        shipping=FLAT_SHIPPING_FEE,
        total=total,
        charge_note=charge_note,
        payments_enabled=flutterwave.is_configured(),
    )


def _start_payment(db, order: dict):
    """Create a fresh Flutterwave transaction for `order` and return its checkout URL.

    The amount always comes from the order row in our database (never from the
    browser). A new reference is used per attempt because Flutterwave rejects reused
    references. Raises FlutterwaveError on any failure.
    """
    email = (session.get("model_email") or "").strip()
    if not is_valid_email(email):
        raise flutterwave.FlutterwaveError("We couldn't find a valid email address for your account.")

    currency, amount = flutterwave.charge_for(order["total"])
    reference = flutterwave.new_reference(order["id"])

    db.table("orders").update({
        "payment_reference": reference,
        "payment_currency": currency,
        "payment_amount": amount,
        "payment_status": "unpaid",
    }).eq("id", order["id"]).neq("payment_status", "paid").execute()

    data = flutterwave.initialize_transaction(
        email=email,
        name=(session.get("model_name") or "").strip(),
        amount=amount,
        currency=currency,
        reference=reference,
        callback=flutterwave.callback_url(url_for("main.payment_callback")),
        metadata={
            "order_id": order["id"],
            "model_id": order["model_id"],
            "package_id": order.get("package_id"),
        },
    )
    return data["link"]


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

    if not all([full_name, address_line1, city, postal_code, country]):
        flash("Please complete all required delivery fields.", "error")
        return redirect(url_for("main.checkout", package_id=package_id))

    if not flutterwave.is_configured():
        flash("Online payment isn't available right now. Please contact support.", "error")
        return redirect(url_for("main.checkout", package_id=package_id))

    # Price is always taken from the database, never from the submitted form.
    subtotal = float(package["model_price"])
    total = subtotal + FLAT_SHIPPING_FEE
    try:
        flutterwave.charge_for(total)  # fail before creating an order if we can't charge
    except flutterwave.FlutterwaveError as exc:
        current_app.logger.error("Cannot start payment for package %s: %s", package_id, exc)
        flash("We couldn't start your payment. Please contact support.", "error")
        return redirect(url_for("main.checkout", package_id=package_id))

    order_insert = db.table("orders").insert({
        "model_id": session["model_id"],
        "package_id": package_id,
        "status": "pending",
        "subtotal": subtotal,
        "shipping_fee": FLAT_SHIPPING_FEE,
        "total": total,
        "payment_method": "flutterwave",
        "payment_status": "unpaid",
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

    try:
        return redirect(_start_payment(db, order))
    except flutterwave.FlutterwaveError as exc:
        current_app.logger.error("Flutterwave initialize failed for order %s: %s", order["id"], exc)
        db.table("orders").update({"payment_status": "failed"}).eq("id", order["id"]).execute()
        flash("We couldn't start your payment. Please try again.", "error")
        return redirect(url_for("main.order_success", order_id=order["id"]))


def _find_order(db, reference: str):
    """Find the order a transaction belongs to: by stored reference first, then by the
    order id embedded in our own tx_ref (covers a late payment on an earlier attempt)."""
    res = db.table("orders").select("*").eq("payment_reference", reference).maybe_single().execute()
    if res and res.data:
        return res.data
    order_id = flutterwave.order_id_from_reference(reference)
    if order_id:
        res = db.table("orders").select("*").eq("id", order_id).maybe_single().execute()
        if res and res.data and res.data.get("payment_method") in flutterwave.ONLINE_METHODS:
            return res.data
    return None


@main_bp.route("/payment/callback")
def payment_callback():
    """Browser lands here after paying on Flutterwave (?status=...&tx_ref=...&transaction_id=...).
    We never trust the query string: the outcome is whatever Flutterwave's verify API says."""
    reference = (request.args.get("tx_ref") or "").strip()
    if not flutterwave.is_valid_reference(reference):
        flash("We couldn't read your payment reference.", "error")
        return redirect(url_for("main.packages"))

    db = get_service_client()
    try:
        tx = flutterwave.verify_transaction(reference)
    except flutterwave.FlutterwaveError as exc:
        current_app.logger.warning("Verify failed in callback for %s: %s", reference, exc)
        order = _find_order(db, reference)
        if order:
            flash("We're still confirming your payment. This page will update shortly.", "error")
            return redirect(url_for("main.order_success", order_id=order["id"]))
        flash("We couldn't confirm that payment. If you were charged, contact support.", "error")
        return redirect(url_for("main.packages"))

    order = _find_order(db, reference)
    if not order:
        current_app.logger.error("Callback for unknown reference %s", reference)
        flash("We couldn't find an order for that payment. If you were charged, contact support.", "error")
        return redirect(url_for("main.packages"))

    outcome = flutterwave.apply_verified_transaction(db, order, tx)
    if outcome == "mismatch":
        flash("Your payment couldn't be matched to this order. Please contact support.", "error")
    return redirect(url_for("main.order_success", order_id=order["id"]))


@main_bp.route("/order/<order_id>/pay", methods=["POST"])
@model_login_required
def retry_payment(order_id):
    """Let the owner retry payment on an unpaid Flutterwave order."""
    db = get_service_client()
    res = db.table("orders").select("*").eq("id", order_id).maybe_single().execute()
    order = res.data if res else None
    if (not order or order.get("model_id") != session["model_id"]
            or order.get("payment_method") not in flutterwave.ONLINE_METHODS):
        flash("Order not found.", "error")
        return redirect(url_for("main.packages"))
    if order.get("payment_status") == "paid":
        return redirect(url_for("main.order_success", order_id=order_id))
    if order.get("status") == "cancelled":
        flash("This order was cancelled.", "error")
        return redirect(url_for("main.order_success", order_id=order_id))

    # The previous attempt may have succeeded already (e.g. webhook not yet received):
    # check before opening a second transaction so the customer can't pay twice.
    if order.get("payment_reference"):
        try:
            tx = flutterwave.verify_transaction(order["payment_reference"])
            if flutterwave.apply_verified_transaction(db, order, tx) in ("paid", "already_paid"):
                return redirect(url_for("main.order_success", order_id=order_id))
        except flutterwave.FlutterwaveError:
            pass

    try:
        return redirect(_start_payment(db, order))
    except flutterwave.FlutterwaveError as exc:
        current_app.logger.error("Flutterwave re-initialize failed for order %s: %s", order_id, exc)
        flash("We couldn't start your payment. Please try again.", "error")
        return redirect(url_for("main.order_success", order_id=order_id))


@main_bp.route("/webhooks/flutterwave", methods=["POST"])
def flutterwave_webhook():
    """Server-to-server notification from Flutterwave (no session, no login).

    Authenticated by the secret hash sent in the `verif-hash` header. Even then we
    re-verify the transaction with Flutterwave's API before touching an order.
    Returning non-2xx makes Flutterwave retry, so we only do that for transient errors.
    """
    raw = request.get_data()
    if not flutterwave.valid_webhook_signature(
        raw, request.headers.get("verif-hash"), request.headers.get("flutterwave-signature")
    ):
        return "", 401

    try:
        event = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "", 400

    if (event.get("event") or event.get("type")) != "charge.completed":
        return "", 200  # acknowledged, nothing to do

    reference = ((event.get("data") or {}).get("tx_ref") or "").strip()
    if not flutterwave.is_valid_reference(reference):
        return "", 200  # not one of our transactions

    db = get_service_client()
    try:
        tx = flutterwave.verify_transaction(reference)
        order = _find_order(db, reference)
        if not order:
            current_app.logger.warning("Webhook for unknown reference %s", reference)
            return "", 200
        outcome = flutterwave.apply_verified_transaction(db, order, tx)
        current_app.logger.info("Webhook %s -> order %s: %s", reference, order["id"], outcome)
    except flutterwave.FlutterwaveError as exc:
        if exc.retryable:
            return "", 500
        current_app.logger.error("Webhook verify rejected for %s: %s", reference, exc)
        return "", 200
    except Exception:
        current_app.logger.exception("Webhook processing error for %s", reference)
        return "", 500
    return "", 200


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

    # If the customer returns before the callback/webhook has been processed, ask
    # Flutterwave directly so the page shows the real state.
    if (order.get("payment_method") in flutterwave.ONLINE_METHODS and order.get("payment_status") != "paid"
            and order.get("payment_reference")):
        try:
            tx = flutterwave.verify_transaction(order["payment_reference"])
            flutterwave.apply_verified_transaction(db, order, tx)
            order = (
                db.table("orders")
                .select("*, order_items(*), packages(name, cover_image_url)")
                .eq("id", order_id).maybe_single().execute().data
            ) or order
        except flutterwave.FlutterwaveError:
            pass  # show the current stored state

    return render_template("order_success.html", order=order)
