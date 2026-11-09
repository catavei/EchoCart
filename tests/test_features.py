"""Spending limit, reorder, order status/history, partial refunds, unlink, product questions."""
import unittest
from datetime import date, timedelta
from decimal import Decimal

from tests.test_dialog import DialogBase, TODAY


class SpendingLimit(DialogBase):
    def test_setting_a_limit_is_immediate_and_enforced(self):
        self.link()
        r = self.say("set my spending limit to 8 euros")
        self.assertIn("I won't pay for any order over 8 euros", r.speech)
        self.assertEqual(self.store.spending_limit, Decimal("8"))
        r = self.say("two packs of AA batteries")   # 9.18 euros
        self.assertIsNone(r.awaiting)
        self.assertIn("over your spending limit of 8 euros", r.speech)
        self.say("yes")
        self.assertEqual(self.orders_created(), [])

    def test_editing_under_the_limit_arms_payment(self):
        self.link()
        self.say("set my spending limit to 8 euros")
        self.say("two packs of AA batteries")
        r = self.say("cheaper option")               # 2 x 2.89 = 5.78 euros
        self.assertEqual(r.awaiting, "payment")
        self.say("yes")
        self.assertEqual(len(self.orders_created()), 1)

    def test_raising_needs_a_yes(self):
        self.say("set my spending limit to 8 euros")
        r = self.say("raise my limit to 20 euros")
        self.assertEqual(r.awaiting, "limit")
        self.assertEqual(self.store.spending_limit, Decimal("8"))
        self.assertTrue(self.say("yeah, I think so").speech.startswith("Sorry, was that a yes?"))
        self.assertEqual(self.store.spending_limit, Decimal("8"))
        self.say("yes")
        self.assertEqual(self.store.spending_limit, Decimal("20"))

    def test_lowering_is_immediate(self):
        self.say("set my spending limit to 20 euros")
        r = self.say("set my limit to 10")
        self.assertIsNone(r.awaiting)
        self.assertEqual(self.store.spending_limit, Decimal("10"))

    def test_removing_needs_a_yes_and_no_keeps_it(self):
        self.say("set my spending limit to 8 euros")
        r = self.say("remove my spending limit")
        self.assertEqual(r.awaiting, "limit")
        self.say("no")
        self.assertEqual(self.store.spending_limit, Decimal("8"))
        self.say("remove my spending limit")
        self.say("confirm")
        self.assertIsNone(self.store.spending_limit)

    def test_limit_yes_cannot_pay(self):
        self.link()
        self.say("dish soap")
        self.say("set my spending limit to 8 euros")  # re-reads cart and arms payment (1.79 euros)
        self.say("raise my limit to 20 euros")        # now armed for limit, not payment
        self.say("yes")
        self.assertEqual(self.orders_created(), [])

    def test_ask_limit(self):
        self.assertIn("no spending limit", self.say("what's my limit").speech)
        self.say("set my spending limit to 15 euros")
        self.assertIn("15 euros per order", self.say("what's my spending limit").speech)


class OrdersByVoice(DialogBase):
    def buy(self, request="two packs of AA batteries and dish soap"):
        if not self.store.linked:
            self.link()
        self.say(request)
        return self.say("yes")

    def test_reorder_reads_back_and_needs_yes(self):
        self.buy()
        r = self.say("same as last time")
        self.assertTrue(r.speech.startswith("Your last order again."))
        self.assertEqual(r.awaiting, "payment")
        self.assertEqual(len(self.orders_created()), 1)
        self.say("yes")
        self.assertEqual(len(self.orders_created()), 2)

    def test_reorder_without_history(self):
        self.assertIn("haven't ordered", self.say("order the same again").speech)

    def test_where_is_my_order(self):
        self.buy()
        r = self.say("where's my order")
        self.assertIn("arrives tomorrow, Saturday 7 November", r.speech)
        self.deps.today = lambda: TODAY + timedelta(days=3)
        self.assertIn("should have arrived", self.say("where is my order").speech)

    def test_history(self):
        self.buy()
        self.buy("bin bags")
        r = self.say("what did I order")
        self.assertIn("Your last 2 orders, newest first", r.speech)
        self.assertLess(r.speech.index("Bin bags"), r.speech.index("AA alkaline"))

    def test_history_single_order(self):
        self.buy()
        r = self.say("what did I order")
        self.assertTrue(r.speech.startswith("Your last order: "), r.speech)
        self.assertNotIn("newest first", r.speech)


