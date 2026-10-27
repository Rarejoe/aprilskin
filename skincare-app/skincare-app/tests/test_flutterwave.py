"""Flutterwave integration tests. No network: Flutterwave's API and Supabase are faked.

Run from the skincare-app folder:   python -m unittest discover -s tests -v
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import types
import unittest
from unittest import mock

# Allow running without supabase / python-dotenv installed (they're not used by these tests).
for _name, _attrs in {"supabase": {"create_client": None, "Client": object},
                      "dotenv": {"load_dotenv": lambda *a, **k: None}}.items():
    try:
        # (the repo's own ./supabase folder can look like a namespace package, so check an attribute)
        _real = __import__(_name)
        getattr(_real, next(iter(_attrs)))
    except (ImportError, AttributeError):
        _m = types.ModuleType(_name)
        _m.__dict__.update(_attrs)
        sys.modules[_name] = _m

SECRET = "FLWSECK_TEST-unit-test-secret-X"
HASH = "my-webhook-secret-hash"
ORDER1 = "11111111-1111-4111-8111-111111111111"
os.environ["FLUTTERWAVE_SECRET_KEY"] = SECRET

from app import create_app, flutterwave  # noqa: E402
from app.config import Config  # noqa: E402


# ---------------------------------------------------------------- fakes
class Res:
    def __init__(self, data):
        self.data = data

    def __bool__(self):
        return True


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.op, self.payload, self.filters, self.single = "select", None, [], False

    def select(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self
    def eq(self, c, v): self.filters.append((c, v, True)); return self
    def neq(self, c, v): self.filters.append((c, v, False)); return self
    def maybe_single(self): self.single = True; return self
    def insert(self, p): self.op, self.payload = "insert", p; return self
    def update(self, p): self.op, self.payload = "update", p; return self

    def _match(self, row):
        return all((row.get(c) == v) == eq for c, v, eq in self.filters)

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "insert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for it in items:
                rid = ORDER1 if (self.table == "orders" and not rows) else f"{self.table}-{len(rows) + 1}"
                row = {"id": rid, "status": "pending",
                       "payment_status": "unpaid", **it}
                rows.append(row); out.append(dict(row))
            return Res(out)
        hits = [r for r in rows if self._match(r)]
        if self.op == "update":
            for r in hits:
                r.update(self.payload)
            return Res([dict(r) for r in hits])
        if self.single:
            return Res(dict(hits[0]) if hits else None)
        return Res([dict(r) for r in hits])


class FakeDB:
    def __init__(self):
        self.tables = {}

    def table(self, name): return Query(self, name)

    def order(self, i=0): return self.tables["orders"][i]


class FakeFlutterwaveAPI:
    """Stands in for requests.request against api.flutterwave.com."""

    def __init__(self):
        self.calls, self.verify_data, self.fail_with = [], {}, None
        self.init_status, self.init_body = 200, None

    def __call__(self, method, url, headers=None, timeout=None, json=None, params=None, **_):
        self.calls.append((method, url, headers, json))
        if self.fail_with:
            raise self.fail_with
        if url.endswith("/payments"):
            body = self.init_body or {"status": "success", "message": "Hosted Link", "data": {
                "link": "https://checkout.flutterwave.com/v3/hosted/pay/abc123"}}
            return Resp(self.init_status, body)
        ref = (params or {}).get("tx_ref")
        if ref in self.verify_data:
            return Resp(200, {"status": "success", "data": self.verify_data[ref]})
        return Resp(400, {"status": "error", "message": "No transaction was found for this id"})


class Resp:
    def __init__(self, code, body): self.status_code, self._b = code, body
    def json(self): return self._b


def b64sign(raw: bytes, key=HASH) -> str:
    return base64.b64encode(hmac.new(key.encode(), raw, hashlib.sha256).digest()).decode()


# ---------------------------------------------------------------- tests
class Base(unittest.TestCase):
    def setUp(self):
        class Cfg(Config):
            TESTING = True
            FLUTTERWAVE_SECRET_KEY = SECRET
            FLUTTERWAVE_SECRET_HASH = HASH
            FLUTTERWAVE_CURRENCY = "USD"
            FLUTTERWAVE_USD_EXCHANGE_RATE = ""
            APP_BASE_URL = "https://shop.example.com"
            SECRET_KEY = "t"
        self.Cfg = Cfg
        self.app = create_app(Cfg)
        self.db = FakeDB()
        self.db.tables["packages"] = [{
            "id": "pkg1", "name": "Glow Set", "model_price": 49.99, "is_active": True,
            "package_items": [{"quantity": 2, "products": {
                "id": "p1", "name": "Serum", "retail_price": 30.0, "image_url": None}}]}]
        self.db.tables["models"] = [{"id": "m1", "display_name": "Ada"}]
        self.api = FakeFlutterwaveAPI()
        self.patches = [
            mock.patch("app.main.routes.get_service_client", lambda: self.db),
            mock.patch("app.admin.routes.get_service_client", lambda: self.db),
            mock.patch("app.flutterwave.requests.request", self.api),
        ]
        for p in self.patches: p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.client = self.app.test_client()
        self.login()

    def login(self, model_id="m1", email="ada@gmail.com"):
        with self.client.session_transaction() as s:
            s["model_id"], s["model_name"], s["model_email"] = model_id, "Ada", email

    FORM = {"full_name": "Ada L", "address_line1": "1 Road", "city": "Lagos",
            "postal_code": "100001", "country": "Nigeria"}

    def place(self, **extra):
        return self.client.post("/checkout/pkg1", data={**self.FORM, **extra})

    def tx(self, ref, **over):
        d = {"id": 987, "tx_ref": ref, "status": "successful", "amount": 49.99,
             "currency": "USD", "created_at": "2026-10-17T10:00:00.000Z", "payment_type": "card",
             "processor_response": "Approved"}
        d.update(over)
        self.api.verify_data[ref] = d
        return d

    def place_and_get_ref(self):
        r = self.place()
        self.assertEqual(r.status_code, 302)
        return r, self.db.order()["payment_reference"]


class CheckoutInit(Base):
    def test_creates_unpaid_order_and_redirects_to_flutterwave(self):
        r, ref = self.place_and_get_ref()
        self.assertEqual(r.headers["Location"], "https://checkout.flutterwave.com/v3/hosted/pay/abc123")
        o = self.db.order()
        self.assertEqual((o["payment_status"], o["status"], o["payment_method"]),
                         ("unpaid", "pending", "flutterwave"))
        self.assertEqual((o["payment_amount"], o["payment_currency"]), (4999, "USD"))
        self.assertTrue(flutterwave.REFERENCE_RE.match(ref))
        self.assertIn(ORDER1.replace("-", ""), ref)
        method, url, headers, body = self.api.calls[0]
        self.assertEqual((method, url), ("POST", "https://api.flutterwave.com/v3/payments"))
        self.assertEqual(headers["Authorization"], f"Bearer {SECRET}")
        self.assertEqual(body["amount"], 49.99)             # major units for Flutterwave
        self.assertEqual(body["currency"], "USD")
        self.assertEqual(body["tx_ref"], ref)
        self.assertEqual(body["customer"]["email"], "ada@gmail.com")
        self.assertEqual(body["redirect_url"], "https://shop.example.com/payment/callback")
        self.assertEqual(body["meta"]["order_id"], ORDER1)
        self.assertEqual(len(self.db.tables["order_items"]), 1)

    def test_tampered_price_in_form_is_ignored(self):
        self.place(total="1", amount="1", subtotal="1")
        self.assertEqual(self.db.order()["total"], 49.99)
        self.assertEqual(self.api.calls[0][3]["amount"], 49.99)

    def test_missing_delivery_field_creates_nothing(self):
        r = self.client.post("/checkout/pkg1", data={"full_name": "x"})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("orders", self.db.tables)
        self.assertEqual(self.api.calls, [])

    def test_not_configured_creates_no_order(self):
        self.app.config["FLUTTERWAVE_SECRET_KEY"] = ""
        self.place()
        self.assertNotIn("orders", self.db.tables)

    def test_non_usd_without_rate_refuses(self):
        self.app.config["FLUTTERWAVE_CURRENCY"] = "NGN"
        self.place()
        self.assertNotIn("orders", self.db.tables)
        self.assertEqual(self.api.calls, [])

    def test_non_usd_with_rate_converts(self):
        self.app.config.update(FLUTTERWAVE_CURRENCY="NGN", FLUTTERWAVE_USD_EXCHANGE_RATE="1500")
        self.place()
        o = self.db.order()
        self.assertEqual((o["payment_currency"], o["payment_amount"]), ("NGN", 7498500))  # 49.99*1500*100
        self.assertEqual(self.api.calls[0][3]["amount"], 74985.0)

    def test_initialize_failure_marks_failed_and_no_redirect_to_gateway(self):
        self.api.init_status, self.api.init_body = 400, {"status": "error", "message": "Invalid currency"}
        r = self.place()
        self.assertEqual(self.db.order()["payment_status"], "failed")
        self.assertNotIn("flutterwave.com", r.headers["Location"])

    def test_bad_checkout_url_from_api_rejected(self):
        self.api.init_body = {"status": "success", "data": {"link": "https://evil.example/x"}}
        r = self.place()
        self.assertNotIn("evil", r.headers["Location"])
        self.assertEqual(self.db.order()["payment_status"], "failed")

    def test_network_error_handled(self):
        import requests
        self.api.fail_with = requests.ConnectionError("boom")
        r = self.place()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.db.order()["payment_status"], "failed")


class Callback(Base):
    def cb(self, ref, status="successful"):
        return self.client.get(f"/payment/callback?status={status}&tx_ref={ref}&transaction_id=987")

    def test_success_marks_paid_and_confirms(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref)
        r = self.cb(ref)
        self.assertIn(f"/order/{ORDER1}/success", r.headers["Location"])
        o = self.db.order()
        self.assertEqual((o["payment_status"], o["status"], o["payment_channel"]), ("paid", "confirmed", "card"))
        self.assertEqual(o["gateway_transaction_id"], 987)

    def test_callback_is_idempotent(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref)
        for _ in range(3):
            self.cb(ref)
        self.assertEqual(self.db.order()["payment_status"], "paid")

    def test_query_string_cannot_fake_success(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref, status="failed")
        self.cb(ref, status="successful")
        self.assertEqual(self.db.order()["payment_status"], "failed")

    def test_no_gateway_record_means_not_paid(self):
        _, ref = self.place_and_get_ref()          # nothing verifiable at Flutterwave
        self.cb(ref, status="successful")
        self.assertEqual(self.db.order()["payment_status"], "unpaid")

    def test_unknown_reference_does_not_touch_orders(self):
        _, ref = self.place_and_get_ref()
        self.cb("AS-" + "0" * 32 + "-" + "0" * 12)
        self.assertEqual(self.db.order()["payment_status"], "unpaid")

    def test_garbage_reference_never_calls_flutterwave(self):
        n = len(self.api.calls)
        self.client.get("/payment/callback?tx_ref=../../etc/passwd")
        self.assertEqual(len(self.api.calls), n)

    def test_verify_returning_a_different_tx_ref_is_rejected(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref, tx_ref="AS-" + "1" * 32 + "-" + "2" * 12)
        self.cb(ref)
        self.assertEqual(self.db.order()["payment_status"], "unpaid")

    def test_amount_mismatch_not_paid(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref, amount=1.00)
        self.cb(ref)
        o = self.db.order()
        self.assertEqual((o["payment_status"], o["status"]), ("unpaid", "pending"))

    def test_currency_mismatch_not_paid(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref, currency="NGN")
        self.cb(ref)
        self.assertEqual(self.db.order()["payment_status"], "unpaid")

    def test_failed_then_success_on_same_reference(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref, status="failed")
        self.cb(ref)
        self.assertEqual(self.db.order()["payment_status"], "failed")
        self.tx(ref)
        self.cb(ref)
        self.assertEqual(self.db.order()["payment_status"], "paid")

    def test_pending_stays_unpaid(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref, status="pending")
        self.cb(ref)
        self.assertEqual(self.db.order()["payment_status"], "unpaid")

    def test_admin_cancelled_status_preserved_when_paid(self):
        _, ref = self.place_and_get_ref()
        self.db.order()["status"] = "cancelled"
        self.tx(ref)
        self.cb(ref)
        o = self.db.order()
        self.assertEqual((o["payment_status"], o["status"]), ("paid", "cancelled"))

    def test_late_payment_on_old_reference_matches_by_embedded_order_id(self):
        _, ref1 = self.place_and_get_ref()
        self.client.post(f"/order/{ORDER1}/pay")          # attempt 2 -> new reference
        ref2 = self.db.order()["payment_reference"]
        self.assertNotEqual(ref1, ref2)
        self.tx(ref1)                                      # attempt 1 succeeds late
        self.cb(ref1)
        o = self.db.order()
        self.assertEqual((o["payment_status"], o["payment_reference"]), ("paid", ref1))


class Webhook(Base):
    def post(self, body: dict, headers=None, raw=None):
        raw = raw if raw is not None else json.dumps(body).encode()
        h = {"verif-hash": HASH} if headers is None else headers
        return self.client.post("/webhooks/flutterwave", data=raw,
                                content_type="application/json", headers=h)

    def ok_body(self, ref):
        return {"event": "charge.completed", "data": {"tx_ref": ref, "status": "successful"}}

    def test_bad_or_missing_hash_rejected(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref)
        body = self.ok_body(ref)
        self.assertEqual(self.post(body, headers={"verif-hash": "wrong"}).status_code, 401)
        self.assertEqual(self.post(body, headers={}).status_code, 401)
        self.assertEqual(self.post(body, headers={"flutterwave-signature": "bogus"}).status_code, 401)
        self.assertEqual(self.db.order()["payment_status"], "unpaid")

    def test_webhook_disabled_when_no_hash_configured(self):
        self.app.config["FLUTTERWAVE_SECRET_HASH"] = ""
        _, ref = self.place_and_get_ref()
        self.tx(ref)
        self.assertEqual(self.post(self.ok_body(ref), headers={"verif-hash": ""}).status_code, 401)
        self.assertEqual(self.post(self.ok_body(ref)).status_code, 401)

    def test_valid_charge_completed_marks_paid_and_is_idempotent(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref)
        for _ in range(2):
            self.assertEqual(self.post(self.ok_body(ref)).status_code, 200)
        self.assertEqual(self.db.order()["payment_status"], "paid")

    def test_hmac_signature_header_accepted(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref)
        raw = json.dumps(self.ok_body(ref)).encode()
        r = self.post(None, headers={"flutterwave-signature": b64sign(raw)}, raw=raw)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.db.order()["payment_status"], "paid")

    def test_forged_payload_is_reverified_with_flutterwave(self):
        # Even an authenticated event is checked against the verify API.
        _, ref = self.place_and_get_ref()
        self.tx(ref, status="failed")
        self.post({"event": "charge.completed", "data": {"tx_ref": ref, "status": "successful", "amount": 49.99}})
        self.assertEqual(self.db.order()["payment_status"], "failed")

    def test_other_events_acknowledged(self):
        self.assertEqual(self.post({"event": "transfer.completed", "data": {}}).status_code, 200)

    def test_foreign_reference_acknowledged_without_api_call(self):
        n = len(self.api.calls)
        r = self.post({"event": "charge.completed", "data": {"tx_ref": "someone-elses-ref"}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.api.calls), n)

    def test_unknown_order_acknowledged(self):
        ref = "AS-" + "a" * 32 + "-" + "b" * 12
        self.tx(ref)
        self.assertEqual(self.post(self.ok_body(ref)).status_code, 200)

    def test_transient_verify_error_returns_500_so_flutterwave_retries(self):
        import requests
        _, ref = self.place_and_get_ref()
        self.api.fail_with = requests.ConnectionError("down")
        self.assertEqual(self.post(self.ok_body(ref)).status_code, 500)

    def test_malformed_json(self):
        r = self.post(None, raw=b"{not json")
        self.assertEqual(r.status_code, 400)

    def test_signature_helper(self):
        with self.app.app_context():
            v = flutterwave.valid_webhook_signature
            self.assertTrue(v(b"x", HASH))
            self.assertTrue(v(b"x", None, b64sign(b"x")))
            self.assertFalse(v(b"x", None, None))
            self.assertFalse(v(b"y", None, b64sign(b"x")))
            self.assertFalse(v(b"x", "nope"))


class PagesAndAccess(Base):
    def test_success_page_states_and_no_secret_leak(self):
        _, ref = self.place_and_get_ref()
        self.db.order()["order_items"] = []
        pages = []
        self.tx(ref, status="pending")
        r = self.client.get(f"/order/{ORDER1}/success"); pages.append(r.get_data(as_text=True))
        self.assertIn("Awaiting payment", pages[-1])
        self.tx(ref, status="failed")
        r = self.client.get(f"/order/{ORDER1}/success"); pages.append(r.get_data(as_text=True))
        self.assertIn("Payment not completed", pages[-1])
        self.tx(ref)
        r = self.client.get(f"/order/{ORDER1}/success"); pages.append(r.get_data(as_text=True))
        self.assertIn("Thank you!", pages[-1]); self.assertIn("payment: paid", pages[-1])
        r = self.client.get("/checkout/pkg1"); pages.append(r.get_data(as_text=True))
        self.assertIn("Pay $49.99 Securely", pages[-1])
        self.assertIn("Flutterwave", pages[-1])
        for html in pages:
            self.assertNotIn(SECRET, html); self.assertNotIn(HASH, html); self.assertNotIn("FLWSECK", html)

    def test_success_page_self_heals_when_webhook_is_late(self):
        _, ref = self.place_and_get_ref()
        self.db.order()["order_items"] = []
        self.tx(ref)
        self.client.get(f"/order/{ORDER1}/success")
        self.assertEqual(self.db.order()["payment_status"], "paid")

    def test_other_model_cannot_view_or_retry(self):
        self.place_and_get_ref()
        self.login(model_id="m2")
        self.assertEqual(self.client.get(f"/order/{ORDER1}/success").status_code, 302)
        n = len(self.api.calls)
        self.client.post(f"/order/{ORDER1}/pay")
        self.assertEqual(len(self.api.calls), n)

    def test_retry_blocked_if_already_paid_upstream(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref)                                   # paid, webhook not yet processed
        self.client.post(f"/order/{ORDER1}/pay")
        o = self.db.order()
        self.assertEqual((o["payment_status"], o["payment_reference"]), ("paid", ref))
        self.assertEqual(len([c for c in self.api.calls if c[1].endswith("/payments")]), 1)

    def test_admin_cannot_ship_unpaid_online_order(self):
        self.place_and_get_ref()
        with self.client.session_transaction() as s:
            s["admin_id"] = "a1"
        self.client.post(f"/admin/orders/{ORDER1}/status", data={"status": "shipped"})
        self.assertEqual(self.db.order()["status"], "pending")
        self.client.post(f"/admin/orders/{ORDER1}/status", data={"status": "cancelled"})
        self.assertEqual(self.db.order()["status"], "cancelled")
        self.db.order().update(status="pending", payment_status="paid")
        self.client.post(f"/admin/orders/{ORDER1}/status", data={"status": "shipped"})
        self.assertEqual(self.db.order()["status"], "shipped")

    def test_legacy_paystack_orders_still_cannot_be_shipped_unpaid(self):
        self.place_and_get_ref()
        self.db.order().update(payment_method="paystack")
        with self.client.session_transaction() as s:
            s["admin_id"] = "a1"
        self.client.post(f"/admin/orders/{ORDER1}/status", data={"status": "shipped"})
        self.assertEqual(self.db.order()["status"], "pending")


if __name__ == "__main__":
    unittest.main()
