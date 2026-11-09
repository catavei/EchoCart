"""Utterance fixtures run against the offline parser + the safety validator.

The same fixtures can be replayed against Claude with tests/manual/eval_llm.py.
"""
import asyncio
import json
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx

from backend.cart import Catalog
from backend.llm import AnthropicCartParser, LLMError, OfflineCartParser
from backend.safety import Rejected, Validated, validate_llm_output

CATALOG = Catalog.load()
TODAY = date(2026, 11, 6)
FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "utterances.json").read_text(encoding="utf-8"))


class OfflineFixtures(unittest.TestCase):
    def test_fixture_count(self):
        self.assertEqual(len(FIXTURES), 30)

    def test_utterances(self):
        parser = OfflineCartParser(CATALOG)
        for case in FIXTURES:
            with self.subTest(case["utterance"]):
                raw = parser.parse_sync(case["utterance"], case.get("current_cart", []))
                result = validate_llm_output(raw, case["utterance"], CATALOG, today=TODAY)
                expect = case["expect"]
                if expect.get("rejected"):
                    self.assertIsInstance(result, Rejected, raw)
                    continue
                self.assertIsInstance(result, Validated, getattr(result, "reason", raw))
                self.assertEqual(result.response.action, expect["action"], raw)
                if "items" in expect:
                    self.assertEqual(result.cart.as_items(), expect["items"])


class OfflineQuantityEdits(unittest.TestCase):
    """Naming an item already in the cart edits that item (the demo script's edit)."""
    CART = [{"product_id": "bat-aa-4-vm", "quantity": 1}, {"product_id": "cln-dish-500", "quantity": 1}]

    def edit(self, utterance):
        raw = OfflineCartParser(CATALOG).parse_sync(utterance, self.CART)
        result = validate_llm_output(raw, utterance, CATALOG, today=TODAY)
        self.assertIsInstance(result, Validated, getattr(result, "reason", raw))
        self.assertEqual(result.response.action, "edit_cart")
        return result.cart.as_items()

    def test_make_named_item_n(self):
        self.assertEqual(self.edit("make the batteries two packs"),
                         [{"product_id": "bat-aa-4-vm", "quantity": 2},
                          {"product_id": "cln-dish-500", "quantity": 1}])

    def test_change_named_item_to_n(self):
        self.assertEqual(self.edit("change the dish soap to 3"),
                         [{"product_id": "bat-aa-4-vm", "quantity": 1},
                          {"product_id": "cln-dish-500", "quantity": 3}])


def _claude_reply(payload: dict, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert request.headers["anthropic-version"]
        return httpx.Response(status, json=payload)
    return httpx.MockTransport(handler)


class AnthropicParser(unittest.TestCase):
    def run_parse(self, payload, status=200):
        parser = AnthropicCartParser("test-key", "claude-opus-5-5", CATALOG,
                                     transport=_claude_reply(payload, status))
        try:
            return asyncio.run(parser.parse("double a batteries", []))
        finally:
            asyncio.run(parser.aclose())

    def test_parses_json_text_block(self):
        cart = {"action": "propose_cart", "items": [{"product_id": "bat-aa-4-vm", "quantity": 1}],
                "question": None, "spoken_summary": "One pack of AA batteries.", "budget_eur": None}
        out = self.run_parse({"stop_reason": "end_turn", "content": [
            {"type": "thinking", "thinking": ""}, {"type": "text", "text": json.dumps(cart)}]})
        self.assertEqual(out, cart)

    def test_refusal_and_errors_raise(self):
        with self.assertRaises(LLMError):
            self.run_parse({"stop_reason": "refusal", "content": []})
        with self.assertRaises(LLMError):
            self.run_parse({"error": "overloaded"}, status=529)
        with self.assertRaises(LLMError):
            self.run_parse({"stop_reason": "end_turn", "content": [{"type": "text", "text": "not json"}]})


if __name__ == "__main__":
    unittest.main()
