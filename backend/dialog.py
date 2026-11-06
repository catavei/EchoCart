"""The conversation: one utterance in, one spoken reply out.

Every word the app speaks is produced here, so this is also where the
confirmation gate is armed (only right after a read-back that states the exact
amount) and disarmed (after anything else).

Text from the LLM is only ever spoken for clarifying questions. Carts, totals,
receipts and refunds are described in backend-generated sentences.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from typing import Callable, Literal

from .cart import Catalog, PricedCart, readback, say_date, say_eur, say_lei
from .llm import CartParser, LLMError
from .paypal_client import PayPalClient, PayPalError, _link, first_capture_id
from .safety import (ConfirmationGate, ConfirmKind, Rejected, is_confirmation, normalize_utterance,
                     refund_fingerprint, validate_llm_output)
from .store import OrderRecord, Store

log = logging.getLogger("echocart.dialog")

HELP = (
    "Say what you'd like to buy, for example: two packs of AA batteries under 50 lei. "
    "I'll read the cart back with the total. Then say yes to pay, or ask for a change: "
    "remove an item, make it three, or cheaper option. "
    "Say repeat to hear the last thing again, read my cart, cancel to empty the cart, "
    "or refund my last order. Press Escape to stop me talking."
)
WELCOME = "Welcome to EchoCart. Press the talk button or the Space bar, then say what you'd like to buy. Say help at any time."
NOT_LINKED_HINT = " Before your first payment you'll link your PayPal account once."

_REPEAT = re.compile(r"^(repeat( that| please| it)?|say (that|it) again|again|pardon|what|sorry)$")
_READ_CART = re.compile(r"\b(read|repeat|what s in|whats in|what is in) (me )?(my |the )?(cart|basket)\b")
_CANCEL = re.compile(r"^(cancel( that| it| order)?|start over|never mind|empty (my |the )?cart|clear (my |the )?cart|stop)$")
_HELP = re.compile(r"^(help|help me|what can i say|ajutor)$")
_NO = re.compile(r"^(no|nope|not now|don t|do not|wait|nu)\b")
_NEAR_YES = re.compile(r"^(yes|yeah|yep|ok|okay|sure|da|go ahead|pay)\b")
_LINK = re.compile(r"\b(link|connect) (my )?pay ?pal\b")
_REFUND = re.compile(r"\b(refund|money back|ramburs)")

Priority = Literal["polite", "assertive"]


@dataclass
class Reply:
    speech: str
    priority: Priority = "polite"
    cart: dict | None = None
    earcon: str | None = None          # "success" | "error"
    action: dict | None = None         # {"type": "link"} | {"type": "approve", "url": ...}
    awaiting: ConfirmKind | None = None
    linked: bool = False
    receipt: dict | None = None

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Deps:
    catalog: Catalog
    parser: CartParser
    paypal: PayPalClient | None
    store: Store
    eur_per_ron: Decimal
    base_url: str
    today: Callable[[], date] = date.today


@dataclass
class Conversation:
    deps: Deps
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cart: PricedCart | None = None
    gate: ConfirmationGate = field(default_factory=ConfirmationGate)
    last_reply: Reply | None = None
    last_arm: tuple[ConfirmKind, str] | None = None
    refund_target: OrderRecord | None = None
    pending_setup_token: str | None = None
    pending_approval: dict | None = None
    vault_unavailable: bool = False
    events: list[Reply] = field(default_factory=list)

    # ---- speaking -------------------------------------------------------
    def _say(self, speech: str, arm: tuple[ConfirmKind, str] | None = None, **kw) -> Reply:
        """Every reply goes through here: arms the gate only for read-backs."""
        if arm:
            self.gate.arm(*arm)
        else:
            self.gate.disarm()
        self.last_arm = arm
        reply = Reply(speech=speech, cart=self.cart.to_json() if self.cart else None,
                      awaiting=arm[0] if arm else None, linked=self.deps.store.linked, **kw)
        self.last_reply = reply
        return reply

    def _readback(self, prefix: str = "") -> Reply:
        assert self.cart is not None
        return self._say(prefix + readback(self.cart, self.deps.today()),
                         arm=("payment", self.cart.fingerprint))

    def welcome(self) -> Reply:
        hint = "" if self.deps.store.linked else NOT_LINKED_HINT
        return self._say(WELCOME + hint)

    def _repeat(self) -> Reply:
        if self.last_reply is None:
            return self.welcome()
        if self.last_arm:  # the same total is being spoken again, so the yes stays valid
            self.gate.arm(*self.last_arm)
        return self.last_reply

    # ---- main entry -----------------------------------------------------
    async def handle(self, text: str) -> Reply:
        norm = normalize_utterance(text)
        if not norm:
            return self._say("I didn't hear anything. Press the talk button and try again.")
        if _REPEAT.match(norm):
            return self._repeat()

        armed = self.gate.armed_for
        if armed == "payment" and self.cart and self.gate.consume(text, "payment", self.cart.fingerprint):
            return await self._pay()
        if armed == "refund" and self.refund_target and self.gate.consume(
                text, "refund", self._refund_fp(self.refund_target)):
            return await self._refund()
        # From here on the gate is disarmed: whatever was said, it was not a valid yes.

        if armed == "payment" and self.cart:
            if _NO.match(norm):
                return self._say("Okay, I won't pay. Tell me what to change, or say read my cart.")
            if _NEAR_YES.match(norm):
                return self._readback("I need a clear yes before paying. ")
        if armed == "refund" and self.refund_target:
            if _NO.match(norm):
                self.refund_target = None
                return self._say("Okay, no refund. Your order stays as it is.")
            if _NEAR_YES.match(norm):
                return self._start_refund("I need a clear yes before refunding. ")
        if not armed and is_confirmation(text):
            if self.cart:
                return self._readback("Before paying I'll read the cart again. ")
            return self._say("There's nothing to confirm yet. Tell me what you'd like to buy.")

        if _HELP.match(norm):
            return self._say(HELP)
        if _CANCEL.match(norm):
            self.cart = None
            return self._say("Cart emptied. What would you like instead?")
        if _READ_CART.search(norm):
            return self._readback() if self.cart else self._say("Your cart is empty.")
        if _LINK.search(norm):
            return self._offer_link()
        if _REFUND.search(norm):
            return self._start_refund()
        return await self._understand(text)

    # ---- understanding requests ----------------------------------------
    async def _understand(self, text: str) -> Reply:
        current = self.cart.as_items() if self.cart else []
        try:
            raw = await self.deps.parser.parse(text, current)
        except LLMError as e:
            log.warning("parser failed: %s", e)
            return self._say("Sorry, I had trouble understanding that. Please try again, or say help.",
                             priority="assertive", earcon="error")
        result = validate_llm_output(raw, text, self.deps.catalog, self.deps.eur_per_ron,
                                     today=self.deps.today())
        if isinstance(result, Rejected):
            log.info("rejected LLM output: %s", result.reason)
            return self._say(result.question)
        resp = result.response
        if resp.action == "clarify":
            return self._say(resp.question or resp.spoken_summary)
        if resp.action == "help":
            return self._say(HELP)
        if resp.action == "cancel":
            self.cart = None
            return self._say("Cart emptied. What would you like instead?")
        if resp.action == "refund_request":
            return self._start_refund()
        prefix = "Updated. " if resp.action == "edit_cart" and self.cart else ""
        self.cart = result.cart
        return self._readback(prefix)

    # ---- paying ---------------------------------------------------------
    def _paypal_missing(self) -> Reply:
        return self._say("PayPal isn't configured on this server, so I can't take payments. "
                         "The cart is still here.", priority="assertive", earcon="error")

    def _offer_link(self, prefix: str = "") -> Reply:
        if self.deps.paypal is None:
            return self._paypal_missing()
        if self.deps.store.linked:
            return self._say("Your PayPal account is already linked.")
        return self._say(
            prefix + "To link PayPal, press Enter now to open the PayPal window. Log in and approve. "
            "I'll tell you when it's linked. You only do this once.",
            action={"type": "link"})

    async def _pay(self) -> Reply:
        cart = self.cart
        if self.deps.paypal is None:
            return self._paypal_missing()
        if self.pending_approval:  # never create a second order while one awaits approval
            return self._say("A PayPal window is already waiting for you to approve this payment. "
                             "Press Enter to open it again.", priority="assertive",
                             action={"type": "approve", "url": self.pending_approval.get("url")})
        ref = f"echocart-{uuid.uuid4().hex[:12]}"
        if not self.deps.store.linked:
            if not self.vault_unavailable:
                return self._offer_link("Your PayPal account isn't linked yet. ")
            base, sid = self.deps.base_url, self.session_id
            try:
                order = await self.deps.paypal.create_order_for_approval(
                    cart.total_eur, self._summary(cart), ref,
                    f"{base}/paypal/approved?session={sid}", f"{base}/paypal/cancelled?session={sid}")
            except PayPalError as e:
                return self._payment_failed(e)
            return self._ask_for_approval(order["id"], order["approve_url"], cart)
        try:
            order = await self.deps.paypal.create_order_with_vault(
                self.deps.store.data.vault_id, cart.total_eur, self._summary(cart), ref)
            if order.get("status") == "PAYER_ACTION_REQUIRED":
                return self._ask_for_approval(order["id"], _link(order, "payer-action"), cart)
            if order.get("status") != "COMPLETED":
                order = await self.deps.paypal.capture_order(order["id"])
        except PayPalError as e:
            return self._payment_failed(e)
        return self._receipt(order, cart)

    def _ask_for_approval(self, order_id: str, url: str | None, cart: PricedCart) -> Reply:
        self.pending_approval = {"order_id": order_id, "cart": cart, "url": url}
        return self._say(
            "PayPal wants you to approve this payment in its own window. Press Enter now to open it. "
            "Log in, then approve. I'll tell you when the payment is done.",
            priority="assertive", action={"type": "approve", "url": url})

    def _payment_failed(self, e: PayPalError) -> Reply:
        log.warning("payment failed: %s %s", e.status, e.body)
        name = e.body.get("name", "an error") if isinstance(e.body, dict) else "an error"
        reply = self._readback(f"The payment didn't go through. PayPal reported {name.replace('_', ' ').lower()}. "
                               "No money was taken. ")
        reply.priority, reply.earcon = "assertive", "error"
        return reply

    @staticmethod
    def _summary(cart: PricedCart) -> str:
        return "; ".join(f"{l.quantity} x {l.product.spoken_name}" for l in cart.lines)

    def _receipt(self, order: dict, cart: PricedCart) -> Reply:
        capture_id = first_capture_id(order)
        record = OrderRecord(order_id=order["id"], capture_id=capture_id or "",
                             total_ron=str(cart.total_ron), total_eur=str(cart.total_eur),
                             delivery_date=cart.delivery_date.isoformat(), summary=self._summary(cart))
        self.deps.store.add_order(record)
        self.cart = None
        ending = ", ".join(order["id"][-4:])
        return self._say(
            f"Payment done. Order number ending {ending}. Charged {say_eur(cart.total_eur)}, "
            f"that's {say_lei(cart.total_ron)}. Delivery {say_date(cart.delivery_date, self.deps.today())}.",
            priority="assertive", earcon="success",
            receipt={"order_id": order["id"], "capture_id": capture_id, "total_eur": str(cart.total_eur),
                     "total_ron": str(cart.total_ron), "delivery_date": cart.delivery_date.isoformat()})

    # ---- refunds --------------------------------------------------------
    @staticmethod
    def _refund_fp(order: OrderRecord) -> str:
        return refund_fingerprint(order.order_id, order.capture_id, Decimal(order.total_eur))

    def _start_refund(self, prefix: str = "") -> Reply:
        order = self.deps.store.last_refundable()
        if order is None:
            self.refund_target = None
            return self._say("There's no order to refund.")
        self.refund_target = order
        ending = ", ".join(order.order_id[-4:])
        return self._say(
            f"{prefix}Refund your last order, number ending {ending}: {order.summary}. "
            f"{say_eur(Decimal(order.total_eur))} back to your PayPal, that's {say_lei(Decimal(order.total_ron))}. "
            "Say yes to refund, or no to keep the order.",
            arm=("refund", self._refund_fp(order)))

    async def _refund(self) -> Reply:
        order, self.refund_target = self.refund_target, None
        if self.deps.paypal is None:
            return self._paypal_missing()
        try:
            refund = await self.deps.paypal.refund_capture(order.capture_id, order.order_id)
        except PayPalError as e:
            log.warning("refund failed: %s %s", e.status, e.body)
            return self._say("The refund didn't go through. Nothing has changed. Say refund my last order to try again.",
                             priority="assertive", earcon="error")
        self.deps.store.mark_refunded(order.order_id, refund.get("id"))
        status = refund.get("status", "")
        when = "" if status == "COMPLETED" else " PayPal is still processing it."
        return self._say(f"Refund done. {say_eur(Decimal(order.total_eur))} will go back to your PayPal account.{when}",
                         priority="assertive", earcon="success")

    # ---- PayPal window callbacks (called by app.py) ----------------------
    async def start_link(self) -> str:
        if self.deps.paypal is None:
            raise PayPalError("PayPal not configured")
        base = self.deps.base_url
        st = await self.deps.paypal.create_setup_token(
            f"{base}/paypal/linked?session={self.session_id}",
            f"{base}/paypal/cancelled?session={self.session_id}")
        self.pending_setup_token = st["id"]
        return st["approve_url"]

    def vault_fallback_notice(self) -> Reply:
        prefix = "PayPal can't save your account on this server, so you'll approve each payment in a PayPal window. "
        if self.cart:
            return self._readback(prefix)
        return self._say(prefix + "Tell me what you'd like to buy.", priority="assertive")

    async def complete_link(self) -> Reply:
        token, self.pending_setup_token = self.pending_setup_token, None
        if not token:
            reply = self._say("I wasn't expecting a PayPal link. Say link PayPal to try again.")
        else:
            try:
                tok = await self.deps.paypal.create_payment_token(token)
            except PayPalError as e:
                log.warning("link failed: %s %s", e.status, e.body)
                reply = self._say("Linking PayPal didn't work. Say link PayPal to try again.",
                                  priority="assertive", earcon="error")
            else:
                self.deps.store.link(tok["vault_id"], tok["customer_id"])
                reply = (self._readback("PayPal is linked. ") if self.cart
                         else self._say("PayPal is linked. Tell me what you'd like to buy."))
                reply.priority, reply.earcon = "assertive", "success"
        self.events.append(reply)
        return reply

    async def complete_approval(self, order_id: str) -> Reply:
        pending, self.pending_approval = self.pending_approval, None
        if not pending or pending["order_id"] != order_id:
            reply = self._say("I wasn't expecting that PayPal approval. Nothing was charged.", priority="assertive")
        else:
            try:
                order = await self.deps.paypal.capture_order(order_id)
                reply = self._receipt(order, pending["cart"])
            except PayPalError as e:
                self.cart = pending["cart"]
                reply = self._payment_failed(e)
        self.events.append(reply)
        return reply

    def cancelled_in_paypal(self) -> Reply:
        self.pending_setup_token = None
        self.pending_approval = None
        reply = self._say("The PayPal window was closed without approving. Nothing was charged. "
                          "Say read my cart to continue.", priority="assertive")
        self.events.append(reply)
        return reply

    def drain_events(self) -> list[Reply]:
        events, self.events = self.events, []
        return events
