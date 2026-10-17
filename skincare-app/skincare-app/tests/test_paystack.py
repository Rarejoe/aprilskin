"""Paystack integration tests. No network: Paystack's API and Supabase are faked.

Run from the skincare-app folder:   python -m unittest discover -s tests -v
"""
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

SECRET = "sk_test_unit_test_secret"
os.environ["PAYSTACK_SECRET_KEY"] = SECRET

from app import create_app, paystack  # noqa: E402
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
                row = {"id": f"{self.table}-{len(rows) + 1}", "status": "pending",
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


class FakePaystackAPI:
    """Stands in for requests.request against api.paystack.co."""

    def __init__(self):
        self.calls, self.verify_data, self.fail_with = [], {}, None
        self.init_status, self.init_body = 200, None

    def __call__(self, method, url, headers=None, timeout=None, json=None, **_):
        self.calls.append((method, url, headers, json))
        if self.fail_with:
            raise self.fail_with
        if url.endswith("/transaction/initialize"):
            body = self.init_body or {"status": True, "data": {
                "authorization_url": "https://checkout.paystack.com/abc123",
                "access_code": "abc123", "reference": json["reference"]}}
            return Resp(self.init_status, body)
        ref = url.rsplit("/", 1)[-1]
        if ref in self.verify_data:
            return Resp(200, {"status": True, "data": self.verify_data[ref]})
        return Resp(404, {"status": False, "message": "Transaction reference not found"})


class Resp:
    def __init__(self, code, body): self.status_code, self._b = code, body
    def json(self): return self._b


def sign(raw: bytes, key=SECRET) -> str:
    return hmac.new(key.encode(), raw, hashlib.sha512).hexdigest()


# ---------------------------------------------------------------- tests
class Base(unittest.TestCase):
    def setUp(self):
        class Cfg(Config):
            TESTING = True
            PAYSTACK_SECRET_KEY = SECRET
            PAYSTACK_CURRENCY = "USD"
            PAYSTACK_USD_EXCHANGE_RATE = ""
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
        self.api = FakePaystackAPI()
        self.patches = [
            mock.patch("app.main.routes.get_service_client", lambda: self.db),
            mock.patch("app.admin.routes.get_service_client", lambda: self.db),
            mock.patch("app.paystack.requests.request", self.api),
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
        d = {"id": 987, "status": "success", "reference": ref, "amount": 4999,
             "currency": "USD", "paid_at": "2026-10-17T10:00:00.000Z", "channel": "card",
             "gateway_response": "Successful", "metadata": {"order_id": "orders-1"}}
        d.update(over)
        self.api.verify_data[ref] = d
        return d

    def place_and_get_ref(self):
        r = self.place()
        self.assertEqual(r.status_code, 302)
        return r, self.db.order()["payment_reference"]


class CheckoutInit(Base):
    def test_creates_unpaid_order_and_redirects_to_paystack(self):
        r, ref = self.place_and_get_ref()
        self.assertEqual(r.headers["Location"], "https://checkout.paystack.com/abc123")
        o = self.db.order()
        self.assertEqual((o["payment_status"], o["status"], o["payment_method"]),
                         ("unpaid", "pending", "paystack"))
        self.assertEqual((o["payment_amount"], o["payment_currency"]), (4999, "USD"))
        self.assertTrue(ref.startswith("AS-"))
        _, url, headers, body = self.api.calls[0]
        self.assertEqual(headers["Authorization"], f"Bearer {SECRET}")
        self.assertEqual(body["amount"], 4999)
        self.assertIsInstance(body["amount"], int)
        self.assertEqual(body["email"], "ada@gmail.com")
        self.assertEqual(body["callback_url"], "https://shop.example.com/payment/callback")
        self.assertEqual(body["metadata"]["order_id"], "orders-1")
        self.assertEqual(len(self.db.tables["order_items"]), 1)

    def test_tampered_price_in_form_is_ignored(self):
        self.place(total="1", amount="1", subtotal="1")
        self.assertEqual(self.db.order()["total"], 49.99)
        self.assertEqual(self.api.calls[0][3]["amount"], 4999)

    def test_missing_delivery_field_creates_nothing(self):
        r = self.client.post("/checkout/pkg1", data={"full_name": "x"})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("orders", self.db.tables)
        self.assertEqual(self.api.calls, [])

    def test_not_configured_creates_no_order(self):
        self.app.config["PAYSTACK_SECRET_KEY"] = ""
        self.place()
        self.assertNotIn("orders", self.db.tables)

    def test_non_usd_without_rate_refuses(self):
        self.app.config["PAYSTACK_CURRENCY"] = "NGN"
        self.place()
        self.assertNotIn("orders", self.db.tables)
        self.assertEqual(self.api.calls, [])

    def test_non_usd_with_rate_converts(self):
        self.app.config.update(PAYSTACK_CURRENCY="NGN", PAYSTACK_USD_EXCHANGE_RATE="1500")
        self.place()
        o = self.db.order()
        self.assertEqual((o["payment_currency"], o["payment_amount"]), ("NGN", 7498500))  # 49.99*1500*100

    def test_initialize_failure_marks_failed_and_no_redirect_to_paystack(self):
        self.api.init_status, self.api.init_body = 400, {"status": False, "message": "Currency not supported by merchant"}
        r = self.place()
        self.assertEqual(self.db.order()["payment_status"], "failed")
        self.assertNotIn("paystack.com", r.headers["Location"])

    def test_bad_checkout_url_from_api_rejected(self):
        self.api.init_body = {"status": True, "data": {"authorization_url": "https://evil.example/x"}}
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
    def test_success_marks_paid_and_confirms(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref)
        r = self.client.get(f"/payment/callback?reference={ref}")
        self.assertIn("/order/orders-1/success", r.headers["Location"])
        o = self.db.order()
        self.assertEqual((o["payment_status"], o["status"], o["payment_channel"]), ("paid", "confirmed", "card"))
        self.assertEqual(o["paystack_transaction_id"], 987)

    def test_callback_is_idempotent(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref)
        for _ in range(3):
            self.client.get(f"/payment/callback?reference={ref}")
        self.assertEqual(self.db.order()["payment_status"], "paid")

    def test_query_string_cannot_fake_success(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref, status="failed")
        self.client.get(f"/payment/callback?reference={ref}&status=success")
        self.assertEqual(self.db.order()["payment_status"], "failed")

    def test_unknown_reference_does_not_touch_orders(self):
        _, ref = self.place_and_get_ref()
        self.client.get("/payment/callback?reference=AS-doesnotexist00")
        self.assertEqual(self.db.order()["payment_status"], "unpaid")

    def test_garbage_reference_never_calls_paystack(self):
        n = len(self.api.calls)
        self.client.get("/payment/callback?reference=../../etc/passwd")
        self.assertEqual(len(self.api.calls), n)

    def test_amount_mismatch_not_paid(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref, amount=100)
        self.client.get(f"/payment/callback?reference={ref}")
        o = self.db.order()
        self.assertEqual((o["payment_status"], o["status"]), ("unpaid", "pending"))

    def test_currency_mismatch_not_paid(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref, currency="NGN")
        self.client.get(f"/payment/callback?reference={ref}")
        self.assertEqual(self.db.order()["payment_status"], "unpaid")

    def test_abandoned_then_success_on_same_reference(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref, status="abandoned")
        self.client.get(f"/payment/callback?reference={ref}")
        self.assertEqual(self.db.order()["payment_status"], "failed")
        self.tx(ref)
        self.client.get(f"/payment/callback?reference={ref}")
        self.assertEqual(self.db.order()["payment_status"], "paid")

    def test_ongoing_stays_unpaid(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref, status="ongoing")
        self.client.get(f"/payment/callback?reference={ref}")
        self.assertEqual(self.db.order()["payment_status"], "unpaid")

    def test_admin_cancelled_status_preserved_when_paid(self):
        _, ref = self.place_and_get_ref()
        self.db.order()["status"] = "cancelled"
        self.tx(ref)
        self.client.get(f"/payment/callback?reference={ref}")
        o = self.db.order()
        self.assertEqual((o["payment_status"], o["status"]), ("paid", "cancelled"))

    def test_late_payment_on_old_reference_matches_by_metadata(self):
        _, ref1 = self.place_and_get_ref()
        self.client.post("/order/orders-1/pay")          # attempt 2 -> new reference
        ref2 = self.db.order()["payment_reference"]
        self.assertNotEqual(ref1, ref2)
        self.tx(ref1)                                     # attempt 1 succeeds late
        self.client.get(f"/payment/callback?reference={ref1}")
        o = self.db.order()
        self.assertEqual((o["payment_status"], o["payment_reference"]), ("paid", ref1))


class Webhook(Base):
    def post(self, body: dict, signature=None, key=SECRET):
        raw = json.dumps(body).encode()
        return self.client.post("/webhooks/paystack", data=raw, content_type="application/json",
                                headers={"X-Paystack-Signature": signature or sign(raw, key)})

    def test_bad_or_missing_signature_rejected(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref)
        body = {"event": "charge.success", "data": {"reference": ref}}
        self.assertEqual(self.post(body, signature="deadbeef").status_code, 401)
        self.assertEqual(self.post(body, key="sk_test_other").status_code, 401)
        r = self.client.post("/webhooks/paystack", json=body)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.db.order()["payment_status"], "unpaid")

    def test_valid_charge_success_marks_paid_and_is_idempotent(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref)
        body = {"event": "charge.success", "data": {"reference": ref, "status": "success"}}
        for _ in range(2):
            self.assertEqual(self.post(body).status_code, 200)
        self.assertEqual(self.db.order()["payment_status"], "paid")

    def test_forged_payload_is_reverified_with_paystack(self):
        # Even a correctly signed event is checked against the verify API.
        _, ref = self.place_and_get_ref()
        self.tx(ref, status="failed")
        self.post({"event": "charge.success", "data": {"reference": ref, "amount": 4999}})
        self.assertEqual(self.db.order()["payment_status"], "failed")

    def test_other_events_acknowledged(self):
        self.assertEqual(self.post({"event": "transfer.success", "data": {}}).status_code, 200)

    def test_unknown_reference_acknowledged(self):
        self.tx("AS-unknown0001", metadata={})
        r = self.post({"event": "charge.success", "data": {"reference": "AS-unknown0001"}})
        self.assertEqual(r.status_code, 200)

    def test_transient_verify_error_returns_500_so_paystack_retries(self):
        import requests
        _, ref = self.place_and_get_ref()
        self.api.fail_with = requests.ConnectionError("down")
        r = self.post({"event": "charge.success", "data": {"reference": ref}})
        self.assertEqual(r.status_code, 500)

    def test_malformed_json(self):
        raw = b"{not json"
        r = self.client.post("/webhooks/paystack", data=raw,
                             headers={"X-Paystack-Signature": sign(raw)})
        self.assertEqual(r.status_code, 400)

    def test_signature_helper(self):
        with self.app.app_context():
            self.assertTrue(paystack.valid_webhook_signature(b"x", sign(b"x")))
            self.assertTrue(paystack.valid_webhook_signature(b"x", sign(b"x").upper()))
            self.assertFalse(paystack.valid_webhook_signature(b"x", None))
            self.assertFalse(paystack.valid_webhook_signature(b"y", sign(b"x")))


class PagesAndAccess(Base):
    def test_success_page_states_and_no_secret_leak(self):
        _, ref = self.place_and_get_ref()
        self.db.order()["order_items"] = []
        pages = []
        self.tx(ref, status="ongoing")
        r = self.client.get("/order/orders-1/success"); pages.append(r.get_data(as_text=True))
        self.assertIn("Awaiting payment", pages[-1])
        self.tx(ref, status="failed")
        r = self.client.get("/order/orders-1/success"); pages.append(r.get_data(as_text=True))
        self.assertIn("Payment not completed", pages[-1])
        self.tx(ref)
        r = self.client.get("/order/orders-1/success"); pages.append(r.get_data(as_text=True))
        self.assertIn("Thank you!", pages[-1]); self.assertIn("payment: paid", pages[-1])
        r = self.client.get("/checkout/pkg1"); pages.append(r.get_data(as_text=True))
        self.assertIn("Pay $49.99 Securely", pages[-1])
        for html in pages:
            self.assertNotIn(SECRET, html); self.assertNotIn("sk_", html)

    def test_success_page_self_heals_when_webhook_is_late(self):
        _, ref = self.place_and_get_ref()
        self.db.order()["order_items"] = []
        self.tx(ref)
        self.client.get("/order/orders-1/success")
        self.assertEqual(self.db.order()["payment_status"], "paid")

    def test_other_model_cannot_view_or_retry(self):
        self.place_and_get_ref()
        self.login(model_id="m2")
        self.assertEqual(self.client.get("/order/orders-1/success").status_code, 302)
        n = len(self.api.calls)
        self.client.post("/order/orders-1/pay")
        self.assertEqual(len(self.api.calls), n)

    def test_retry_blocked_if_already_paid_upstream(self):
        _, ref = self.place_and_get_ref()
        self.tx(ref)                                   # paid, webhook not yet processed
        self.client.post("/order/orders-1/pay")
        o = self.db.order()
        self.assertEqual((o["payment_status"], o["payment_reference"]), ("paid", ref))
        self.assertFalse([c for c in self.api.calls if c[1].endswith("/initialize")][1:])

    def test_admin_cannot_ship_unpaid_paystack_order(self):
        self.place_and_get_ref()
        with self.client.session_transaction() as s:
            s["admin_id"] = "a1"
        self.client.post("/admin/orders/orders-1/status", data={"status": "shipped"})
        self.assertEqual(self.db.order()["status"], "pending")
        self.client.post("/admin/orders/orders-1/status", data={"status": "cancelled"})
        self.assertEqual(self.db.order()["status"], "cancelled")
        self.db.order().update(status="pending", payment_status="paid")
        self.client.post("/admin/orders/orders-1/status", data={"status": "shipped"})
        self.assertEqual(self.db.order()["status"], "shipped")


if __name__ == "__main__":
    unittest.main()
