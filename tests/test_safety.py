import unittest
from datetime import date
from decimal import Decimal

from backend.cart import Catalog, price_cart
from backend.safety import (ConfirmationGate, Rejected, Validated, extract_budget,
                            is_confirmation, refund_fingerprint, validate_llm_output)

CATALOG = Catalog.load()
TODAY = date(2026, 11, 6)


def cart_of(*items):
    return price_cart([{"product_id": p, "quantity": q} for p, q in items], CATALOG, TODAY)


class ConfirmationPhrases(unittest.TestCase):
    def test_the_ways_people_say_yes(self):
        for phrase in ["yes", "Yes.", "YES, pay", "yes pay!", "Confirm", "  confirm  ", "yeah", "Yeah, go ahead.",
                       "yes please", "ok", "Okay.", "sure", "Sure, pay now.", "yep", "yes yes", "confirmed",
                       "pay", "go ahead", "That's right.", "do it", "um yes", "yes, place the order",
                       "yes, thank you", "absolutely"]:
            self.assertTrue(is_confirmation(phrase), phrase)

    def test_anything_more_than_agreement_is_not_a_yes(self):
        for phrase in ["", "no", "yes but remove the batteries", "yeah wait", "yes no", "not yet", "don't pay",
                       "pay later", "no, don't do it", "maybe", "I guess", "yes I think so", "ok so add milk",
                       "yeah hold on", "go", "fine", "thanks", "right", "da", "refund it", "yes, two more"]:
            self.assertFalse(is_confirmation(phrase), phrase)

    def test_action_words_only_agree_to_their_own_action(self):
        self.assertTrue(is_confirmation("yes, refund it", "refund"))
        self.assertFalse(is_confirmation("yes, refund it", "payment"))
        self.assertFalse(is_confirmation("yes pay", "refund"))
        self.assertTrue(is_confirmation("yeah, remove it", "unlink"))
        self.assertFalse(is_confirmation("yeah, remove it", "payment"))
        self.assertTrue(is_confirmation("sure", "limit"))


class PaymentGate(unittest.TestCase):
    def setUp(self):
        self.gate = ConfirmationGate()
        self.cart = cart_of(("bat-aa-4-vm", 2))

    def test_no_capture_without_readback(self):
        self.assertFalse(self.gate.consume("yes", "payment", self.cart.fingerprint))

    def test_yes_after_readback_passes_once(self):
        self.gate.arm("payment", self.cart.fingerprint)
        self.assertTrue(self.gate.consume("yes", "payment", self.cart.fingerprint))
        # replaying the same yes must not pay twice
        self.assertFalse(self.gate.consume("yes", "payment", self.cart.fingerprint))

    def test_cart_change_invalidates_yes(self):
        self.gate.arm("payment", self.cart.fingerprint)
        changed = cart_of(("bat-aa-4-vm", 3))
        self.assertFalse(self.gate.consume("yes", "payment", changed.fingerprint))

    def test_new_readback_after_change_needs_new_yes(self):
        self.gate.arm("payment", self.cart.fingerprint)
        changed = cart_of(("bat-aa-4-vm", 3))
        self.gate.disarm()  # app disarms on any cart change
        self.assertFalse(self.gate.consume("yes", "payment", changed.fingerprint))
        self.gate.arm("payment", changed.fingerprint)
        self.assertTrue(self.gate.consume("yes", "payment", changed.fingerprint))

    def test_non_yes_reply_voids_the_readback(self):
        self.gate.arm("payment", self.cart.fingerprint)
        self.assertFalse(self.gate.consume("hmm what", "payment", self.cart.fingerprint))
        self.assertFalse(self.gate.consume("yes", "payment", self.cart.fingerprint))

    def test_intervening_speech_disarms(self):
        self.gate.arm("payment", self.cart.fingerprint)
        self.gate.disarm()  # e.g. help text was spoken
        self.assertFalse(self.gate.consume("yes", "payment", self.cart.fingerprint))

    def test_payment_yes_cannot_authorise_refund(self):
        self.gate.arm("payment", self.cart.fingerprint)
        self.assertFalse(self.gate.consume("yes", "refund", self.cart.fingerprint))


