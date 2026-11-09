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