class PartialRefunds(DialogBase):
    def setUp(self):
        super().setUp()
        self.link()
        self.say("two packs of AA batteries and dish soap")  # 9.18 + 1.79 = 10.97 euros
        self.say("yes")

    def refund_bodies(self):
        return [b for m, p, b in self.fake.calls if p.endswith("/refund")]

    def test_refund_one_item(self):
        r = self.say("refund just the batteries")
        self.assertEqual(r.awaiting, "refund")
        self.assertIn("The rest of the order stays", r.speech)
        self.assertIn("9 euros 18 cents back to your PayPal.", r.speech)
        self.say("yes")
        self.assertEqual(self.refund_bodies(), [{"amount": {"currency_code": "EUR", "value": "9.18"}}])
        order = self.store.data.orders[0]
        self.assertFalse(order.refunded)
        self.assertEqual(order.refunded_eur, "9.18")

    def test_then_refund_the_rest_uses_exact_remainder(self):
        self.say("refund just the batteries")
        self.say("yes")
        r = self.say("refund my last order")
        self.assertIn("Refund the rest of your last order", r.speech)
        self.assertIn("1 euro 79 cents", r.speech)  # 10.97 - 9.18
        self.say("yes")
        self.assertEqual(self.refund_bodies()[-1], {"amount": {"currency_code": "EUR", "value": "1.79"}})
        self.assertTrue(self.store.data.orders[0].refunded)

    def test_refund_item_not_in_order(self):
        r = self.say("refund the coffee")
        self.assertIn("didn't include that", r.speech)
        self.assertIsNone(r.awaiting)

    def test_whole_order_refund_is_full(self):
        self.say("refund my last order")
        self.say("yes")
        self.assertEqual(self.refund_bodies(), [{}])


class Unlink(DialogBase):
    def test_unlink_needs_yes(self):
        self.link()
        r = self.say("unlink my PayPal")
        self.assertEqual(r.awaiting, "unlink")
        self.say("no")
        self.assertTrue(self.store.linked)
        self.say("forget my paypal")
        self.say("yes")
        self.assertFalse(self.store.linked)
        self.assertTrue(any(m == "DELETE" for m, p, b in self.fake.calls))

    def test_unlink_when_not_linked(self):
        self.assertIn("No PayPal account", self.say("unlink paypal").speech)


class ProductQuestions(DialogBase):
    def test_price_question_leaves_cart_alone(self):
        self.link()
        self.say("dish soap")
        r = self.say("how much is coffee")
        self.assertIn("Boaba Ground coffee, 250 g: 4 euros 39 cents", r.speech)
        self.assertIn("Your cart hasn't changed", r.speech)
        self.assertEqual(self.conv.cart.as_items(), [{"product_id": "cln-dish-500", "quantity": 1}])
        self.assertIsNone(r.awaiting)   # a question in between voids the yes
        self.say("yes")
        self.assertEqual(self.orders_created(), [])

    def test_options_and_out_of_stock(self):
        r = self.say("what tea do you have")
        self.assertIn("Black tea bags", r.speech)
        self.assertIn("Mint tea bags", r.speech)
        self.assertIn("out of stock", self.say("do you have shampoo").speech)


class Apostrophes(DialogBase):
    def test_contractions_match_commands(self):
        self.link()
        self.say("dish soap")
        self.assertIn("won't pay", self.say("don't").speech)
        self.assertEqual(self.say("what's in my cart").awaiting, "payment")
        self.assertIn("Casa Clean", self.say("what’s in my basket").speech)  # curly apostrophe


if __name__ == "__main__":
    unittest.main()
