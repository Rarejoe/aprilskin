"""
Flutterwave integration (server-side only, Standard / hosted checkout, API v3).

The secret key is read from the environment via app config and is only ever
used from this module, in an `Authorization` header sent to api.flutterwave.com.
It is never rendered into a template or returned to the browser.

Flow:
  1. initialize_transaction()  -> Flutterwave returns a hosted checkout link
  2. the customer pays on Flutterwave's page and is redirected back to our callback
  3. verify_transaction()      -> we ask Flutterwave (server-to-server) for the truth
  4. apply_verified_transaction() -> only a verified, amount- and currency-matching
     success marks the order paid. Callback and webhook both use this function,
     and it is idempotent, so whichever arrives first wins and the other is a no-op.
"""
import base64
import hashlib
import hmac
import re
import uuid
from decimal import Decimal, ROUND_HALF_UP

import requests
from flask import current_app

API_BASE = "https://api.flutterwave.com/v3"
CHECKOUT_HOST_PREFIX = "https://checkout.flutterwave.com/"
REQUEST_TIMEOUT = (5, 20)  # (connect, read) seconds

# Our own transaction references look like AS-<32 hex order id>-<12 hex random>.
REFERENCE_RE = re.compile(r"^AS-([0-9a-f]{32})-[0-9a-f]{12}$")

# payment_method values that mean "paid online through a gateway". "paystack" is kept so
# orders created before the switch to Flutterwave stay protected (never shippable unpaid).
ONLINE_METHODS = ("flutterwave", "paystack")

# Transaction statuses returned by Flutterwave's verify endpoint (compared lower-case).
SUCCESS_STATUSES = {"successful", "success"}
FAILED_STATUSES = {"failed", "cancelled", "canceled"}


class FlutterwaveError(Exception):
    """Raised for any Flutterwave/API problem. `retryable` is True for network/5xx/429."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class FlutterwaveConfigError(FlutterwaveError):
    """Raised when Flutterwave isn't configured (missing key, missing exchange rate...)."""


# ---------------------------------------------------------------------
# Config / helpers
# ---------------------------------------------------------------------
def _secret_key() -> str:
    key = (current_app.config.get("FLUTTERWAVE_SECRET_KEY") or "").strip()
    if not key:
        raise FlutterwaveConfigError("Online payments are not configured yet.")
    return key


def is_configured() -> bool:
    return bool((current_app.config.get("FLUTTERWAVE_SECRET_KEY") or "").strip())


def is_valid_reference(reference: str) -> bool:
    return bool(reference) and REFERENCE_RE.match(reference) is not None


def new_reference(order_id: str) -> str:
    """Unique, unguessable transaction reference (tx_ref) that embeds the order id, so a
    late payment on an earlier attempt can still be matched to its order."""
    return f"AS-{uuid.UUID(str(order_id)).hex}-{uuid.uuid4().hex[:12]}"


def order_id_from_reference(reference: str) -> str | None:
    m = REFERENCE_RE.match(reference or "")
    return str(uuid.UUID(m.group(1))) if m else None


def to_subunit(amount: Decimal) -> int:
    """Major units (dollars/naira) -> subunits (cents/kobo), rounded half-up.
    We store amounts as subunits in the database."""
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def charge_for(total) -> tuple[str, int]:
    """Return (currency, amount_in_subunits) to charge for an order total.

    Store prices are in USD. If FLUTTERWAVE_CURRENCY is not USD,
    FLUTTERWAVE_USD_EXCHANGE_RATE must be set, otherwise we refuse to charge
    (we never guess a conversion rate).
    """
    currency = (current_app.config.get("FLUTTERWAVE_CURRENCY") or "USD").strip().upper()
    amount = Decimal(str(total))
    if currency != "USD":
        raw_rate = str(current_app.config.get("FLUTTERWAVE_USD_EXCHANGE_RATE") or "").strip()
        try:
            rate = Decimal(raw_rate)
        except Exception:
            rate = Decimal(0)
        if rate <= 0:
            raise FlutterwaveConfigError(
                "Payment currency is set to %s but FLUTTERWAVE_USD_EXCHANGE_RATE is missing." % currency
            )
        amount = amount * rate
    subunit = to_subunit(amount)
    if subunit <= 0:
        raise FlutterwaveError("Order total must be greater than zero.")
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
# Flutterwave API
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
        current_app.logger.warning("Flutterwave request failed (%s %s): %s", method, path, type(exc).__name__)
        raise FlutterwaveError("Could not reach Flutterwave.", retryable=True) from exc

    try:
        body = resp.json()
    except ValueError:
        body = {}

    if resp.status_code >= 400 or body.get("status") != "success":
        message = body.get("message") or f"Flutterwave returned HTTP {resp.status_code}."
        retryable = resp.status_code >= 500 or resp.status_code == 429
        current_app.logger.warning("Flutterwave API error (%s %s): %s %s", method, path, resp.status_code, message)
        raise FlutterwaveError(message, retryable=retryable)
    return body.get("data") or {}


