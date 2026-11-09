"""The demo shop website: its product list comes from the same catalogue EchoCart sells from."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import get_settings
from backend.dialog import Deps
from backend.feed import load_catalog
from backend.llm import OfflineCartParser
from backend.store import Store
from tests.test_dialog import TODAY

SAMPLE = "examples/linden-street-pharmacy.json"


class ShopSite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        catalog = load_catalog(SAMPLE)
        self.deps = Deps(catalog, OfflineCartParser(catalog), None, Store(Path(self.tmp.name) / "s.json"),
                         "http://localhost:8000", today=lambda: TODAY, shop_name="Linden Street Pharmacy")

    def test_catalogue_for_the_shop_page(self):
        with TestClient(create_app(self.deps)) as client:
            body = client.get("/api/catalog").json()
        self.assertEqual(body["shop_name"], "Linden Street Pharmacy")
        self.assertEqual(len(body["products"]), 12)
        ibu = next(p for p in body["products"] if p["id"] == "ibuprofen-200-16-tablets")
        self.assertEqual(ibu, {"id": "ibuprofen-200-16-tablets", "name": "Ibuprofen 200 mg tablets",
                               "brand": "Linden Health", "pack_size": "16 tablets", "price_eur": "2.49",
                               "category": ibu["category"], "in_stock": True})
        lozenges = next(p for p in body["products"] if p["id"] == "throat-lozenges-blackcurrant")
        self.assertFalse(lozenges["in_stock"])

    def test_shop_page_is_served(self):
        with TestClient(create_app(self.deps)) as client:
            page = client.get("/shop/")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="voice-panel"', page.text)

    def test_shop_name_setting(self):
        with mock.patch.dict(os.environ, {"SHOP_NAME": " Linden Street Pharmacy "}):
            self.assertEqual(get_settings().shop_name, "Linden Street Pharmacy")
        with mock.patch.dict(os.environ, {"SHOP_NAME": ""}):
            self.assertEqual(get_settings().shop_name, "Demo shop")


class AddToBasket(unittest.TestCase):
    """The shop's Add to basket button adds to the same EchoCart cart, read back, still needing a yes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        catalog = load_catalog(SAMPLE)
        self.deps = Deps(catalog, OfflineCartParser(catalog), None, Store(Path(self.tmp.name) / "s.json"),
                         "http://localhost:8000", today=lambda: TODAY)

    def add(self, client, sid, product_id, status=200):
        r = client.post("/api/cart/add", json={"session_id": sid, "product_id": product_id})
        self.assertEqual(r.status_code, status)
        return r.json()

    def test_adds_and_reads_back(self):
        with TestClient(create_app(self.deps)) as client:
            sid = client.post("/api/session").json()["session_id"]
            r = self.add(client, sid, "ibuprofen-200-16-tablets")
            self.assertTrue(r["speech"].startswith("Added Linden Health Ibuprofen 200 mg tablets, 16 tablets. Your cart:"))
            self.assertIn("Total 2 euros 49 cents.", r["speech"])
            self.assertEqual(r["awaiting"], "payment")
            r = self.add(client, sid, "ibuprofen-200-16-tablets")
            self.assertTrue(r["speech"].startswith("Updated. Your cart:"))
            self.assertIn("quantity two. Total 4 euros 98 cents.", r["speech"])
            r = self.add(client, sid, "vitamin-d3")
            self.assertEqual([l["product_id"] for l in r["cart"]["lines"]], ["ibuprofen-200-16-tablets", "vitamin-d3"])

    def test_out_of_stock_and_unknown(self):
        with TestClient(create_app(self.deps)) as client:
            sid = client.post("/api/session").json()["session_id"]
            r = self.add(client, sid, "throat-lozenges-blackcurrant")
            self.assertIn("out of stock", r["speech"])
            self.assertIsNone(r["cart"])
            r = self.add(client, sid, "no-such-product")
            self.assertIn("isn't in the shop", r["speech"])
            self.add(client, sid, "x" * 200, status=422)
