"""The conversation: one utterance in, one spoken reply out.

Every word the app speaks is produced here, so this is also where the
confirmation gate is armed (only right after a read-back that states exactly
what will happen) and disarmed (after anything else).

Text from the LLM is only ever spoken for clarifying questions. Carts, totals,
receipts and refunds are described in backend-generated sentences.

Actions that need a read-back and a clear yes:
  payment  pay for the cart (amount spoken)
  refund   refund all or part of the last order (amount spoken)
  unlink   remove the saved PayPal account
  limit    raise or remove the per-order spending limit
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Literal

from .cart import Catalog, CartError, PricedCart, price_cart, readback, say_date, say_eur
from .llm import CartParser, LLMError, OfflineCartParser
from .paypal_client import PayPalClient, PayPalError, PayPalUnreachable, _link, first_capture_id
from .safety import (ConfirmationGate, ConfirmKind, Rejected, is_confirmation, normalize_utterance,
                     refund_fingerprint, validate_llm_output)
from .pin import LOCK_MINUTES, valid_pin
from .store import OrderLine, OrderRecord, Store

log = logging.getLogger("echocart.dialog")

HELP = (
    "Say what you'd like to buy, for example: two packs of AA batteries under 10 euros. "
    "I'll read the cart back with the total. Then say yes to pay, or ask for a change: "
    "remove an item, make it three, or cheaper option. "
    "You can also ask how much something costs, say same as last time, ask where's my order, "
    "refund my last order or just one item from it, or set a spending limit. "
    "Say repeat to hear the last thing again, read my cart, or cancel. Press Escape to stop me talking. "
    "To protect payments with a PIN, use the PIN settings: a PIN is typed, never spoken."
)
PIN_PROMPTS = {
    "payment": "Type your PIN and press Enter to pay, or press Escape to cancel.",
    "limit": "Type your PIN and press Enter to change your spending limit, or press Escape to keep it.",
    "unlink": "Type your PIN and press Enter to unlink PayPal, or press Escape to keep it linked.",
}
WELCOME = "Welcome to EchoCart. Press the talk button or the Space bar, then say what you'd like to buy. Say help at any time."
NOT_LINKED_HINT = " Before your first payment you'll link your PayPal account once."

_N = r"(\d+(?:[.,]\d{1,2})?)"
_REPEAT = re.compile(r"^(repeat( that| please| it)?|say (that|it) again|again|pardon|what|sorry)$")
_READ_CART = re.compile(r"\b(read|repeat|what s in|whats in|what is in) (me )?(my |the )?(cart|basket)\b")
_CANCEL = re.compile(r"^(cancel( that| it| order)?|start over|never mind|empty (my |the )?cart|clear (my |the )?cart|stop)$")
_HELP = re.compile(r"^(help|help me|what can i say|ajutor)$")
_NO = re.compile(r"^(no|nope|not now|don t|do not|wait|keep it|nu)\b")
# Starts like a yes but says something else too ("yeah, wait", "yes I think so"): ask again.
_NEAR_YES = re.compile(r"^(yes|yeah|yep|yup|ok|okay|sure|alright|go ahead|pay|refund it)\b")
_UNLINK = re.compile(r"\b(unlink|disconnect|forget|remove|delete) (my )?pay ?pal\b")
_LINK = re.compile(r"\b(link|connect) (my )?pay ?pal\b")
_REFUND = re.compile(r"\b(refund|money back|ramburs)")
_REORDER = re.compile(r"\b(reorder|re order|order (it |that |them )?again|same (thing |order )?as (last time|before|last week)|"
                      r"the same again|buy (it|that|them) again|repeat my (last )?order)\b")
_WHERE = re.compile(r"\b(where s|where is|track|status of)\b.*\b(order|delivery|parcel|package)\b|"
                    r"\bwhen (will|does|is) (it|my order|the order|my delivery)\b")
_HISTORY = re.compile(r"\bwhat did i (order|buy)\b|\b(my|order) history\b|\bmy (last|recent|previous) orders\b")
_LIMIT_ASK = re.compile(r"\bwhat s my (spending )?limit\b|\bwhat is my (spending )?limit\b|\bdo i have a (spending )?limit\b")
_LIMIT_OFF = re.compile(r"\b(remove|turn off|clear|delete|cancel|no more) (my |the |a )?(spending )?limit\b")
_LIMIT_SET = re.compile(rf"\b(?:spending )?limit\b.*?\b{_N}\b|\blimit (?:me|each order|orders) to {_N}\b")
_PIN_CMD = re.compile(r"\b(set|change|add|remove|create|turn on|turn off|use|need) (up )?(a |my |the )?"
                      r"(pin|pin code|pin number|passcode)\b")
_INFO = re.compile(r"^(how much (is|are|does|do|for)|what s the price|what is the price|price of|do you (have|sell)|"
                   r"what (kind|kinds|types|sort|sorts) of|what .+ do you (have|sell)|tell me about|is there|are there|"
                   r"what .+ are there)\b")

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
    spending_limit_eur: str | None = None
    receipt: dict | None = None
    pin_set: bool = False
    pin_over_eur: str | None = None    # payments above this ask for the PIN ("0" = every payment)

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Deps:
    catalog: Catalog
    parser: CartParser
    paypal: PayPalClient | None
    store: Store
    base_url: str
    today: Callable[[], date] = date.today
    matcher: OfflineCartParser | None = None
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def __post_init__(self):
        if self.matcher is None:
            self.matcher = OfflineCartParser(self.catalog)


@dataclass
class RefundPlan:
    order: OrderRecord
    lines: list[OrderLine]        # the lines being refunded
    amount_eur: Decimal
    whole_order: bool

    @property
    def fingerprint(self) -> str:
        ids = ",".join(sorted(line.product_id for line in self.lines))
        return f"{refund_fingerprint(self.order.order_id, self.order.capture_id, self.amount_eur)}:{ids}"


def _ending(order_id: str) -> str:
    return ", ".join(order_id[-4:])


def _parse_amount(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", "."))
    except InvalidOperation:
        return None


@dataclass
class Conversation:
    deps: Deps
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cart: PricedCart | None = None
    gate: ConfirmationGate = field(default_factory=ConfirmationGate)
    last_reply: Reply | None = None
    last_arm: tuple[ConfirmKind, str] | None = None
    refund_plan: RefundPlan | None = None
    pending_limit: Decimal | None = None   # meaningful only while the gate is armed for "limit"
    pending_setup_token: str | None = None
    pending_approval: dict | None = None
    # (cart fingerprint, PayPal-Request-Id) of a payment PayPal never answered: paying
    # the same cart again reuses the id, so PayPal returns that order instead of a new one.
    unanswered_payment: tuple[str, str] | None = None
    # A yes that still needs the typed PIN: (kind, fingerprint of what the yes was for).
    pending_pin: tuple[ConfirmKind, str] | None = None
    vault_unavailable: bool = False
    events: list[Reply] = field(default_factory=list)

    @property
    def store(self) -> Store:
        return self.deps.store

    # ---- speaking -------------------------------------------------------
    def _say(self, speech: str, arm: tuple[ConfirmKind, str] | None = None, **kw) -> Reply:
        """Every reply goes through here: arms the gate only for read-backs."""
        if arm:
            self.gate.arm(*arm)
        else:
            self.gate.disarm()
        self.last_arm = arm
        limit = self.store.spending_limit
        has_pin = self.store.has_pin
        reply = Reply(speech=speech, cart=self.cart.to_json() if self.cart else None,
                      awaiting=arm[0] if arm else None, linked=self.store.linked,
                      spending_limit_eur=str(limit) if limit is not None else None,
                      pin_set=has_pin, pin_over_eur=str(self.store.pin_over_eur) if has_pin else None, **kw)
        self.last_reply = reply
        return reply

    def _over_limit(self, cart: PricedCart) -> bool:
        limit = self.store.spending_limit
        return limit is not None and cart.total_eur > limit

    def _propose(self, prefix: str = "") -> Reply:
        """Read the cart back. Arms payment only if the cart is within the spending limit."""
        assert self.cart is not None
        if self._over_limit(self.cart):
            limit = self.store.spending_limit
            return self._say(
                f"{prefix}That comes to {say_eur(self.cart.total_eur)}, over your spending limit of "
                f"{say_eur(limit)} per order, so I can't pay for it. Remove something, ask for a cheaper option, "
                "or say raise my limit to a new amount.", priority="assertive")
        return self._say(prefix + readback(self.cart, self.deps.today()),
                         arm=("payment", self.cart.fingerprint))

    def welcome(self) -> Reply:
        hint = "" if self.store.linked else NOT_LINKED_HINT
        return self._say(WELCOME + hint)

    def _repeat(self) -> Reply:
        if self.last_reply is None:
            return self.welcome()
        if self.last_arm:  # the same words are spoken again, so the yes stays valid
            self.gate.arm(*self.last_arm)
        return self.last_reply

    # ---- main entry -----------------------------------------------------
    async def handle(self, text: str) -> Reply:
        # Commands are matched with apostrophes as spaces: "what's" -> "what s", "don't" -> "don t".
        norm = normalize_utterance(text).replace("'", " ")
        if not norm:
            return self._say("I didn't hear anything. Press the talk button and try again.")
        if _REPEAT.match(norm):
            return self._repeat()  # a pending PIN step stays pending
        if self.pending_pin:
            # Anything said while EchoCart waits for the PIN cancels that step.
            kind, self.pending_pin = self.pending_pin[0], None
            if _NO.match(norm) or _CANCEL.match(norm):
                return self._decline(kind)

        armed = self.gate.armed_for
        if armed == "payment" and self.cart and self.gate.consume(text, "payment", self.cart.fingerprint):
            return await self._confirmed("payment", self.cart.fingerprint)
        if armed == "refund" and self.refund_plan and self.gate.consume(
                text, "refund", self.refund_plan.fingerprint):
            return await self._refund()  # money goes back to the shopper: no PIN
        if armed == "unlink" and self.gate.consume(text, "unlink", self._unlink_fp()):
            return await self._confirmed("unlink", self._unlink_fp())
        if armed == "limit" and self.gate.consume(text, "limit", self._limit_fp(self.pending_limit)):
            return await self._confirmed("limit", self._limit_fp(self.pending_limit))
        # From here on the gate is disarmed: whatever was said, it was not a valid yes.

        if armed and _NO.match(norm):
            return self._decline(armed)
        if armed and _NEAR_YES.match(norm):
            return self._reask(armed)
        if not armed and is_confirmation(text):
            if self.cart:
                return self._propose("Before paying I'll read the cart again. ")
            return self._say("There's nothing to confirm yet. Tell me what you'd like to buy.")

        if _HELP.match(norm):
            return self._say(HELP)
        if _CANCEL.match(norm):
            self.cart = None
            return self._say("Cart emptied. What would you like instead?")
        if _READ_CART.search(norm):
            return self._propose() if self.cart else self._say("Your cart is empty.")
        if _PIN_CMD.search(norm):
            return self._say("For privacy, a PIN is typed, never spoken. I've moved you to the PIN settings. "
                             "Type 4 to 8 digits there and choose when I should ask for it.",
                             action={"type": "pin_settings"})
        if _UNLINK.search(norm):
            return self._offer_unlink()
        if _LINK.search(norm):
            return self._offer_link()
        if _LIMIT_ASK.search(norm):
            return self._say_limit()
        if _LIMIT_OFF.search(norm):
            return self._request_limit(None)
        m = _LIMIT_SET.search(re.sub(r" (?:euros?|eur)\b", "", norm))
        if m and "limit" in norm:
            amount = _parse_amount(m.group(1) or m.group(2))
            if amount is not None and amount > 0:
                return self._request_limit(amount)
        if _REFUND.search(norm):
            return self._start_refund(text)
        if _REORDER.search(norm):
            return self._reorder()
        if _WHERE.search(norm):
            return self._order_status()
        if _HISTORY.search(norm):
            return self._order_history()
        if _INFO.match(norm):
            return self._product_info(text)
        return await self._understand(text)

    def _decline(self, armed: ConfirmKind) -> Reply:
        if armed == "payment":
            return self._say("Okay, I won't pay. Tell me what to change, or say read my cart.")
        if armed == "refund":
            self.refund_plan = None
            return self._say("Okay, no refund. Your order stays as it is.")
        if armed == "unlink":
            return self._say("Okay, your PayPal account stays linked.")
        self.pending_limit = None
        return self._say_limit("Okay, I haven't changed it. ")

    def _reask(self, armed: ConfirmKind) -> Reply:
        prefix = "Sorry, was that a yes? "
        if armed == "payment" and self.cart:
            return self._propose(prefix)
        if armed == "refund" and self.refund_plan:
            return self._speak_refund(prefix)
        if armed == "unlink":
            return self._offer_unlink(prefix)
        return self._request_limit(self.pending_limit, prefix)

    # ---- PIN: a typed second step after the spoken yes -------------------
    def _needs_pin(self, kind: ConfirmKind) -> bool:
        if not self.store.has_pin:
            return False
        if kind == "payment":
            return self.cart is not None and self.cart.total_eur > self.store.pin_over_eur
        return kind in ("limit", "unlink")  # limit only reaches here when raising or removing it

    def _current_fp(self, kind: ConfirmKind) -> str | None:
        if kind == "payment":
            return self.cart.fingerprint if self.cart else None
        if kind == "unlink":
            return self._unlink_fp()
        if kind == "limit":
            return self._limit_fp(self.pending_limit)
        return None

    async def _confirmed(self, kind: ConfirmKind, fingerprint: str) -> Reply:
        """The spoken yes was valid. Ask for the PIN if this action needs one, else act."""
        if self._needs_pin(kind):
            if self.store.pin_locked(self.deps.now()):
                return self._pin_locked()
            self.pending_pin = (kind, fingerprint)
            return self._say(PIN_PROMPTS[kind], priority="assertive", action={"type": "pin"})
        return await self._act(kind)

    async def _act(self, kind: ConfirmKind) -> Reply:
        if kind == "payment":
            return await self._pay()
        if kind == "unlink":
            return await self._unlink()
        return self._apply_limit(self.pending_limit)

    def _pin_locked(self) -> Reply:
        minutes = self.store.pin_lock_minutes_left(self.deps.now())
        return self._say(f"After too many wrong PINs, payments and account changes are locked for "
                         f"{minutes} more minute{'s' if minutes != 1 else ''}. Nothing was charged or changed.",
                         priority="assertive", earcon="error")

    def _wrong_pin(self, tail: str) -> Reply:
        left = self.store.pin_tries_left
        return self._say(f"That PIN isn't right. {left} {'try' if left == 1 else 'tries'} left. {tail}",
                         priority="assertive", earcon="error",
                         action={"type": "pin"} if self.pending_pin else None)

    async def submit_pin(self, pin: str) -> Reply:
        pending, self.pending_pin = self.pending_pin, None
        if not pending:
            return self._say("There's nothing waiting for your PIN. Ask for what you'd like, say yes to the "
                             "total, and then type your PIN.")
        kind, fingerprint = pending
        if fingerprint != self._current_fp(kind):  # defensive: anything said in between clears pending_pin
            return self._say("That confirmation has expired. Nothing was charged or changed.", priority="assertive")
        result = self.store.check_pin(pin, self.deps.now())
        if result == "ok":
            return await self._act(kind)
        if result == "locked":
            return self._say(f"Too many wrong PINs, so payments and account changes are locked for {LOCK_MINUTES} "
                             "minutes. Nothing was charged or changed.", priority="assertive", earcon="error")
        self.pending_pin = pending
        return self._wrong_pin("Type your PIN again, or press Escape to cancel.")

    def _check_current_pin(self, current: str | None) -> Reply | None:
        """None when the current PIN is right; otherwise the reply to give."""
        result = self.store.check_pin(current or "", self.deps.now())
        if result == "ok":
            return None
        if result == "locked":
            return self._pin_locked()
        return self._wrong_pin("Nothing was changed.")

    def set_pin(self, new_pin: str | None, current_pin: str | None, over_eur: Decimal) -> Reply:
        had = self.store.has_pin
        if over_eur < 0:
            return self._say("The amount can't be negative. Nothing was changed.", priority="assertive",
                             earcon="error")
        if had:
            refused = self._check_current_pin(current_pin)
            if refused:
                return refused
        if had and not new_pin:  # keep the PIN, change when it's asked for
            self.store.set_pin_over(over_eur)
            speech = "PIN setting saved."
        elif not valid_pin(new_pin or ""):
            return self._say("A PIN must be 4 to 8 digits. Nothing was changed.", priority="assertive",
                             earcon="error")
        else:
            self.store.set_pin(new_pin, over_eur)
            speech = "PIN changed." if had else "PIN set."
        when = "every payment" if over_eur == 0 else f"payments over {say_eur(over_eur)}"
        return self._say(f"{speech} After your yes, I'll ask for it on {when}, and before raising your "
                         "spending limit or unlinking PayPal.", priority="assertive", earcon="success")

    def remove_pin(self, current_pin: str | None) -> Reply:
        if not self.store.has_pin:
            return self._say("You don't have a PIN set.")
        refused = self._check_current_pin(current_pin)
        if refused:
            return refused
        self.store.clear_pin()
        self.pending_pin = None
        return self._say("PIN removed. Payments now need only your spoken yes.", priority="assertive",
                         earcon="success")

    # ---- understanding requests ----------------------------------------
    async def _understand(self, text: str) -> Reply:
        current = self.cart.as_items() if self.cart else []
        try:
            raw = await self.deps.parser.parse(text, current)
        except LLMError as e:
            log.warning("parser failed: %s", e)
            return self._say("Sorry, I had trouble understanding that. Please try again, or say help.",
                             priority="assertive", earcon="error")
        result = validate_llm_output(raw, text, self.deps.catalog,
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
            return self._start_refund(text)
        prefix = "Updated. " if resp.action == "edit_cart" and self.cart else ""
        self.cart = result.cart
        return self._propose(prefix)

    def _product_info(self, text: str) -> Reply:
        products = self.deps.matcher.find(text)
        if not products:
            return self._say("I couldn't find that in the shop. Try another name for it.")
        parts = []
        for p in products[:4]:
            stock = "" if p.stock else ", out of stock"
            parts.append(f"{p.brand} {p.name}, {p.pack_size}: {say_eur(p.price_eur)}{stock}")
        more = f" And {len(products) - 4} more." if len(products) > 4 else ""
        tail = " Your cart hasn't changed." if self.cart else " Say what you'd like and how many."
        return self._say("; ".join(parts) + "." + more + tail)

    # ---- paying ---------------------------------------------------------
    def _paypal_missing(self) -> Reply:
        return self._say("PayPal isn't configured on this server, so I can't take payments or refunds. "
                         "The cart is still here.", priority="assertive", earcon="error")

    def _offer_link(self, prefix: str = "") -> Reply:
        if self.deps.paypal is None:
            return self._paypal_missing()
        if self.store.linked:
            return self._say("Your PayPal account is already linked.")
        return self._say(
            prefix + "To link PayPal, press Enter now to open the PayPal window. Log in and approve. "
            "I'll tell you when it's linked. You only do this once.",
            action={"type": "link"})

    async def _pay(self) -> Reply:
        cart = self.cart
        if self.deps.paypal is None:
            return self._paypal_missing()
        if self._over_limit(cart):  # defence in depth: _propose never arms an over-limit cart
            return self._propose()
        if self.pending_approval:  # never create a second order while one awaits approval
            return self._say("A PayPal window is already waiting for you to approve this payment. "
                             "Press Enter to open it again.", priority="assertive",
                             action={"type": "approve", "url": self.pending_approval.get("url")})
        retry = self.unanswered_payment
        ref = retry[1] if retry and retry[0] == cart.fingerprint else f"echocart-{uuid.uuid4().hex[:12]}"
        self.unanswered_payment = None
        if not self.store.linked:
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
                self.store.data.vault_id, cart.total_eur, self._summary(cart), ref)
            if order.get("status") == "PAYER_ACTION_REQUIRED":
                return self._ask_for_approval(order["id"], _link(order, "payer-action"), cart)
            if order.get("status") != "COMPLETED":
                order = await self.deps.paypal.capture_order(order["id"])
        except PayPalUnreachable:
            self.unanswered_payment = (cart.fingerprint, ref)
            reply = self._propose("PayPal didn't answer in time, so I can't tell yet whether that payment "
                                  "went through. If you say yes again, I'll ask PayPal about this same payment. "
                                  "You won't be charged twice. ")
            reply.priority, reply.earcon = "assertive", "error"
            return reply
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
        reply = self._propose(f"The payment didn't go through. PayPal reported {name.replace('_', ' ').lower()}. "
                              "No money was taken. ")
        reply.priority, reply.earcon = "assertive", "error"
        return reply

    @staticmethod
    def _summary(cart: PricedCart) -> str:
        return "; ".join(f"{l.quantity} x {l.product.spoken_name}" for l in cart.lines)

    def _receipt(self, order: dict, cart: PricedCart) -> Reply:
        capture_id = first_capture_id(order)
        record = OrderRecord(
            order_id=order["id"], capture_id=capture_id or "", total_eur=str(cart.total_eur),
            delivery_date=cart.delivery_date.isoformat(),
            summary=self._summary(cart), created_at=self.deps.today().isoformat(),
            items=[OrderLine(l.product.id, l.product.spoken_name, l.quantity, str(l.subtotal_eur))
                   for l in cart.lines])
        self.store.add_order(record)
        self.cart = None
        return self._say(
            f"Payment done. Order number ending {_ending(order['id'])}. Charged {say_eur(cart.total_eur)}. "
            f"Delivery {say_date(cart.delivery_date, self.deps.today())}.",
            priority="assertive", earcon="success",
            receipt={"order_id": order["id"], "capture_id": capture_id, "total_eur": str(cart.total_eur),
                     "delivery_date": cart.delivery_date.isoformat()})

    # ---- orders: reorder, status, history -------------------------------
    def _reorder(self) -> Reply:
        last = self.store.last_order()
        if last is None:
            return self._say("You haven't ordered anything yet. Tell me what you'd like to buy.")
        if not last.items:
            return self._say("I can't see the items of your last order. Tell me what you'd like to buy.")
        try:
            cart = price_cart([{"product_id": l.product_id, "quantity": l.quantity} for l in last.items],
                              self.deps.catalog, self.deps.today())
        except CartError as e:
            return self._say(f"I can't repeat your last order exactly. {e} Tell me what you'd like instead.")
        self.cart = cart
        return self._propose("Your last order again. ")

    def _describe_order(self, order: OrderRecord) -> str:
        due = date.fromisoformat(order.delivery_date)
        today = self.deps.today()
        if order.refunded:
            state = "was refunded"
        elif due < today:
            state = f"was due {say_date(due, today)}, so it should have arrived"
        elif due == today:
            state = "arrives today"
        else:
            state = f"arrives {say_date(due, today)}"
        partly = " Part of it was refunded." if not order.refunded and Decimal(order.refunded_eur) > 0 else ""
        return f"order number ending {_ending(order.order_id)}, {order.summary}, {state}.{partly}"

    def _order_status(self) -> Reply:
        last = self.store.last_order()
        if last is None:
            return self._say("You haven't ordered anything yet.")
        return self._say("Your last " + self._describe_order(last))

    def _order_history(self) -> Reply:
        orders = self.store.data.orders[-3:][::-1]
        if not orders:
            return self._say("You haven't ordered anything yet.")
        if len(orders) == 1:
            o = orders[0]
            return self._say(f"Your last order: {say_eur(Decimal(o.total_eur))}, {self._describe_order(o)} "
                             "Say same as last time to order it again.")
        parts = [f"{i}: {say_eur(Decimal(o.total_eur))}, {self._describe_order(o)}"
                 for i, o in enumerate(orders, 1)]
        return self._say(f"Your last {len(orders)} orders, newest first. " + " ".join(parts) +
                         " Say same as last time to order the newest one again.")

    # ---- refunds --------------------------------------------------------
    def _start_refund(self, text: str = "") -> Reply:
        order = self.store.last_refundable()
        if order is None:
            self.refund_plan = None
            return self._say("There's no order to refund.")
        open_lines = order.open_lines
        named = {p.id for p in self.deps.matcher.find(text)} if text else set()
        chosen = [l for l in open_lines if l.product_id in named]
        if named and not chosen:
            listing = "; ".join(l.name for l in open_lines) or order.summary
            self.refund_plan = None
            return self._say(f"Your last order didn't include that. It has: {listing}. "
                             "Which item should I refund, or say refund my last order for all of it?")
        if not chosen or len(chosen) == len(open_lines):
            lines = open_lines
            amount_eur = order.remaining_eur
            whole = True
        else:
            lines = chosen
            amount_eur = min(sum((Decimal(l.subtotal_eur) for l in lines), Decimal("0")), order.remaining_eur)
            whole = False
        self.refund_plan = RefundPlan(order, lines, amount_eur, whole)
        return self._speak_refund()

    def _speak_refund(self, prefix: str = "") -> Reply:
        plan = self.refund_plan
        money = f"{say_eur(plan.amount_eur)} back to your PayPal."
        if plan.whole_order:
            what = "Refund your last order" if Decimal(plan.order.refunded_eur) == 0 else "Refund the rest of your last order"
            items = "; ".join(l.name for l in plan.lines) or plan.order.summary
            speech = (f"{prefix}{what}, number ending {_ending(plan.order.order_id)}: {items}. {money} "
                      "Say yes to refund, or no to keep the order.")
        else:
            items = "; ".join(f"{l.name}, quantity {l.quantity}" for l in plan.lines)
            speech = (f"{prefix}Refund {items}, from your last order, number ending {_ending(plan.order.order_id)}. "
                      f"{money} The rest of the order stays. Say yes to refund, or no to keep it.")
        return self._say(speech, arm=("refund", plan.fingerprint))

    async def _refund(self) -> Reply:
        plan, self.refund_plan = self.refund_plan, None
        if self.deps.paypal is None:
            return self._paypal_missing()
        full = plan.whole_order and Decimal(plan.order.refunded_eur) == 0
        key = "refund-" + hashlib.sha256(plan.fingerprint.encode()).hexdigest()[:24]
        try:
            refund = await self.deps.paypal.refund_capture(
                plan.order.capture_id, plan.order.order_id, None if full else plan.amount_eur, key)
        except PayPalUnreachable:
            return self._say("PayPal didn't answer in time, so I can't tell yet whether the refund went through. "
                             "Say refund my last order to ask again. It won't be refunded twice.",
                             priority="assertive", earcon="error")
        except PayPalError as e:
            log.warning("refund failed: %s %s", e.status, e.body)
            return self._say("The refund didn't go through. Nothing has changed. Say refund my last order to try again.",
                             priority="assertive", earcon="error")
        ids = None if plan.whole_order else {l.product_id for l in plan.lines}
        self.store.record_refund(plan.order.order_id, refund.get("id"), plan.amount_eur, ids)
        when = "" if refund.get("status", "COMPLETED") == "COMPLETED" else " PayPal is still processing it."
        return self._say(f"Refund done. {say_eur(plan.amount_eur)} will go back to your PayPal account.{when}",
                         priority="assertive", earcon="success")

    # ---- unlink ---------------------------------------------------------
    def _unlink_fp(self) -> str:
        return f"unlink:{self.store.data.vault_id}"

    def _offer_unlink(self, prefix: str = "") -> Reply:
        if not self.store.linked:
            return self._say("No PayPal account is linked.")
        return self._say(f"{prefix}This removes your saved PayPal account from EchoCart. To pay again you'd link it "
                         "again. Say yes to unlink, or no to keep it.", arm=("unlink", self._unlink_fp()))

    async def _unlink(self) -> Reply:
        vault_id = self.store.data.vault_id
        if self.deps.paypal is not None:
            try:
                await self.deps.paypal.delete_payment_token(vault_id)
            except PayPalError as e:
                if e.status != 404:  # already gone at PayPal is fine
                    log.warning("unlink failed: %s %s", e.status, e.body)
                    return self._say("PayPal couldn't remove the saved account. It's still linked. Please try again.",
                                     priority="assertive", earcon="error")
        self.store.unlink()
        return self._say("Your PayPal account is unlinked. EchoCart no longer has a saved way to pay.",
                         priority="assertive", earcon="success")

    # ---- spending limit -------------------------------------------------
    @staticmethod
    def _limit_fp(limit: Decimal | None) -> str:
        return f"limit:{limit}"

    def _say_limit(self, prefix: str = "") -> Reply:
        limit = self.store.spending_limit
        if limit is None:
            return self._say(prefix + "You have no spending limit. Say set my limit to, and an amount in euros, to add one.")
        return self._say(prefix + f"Your spending limit is {say_eur(limit)} per order.")

    def _request_limit(self, new: Decimal | None, prefix: str = "") -> Reply:
        current = self.store.spending_limit
        # Lowering (or setting a first limit) only makes things safer: apply at once.
        if new is not None and (current is None or new <= current):
            return self._apply_limit(new)
        if current is None:
            return self._say("You have no spending limit to remove.")
        self.pending_limit = new
        change = ("remove your spending limit, so any order total can be paid" if new is None
                  else f"raise your spending limit from {say_eur(current)} to {say_eur(new)} per order")
        return self._say(f"{prefix}This will {change}. Say yes to confirm, or no to keep it.",
                         arm=("limit", self._limit_fp(new)))

    def _apply_limit(self, new: Decimal | None) -> Reply:
        self.pending_limit = None
        self.store.set_spending_limit(new)
        speech = ("Spending limit removed." if new is None
                  else f"Spending limit set. I won't pay for any order over {say_eur(new)}.")
        if self.cart:
            return self._propose(speech + " ")
        return self._say(speech)

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
            return self._propose(prefix)
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
                self.store.link(tok["vault_id"], tok["customer_id"])
                reply = (self._propose("PayPal is linked. ") if self.cart
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
