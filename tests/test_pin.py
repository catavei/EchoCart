"""Optional payment PIN: typed (never spoken), asked after the spoken yes, hashed at rest."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.cart import Catalog
from backend.dialog import Deps
from backend.llm import OfflineCartParser
from backend.pin import hash_pin, valid_pin, verify_pin
from backend.store import Store
from tests.test_dialog import DialogBase, TODAY

CATALOG = Catalog.load()
T0 = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


class Hashing(unittest.TestCase):
    def test_valid_pins(self):
        for pin in ("1234", "00000000", "482913"):
            self.assertTrue(valid_pin(pin), pin)
        for pin in ("123", "123456789", "12a4", "", " 1234", "１２３４"):
            self.assertFalse(valid_pin(pin), pin)

    def test_hash_is_salted_and_verifies(self):
        a, b = hash_pin("4821"), hash_pin("4821")
        self.assertNotEqual(a, b)
        self.assertNotIn("4821", a)
        self.assertTrue(verify_pin("4821", a))
        self.assertFalse(verify_pin("4822", a))
        self.assertFalse(verify_pin("4821", "garbage"))


class StoreLockout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "store.json"
        self.store = Store(self.path)
        self.store.set_pin("4821", Decimal("0"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_pin_never_stored_in_plain_text(self):
        self.assertNotIn("4821", self.path.read_text())
        self.assertTrue(Store(self.path).has_pin)  # survives a restart

    def test_three_wrong_tries_lock_for_ten_minutes(self):
        self.assertEqual(self.store.check_pin("0000", T0), "wrong")
        self.assertEqual(self.store.pin_tries_left, 2)
        self.store.check_pin("0000", T0)
        self.assertEqual(self.store.check_pin("0000", T0), "locked")
        self.assertEqual(self.store.check_pin("4821", T0 + timedelta(minutes=9)), "locked")
        self.assertEqual(self.store.check_pin("4821", T0 + timedelta(minutes=10, seconds=1)), "ok")
        self.assertEqual(self.store.pin_tries_left, 3)

    def test_success_resets_the_count(self):
        self.store.check_pin("0000", T0)
        self.store.check_pin("0000", T0)
        self.assertEqual(self.store.check_pin("4821", T0), "ok")
        self.assertEqual(self.store.pin_tries_left, 3)


class PinDialog(DialogBase):
    def setUp(self):
        super().setUp()
        self.clock = [T0]
        self.deps.now = lambda: self.clock[0]
        self.link()

    def pin(self, value):
        return self.loop.run_until_complete(self.conv.submit_pin(value))

    def set_pin(self, new, current=None, over="0"):
        return self.conv.set_pin(new, current, Decimal(over))

    def test_no_pin_set_pays_on_yes_as_before(self):
        self.say("two packs of AA batteries")
        self.assertIn("Payment done", self.say("yes").speech)

    def test_yes_then_pin_then_payment(self):
        self.set_pin("4821")
        self.say("two packs of AA batteries")
        r = self.say("yes")
        self.assertEqual(r.action, {"type": "pin"})
        self.assertIn("Type your PIN", r.speech)
        self.assertEqual(self.orders_created(), [])

        r = self.pin("1111")
        self.assertIn("isn't right", r.speech)
        self.assertIn("2 tries left", r.speech)
        self.assertEqual(r.action, {"type": "pin"})
        self.assertEqual(self.orders_created(), [])

        r = self.pin("4821")
        self.assertIn("Payment done", r.speech)
        self.assertEqual(len(self.orders_created()), 1)

    def test_pin_is_not_a_yes(self):
        """The PIN only completes a yes; it can't stand in for one."""
        self.set_pin("4821")
        self.say("two packs of AA batteries")
        r = self.pin("4821")
        self.assertIn("nothing waiting", r.speech.lower())
        self.assertEqual(self.orders_created(), [])

    def test_saying_anything_else_cancels_the_pin_step(self):
        self.set_pin("4821")
        self.say("two packs of AA batteries")
        self.say("yes")
        r = self.say("make it three")
        self.assertIsNone(r.action)
        self.pin("4821")
        self.assertEqual(self.orders_created(), [])  # the new cart needs its own yes

    def test_no_cancels_the_pin_step(self):
        self.set_pin("4821")
        self.say("two packs of AA batteries")
        self.say("yes")
        self.assertIn("won't pay", self.say("no").speech)
        self.assertIn("nothing waiting", self.pin("4821").speech.lower())

    def test_repeat_keeps_the_pin_step(self):
        self.set_pin("4821")
        self.say("two packs of AA batteries")
        self.say("yes")
        self.assertIn("Type your PIN", self.say("repeat").speech)
        self.assertIn("Payment done", self.pin("4821").speech)

    def test_threshold_only_asks_above_amount(self):
        self.set_pin("4821", over="50")
        self.say("two packs of AA batteries")     # 45.80 lei
        self.assertIn("Payment done", self.say("yes").speech)
        self.say("three packs of AA batteries")   # 68.70 lei
        self.assertEqual(self.say("yes").action, {"type": "pin"})

    def test_lockout_blocks_payments_then_expires(self):
        self.set_pin("4821")
        self.say("two packs of AA batteries")
        self.say("yes")
        self.pin("0000"); self.pin("0000")
        r = self.pin("0000")
        self.assertIn("locked for 10 minutes", r.speech)
        self.assertIsNone(r.action)
        self.say("read my cart")
        r = self.say("yes")
        self.assertIn("locked", r.speech)
        self.assertEqual(self.orders_created(), [])
        self.clock[0] = T0 + timedelta(minutes=11)
        self.say("read my cart")
        self.say("yes")
        self.assertIn("Payment done", self.pin("4821").speech)

    def test_raising_the_limit_and_unlinking_need_the_pin(self):
        self.say("set my spending limit to 50 lei")
        self.set_pin("4821", over="1000")  # payments under 1000 lei don't ask
        self.say("raise my limit to 200 lei")
        self.assertEqual(self.say("yes").action, {"type": "pin"})
        self.assertEqual(self.store.spending_limit, Decimal("50"))
        self.pin("4821")
        self.assertEqual(self.store.spending_limit, Decimal("200"))

        self.say("unlink my PayPal")
        self.assertEqual(self.say("yes").action, {"type": "pin"})
        self.assertTrue(self.store.linked)
        self.pin("4821")
        self.assertFalse(self.store.linked)

    def test_lowering_the_limit_and_refunds_do_not_need_the_pin(self):
        self.say("two packs of AA batteries")
        self.say("yes")
        self.set_pin("4821")
        self.assertIn("Spending limit set", self.say("set my spending limit to 30 lei").speech)
        self.say("refund my last order")
        self.assertIn("Refund done", self.say("yes").speech)

    def test_changing_or_removing_the_pin_needs_the_current_one(self):
        self.set_pin("4821")
        r = self.set_pin("9999", current="1111")
        self.assertIn("isn't right", r.speech)
        self.assertTrue(self.store.check_pin("4821", T0) == "ok")
        self.assertIn("PIN changed", self.set_pin("9999", current="4821").speech)
        self.assertIn("isn't right", self.conv.remove_pin("4821").speech)
        self.assertIn("PIN removed", self.conv.remove_pin("9999").speech)
        self.assertFalse(self.store.has_pin)

    def test_invalid_new_pin_is_refused(self):
        r = self.set_pin("12a4")
        self.assertIn("4 to 8 digits", r.speech)
        self.assertFalse(self.store.has_pin)

    def test_asking_by_voice_points_to_settings(self):
        r = self.say("set a PIN")
        self.assertIn("typed", r.speech)
        self.assertEqual(r.action, {"type": "pin_settings"})

    def test_replies_report_pin_state_without_the_pin(self):
        r = self.set_pin("4821", over="100")
        self.assertTrue(r.pin_set)
        self.assertEqual(r.pin_over_ron, "100")
        self.assertNotIn("4821", json.dumps(r.to_json()))


