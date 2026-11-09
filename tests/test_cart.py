import json
import unittest
from datetime import date
from decimal import Decimal

from backend.cart import CATALOG_PATH, Catalog, CartError, price_cart, readback, say_eur

CATALOG = Catalog.load()
TODAY = date(2026, 11, 6)  # a Friday


class CatalogShape(unittest.TestCase):
    def test_about_forty_unique_products(self):
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        ids = [p["id"] for p in raw]
        self.assertGreaterEqual(len(ids), 38)
        self.assertEqual(len(ids), len(set(ids)))
        for p in raw:
            for field in ("id", "name", "brand", "pack_size", "price_eur", "stock", "delivery_days"):
                self.assertIn(field, p)


class Pricing(unittest.TestCase):
    def test_totals_come_from_catalogue(self):
        cart = price_cart([{"product_id": "bat-aa-4-vm", "quantity": 2},
                           {"product_id": "cln-dish-500", "quantity": 1}], CATALOG, TODAY)
        self.assertEqual(cart.total_eur, Decimal("10.97"))  # 2 x 4.59 + 1.79
        self.assertEqual(cart.to_json()["lines"][0]["subtotal_eur"], "9.18")

    def test_duplicates_merge(self):
        cart = price_cart([{"product_id": "bat-aa-4-vm", "quantity": 1},
                           {"product_id": "bat-aa-4-vm", "quantity": 2}], CATALOG, TODAY)
        self.assertEqual(len(cart.lines), 1)
        self.assertEqual(cart.lines[0].quantity, 3)

    def test_delivery_is_slowest_item(self):
        cart = price_cart([{"product_id": "bat-aa-4-vm", "quantity": 1},
                           {"product_id": "ph-thermometer", "quantity": 1}], CATALOG, TODAY)
        self.assertEqual(cart.delivery_date, date(2026, 11, 9))

    def test_rejects_bad_carts(self):
        for items in ([], [{"product_id": "nope", "quantity": 1}],
                      [{"product_id": "pc-shampoo-400", "quantity": 1}],
                      [{"product_id": "ph-thermometer", "quantity": 11}],
                      [{"product_id": "bat-aa-4-vm", "quantity": 0}]):
            with self.assertRaises(CartError, msg=items):
                price_cart(items, CATALOG, TODAY)

    def test_fingerprint_changes_with_cart(self):
        a = price_cart([{"product_id": "bat-aa-4-vm", "quantity": 2}], CATALOG, TODAY)
        b = price_cart([{"product_id": "bat-aa-4-vm", "quantity": 3}], CATALOG, TODAY)
        c = price_cart([{"product_id": "bat-aa-4-vm", "quantity": 2}], CATALOG, TODAY)
        self.assertNotEqual(a.fingerprint, b.fingerprint)
        self.assertEqual(a.fingerprint, c.fingerprint)


class Speech(unittest.TestCase):
    def test_money(self):
        self.assertEqual(say_eur(Decimal("9")), "9 euros")
        self.assertEqual(say_eur(Decimal("9.85")), "9 euros 85 cents")
        self.assertEqual(say_eur(Decimal("1.01")), "1 euro 1 cent")

    def test_readback_states_total_and_prompt(self):
        cart = price_cart([{"product_id": "bat-aa-4-vm", "quantity": 2}], CATALOG, TODAY)
        text = readback(cart, TODAY)
        self.assertIn("VoltMax AA alkaline batteries, 4 pack, quantity two", text)
        self.assertIn("Total 9 euros 18 cents. Delivery", text)
        self.assertNotIn("lei", text)
        self.assertIn("tomorrow, Saturday 7 November", text)
        self.assertTrue(text.endswith("Say yes to pay, or tell me what to change."))


if __name__ == "__main__":
    unittest.main()
