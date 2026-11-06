"""Tiny JSON store for the single demo shopper: linked wallet + order history.

A hackathon demo has one shopper per running instance, so a JSON file in
DATA_DIR is enough and keeps the app dependency-free.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class OrderRecord:
    order_id: str
    capture_id: str
    total_ron: str
    total_eur: str
    delivery_date: str
    summary: str
    refunded: bool = False
    refund_id: str | None = None


@dataclass
class StoreData:
    vault_id: str | None = None
    customer_id: str | None = None
    orders: list[OrderRecord] = field(default_factory=list)


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.data = self._load()

    def _load(self) -> StoreData:
        if not self.path.is_file():
            return StoreData()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return StoreData(raw.get("vault_id"), raw.get("customer_id"),
                         [OrderRecord(**o) for o in raw.get("orders", [])])

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self.data), indent=2), encoding="utf-8")
            tmp.replace(self.path)

    @property
    def linked(self) -> bool:
        return bool(self.data.vault_id)

    def link(self, vault_id: str, customer_id: str | None) -> None:
        self.data.vault_id, self.data.customer_id = vault_id, customer_id
        self.save()

    def unlink(self) -> None:
        self.data.vault_id = self.data.customer_id = None
        self.save()

    def add_order(self, order: OrderRecord) -> None:
        self.data.orders.append(order)
        self.save()

    def last_refundable(self) -> OrderRecord | None:
        for order in reversed(self.data.orders):
            if not order.refunded:
                return order
        return None

    def mark_refunded(self, order_id: str, refund_id: str | None) -> None:
        for order in self.data.orders:
            if order.order_id == order_id:
                order.refunded, order.refund_id = True, refund_id
        self.save()