class RefundGate(unittest.TestCase):
    def test_refund_requires_readback_and_yes(self):
        gate = ConfirmationGate()
        fp = refund_fingerprint("ORDER1", "CAP1", Decimal("9.85"))
        self.assertFalse(gate.consume("yes", "refund", fp))
        gate.arm("refund", fp)
        self.assertFalse(gate.consume("yes", "refund", refund_fingerprint("ORDER2", "CAP2", Decimal("9.85"))))
        gate.arm("refund", fp)
        self.assertTrue(gate.consume("confirm", "refund", fp))


class LLMValidation(unittest.TestCase):
    def check(self, raw, transcript="two packs of AA batteries"):
        return validate_llm_output(raw, transcript, CATALOG, today=TODAY)

    def test_valid_cart(self):
        r = self.check({"action": "propose_cart", "items": [{"product_id": "bat-aa-4-vm", "quantity": 2}],
                        "question": None, "spoken_summary": "Two packs of AA batteries."})
        self.assertIsInstance(r, Validated)
        self.assertEqual(r.cart.total_eur, Decimal("9.18"))

    def test_schema_failure(self):
        self.assertIsInstance(self.check({"action": "buy", "items": []}), Rejected)
        self.assertIsInstance(self.check("not json"), Rejected)
        self.assertIsInstance(self.check({"action": "propose_cart", "items": [{"product_id": "bat-aa-4-vm",
                              "quantity": 0}], "question": None, "spoken_summary": "x"}), Rejected)

    def test_unknown_product(self):
        r = self.check({"action": "propose_cart", "items": [{"product_id": "bat-aa-999", "quantity": 1}],
                        "question": None, "spoken_summary": "x"})
        self.assertIsInstance(r, Rejected)
        self.assertIn("unknown", r.reason)

    def test_llm_prices_are_ignored(self):
        raw = {"action": "propose_cart", "items": [{"product_id": "bat-aa-4-vm", "quantity": 1,
               "price": "0.01"}], "question": None, "spoken_summary": "x", "total": "0.01"}
        r = self.check(raw)
        self.assertEqual(r.cart.total_eur, Decimal("4.59"))

    def test_over_budget_from_transcript_even_if_llm_omits_it(self):
        r = self.check({"action": "propose_cart", "items": [{"product_id": "bat-aa-8-vm", "quantity": 2}],
                        "question": None, "spoken_summary": "x", "budget_eur": None},
                       transcript="two packs of AA batteries under 10 euros")
        self.assertIsInstance(r, Rejected)
        self.assertIn("budget", r.question)

    def test_out_of_stock(self):
        r = self.check({"action": "propose_cart", "items": [{"product_id": "pc-shampoo-400", "quantity": 1}],
                        "question": None, "spoken_summary": "x"})
        self.assertIsInstance(r, Rejected)

    def test_clarify_needs_a_question(self):
        self.assertIsInstance(self.check({"action": "clarify", "items": [], "question": "Which size?",
                                          "spoken_summary": "Which size?"}), Validated)
        self.assertIsInstance(self.check({"action": "clarify", "items": [], "question": None,
                                          "spoken_summary": ""}), Rejected)

    def test_extract_budget(self):
        self.assertEqual(extract_budget("AA batteries under 10 euros"), Decimal("10"))
        self.assertEqual(extract_budget("AA batteries under €10"), Decimal("10"))
        self.assertEqual(extract_budget("baterii sub 6 euro"), Decimal("6"))
        self.assertEqual(extract_budget("baterii sub 6 de euro"), Decimal("6"))
        self.assertEqual(extract_budget("no more than 12,50 euros"), Decimal("12.50"))
        self.assertIsNone(extract_budget("two packs of batteries"))


if __name__ == "__main__":
    unittest.main()
