"""Catalogue, cart pricing and spoken read-backs.

All money is computed here from catalog.json, in Decimal. Nothing the LLM says
about prices or totals is ever used.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from pydantic import BaseModel, Field

CATALOG_PATH = Path(__file__).with_name("catalog.json")
MAX_QUANTITY = 20
CENT = Decimal("0.01")


class Product(BaseModel):
    id: str
    name: str
    name_ro: str
    brand: str
    pack_size: str
    price_ron: Decimal
    stock: int = Field(ge=0)
    delivery_days: int = Field(ge=0)
    category: str
    keywords: list[str] = []

    @property
    def spoken_name(self) -> str:
        return f"{self.brand} {self.name}, {self.pack_size}"


class Catalog:
    def __init__(self, products: list[Product]):
        self._by_id = {p.id: p for p in products}

    @classmethod
    def load(cls, path: Path = CATALOG_PATH) -> "Catalog":
        return cls([Product(**p) for p in json.loads(path.read_text(encoding="utf-8"))])

    def get(self, product_id: str) -> Product | None:
        return self._by_id.get(product_id)

    def __contains__(self, product_id: str) -> bool:
        return product_id in self._by_id

    def __iter__(self):
        return iter(self._by_id.values())

    def for_prompt(self) -> str:
        """Compact catalogue for the LLM: id, names, brand, pack, price, stock."""
        rows = [
            f"{p.id} | {p.brand} {p.name} ({p.name_ro}) | {p.pack_size} | {p.price_ron} lei | stock {p.stock}"
            for p in self
        ]
        return "\n".join(rows)


class CartError(ValueError):
    """The requested cart can't be built (unknown product, no stock, bad qty)."""


@dataclass(frozen=True)
class CartLine:
    product: Product
    quantity: int

    @property
    def subtotal_ron(self) -> Decimal:
        return (self.product.price_ron * self.quantity).quantize(CENT)


@dataclass(frozen=True)
class PricedCart:
    lines: tuple[CartLine, ...]
    total_ron: Decimal
    total_eur: Decimal
    delivery_date: date

    @property
    def fingerprint(self) -> str:
        """Identifies exactly what the shopper heard: items, quantities and totals."""
        key = "|".join(f"{l.product.id}x{l.quantity}" for l in self.lines)
        key += f"|{self.total_ron}|{self.total_eur}|{self.delivery_date.isoformat()}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def as_items(self) -> list[dict]:
        return [{"product_id": l.product.id, "quantity": l.quantity} for l in self.lines]

    def to_json(self) -> dict:
        return {
            "lines": [
                {
                    "product_id": l.product.id,
                    "name": l.product.spoken_name,
                    "quantity": l.quantity,
                    "unit_price_ron": str(l.product.price_ron),
                    "subtotal_ron": str(l.subtotal_ron),
                }
                for l in self.lines
            ],
            "total_ron": str(self.total_ron),
            "total_eur": str(self.total_eur),
            "delivery_date": self.delivery_date.isoformat(),
            "fingerprint": self.fingerprint,
        }


def ron_to_eur(amount_ron: Decimal, eur_per_ron: Decimal) -> Decimal:
    return (amount_ron * eur_per_ron).quantize(CENT, rounding=ROUND_HALF_UP)


def price_cart(items: list[dict], catalog: Catalog, eur_per_ron: Decimal,
               today: date | None = None) -> PricedCart:
    """Build a priced cart from [{product_id, quantity}], merging duplicate ids."""
    if not items:
        raise CartError("The cart is empty.")
    merged: dict[str, int] = {}
    for item in items:
        pid, qty = item["product_id"], int(item["quantity"])
        if pid not in catalog:
            raise CartError(f"Unknown product id {pid!r}.")
        merged[pid] = merged.get(pid, 0) + qty
    lines = []
    for pid, qty in merged.items():
        product = catalog.get(pid)
        if not 1 <= qty <= MAX_QUANTITY:
            raise CartError(f"Quantity {qty} for {product.spoken_name} is out of range.")
        if qty > product.stock:
            raise CartError(f"Only {product.stock} of {product.spoken_name} in stock.")
        lines.append(CartLine(product, qty))
    total = sum((l.subtotal_ron for l in lines), Decimal("0")).quantize(CENT)
    days = max(l.product.delivery_days for l in lines)
    return PricedCart(
        lines=tuple(lines),
        total_ron=total,
        total_eur=ron_to_eur(total, eur_per_ron),
        delivery_date=(today or date.today()) + timedelta(days=days),
    )


# ---- speech formatting ----------------------------------------------------
_NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                 "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
                 "seventeen", "eighteen", "nineteen", "twenty"]


def say_count(n: int) -> str:
    return _NUMBER_WORDS[n] if 0 <= n < len(_NUMBER_WORDS) else str(n)


def _say_money(amount: Decimal, major: tuple[str, str], minor: tuple[str, str]) -> str:
    whole, cents = divmod(int((amount * 100).quantize(Decimal("1"))), 100)
    text = f"{whole} {major[0] if whole == 1 else major[1]}"
    if cents:
        text += f" {cents} {minor[0] if cents == 1 else minor[1]}"
    return text


def say_lei(amount: Decimal) -> str:
    """49.90 -> '49 lei 90 bani' (how Romanians say prices)."""
    return _say_money(amount, ("leu", "lei"), ("ban", "bani"))


def say_eur(amount: Decimal) -> str:
    """9.85 -> '9 euros 85 cents'."""
    return _say_money(amount, ("euro", "euros"), ("cent", "cents"))


def say_date(d: date, today: date | None = None) -> str:
    today = today or date.today()
    if d == today + timedelta(days=1):
        prefix = "tomorrow, "
    else:
        prefix = ""
    return f"{prefix}{d.strftime('%A')} {d.day} {d.strftime('%B')}"


def describe_lines(cart: PricedCart) -> str:
    # "quantity two" reads unambiguously in every TTS voice; "2 ×" is read as "times".
    parts = [f"{l.product.spoken_name}, quantity {say_count(l.quantity)}" for l in cart.lines]
    return "; ".join(parts)


def readback(cart: PricedCart, today: date | None = None) -> str:
    """The exact sentence that must be spoken before a payment 'yes' counts."""
    return (
        f"Your cart: {describe_lines(cart)}. "
        f"Total {say_lei(cart.total_ron)}, charged as {say_eur(cart.total_eur)}. "
        f"Delivery {say_date(cart.delivery_date, today)}. "
        "Say yes to pay, or tell me what to change."
    )
