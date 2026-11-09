"""Safety rules, enforced in code.

1. Money moves only after an explicit confirmation ("yes", "yes, pay",
   "confirm") given right after the backend itself spoke the exact total.
2. Any change to the cart, or anything else spoken in between, voids the yes.
3. Refunds go through the same gate.
4. LLM output is validated: schema, catalogue ids, stock, stated budget.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .cart import MAX_QUANTITY, Catalog, CartError, PricedCart, price_cart, say_eur

# ---- 1-3. confirmation gate -----------------------------------------------
ACCEPTED_CONFIRMATIONS = frozenset({"yes", "yes pay", "confirm"})


def normalize_utterance(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^\w\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_confirmation(text: str) -> bool:
    return normalize_utterance(text) in ACCEPTED_CONFIRMATIONS


# payment / refund move money; unlink removes the saved PayPal account;
# limit raises or removes the spending limit. All need a read-back and a clear yes.
ConfirmKind = Literal["payment", "refund", "unlink", "limit"]


@dataclass
class _Pending:
    kind: ConfirmKind
    fingerprint: str
    nonce: str


class ConfirmationGate:
    """One-shot permission to move money, tied to what was just read back.

    arm()     after speaking a read-back that states the exact amount.
    disarm()  whenever anything else is spoken or the cart changes.
    consume() when the shopper replies; True at most once per arm().
    """

    def __init__(self) -> None:
        self._pending: _Pending | None = None

    @property
    def armed_for(self) -> ConfirmKind | None:
        return self._pending.kind if self._pending else None

    def arm(self, kind: ConfirmKind, fingerprint: str) -> str:
        self._pending = _Pending(kind, fingerprint, secrets.token_hex(8))
        return self._pending.nonce

    def disarm(self) -> None:
        self._pending = None

    def consume(self, utterance: str, kind: ConfirmKind, current_fingerprint: str) -> bool:
        pending, self._pending = self._pending, None  # single use, whatever happens
        return (
            pending is not None
            and pending.kind == kind
            and pending.fingerprint == current_fingerprint
            and is_confirmation(utterance)
        )


def refund_fingerprint(order_id: str, capture_id: str, amount_eur: Decimal) -> str:
    return f"refund:{order_id}:{capture_id}:{amount_eur}"


# ---- 4. LLM output validation ---------------------------------------------
class LLMItem(BaseModel):
    product_id: str
    quantity: int = Field(ge=1, le=MAX_QUANTITY)


class LLMCartResponse(BaseModel):
    action: Literal["propose_cart", "clarify", "edit_cart", "refund_request", "help", "cancel"]
    items: list[LLMItem]
    question: str | None
    spoken_summary: str = Field(max_length=400)
    budget_eur: float | None = None


_BUDGET_RE = re.compile(
    r"\b(?:under|below|less than|max(?:imum)?|at most|up to|no more than|within|sub|maxim|până la)\s+"
    r"(?:€\s*)?(\d+(?:[.,]\d{1,2})?)\s*(?:de\s+)?(?:euros?|eur|€)?",
    re.IGNORECASE,
)


def extract_budget(transcript: str) -> Decimal | None:
    """Budget the shopper stated, read straight from their words."""
    m = _BUDGET_RE.search(transcript)
    if not m:
        return None
    try:
        return Decimal(m.group(1).replace(",", "."))
    except InvalidOperation:
        return None


@dataclass
class Validated:
    response: LLMCartResponse
    cart: PricedCart | None  # None for clarify/help/cancel/refund_request


@dataclass
class Rejected:
    """Don't show a cart; ask this instead."""
    question: str
    reason: str


def effective_budget(transcript: str, llm_budget: float | None) -> Decimal | None:
    budgets = [b for b in (extract_budget(transcript),
                           Decimal(str(llm_budget)) if llm_budget is not None else None) if b]
    return min(budgets) if budgets else None


def validate_llm_output(raw: object, transcript: str, catalog: Catalog,
                        budget: Decimal | None = None,
                        today=None) -> Validated | Rejected:
    try:
        resp = LLMCartResponse.model_validate(raw)
    except ValidationError as e:
        return Rejected("Sorry, I didn't catch that. What would you like to buy?", f"schema: {e.errors()[:2]}")

    if resp.action == "clarify":
        q = (resp.question or resp.spoken_summary or "").strip()
        return Validated(resp, None) if q else Rejected(
            "Could you say that another way?", "clarify without a question")

    if resp.action not in ("propose_cart", "edit_cart"):
        return Validated(resp, None)

    unknown = [i.product_id for i in resp.items if i.product_id not in catalog]
    if unknown:
        return Rejected("I couldn't find that in the shop. Could you describe it differently?",
                        f"unknown product ids {unknown}")
    try:
        cart = price_cart([i.model_dump() for i in resp.items], catalog, today)
    except CartError as e:
        return Rejected(f"{e} Would you like something else instead?", f"cart: {e}")

    limit = budget if budget is not None else effective_budget(transcript, resp.budget_eur)
    if limit is not None and cart.total_eur > limit:
        return Rejected(
            f"That would come to more than your {say_eur(limit)} budget. "
            "Should I look for cheaper options, or fewer items?",
            f"over budget: {cart.total_eur} > {limit}",
        )
    return Validated(resp, cart)
