"""
Paystack integration (server-side only).

The secret key is read from the environment via app config and is only ever
used from this module, in an `Authorization` header sent to api.paystack.co.
It is never rendered into a template or returned to the browser.

Flow:
  1. initialize_transaction()  -> Paystack returns a hosted checkout URL
  2. the customer pays on Paystack's page and is redirected back to our callback
  3. verify_transaction()      -> we ask Paystack (server-to-server) for the truth
  4. apply_verified_transaction() -> only a verified, amount- and currency-matching
     success marks the order paid. Callback and webhook both use this function,
     and it is idempotent, so whichever arrives first wins and the other is a no-op.
"""
import hashlib
import hmac
import re
import uuid
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote

import requests
from flask import current_app

API_BASE = "https://api.paystack.co"
CHECKOUT_HOST_PREFIX = "https://checkout.paystack.com/"
REQUEST_TIMEOUT = (5, 20)  # (connect, read) seconds
REFERENCE_RE = re.compile(r"^[A-Za-z0-9._=-]{8,100}$")

# Transaction statuses returned by Paystack's verify endpoint.
FAILED_STATUSES = {"failed", "abandoned", "reversed"}


class PaystackError(Exception):
    """Raised for any Paystack/API problem. `retryable` is True for network/5xx/429."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class PaystackConfigError(PaystackError):
    """Raised when Paystack isn't configured (missing key, missing exchange rate...)."""


# ---------------------------------------------------------------------
# Config / helpers
# ---------------------------------------------------------------------
def _secret_key() -> str:
    key = (current_app.config.get("PAYSTACK_SECRET_KEY") or "").strip()
    if not key:
        raise PaystackConfigError("Online payments are not configured yet.")
    return key


def is_configured() -> bool:
    return bool((current_app.config.get("PAYSTACK_SECRET_KEY") or "").strip())


def is_valid_reference(reference: str) -> bool:
    return bool(reference) and REFERENCE_RE.match(reference) is not None


def new_reference() -> str:
    """Unique, unguessable transaction reference (Paystack allows [A-Za-z0-9.=-_])."""
    return f"AS-{uuid.uuid4().hex}"


def to_subunit(amount: Decimal) -> int:
    """Major units (e.g. dollars/naira) -> Paystack subunits (cents/kobo), rounded half-up."""
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def charge_for(total) -> tuple[str, int]:
    """Return (currency, amount_in_subunits) to charge for an order total.

    Store prices are in USD. If PAYSTACK_CURRENCY is not USD, PAYSTACK_USD_EXCHANGE_RATE
    must be set, otherwise we refuse to charge (we never guess a conversion rate).
    """
    currency = (current_app.config.get("PAYSTACK_CURRENCY") or "USD").strip().upper()
    amount = Decimal(str(total))
    if currency != "USD":
        raw_rate = str(current_app.config.get("PAYSTACK_USD_EXCHANGE_RATE") or "").strip()
        try:
            rate = Decimal(raw_rate)
        except Exception:
            rate = Decimal(0)
        if rate <= 0:
            raise PaystackConfigError(
                "Payment currency is set to %s but PAYSTACK_USD_EXCHANGE_RATE is missing." % currency
            )
        amount = amount * rate
    subunit = to_subunit(amount)
    if subunit <= 0:
        raise PaystackError("Order total must be greater than zero.")
    return currency, subunit


def callback_url(path: str) -> str:
    """Absolute callback URL. APP_BASE_URL is recommended in production so the URL
    is always https and never depends on proxy headers."""
    base = (current_app.config.get("APP_BASE_URL") or "").strip().rstrip("/")
    if base:
        return f"{base}{path}"
    from flask import request
    return request.url_root.rstrip("/") + path


# ---------------------------------------------------------------------
# Paystack API
# ---------------------------------------------------------------------
def _call(method: str, path: str, **kwargs) -> dict:
    headers = {
        "Authorization": f"Bearer {_secret_key()}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.request(
            method, f"{API_BASE}{path}", headers=headers, timeout=REQUEST_TIMEOUT, **kwargs
        )
    except requests.RequestException as exc:
        current_app.logger.warning("Paystack request failed (%s %s): %s", method, path, type(exc).__name__)
        raise PaystackError("Could not reach Paystack.", retryable=True) from exc

    try:
        body = resp.json()
    except ValueError:
        body = {}

    if resp.status_code >= 400 or body.get("status") is not True:
        message = body.get("message") or f"Paystack returned HTTP {resp.status_code}."
        retryable = resp.status_code >= 500 or resp.status_code == 429
        current_app.logger.warning("Paystack API error (%s %s): %s %s", method, path, resp.status_code, message)
        raise PaystackError(message, retryable=retryable)
    return body.get("data") or {}


