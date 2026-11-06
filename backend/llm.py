"""Turn an utterance into a cart proposal.

Two interchangeable parsers return the same raw dict shape (validated later by
safety.validate_llm_output, never trusted here):

- AnthropicCartParser: Claude via the Messages API with a JSON-schema response.
  Called over raw httpx because the project keeps its dependency list to
  fastapi / httpx / pydantic / uvicorn.
- OfflineCartParser: deterministic keyword matcher so the app runs (and judges
  can test it) without an API key.
"""
from __future__ import annotations

import json
import logging
import re
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import httpx

from .cart import Catalog, Product
from .safety import _BUDGET_RE, extract_budget, normalize_utterance

log = logging.getLogger("echocart.llm")

PROMPT_PATH = Path(__file__).with_name("prompts") / "cart_system.md"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2026-10-21"

CART_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "items", "question", "spoken_summary", "budget_lei"],
    "properties": {
        "action": {"type": "string", "enum": ["propose_cart", "clarify", "edit_cart",
                                              "refund_request", "help", "cancel"]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["product_id", "quantity"],
                "properties": {"product_id": {"type": "string"}, "quantity": {"type": "integer"}},
            },
        },
        "question": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "spoken_summary": {"type": "string"},
        "budget_lei": {"anyOf": [{"type": "number"}, {"type": "null"}]},
    },
}


class LLMError(RuntimeError):
    pass


class CartParser(Protocol):
    async def parse(self, transcript: str, current_items: list[dict]) -> object: ...


def _user_message(transcript: str, current_items: list[dict]) -> str:
    return json.dumps({"shopper_said": transcript, "current_cart": current_items}, ensure_ascii=False)


