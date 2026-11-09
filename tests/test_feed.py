"""A merchant's own catalogue from a Shopify-style product feed (CATALOG_URL)."""
import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

import httpx

from backend.cart import MAX_QUANTITY
from backend.config import ROOT, get_settings
from backend.dialog import Conversation, Deps
from backend.feed import MAX_PRODUCTS, FeedError, load_catalog, products_from_feed
from backend.llm import OfflineCartParser
from tests.test_dialog import TODAY, DialogBase

SAMPLE = "examples/linden-street-pharmacy.json"


def feed(*products, **top):
    return {"products": list(products), **top}


def product(title="Hand gel", handle="hand-gel", variants=None, **kw):
    return {"title": title, "handle": handle, "vendor": "Clearwell", "product_type": "Hygiene",
            "variants": variants or [{"id": 1, "title": "100 ml", "price": "1.99", "available": True}], **kw}


class SampleFeed(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog(SAMPLE)

    def test_each_variant_is_a_product(self):
        ids = [p.id for p in self.catalog]
        self.assertEqual(len(ids), 12)
        self.assertEqual(len(ids), len(set(ids)))
        ibu = self.catalog.get("ibuprofen-200-32-tablets")
        self.assertEqual((ibu.price_eur, ibu.brand, ibu.pack_size), (Decimal("3.99"), "Linden Health", "32 tablets"))
        self.assertEqual(ibu.spoken_name, "Linden Health Ibuprofen 200 mg tablets, 32 tablets")

    def test_stock_delivery_and_default_title(self):
        self.assertEqual(self.catalog.get("throat-lozenges-blackcurrant").stock, 0)   # "available": false
        self.assertEqual(self.catalog.get("throat-lozenges-honey-and-lemon").stock, MAX_QUANTITY)
        self.assertEqual(self.catalog.get("sun-cream-spf50").delivery_days, 2)       # "delivery:2" tag
        self.assertNotIn("delivery:2", self.catalog.get("sun-cream-spf50").keywords)
        thermometer = self.catalog.get("digital-thermometer")
        self.assertEqual(thermometer.pack_size, "")
        self.assertEqual(thermometer.spoken_name, "Linden Health Digital thermometer")

    def test_prompt_lists_the_merchants_products(self):
        prompt = self.catalog.for_prompt()
        self.assertIn("ibuprofen-200-16-tablets | Linden Health Ibuprofen 200 mg tablets | 16 tablets | 2.49 euros", prompt)
        self.assertIn("digital-thermometer | Linden Health Digital thermometer | - | 7.99 euros", prompt)
        self.assertNotIn("()", prompt)

    def test_offline_parser_understands_it(self):
        parser = OfflineCartParser(self.catalog)
        self.assertEqual(parser.parse_sync("two packs of ibuprofen", [])["items"],
                         [{"product_id": "ibuprofen-200-16-tablets", "quantity": 2}])
        # "32 tablets" names the pack size; it isn't a quantity of 32
        self.assertEqual(parser.parse_sync("ibuprofen 32 tablets", [])["items"],
                         [{"product_id": "ibuprofen-200-32-tablets", "quantity": 1}])
        r = parser.parse_sync("painkillers under 3 euros", [])
        self.assertEqual(r["action"], "clarify")
        self.assertIn("Ibuprofen", r["question"])
        self.assertIn("Paracetamol", r["question"])


class FeedShape(unittest.TestCase):
    def test_tags_as_a_comma_separated_string(self):
        [p] = products_from_feed(feed(product(tags="hand gel, sanitiser, delivery: 3")))
        self.assertEqual(p.keywords, ["hand gel", "sanitiser", "100 ml"])
        self.assertEqual(p.delivery_days, 3)

    def test_inventory_quantity_wins_over_available(self):
        [p] = products_from_feed(feed(product(variants=[
            {"id": 1, "title": "100 ml", "price": "1.99", "available": True, "inventory_quantity": 4}])))
        self.assertEqual(p.stock, 4)

    def test_bad_prices_are_skipped_not_guessed(self):
        products = products_from_feed(feed(product(variants=[
            {"id": 1, "title": "100 ml", "price": "free"}, {"id": 2, "title": "250 ml", "price": "0"},
            {"id": 3, "title": "500 ml", "price": "4.5"}])))
        self.assertEqual([(p.pack_size, p.price_eur) for p in products], [("500 ml", Decimal("4.50"))])

    def test_ids_stay_unique(self):
        products = products_from_feed(feed(product(), product()))
        self.assertEqual(len({p.id for p in products}), 2)

    def test_refuses_unusable_feeds(self):
        for bad in ({}, {"products": "x"}, feed(), feed({"handle": "no-title"}),
                    feed(product(variants=[{"id": 1, "price": "x"}])),
                    feed(product(), currency="USD")):
            with self.assertRaises(FeedError, msg=bad):
                products_from_feed(bad)
        too_many = feed(*[product(title=f"Item {i}", handle=f"item-{i}") for i in range(MAX_PRODUCTS + 1)])
        with self.assertRaises(FeedError):
            products_from_feed(too_many)


class Loading(unittest.TestCase):
    def test_no_feed_means_the_built_in_catalogue(self):
        self.assertIn("bat-aa-4-vm", load_catalog(""))

    def test_url(self):
        seen = []

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, json=feed(product()))
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            catalog = load_catalog("https://shop.example/products.json", client)
        self.assertEqual(seen, ["https://shop.example/products.json"])
        self.assertIn("hand-gel", catalog)

    def test_url_failures(self):
        for response in (httpx.Response(404), httpx.Response(200, text="<html>")):
            with httpx.Client(transport=httpx.MockTransport(lambda r, resp=response: resp)) as client:
                with self.assertRaises(FeedError):
                    load_catalog("https://shop.example/products.json", client)
        with self.assertRaises(FeedError):
            load_catalog("ftp://shop.example/products.json")
        with self.assertRaises(FeedError):
            load_catalog("examples/no-such-feed.json")

    def test_relative_paths_resolve_from_the_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feed.json"
            path.write_text(json.dumps(feed(product())), encoding="utf-8")
            self.assertIn("hand-gel", load_catalog(str(path)))
        self.assertTrue((ROOT / SAMPLE).is_file())

    def test_setting(self):
        with mock.patch.dict(os.environ, {"CATALOG_URL": " https://shop.example/products.json "}):
            self.assertEqual(get_settings().catalog_url, "https://shop.example/products.json")


class MerchantCheckout(DialogBase):
    """The same voice checkout and PayPal flow, selling the merchant's products."""

    def setUp(self):
        super().setUp()
        catalog = load_catalog(SAMPLE)
        self.deps = Deps(catalog, OfflineCartParser(catalog), self.paypal, self.store,
                         "http://localhost:8000", today=lambda: TODAY)
        self.conv = Conversation(self.deps)

    def test_buy_from_the_feed(self):
        self.link()
        r = self.say("two packs of ibuprofen")
        self.assertIn("Linden Health Ibuprofen 200 mg tablets, 16 tablets, quantity two. Total 4 euros 98 cents.",
                      r.speech)
        self.assertEqual(r.awaiting, "payment")
        self.assertIn("Payment done", self.say("yes").speech)
        [order] = self.orders_created()
        self.assertEqual(order["purchase_units"][0]["amount"], {"currency_code": "EUR", "value": "4.98"})

    def test_unavailable_variant(self):
        r = self.say("blackcurrant lozenges")
        self.assertIn("out of stock", r.speech)
        self.assertIsNone(r.cart)