def initialize_transaction(*, email: str, amount: int, currency: str, reference: str,
                           callback: str, metadata: dict) -> dict:
    """Create a transaction and return {'authorization_url', 'access_code', 'reference'}."""
    data = _call("POST", "/transaction/initialize", json={
        "email": email,
        "amount": amount,          # subunits, as an integer
        "currency": currency,
        "reference": reference,
        "callback_url": callback,
        "metadata": metadata,
    })
    url = data.get("authorization_url") or ""
    if not url.startswith(CHECKOUT_HOST_PREFIX):
        raise PaystackError("Paystack returned an unexpected checkout URL.")
    return data


def verify_transaction(reference: str) -> dict:
    """Fetch the authoritative transaction record from Paystack."""
    if not is_valid_reference(reference):
        raise PaystackError("Invalid payment reference.")
    return _call("GET", f"/transaction/verify/{quote(reference, safe='')}")


def valid_webhook_signature(raw_body: bytes, signature: str | None) -> bool:
    """Paystack signs the raw request body with HMAC-SHA512 using the secret key."""
    key = (current_app.config.get("PAYSTACK_SECRET_KEY") or "").strip()
    if not key or not signature:
        return False
    expected = hmac.new(key.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


# ---------------------------------------------------------------------
# Settling an order from a verified transaction
# ---------------------------------------------------------------------
def transaction_metadata(tx: dict) -> dict:
    meta = tx.get("metadata")
    return meta if isinstance(meta, dict) else {}


def apply_verified_transaction(db, order: dict, tx: dict) -> str:
    """Update `order` from a transaction fetched via verify_transaction().

    Returns one of: 'paid', 'already_paid', 'mismatch', 'failed', 'pending'.
    Safe to call repeatedly and concurrently: the paid update is conditional on the
    order not already being paid, so only one caller performs the transition.
    """
    status = (tx.get("status") or "").lower()
    tx_reference = tx.get("reference") or ""

    if order.get("payment_status") == "paid":
        if status == "success" and tx_reference != order.get("payment_reference"):
            current_app.logger.error(
                "DUPLICATE PAYMENT: order %s already paid under %s, but %s also succeeded. "
                "Refund the extra payment in the Paystack dashboard.",
                order["id"], order.get("payment_reference"), tx_reference,
            )
        return "already_paid"

    if status == "success":
        paid_amount = tx.get("amount")
        paid_currency = (tx.get("currency") or "").upper()
        expected_amount = order.get("payment_amount")
        expected_currency = (order.get("payment_currency") or "").upper()
        if paid_amount != expected_amount or paid_currency != expected_currency:
            current_app.logger.error(
                "PAYMENT MISMATCH on order %s: expected %s %s, Paystack reports %s %s (ref %s). "
                "Order NOT marked paid.",
                order["id"], expected_amount, expected_currency, paid_amount, paid_currency, tx_reference,
            )
            return "mismatch"

        update = {
            "payment_status": "paid",
            "payment_reference": tx_reference,
            "paid_at": tx.get("paid_at") or None,
            "paystack_transaction_id": tx.get("id"),
            "payment_channel": tx.get("channel"),
            "payment_gateway_response": tx.get("gateway_response"),
        }
        # Only advance fulfilment status if the admin hasn't already moved it.
        if order.get("status") == "pending":
            update["status"] = "confirmed"
        result = (
            db.table("orders").update(update)
            .eq("id", order["id"]).neq("payment_status", "paid")
            .execute()
        )
        return "paid" if result.data else "already_paid"

    if status in FAILED_STATUSES:
        # A failed attempt can still be retried to success on the same reference,
        # so this is deliberately overwritable by a later 'paid' update.
        db.table("orders").update({
            "payment_status": "failed",
            "payment_gateway_response": tx.get("gateway_response") or status,
        }).eq("id", order["id"]).neq("payment_status", "paid").execute()
        return "failed"

    return "pending"  # ongoing / pending / processing / queued