def initialize_transaction(*, email: str, name: str, amount: int, currency: str,
                           reference: str, callback: str, metadata: dict) -> dict:
    """Create a hosted payment and return {'link': <checkout url>}.

    `amount` is in subunits (as stored); Flutterwave expects major units.
    """
    major = float(Decimal(amount) / 100)
    data = _call("POST", "/payments", json={
        "tx_ref": reference,
        "amount": major,
        "currency": currency,
        "redirect_url": callback,
        "customer": {"email": email, "name": name or email},
        "meta": metadata,
    })
    link = data.get("link") or ""
    if not link.startswith(CHECKOUT_HOST_PREFIX):
        raise FlutterwaveError("Flutterwave returned an unexpected checkout URL.")
    return data


def verify_transaction(reference: str) -> dict:
    """Fetch the authoritative transaction record from Flutterwave by our tx_ref."""
    if not is_valid_reference(reference):
        raise FlutterwaveError("Invalid payment reference.")
    data = _call("GET", "/transactions/verify_by_reference", params={"tx_ref": reference})
    # The record must be for the reference we asked about.
    if data.get("tx_ref") != reference:
        raise FlutterwaveError("Flutterwave returned a different transaction.")
    return data


def valid_webhook_signature(raw_body: bytes, verif_hash: str | None,
                            flw_signature: str | None = None) -> bool:
    """Authenticate a webhook using the secret hash you configure in the Flutterwave
    dashboard. Flutterwave sends it in `verif-hash`; newer accounts may instead send
    `flutterwave-signature` (base64 HMAC-SHA256 of the raw body keyed with the hash)."""
    secret = (current_app.config.get("FLUTTERWAVE_SECRET_HASH") or "").strip()
    if not secret:
        return False
    if verif_hash and hmac.compare_digest(verif_hash.strip(), secret):
        return True
    if flw_signature:
        digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode("ascii")
        return hmac.compare_digest(expected, flw_signature.strip())
    return False


# ---------------------------------------------------------------------
# Settling an order from a verified transaction
# ---------------------------------------------------------------------
def apply_verified_transaction(db, order: dict, tx: dict) -> str:
    """Update `order` from a transaction fetched via verify_transaction().

    Returns one of: 'paid', 'already_paid', 'mismatch', 'failed', 'pending'.
    Safe to call repeatedly and concurrently: the paid update is conditional on the
    order not already being paid, so only one caller performs the transition.
    """
    status = str(tx.get("status") or "").lower()
    tx_reference = tx.get("tx_ref") or ""

    if order.get("payment_status") == "paid":
        if status in SUCCESS_STATUSES and tx_reference != order.get("payment_reference"):
            current_app.logger.error(
                "DUPLICATE PAYMENT: order %s already paid under %s, but %s also succeeded. "
                "Refund the extra payment in the Flutterwave dashboard.",
                order["id"], order.get("payment_reference"), tx_reference,
            )
        return "already_paid"

    if status in SUCCESS_STATUSES:
        try:
            paid_amount = to_subunit(Decimal(str(tx.get("amount"))))
        except Exception:
            paid_amount = None
        paid_currency = str(tx.get("currency") or "").upper()
        expected_amount = order.get("payment_amount")
        expected_currency = (order.get("payment_currency") or "").upper()
        if paid_amount != expected_amount or paid_currency != expected_currency:
            current_app.logger.error(
                "PAYMENT MISMATCH on order %s: expected %s %s (subunits), Flutterwave reports %s %s (ref %s). "
                "Order NOT marked paid.",
                order["id"], expected_amount, expected_currency, paid_amount, paid_currency, tx_reference,
            )
            return "mismatch"

        update = {
            "payment_status": "paid",
            "payment_reference": tx_reference,
            "paid_at": tx.get("created_at") or None,
            "gateway_transaction_id": tx.get("id"),
            "payment_channel": tx.get("payment_type"),
            "payment_gateway_response": tx.get("processor_response"),
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
            "payment_gateway_response": tx.get("processor_response") or status,
        }).eq("id", order["id"]).neq("payment_status", "paid").execute()
        return "failed"

    return "pending"  # still processing
