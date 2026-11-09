"""A merchant's own catalogue, loaded from a Shopify-style product feed.

By default EchoCart sells from its demo catalogue (catalog.json). A merchant
can instead set CATALOG_URL to their product feed: a URL or a local file in
the shape Shopify storefronts serve at /products.json:

    {"products": [{"id", "title", "handle", "vendor", "product_type", "tags",
                   "variants": [{"id", "title", "price", "available",
                                 "inventory_quantity" (optional)}]}]}

Each variant becomes one product the shopper can buy. Prices are read as
euros, the currency PayPal is charged in; a feed that names another currency
is refused. Everything else in the feed (descriptions, images, dates) is
ignored. Prices still come only from here, never from the language model.
"""
from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from .cart import CENT, MAX_QUANTITY, Catalog, Product
from .config import ROOT

log = logging.getLogger("echocart.feed")

MAX_PRODUCTS = 500          # the whole catalogue goes into the language model's prompt
MAX_FEED_BYTES = 5_000_000
DEFAULT_DELIVERY_DAYS = 1
_DELIVERY_TAG = re.compile(r"^delivery[-_ ]?(?:days)?\s*:\s*(\d{1,2})$", re.IGNORECASE)


class FeedError(ValueError):
    """The product feed can't be used."""


def load_catalog(source: str | None, client: httpx.Client | None = None) -> Catalog:
    """The merchant's feed when CATALOG_URL is set, otherwise the built-in catalogue."""
    if not source:
        return Catalog.load()
    products = products_from_feed(load_feed(source, client))
    log.info("Catalogue: %d products from %s", len(products), source)
    return Catalog(products)


def load_feed(source: str, client: httpx.Client | None = None) -> dict:
    if "://" in source:
        if not source.startswith(("https://", "http://")):
            raise FeedError(f"CATALOG_URL must be an http(s) URL or a file path, not {source!r}.")
        own = client is None
        client = client or httpx.Client(timeout=15.0, follow_redirects=True)
        try:
            resp = client.get(source, headers={"Accept": "application/json"})
        except httpx.HTTPError as e:
            raise FeedError(f"Couldn't fetch the product feed: {type(e).__name__}") from e
        finally:
            if own:
                client.close()
        if resp.status_code != 200:
            raise FeedError(f"The product feed returned HTTP {resp.status_code}.")
        raw = resp.content
    else:
        path = Path(source)
        path = path if path.is_absolute() else ROOT / path
        if not path.is_file():
            raise FeedError(f"No product feed at {path}.")
        raw = path.read_bytes()
    if len(raw) > MAX_FEED_BYTES:
        raise FeedError("The product feed is larger than 5 MB.")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise FeedError("The product feed isn't valid JSON.") from e


def products_from_feed(feed: object) -> list[Product]:
    if not isinstance(feed, dict) or not isinstance(feed.get("products"), list):
        raise FeedError('The product feed needs a "products" list.')
    currency = str(feed.get("currency") or "EUR").upper()
    if currency != "EUR":
        raise FeedError(f"The product feed is priced in {currency}; EchoCart charges PayPal in EUR.")

    products: list[Product] = []
    seen: set[str] = set()
    skipped = 0
    for item in feed["products"]:
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            skipped += 1
            continue
        variants = [v for v in item.get("variants") or [] if isinstance(v, dict)]
        tags = _tags(item.get("tags"))
        delivery = next((int(m.group(1)) for t in tags if (m := _DELIVERY_TAG.match(t))),
                        DEFAULT_DELIVERY_DAYS)
        keywords = [t for t in tags if not _DELIVERY_TAG.match(t)]
        handle = _slug(item.get("handle") or item["title"])
        category = _slug(item.get("product_type") or "") or "other"
        for v in variants:
            price = _price(v.get("price"))
            if price is None:
                skipped += 1
                continue
            pack = str(v.get("title") or "").strip()
            if pack.lower() == "default title":
                pack = ""
            pid = handle if len(variants) == 1 else f"{handle}-{_slug(pack) or v.get('id')}"
            if pid in seen:
                pid = f"{pid}-{v.get('id')}"
            seen.add(pid)
            products.append(Product(
                id=pid, name=str(item["title"]).strip(), brand=str(item.get("vendor") or "").strip(),
                pack_size=pack, price_eur=price, stock=_stock(v), delivery_days=delivery,
                category=category, keywords=keywords + ([pack.lower()] if pack else []),
            ))
    if skipped:
        log.warning("Product feed: skipped %d entries without a title or a valid price", skipped)
    if not products:
        raise FeedError("The product feed has no products with a valid price.")
    if len(products) > MAX_PRODUCTS:
        raise FeedError(f"The product feed has {len(products)} products; the limit is {MAX_PRODUCTS}.")
    return products


def _tags(raw: object) -> list[str]:
    """Shopify's storefront feed sends a list; its admin API sends one comma-separated string."""
    parts = raw.split(",") if isinstance(raw, str) else raw if isinstance(raw, list) else []
    return [str(t).strip() for t in parts if str(t).strip()]


def _price(raw: object) -> Decimal | None:
    try:
        price = Decimal(str(raw)).quantize(CENT)
    except (InvalidOperation, ValueError):
        return None
    return price if price > 0 else None


def _stock(variant: dict) -> int:
    qty = variant.get("inventory_quantity")
    if isinstance(qty, int) and not isinstance(qty, bool):
        return max(qty, 0)
    # The public feed only says whether a variant can be bought.
    return MAX_QUANTITY if variant.get("available", True) else 0


def _slug(text: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