class AnthropicCartParser:
    def __init__(self, api_key: str, model: str, catalog: Catalog, effort: str = "low",
                 transport: httpx.AsyncBaseTransport | None = None):
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set (or use LLM_MODE=offline).")
        self._model, self._effort = model, effort
        # Stable prefix (rules + catalogue) first so prompt caching can reuse it.
        self._system = PROMPT_PATH.read_text(encoding="utf-8") + catalog.for_prompt()
        self._http = httpx.AsyncClient(timeout=60, transport=transport, headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            # Server-side fallback if a safety classifier declines the request.
            "anthropic-beta": "server-side-fallback-2026-07-01",
            "content-type": "application/json",
        })

    async def aclose(self) -> None:
        await self._http.aclose()

    async def parse(self, transcript: str, current_items: list[dict]) -> object:
        payload = {
            "model": self._model,
            "max_tokens": 4000,
            "system": [{"type": "text", "text": self._system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": _user_message(transcript, current_items)}],
            "output_config": {"effort": self._effort,
                              "format": {"type": "json_schema", "schema": CART_SCHEMA}},
            "fallbacks": "default",
        }
        try:
            resp = await self._http.post(ANTHROPIC_URL, json=payload)
        except httpx.HTTPError as e:
            raise LLMError(f"Claude request failed: {e}") from e
        if resp.status_code != 200:
            raise LLMError(f"Claude returned {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        if body.get("stop_reason") in ("refusal", "max_tokens"):
            raise LLMError(f"Claude stopped: {body.get('stop_reason')}")
        text = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError("Claude did not return JSON") from e


# ---- offline fallback -----------------------------------------------------
_WORD_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                 "seven": 7, "eight": 8, "nine": 9, "ten": 10, "couple": 2, "pair": 2,
                 "un": 1, "o": 1, "doi": 2, "două": 2, "doua": 2, "trei": 3, "patru": 4, "cinci": 5}
_SYNONYMS = [(r"\bdouble a\b", "aa"), (r"\btriple a\b", "aaa"), (r"\bdouble aa\b", "aa"),
             (r"\bdouble-a\b", "aa"), (r"\b(\d+) volt\b", r"\1v"), (r"\bnine volt\b", "9v")]
_STOPWORDS = {"of", "the", "some", "and", "pack", "packs", "please", "i", "want", "need", "buy",
              "get", "me", "under", "lei", "ron", "de", "si", "și", "with", "for", "box", "boxes",
              "bottle", "bottles", "could", "you", "order", "would", "like", "also", "sub", "pentru"}
# Words shared by a whole category; they don't make two products "the same kind".
_GENERIC_TERMS = {"batteries", "baterii", "alkaline", "alcaline", "battery", "baterie", "cheap",
                  "tablets", "bags", "mineral", "minerala", "apa", "water"}
_QTY_RE = re.compile(r"\b(\d{1,2})\b(?!\s*(?:lei|ron|v\b|ml|g\b|kg|l\b|litre|liter))")
_DIACRITICS = str.maketrans("ăâîșşțţ", "aaisstt")


def _fold(text: str) -> str:
    return normalize_utterance(text).translate(_DIACRITICS)


def _product_terms(p: Product) -> set[str]:
    words = set(_fold(f"{p.name} {p.name_ro}").split())
    for kw in p.keywords:
        words.add(_fold(kw))
        words.update(_fold(kw).split())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


class OfflineCartParser:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self._terms = {p.id: _product_terms(p) for p in catalog}

    async def parse(self, transcript: str, current_items: list[dict]) -> object:
        return self.parse_sync(transcript, current_items)

    # ---- helpers
    def _clean(self, text: str) -> str:
        text = _fold(text)
        for pat, rep in _SYNONYMS:
            text = re.sub(pat, rep, text)
        return text

    def _match(self, segment: str) -> list[Product]:
        tokens = [t for t in segment.split() if t not in _STOPWORDS]
        phrase = " ".join(tokens)
        best, scored = 0, []
        for p in self.catalog:
            terms = self._terms[p.id]
            score = sum(1 for t in tokens if t in terms)
            score += sum(2 for kw in p.keywords if " " in kw and _fold(kw) in phrase)
            if score:
                scored.append((score, p))
                best = max(best, score)
        return [p for s, p in scored if s == best]

    @staticmethod
    def _quantity(segment: str) -> int:
        m = _QTY_RE.search(segment)
        if m:
            return int(m.group(1))
        for word in segment.split():
            if word in _WORD_NUMBERS:
                return _WORD_NUMBERS[word]
        return 1

    def _choose(self, candidates: list[Product], qty: int, budget: Decimal | None):
        """Pick one product, or return a question if the kinds genuinely differ."""
        in_stock = [p for p in candidates if p.stock >= qty]
        if not in_stock:
            return None, f"Sorry, {candidates[0].name} is out of stock. Would you like something else?"
        kinds = list(dict.fromkeys(p.name for p in in_stock))
        if len(kinds) > 1:
            return None, f"Did you mean {' or '.join(kinds[:3])}?"
        if budget is not None:
            fitting = [p for p in in_stock if p.price_ron * qty <= budget]
            if fitting:
                return fitting[0], None
            return min(in_stock, key=lambda p: p.price_ron), None  # validator reports the overrun
        return in_stock[0], None

    def _cheaper(self, product_id: str) -> str:
        p = self.catalog.get(product_id)
        same_kind = [c for c in self.catalog if c.category == p.category and c.stock > 0
                     and self._terms[c.id] & self._terms[p.id] - _GENERIC_TERMS
                     and c.price_ron < p.price_ron]
        return min(same_kind, key=lambda c: c.price_ron).id if same_kind else product_id

    def _result(self, action, items=(), question=None, summary="", budget=None):
        return {"action": action, "items": list(items), "question": question,
                "spoken_summary": summary or (question or ""),
                "budget_lei": float(budget) if budget is not None else None}

    # ---- main
    def parse_sync(self, transcript: str, current_items: list[dict]) -> dict:
        budget = extract_budget(transcript)
        # Drop the budget phrase so "under 30 lei" isn't read as a quantity of 30.
        # Commas separate items; turn them into "and" before punctuation is stripped.
        text = self._clean(_BUDGET_RE.sub(" ", transcript).replace(",", " and "))
        current = [dict(i) for i in current_items]

        if re.search(r"\b(refund|ramburs|money back)\b", text):
            return self._result("refund_request", summary="You want a refund of your last order.")
        if re.search(r"\b(help|ajutor)\b", text):
            return self._result("help")
        if re.fullmatch(r"(cancel|stop|anuleaza|never mind)", text):
            return self._result("cancel")

        if current:
            if re.search(r"\b(cheaper|ieftin)", text):
                new = [{"product_id": self._cheaper(i["product_id"]), "quantity": i["quantity"]} for i in current]
                return self._result("edit_cart", new, summary="Here are cheaper options.")
            m = re.search(r"\b(remove|delete|take out|drop|scoate)\b(.*)", text)
            if m:
                targets = {p.id for p in self._match(m.group(2))}
                kept = [i for i in current if i["product_id"] not in targets]
                if len(kept) == len(current):
                    return self._result("clarify", question="Which item should I remove?")
                if not kept:
                    return self._result("clarify", question="That empties the cart. What would you like instead?")
                return self._result("edit_cart", kept, summary="Removed.")
            m = re.search(r"\bmake (?:it|that)\s+(\w+)", text)
            if m:
                n = int(m.group(1)) if m.group(1).isdigit() else _WORD_NUMBERS.get(m.group(1))
                if not n:
                    return self._result("clarify", question="How many would you like?")
                current[-1]["quantity"] = n
                return self._result("edit_cart", current, summary=f"Changed to {n}.")

        segments = [s for s in re.split(r"\band\b|\bsi\b|\bplus\b", text) if s.strip()]
        items, names = [], []
        for seg in segments:
            candidates = self._match(seg)
            if not candidates:
                continue
            qty = self._quantity(seg)
            chosen, question = self._choose(candidates, qty, budget)
            if question:
                return self._result("clarify", question=question, budget=budget)
            items.append({"product_id": chosen.id, "quantity": qty})
            names.append(f"{qty} {chosen.name}")
        if not items:
            return self._result("clarify", question="I couldn't find that. What would you like to buy?",
                                budget=budget)
        action = "edit_cart" if current and re.search(r"\b(add|also|plus)\b", text) else "propose_cart"
        if action == "edit_cart":
            items = current + items
        return self._result(action, items, summary=", ".join(names) + ".", budget=budget)


def make_parser(settings, catalog: Catalog) -> CartParser:
    if settings.llm_mode == "offline":
        return OfflineCartParser(catalog)
    if not settings.anthropic_api_key:
        log.warning("ANTHROPIC_API_KEY not set: using the offline parser instead of Claude")
        return OfflineCartParser(catalog)
    return AnthropicCartParser(settings.anthropic_api_key, settings.anthropic_model, catalog,
                               settings.anthropic_effort)
