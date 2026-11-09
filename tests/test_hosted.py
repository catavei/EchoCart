"""Hosted demo: each browser is its own shopper, so judges trying it at once don't share a cart,
a linked PayPal account, orders or a PIN."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import get_settings
from backend.dialog import Deps
from backend.llm import OfflineCartParser
from backend.store import Store
from tests.test_dialog import CATALOG, TODAY


class PerBrowserShoppers(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "shoppers"
        self.deps = Deps(CATALOG, OfflineCartParser(CATALOG), None, Store(Path(tmp.name) / "store.json"),
                         "http://localhost:8000", today=lambda: TODAY, shoppers_dir=self.dir)

    def start(self, client, shopper_id=None):
        return client.post("/api/session", json={"shopper_id": shopper_id}).json()

    def test_each_browser_gets_its_own_shopper(self):
        with TestClient(create_app(self.deps)) as client:
            a, b = self.start(client), self.start(client)
            self.assertRegex(a["shopper_id"], r"^[0-9a-f]{32}$")
            self.assertNotEqual(a["shopper_id"], b["shopper_id"])
            client.post("/api/utterance", json={"session_id": a["session_id"], "text": "set my spending limit to 20 euros"})
            again = self.start(client, a["shopper_id"])
            other = self.start(client, b["shopper_id"])
        self.assertEqual(again["shopper_id"], a["shopper_id"])
        self.assertEqual(again["spending_limit_eur"], "20")
        self.assertIsNone(other["spending_limit_eur"])

    def test_shopper_survives_a_restart(self):
        with TestClient(create_app(self.deps)) as client:
            a = self.start(client)
            client.post("/api/utterance", json={"session_id": a["session_id"], "text": "set my spending limit to 20 euros"})
        with TestClient(create_app(self.deps)) as client:
            again = self.start(client, a["shopper_id"])
        self.assertEqual(again["shopper_id"], a["shopper_id"])
        self.assertEqual(again["spending_limit_eur"], "20")

    def test_made_up_ids_get_a_fresh_shopper(self):
        with TestClient(create_app(self.deps)) as client:
            for bad in ["0" * 32, "../store", "x" * 32, ""]:
                reply = self.start(client, bad)
                self.assertNotEqual(reply["shopper_id"], bad)
                self.assertRegex(reply["shopper_id"], r"^[0-9a-f]{32}$")
            self.assertEqual(client.post("/api/session").status_code, 200)   # no body at all
        self.assertFalse((self.dir.parent / "store.json").exists())

    def test_single_shopper_mode_is_unchanged(self):
        deps = Deps(CATALOG, OfflineCartParser(CATALOG), None, self.deps.store,
                    "http://localhost:8000", today=lambda: TODAY)
        with TestClient(create_app(deps)) as client:
            a = self.start(client)
            client.post("/api/utterance", json={"session_id": a["session_id"], "text": "set my spending limit to 20 euros"})
            b = client.post("/api/session").json()
        self.assertIsNone(a["shopper_id"])
        self.assertEqual(b["spending_limit_eur"], "20")

    def test_settings(self):
        with mock.patch.dict(os.environ, {"SHOPPERS": "per_browser", "APP_BASE_URL": "",
                                          "RENDER_EXTERNAL_URL": "https://echocart.onrender.com/"}):
            s = get_settings()
        self.assertEqual(s.shoppers, "per_browser")
        self.assertEqual(s.app_base_url, "https://echocart.onrender.com")
        with mock.patch.dict(os.environ, {"SHOPPERS": "", "APP_BASE_URL": "http://localhost:9000"}):
            s = get_settings()
        self.assertEqual(s.shoppers, "single")
        self.assertEqual(s.app_base_url, "http://localhost:9000")


if __name__ == "__main__":
    unittest.main()
