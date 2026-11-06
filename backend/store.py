"""Tiny JSON store for the single demo shopper: linked wallet, preferences, orders.

A hackathon demo has one shopper per running instance, so a JSON file in
DATA_DIR is enough and keeps the app dependency-free.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path


@dataclass
class OrderLine:
    product_id: str
    name: str
    quantity: int
    subtotal_ron: str
    refunded: bool = False


@dataclass
class OrderRecord:
    order_id: str
    capture_id: str
    total_ron: str
    total_eur: str
    delivery_date: str
    summary: str
    created_at: str = ""
    items: list[OrderLine] = field(default_factory=list)
    refunded_eur: str = "0.00"
    refund_ids: list[str] = field(default_factory=list)

    @property
    def refunded(self) -> bool:
        if self.items:
            return all(line.refunded for line in self.items)
        return Decimal(self.refunded_eur) >= Decimal(self.total_eur)

    @property
    def open_lines(self) -> list[OrderLine]:
        return [line for line in self.items if not line.refunded]

    @property
    def remaining_eur(self) -> Decimal:
        return Decimal(self.total_eur) - Decimal(self.refunded_eur)


@dataclass
class StoreData:
    vault_id: str | None = None
    customer_id: str | None = None
    spending_limit_ron: str | None = None
    orders: list[OrderRecord] = field(default_factory=list)


def _order_from_json(raw: dict) -> OrderRecord:
    raw = dict(raw)
    raw.pop("refunded", None)          # older files stored a flag; now derived
    raw.pop("refund_id", None)
    raw["items"] = [OrderLine(**line) for line in raw.get("items", [])]
    return OrderRecord(**raw)


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.data = self._load()

    def _load(self) -> StoreData:
        if not self.path.is_file():
            return StoreData()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return StoreData(raw.get("vault_id"), raw.get("customer_id"), raw.get("spending_limit_ron"),
                         [_order_from_json(o) for o in raw.get("orders", [])])

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self.data), indent=2), encoding="utf-8")
            tmp.replace(self.path)

    # ---- wallet
    @property
    def linked(self) -> bool:
        return bool(self.data.vault_id)

    def link(self, vault_id: str, customer_id: str | None) -> None:
        self.data.vault_id, self.data.customer_id = vault_id, customer_id
        self.save()

    def unlink(self) -> None:
        self.data.vault_id = self.data.customer_id = None
        self.save()

    # ---- spending limit
    @property
    def spending_limit(self) -> Decimal | None:
        return Decimal(self.data.spending_limit_ron) if self.data.spending_limit_ron else None

    def set_spending_limit(self, limit: Decimal | None) -> None:
        self.data.spending_limit_ron = str(limit) if limit is not None else None
        self.save()

    # ---- orders
    def add_order(self, order: OrderRecord) -> None:
        self.data.orders.append(order)
        self.save()

    def last_order(self) -> OrderRecord | None:
        return self.data.orders[-1] if self.data.orders else None

    def last_refundable(self) -> OrderRecord | None:
        for order in reversed(self.data.orders):
            if not order.refunded:
                return order
        return None

    def record_refund(self, order_id: str, refund_id: str | None, amount_eur: Decimal,
                      product_ids: set[str] | None) -> None:
        """product_ids None means the whole remaining order was refunded."""
        for order in self.data.orders:
            if order.order_id != order_id:
                continue
            order.refunded_eur = str((Decimal(order.refunded_eur) + amount_eur).quantize(Decimal("0.01")))
            if refund_id:
                order.refund_ids.append(refund_id)
            for line in order.items:
                if product_ids is None or line.product_id in product_ids:
                    line.refunded = True
        self.save()