class PinApi(unittest.TestCase):
    def test_pin_endpoints_and_log_never_contain_the_pin(self):
        tmp = tempfile.TemporaryDirectory()
        deps = Deps(CATALOG, OfflineCartParser(CATALOG), None, Store(Path(tmp.name) / "s.json"),
                    Decimal("0.201"), "http://localhost:8000", today=lambda: TODAY)
        with TestClient(create_app(deps)) as client:
            sid = client.post("/api/session").json()["session_id"]
            r = client.post("/api/pin/set", json={"session_id": sid, "new_pin": "4821", "over_ron": "0"}).json()
            self.assertIn("PIN set", r["speech"])
            client.post("/api/utterance", json={"session_id": sid, "text": "dish soap"})
            r = client.post("/api/utterance", json={"session_id": sid, "text": "yes"}).json()
            self.assertEqual(r["action"], {"type": "pin"})
            r = client.post("/api/pin", json={"session_id": sid, "pin": "1111"}).json()
            self.assertIn("isn't right", r["speech"])
            bad = client.post("/api/pin", json={"session_id": sid, "pin": "x" * 50})
            self.assertEqual(bad.status_code, 422)
            r = client.post("/api/pin/remove", json={"session_id": sid, "current_pin": "4821"}).json()
            self.assertIn("PIN removed", r["speech"])
        stored = (Path(tmp.name) / "s.json").read_text()
        self.assertNotIn("4821", stored)
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
