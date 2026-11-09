"""Conversation flows end to end: offline parser, fake PayPal over httpx."""
import asyncio
import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.cart import Catalog
from backend.dialog import Conversation, Deps
from backend.llm import OfflineCartParser
from backend.paypal_client import PaymentLog, PayPalClient
from backend.store import Store
from tests.test_paypal_mock import FakePayPal

CATALOG = Catalog.load()
TODAY = date(2026, 11, 6)


class DialogBase(unittest.TestCase):
    order_status = "COMPLETED"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.fake = FakePayPal(order_status=self.order_status)
        self.paypal = PayPalClient("id", "secret", PaymentLog(data / "payments.jsonl"),
                                   transport=httpx.MockTransport(self.fake))
        self.store = Store(data / "store.json")
        self.deps = Deps(CATALOG, OfflineCartParser(CATALOG), self.paypal, self.store,
                         "http://localhost:8000", today=lambda: TODAY)
        self.conv = Conversation(self.deps)
        self.loop = asyncio.new_event_loop()

    def tearDown(self):
        self.loop.run_until_complete(self.paypal.aclose())
        self.loop.close()
        self.tmp.cleanup()

    def say(self, text):
        return self.loop.run_until_complete(self.conv.handle(text))

    def link(self):
        self.loop.run_until_complete(self.conv.start_link())
        return self.loop.run_until_complete(self.conv.complete_link())

    def orders_created(self):
        return [b for m, p, b in self.fake.calls if p == "/v2/checkout/orders"]

    def refunds(self):
        return [p for m, p, b in self.fake.calls if p.endswith("/refund")]


class PayPalSilent(DialogBase):
    """PayPal doesn't answer: never claim nothing was charged, and never charge twice."""

    def setUp(self):
        super().setUp()
        self.paypal._retry_delays = (0, 0)
        self.link()
        self.say("two packs of AA batteries")

    def test_unanswered_payment_then_yes_again_reuses_the_same_order(self):
        self.fake.timeouts["/v2/checkout/orders"] = 3  # every attempt of the first yes
        r = self.say("yes")
        self.assertIn("can't tell yet", r.speech)
        self.assertNotIn("No money was taken", r.speech)
        self.assertEqual(r.awaiting, "payment")  # the total was read back, so a new yes is valid
        self.assertEqual(self.store.data.orders, [])

        r = self.say("yes")  # PayPal answers now
        self.assertIn("Payment done", r.speech)
        ids = {rid for p, rid in self.fake.request_ids if p == "/v2/checkout/orders"}
        self.assertEqual(len(ids), 1)  # one PayPal-Request-Id: PayPal returns the same order
        self.assertEqual(len(self.store.data.orders), 1)

    def test_changed_cart_gets_a_new_request_id(self):
        self.fake.timeouts["/v2/checkout/orders"] = 3
        self.say("yes")
        self.say("make it three")
        self.say("yes")
        ids = [rid for p, rid in self.fake.request_ids if p == "/v2/checkout/orders"]
        self.assertEqual(len(set(ids)), 2)

    def test_unanswered_refund_does_not_say_nothing_changed(self):
        self.say("yes")
        self.say("refund my last order")
        self.fake.timeouts["/v2/payments/captures/CAP1/refund"] = 3
        r = self.say("yes")
        self.assertIn("can't tell yet", r.speech)
        self.assertNotIn("Nothing has changed", r.speech)


class PurchaseFlow(DialogBase):
    def test_link_buy_receipt(self):
        linked = self.link()
        self.assertEqual(linked.earcon, "success")
        self.assertTrue(self.store.linked)

        r = self.say("two packs of AA batteries under 10 euros")
        self.assertEqual(r.awaiting, "payment")
        self.assertIn("Total 9 euros 18 cents.", r.speech)
        self.assertEqual(self.orders_created(), [])

        r = self.say("Yes.")
        self.assertIn("Payment done", r.speech)
        self.assertEqual((r.priority, r.earcon), ("assertive", "success"))
        self.assertIn("Charged 9 euros 18 cents.", r.speech)
        [order] = self.orders_created()
        self.assertEqual(order["purchase_units"][0]["amount"]["value"], "9.18")
        self.assertEqual(len(self.store.data.orders), 1)
        self.assertIsNone(self.conv.cart)

    def test_yes_without_readback_never_pays(self):
        self.link()
        r = self.say("yes")
        self.assertIn("nothing to confirm", r.speech)
        self.assertEqual(self.orders_created(), [])

    def test_edit_requires_new_readback_and_yes(self):
        self.link()
        self.say("two packs of AA batteries")
        r = self.say("make it three")
        self.assertTrue(r.speech.startswith("Updated."))
        self.assertIn("quantity three", r.speech)
        self.assertEqual(r.awaiting, "payment")
        self.say("yes")
        [order] = self.orders_created()
        self.assertEqual(order["purchase_units"][0]["amount"]["value"], "13.77")  # 3 x 4.59

    def test_near_yes_rereads_instead_of_paying(self):
        self.link()
        self.say("two packs of AA batteries")
        r = self.say("yes please")
        self.assertTrue(r.speech.startswith("I need a clear yes"))
        self.assertEqual(self.orders_created(), [])
        self.say("yes")
        self.assertEqual(len(self.orders_created()), 1)

    def test_help_in_between_voids_the_yes(self):
        self.link()
        self.say("two packs of AA batteries")
        self.say("help")
        r = self.say("yes")
        self.assertEqual(self.orders_created(), [])
        self.assertEqual(r.awaiting, "payment")  # it read the cart again instead

    def test_repeat_keeps_the_yes_valid(self):
        self.link()
        first = self.say("two packs of AA batteries")
        again = self.say("repeat that")
        self.assertEqual(first.speech, again.speech)
        self.say("yes")
        self.assertEqual(len(self.orders_created()), 1)

    def test_no_keeps_cart_and_does_not_pay(self):
        self.link()
        self.say("two packs of AA batteries")
        r = self.say("no")
        self.assertIn("won't pay", r.speech)
        self.assertIsNotNone(self.conv.cart)
        self.assertEqual(self.orders_created(), [])

    def test_over_budget_shows_no_cart(self):
        self.link()
        r = self.say("five packs of AA batteries under 10 euros")
        self.assertIsNone(r.cart)
        self.assertIsNone(r.awaiting)
        self.assertIn("budget", r.speech)

    def test_not_linked_offers_link_and_does_not_pay(self):
        self.say("two packs of AA batteries")
        r = self.say("yes")
        self.assertEqual(r.action, {"type": "link"})
        self.assertEqual(self.orders_created(), [])
        # after linking, the cart is read back again and needs a fresh yes
        linked = self.link()
        self.assertEqual(linked.awaiting, "payment")
        self.say("yes")
        self.assertEqual(len(self.orders_created()), 1)

    def test_cancel_empties_cart(self):
        self.say("dish soap")
        r = self.say("cancel")
        self.assertIsNone(r.cart)
        self.assertIsNone(self.conv.cart)


