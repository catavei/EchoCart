import asyncio
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import httpx

from backend.config import LiveCredentialsRefused, Settings
from backend.paypal_client import PaymentLog, PayPalClient, PayPalError, first_capture_id


class FakePayPal:
    """Records requests and answers like the sandbox does in PayPal's docs."""

    def __init__(self, order_status="COMPLETED"):
        self.calls: list[tuple[str, str, dict]] = []
        self.order_status = order_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content and path != "/v1/oauth2/token" else {}
        self.calls.append((request.method, path, body))
        if path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "A21-test", "expires_in": 32400})
        assert request.headers["Authorization"] == "Bearer A21-test"
        assert request.headers["PayPal-Request-Id"]
        if path == "/v3/vault/setup-tokens":
            return httpx.Response(201, json={"id": "ST1", "status": "PAYER_ACTION_REQUIRED", "links": [
                {"rel": "approve", "href": "https://www.sandbox.paypal.com/agreements/approve?approval_session_id=X"}]})
        if path == "/v3/vault/payment-tokens":
            return httpx.Response(201, json={"id": "VAULT1", "customer": {"id": "CUST1"}})
        if path == "/v2/checkout/orders":
            captures = [{"id": "CAP1", "status": "COMPLETED"}] if self.order_status == "COMPLETED" else []
            return httpx.Response(201, json={"id": "ORDER1", "status": self.order_status,
                                             "purchase_units": [{"payments": {"captures": captures}}]})
        if path == "/v2/checkout/orders/ORDER1/capture":
            return httpx.Response(201, json={"id": "ORDER1", "status": "COMPLETED",
                                             "purchase_units": [{"payments": {"captures": [{"id": "CAP1"}]}}]})
        if path == "/v2/payments/captures/CAP1/refund":
            return httpx.Response(201, json={"id": "REF1", "status": "COMPLETED"})
        if path == "/v2/payments/captures/BAD/refund":
            return httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY"})
        return httpx.Response(404, json={})


class PayPalClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log_path = Path(self.tmp.name) / "payments.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, fake):
        return PayPalClient("id", "secret", PaymentLog(self.log_path), transport=httpx.MockTransport(fake))

    def run_flow(self, fake):
        async def flow():
            pp = self.client(fake)
            try:
                st = await pp.create_setup_token("http://localhost/ok", "http://localhost/cancel")
                tok = await pp.create_payment_token(st["id"])
                order = await pp.create_order_with_vault(tok["vault_id"], Decimal("9.85"), "AA batteries", "ref-1")
                if order["status"] != "COMPLETED":
                    order = await pp.capture_order(order["id"])
                refund = await pp.refund_capture(first_capture_id(order), order["id"])
                return st, tok, order, refund
            finally:
                await pp.aclose()
        return asyncio.run(flow())

    def test_vault_flow_and_audit_log(self):
        fake = FakePayPal()
        st, tok, order, refund = self.run_flow(fake)
        self.assertIn("approve", st["approve_url"])
        self.assertEqual(tok["vault_id"], "VAULT1")
        self.assertEqual(refund["status"], "COMPLETED")
        order_req = next(b for m, p, b in fake.calls if p == "/v2/checkout/orders")
        self.assertEqual(order_req["payment_source"], {"paypal": {"vault_id": "VAULT1"}})
        self.assertEqual(order_req["purchase_units"][0]["amount"], {"currency_code": "EUR", "value": "9.85"})
        entries = [json.loads(l) for l in self.log_path.read_text().splitlines()]
        self.assertEqual([e["action"] for e in entries], ["create", "refund"])
        for e in entries:
            self.assertTrue(e["ts"])
            self.assertEqual(e["order_id"], "ORDER1")

    def test_explicit_capture_when_not_completed(self):
        fake = FakePayPal(order_status="APPROVED")
        self.run_flow(fake)
        actions = [json.loads(l)["action"] for l in self.log_path.read_text().splitlines()]
        self.assertEqual(actions, ["create", "capture", "refund"])

    def test_errors_surface(self):
        async def bad():
            pp = self.client(FakePayPal())
            try:
                await pp.refund_capture("BAD", "ORDER1")
            finally:
                await pp.aclose()
        with self.assertRaises(PayPalError) as ctx:
            asyncio.run(bad())
        self.assertEqual(ctx.exception.status, 422)


class SandboxOnly(unittest.TestCase):
    def test_client_refuses_live_url(self):
        with self.assertRaises(LiveCredentialsRefused):
            PayPalClient("id", "secret", PaymentLog(Path("unused")), api_base="https://api-m.paypal.com")

    def test_settings_refuse_live_url(self):
        s = Settings("id", "secret", "https://api-m.paypal.com", Decimal("0.201"), "offline",
                     "", "m", "low", "http://localhost:8000", Path("."))
        with self.assertRaises(LiveCredentialsRefused):
            s.assert_sandbox()


if __name__ == "__main__":
    unittest.main()
