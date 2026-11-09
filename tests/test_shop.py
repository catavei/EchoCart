"""Shops add EchoCart to their own website with embed.js. The demo shop (examples/shop/) runs on
its own address; EchoCart opens only on the shop sites in SHOP_ORIGINS."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import get_settings, parse_origins
from backend.dialog import Deps
from backend.feed import load_catalog
from backend.llm import OfflineCartParser
from backend.store import Store
from tests.test_dialog import TODAY

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = "examples/linden-street-pharmacy.json"


class EmbedOnShopSites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.catalog = load_catalog(SAMPLE)

    def client(self, origins=()):
        deps = Deps(self.catalog, OfflineCartParser(self.catalog), None, Store(Path(self.tmp.name) / "s.json"),
                    "http://localhost:8000", today=lambda: TODAY, shop_origins=origins)
        return TestClient(create_app(deps))

    def test_embed_script_is_served(self):
        with self.client() as client:
            r = client.get("/embed.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("javascript", r.headers["content-type"])
        self.assertIn("window.EchoCart", r.text)

    def test_only_listed_shops_may_show_echocart(self):
        with self.client() as client:
            self.assertEqual(client.get("/").headers["content-security-policy"], "frame-ancestors 'self'")
        with self.client(("http://localhost:8012", "https://shop.example")) as client:
            for path in ("/", "/api/status"):
                self.assertEqual(client.get(path).headers["content-security-policy"],
                                 "frame-ancestors 'self' http://localhost:8012 https://shop.example")

    def test_shop_origins_setting(self):
        self.assertEqual(parse_origins(" http://localhost:8012/ , HTTPS://Shop.example,,https://shop.example"),
                         ("http://localhost:8012", "https://shop.example"))
        self.assertEqual(parse_origins(""), ())
        for bad in ("https://shop.example/basket", "shop.example", "javascript:alert(1)", "https://a b.example", "*"):
            with self.assertRaises(ValueError, msg=bad):
                parse_origins(bad)
        with mock.patch.dict(os.environ, {"SHOP_ORIGINS": "http://localhost:8012"}):
            self.assertEqual(get_settings().shop_origins, ("http://localhost:8012",))

    def test_the_demo_shop_is_its_own_site(self):
        with self.client() as client:
            self.assertEqual(client.get("/shop/").status_code, 404)   # EchoCart doesn't serve it
        page = (ROOT / "examples" / "shop" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<script src="http://localhost:8011/embed.js"></script>', page)
        self.assertNotIn("<iframe", page)   # embed.js adds EchoCart
        self.assertIn("../linden-street-pharmacy.json", (ROOT / "examples" / "shop" / "shop.js").read_text(encoding="utf-8"))

    def test_feed_variant_ids(self):
        self.assertEqual(self.catalog.find("4400101").id, "ibuprofen-200-16-tablets")
        self.assertEqual(self.catalog.find("ibuprofen-200-16-tablets").variant_id, "4400101")
        self.assertIsNone(self.catalog.find("no-such-variant"))


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
            r = self.add(client, sid, "4400102")   # the shop's variant id: ibuprofen, 32 tablets
            self.assertTrue(r["speech"].startswith("Added Linden Health Ibuprofen 200 mg tablets, 32 tablets."))
            r = self.add(client, sid, "vitamin-d3")
            self.assertEqual([l["product_id"] for l in r["cart"]["lines"]], ["ibuprofen-200-16-tablets", "ibuprofen-200-32-tablets", "vitamin-d3"])

    def test_out_of_stock_and_unknown(self):
        with TestClient(create_app(self.deps)) as client:
            sid = client.post("/api/session").json()["session_id"]
            r = self.add(client, sid, "throat-lozenges-blackcurrant")
            self.assertIn("out of stock", r["speech"])
            self.assertIsNone(r["cart"])
            r = self.add(client, sid, "no-such-product")
            self.assertIn("isn't in the shop", r["speech"])
            self.add(client, sid, "x" * 200, status=422)