class RefundFlow(DialogBase):
    def buy(self):
        self.link()
        self.say("two packs of AA batteries")
        self.say("yes")

    def test_refund_needs_readback_and_yes(self):
        self.buy()
        r = self.say("refund my last order")
        self.assertEqual(r.awaiting, "refund")
        self.assertIn("9 euros 18 cents back to your PayPal.", r.speech)
        self.assertEqual(self.refunds(), [])
        r = self.say("yes")
        self.assertIn("Refund done", r.speech)
        self.assertEqual(self.refunds(), ["/v2/payments/captures/CAP1/refund"])
        self.assertTrue(self.store.data.orders[0].refunded)
        self.assertIn("no order to refund", self.say("refund my last order").speech)

    def test_refund_no(self):
        self.buy()
        self.say("refund my last order")
        r = self.say("no")
        self.assertIn("no refund", r.speech)
        self.say("yes")
        self.assertEqual(self.refunds(), [])

    def test_payment_yes_does_not_refund(self):
        self.buy()
        self.say("two packs of AA batteries")
        self.say("refund my last order")  # refund read-back replaces the payment one
        self.say("dish soap")              # something else: refund gate disarmed
        self.say("yes")                    # not armed for refund any more
        self.assertEqual(self.refunds(), [])


class ApprovalFallback(DialogBase):
    order_status = "PAYER_ACTION_REQUIRED"

    def test_popup_approval_when_vault_unavailable(self):
        self.conv.vault_unavailable = True
        self.say("two packs of AA batteries")
        r = self.say("yes")
        self.assertEqual(r.action["type"], "approve")
        self.assertIn("Press Enter", r.speech)
        # a second yes must not create a second order
        self.say("read my cart")
        again = self.say("yes")
        self.assertIn("already waiting", again.speech)
        self.assertEqual(len(self.orders_created()), 1)
        receipt = self.loop.run_until_complete(self.conv.complete_approval("ORDER1"))
        self.assertIn("Payment done", receipt.speech)
        self.assertEqual(self.conv.drain_events(), [receipt])


class Api(unittest.TestCase):
    def test_frontend_files_revalidate(self):
        tmp = tempfile.TemporaryDirectory()
        deps = Deps(CATALOG, OfflineCartParser(CATALOG), None, Store(Path(tmp.name) / "s.json"),
                    "http://localhost:8000", today=lambda: TODAY)
        with TestClient(create_app(deps)) as client:
            for path in ("/", "/app.js"):
                self.assertEqual(client.get(path).headers.get("cache-control"), "no-cache", path)
        tmp.cleanup()

    def test_session_and_utterance(self):
        tmp = tempfile.TemporaryDirectory()
        deps = Deps(CATALOG, OfflineCartParser(CATALOG), None, Store(Path(tmp.name) / "s.json"),
                    "http://localhost:8000", today=lambda: TODAY)
        with TestClient(create_app(deps)) as client:
            s = client.post("/api/session").json()
            self.assertIn("Welcome", s["speech"])
            r = client.post("/api/utterance", json={"session_id": s["session_id"], "text": "dish soap"}).json()
            self.assertEqual(r["awaiting"], "payment")
            r = client.post("/api/utterance", json={"session_id": s["session_id"], "text": "yes"}).json()
            self.assertIn("isn't configured", r["speech"])
            self.assertEqual(client.post("/api/utterance", json={"session_id": "nope", "text": "x"}).status_code, 404)
            self.assertEqual(client.get("/").status_code, 200)
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
