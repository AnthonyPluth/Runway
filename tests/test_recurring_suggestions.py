"""The Recurring form's API contract (amounts stay signed) and dismissing suggestions."""
import json
import unittest

from sqlalchemy import insert, select, update

from runway.storage import db
from runway.domain import forecast
from runway.storage import settings_keys as sk
from runway.server.api import recurring as api_recurring
from runway.server.common import ApiError
from runway.storage.models import Recurring, Transaction
from tests.shared import TODAY, LedgerCase


class SuggestionDismissTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 3000.0)
        self.acct("sav", "savings", 500.0)
        for d in ["2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"]:
            self.tx("chk", d, -2500.0, "MORTGAGE CO", "Mortgage")
        for d in ["2026-08-14", "2026-08-28", "2026-09-11"]:
            self.tx("chk", d, 3100.0, "ACME PAYROLL", "Income")

    def matches(self):
        return {s["match"] for s in api_recurring.api_recurring_suggestions(self.conn, None, None)}

    def test_each_suggestion_has_a_stable_key(self):
        s = {x["match"]: x for x in forecast.suggest_recurring(self.conn, TODAY)}
        self.assertEqual(s["mortgage co"]["key"], "chk|mortgage co|monthly")
        self.assertEqual(s["acme payroll"]["key"], forecast.suggestion_key("chk", "ACME Payroll ", "biweekly"))

    def test_a_suggestion_says_the_range_its_amounts_came_in(self):
        self.tx("chk", "2026-09-30", -2600.0, "MORTGAGE CO", "Mortgage")
        s = {x["match"]: x for x in forecast.suggest_recurring(self.conn, TODAY)}
        self.assertEqual((s["mortgage co"]["amount_low"], s["mortgage co"]["amount_high"]), (2500.0, 2600.0))
        self.assertEqual((s["acme payroll"]["amount_low"], s["acme payroll"]["amount_high"]), (3100.0, 3100.0))

    def test_dismissing_hides_that_suggestion_only(self):
        self.assertEqual({s["match"] for s in forecast.suggest_recurring(self.conn, TODAY)}, {"mortgage co", "acme payroll"})
        self.assertEqual(api_recurring.api_recurring_suggestion_dismiss(self.conn, None, {"key": "chk|mortgage co|monthly"}), {"ok": True})
        self.assertEqual({s["match"] for s in forecast.suggest_recurring(self.conn, TODAY)}, {"acme payroll"})

    def test_a_dismissal_is_kept_in_settings_and_survives_a_new_connection(self):
        api_recurring.api_recurring_suggestion_dismiss(self.conn, None, {"key": "chk|mortgage co|monthly"})
        api_recurring.api_recurring_suggestion_dismiss(self.conn, None, {"key": "chk|mortgage co|monthly"})
        api_recurring.api_recurring_suggestion_dismiss(self.conn, None, {"key": "chk|acme payroll|biweekly"})
        self.assertEqual(json.loads(db.get_setting(self.conn, sk.RECURRING_SUGGESTIONS_DISMISSED)),
                         ["chk|acme payroll|biweekly", "chk|mortgage co|monthly"])
        other = db.connect(self.path)
        try:
            self.assertEqual(forecast.suggest_recurring(other, TODAY), [])
        finally:
            other.close()

    def test_the_same_payee_on_another_account_or_schedule_is_still_offered(self):
        api_recurring.api_recurring_suggestion_dismiss(self.conn, None, {"key": "sav|mortgage co|monthly"})
        self.assertIn("mortgage co", {s["match"] for s in forecast.suggest_recurring(self.conn, TODAY)})

    def test_an_unreadable_list_is_treated_as_empty(self):
        for raw in ("not json", '{"a": 1}', '[1, "chk|mortgage co|monthly"]'):
            db.set_setting(self.conn, sk.RECURRING_SUGGESTIONS_DISMISSED, raw)
            want = {"acme payroll"} if "mortgage" in raw else {"mortgage co", "acme payroll"}
            self.assertEqual({s["match"] for s in forecast.suggest_recurring(self.conn, TODAY)}, want, raw)

    def test_a_one_time_item_doesnt_hide_a_payee_that_repeats(self):
        self.conn.execute(insert(Recurring).values(name="Escrow", account_id="chk", amount=-2500, frequency="once",
                                                   anchor_date="2026-06-01", match="mortgage co"))
        self.assertIn("mortgage co", {s["match"] for s in forecast.suggest_recurring(self.conn, TODAY)})
        self.conn.execute(update(Recurring).values(frequency="monthly"))
        self.assertNotIn("mortgage co", {s["match"] for s in forecast.suggest_recurring(self.conn, TODAY)})

    def test_rejects_a_missing_or_silly_key(self):
        for body in ({}, {"key": ""}, {"key": "  "}, {"key": 5}, {"key": "x" * 301}):
            with self.assertRaises(ApiError):
                api_recurring.api_recurring_suggestion_dismiss(self.conn, None, body)


class SuggestionRestoreTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 3000.0)
        for d in ["2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"]:
            self.tx("chk", d, -2500.0, "MORTGAGE CO", "Mortgage")
        self.key = "chk|mortgage co|monthly"

    def dismissed(self):
        return api_recurring.api_recurring_suggestions_dismissed(self.conn, None, None)

    def test_lists_what_a_dismissed_key_says(self):
        self.assertEqual(self.dismissed(), [])
        api_recurring.api_recurring_suggestion_dismiss(self.conn, None, {"key": self.key})
        [d] = self.dismissed()
        self.assertEqual({k: d[k] for k in ("key", "account_id", "match", "frequency")},
                         {"key": self.key, "account_id": "chk", "match": "mortgage co", "frequency": "monthly"})
        self.assertTrue(d["account_name"])

    def test_a_merchant_with_a_bar_and_a_gone_account_still_list(self):
        key = forecast.suggestion_key("old", "A|B pay", "weekly")
        db.set_setting(self.conn, sk.RECURRING_SUGGESTIONS_DISMISSED, json.dumps([key, "junk"]))
        self.assertEqual(self.dismissed(), [{"key": key, "account_id": "old", "account_name": None, "match": "a|b pay", "name": "a|b pay",
                                             "frequency": "weekly"}])

    def test_a_dismissed_one_is_named_as_its_transactions_are(self):
        api_recurring.api_recurring_suggestion_dismiss(self.conn, None, {"key": self.key})
        payee = self.conn.execute(select(Transaction.payee).where(Transaction.account_id == "chk")).scalar()
        self.assertNotEqual(payee, "mortgage co")
        self.assertEqual(self.dismissed()[0]["name"], payee)

    def test_restoring_offers_it_again_and_keeps_the_others_dismissed(self):
        other = "chk|acme payroll|biweekly"
        api_recurring.api_recurring_suggestion_dismiss(self.conn, None, {"key": self.key})
        api_recurring.api_recurring_suggestion_dismiss(self.conn, None, {"key": other})
        self.assertEqual(forecast.suggest_recurring(self.conn, TODAY), [])
        self.assertEqual(api_recurring.api_recurring_suggestion_restore(self.conn, None, {"key": self.key}), {"ok": True})
        self.assertEqual([s["key"] for s in forecast.suggest_recurring(self.conn, TODAY)], [self.key])
        self.assertEqual([d["key"] for d in self.dismissed()], [other])
        self.assertEqual(json.loads(db.get_setting(self.conn, sk.RECURRING_SUGGESTIONS_DISMISSED)), [other])

    def test_restoring_what_isnt_dismissed_is_an_error(self):
        for body in ({}, {"key": 5}, {"key": self.key}):
            with self.assertRaises(ApiError):
                api_recurring.api_recurring_suggestion_restore(self.conn, None, body)


class CardSuggestionTests(LedgerCase):
    """Credit cards are read like cash accounts: a card's repeating charges are suggested, on that card."""
    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 3000.0)
        self.acct("cc", "credit", -400.0, pay_from="chk")
        for d in ["2026-07-05", "2026-08-05", "2026-09-05"]:
            self.tx("cc", d, -15.99, "STREAMCO", "Entertainment")

    def suggestions(self):
        return {s["match"]: s for s in forecast.suggest_recurring(self.conn, TODAY)}

    def test_a_monthly_card_charge_is_suggested_on_the_card(self):
        s = self.suggestions()["streamco"]
        self.assertEqual((s["key"], s["account_id"], s["amount"], s["frequency"], s["anchor_date"]),
                         ("cc|streamco|monthly", "cc", -15.99, "monthly", "2026-09-05"))
        self.assertEqual((s["amount_low"], s["amount_high"]), (15.99, 15.99))

    def test_adding_it_puts_the_item_on_the_card_and_its_statements(self):
        s = self.suggestions()["streamco"]
        api_recurring.api_recurring_add(self.conn, None, {k: s[k] for k in ("name", "account_id", "amount", "frequency", "anchor_date", "match")})
        self.assertEqual([tuple(r) for r in self.conn.execute(select(Recurring.account_id, Recurring.amount)).fetchall()], [("cc", -15.99)])
        self.assertEqual({e["account_id"] for e in forecast.build(self.conn, TODAY, 60)["charges"]}, {"cc"})
        self.assertNotIn("streamco", self.suggestions())   # now it's an item

    def test_card_payments_and_transfers_are_not_suggested(self):
        for d in ["2026-07-20", "2026-08-20", "2026-09-20"]:
            self.tx("cc", d, 300.0, "PAYMENT THANK YOU", "Credit Card Payment")
            self.tx("chk", d, -300.0, "CARD AUTOPAY", "Credit Card Payment")
            self.tx("cc", d, -50.0, "MOVE MONEY", "Transfer")
            self.tx("chk", d, -50.0, "MOVE MONEY", "Transfer")
        self.assertEqual(set(self.suggestions()), {"streamco"})

    def test_dismissing_a_card_suggestion_hides_it(self):
        api_recurring.api_recurring_suggestion_dismiss(self.conn, None, {"key": "cc|streamco|monthly"})
        self.assertEqual(self.suggestions(), {})
        [d] = api_recurring.api_recurring_suggestions_dismissed(self.conn, None, None)
        self.assertEqual((d["key"], d["account_id"]), ("cc|streamco|monthly", "cc"))
        api_recurring.api_recurring_suggestion_restore(self.conn, None, {"key": "cc|streamco|monthly"})
        self.assertIn("streamco", self.suggestions())

    def test_cash_accounts_are_suggested_as_before_and_loans_and_investments_are_not(self):
        self.acct("loan", "loan", -9000.0)
        self.acct("brk", "investment", 5000.0)
        for d in ["2026-07-01", "2026-08-01", "2026-09-01"]:
            self.tx("chk", d, -900.0, "RENT CO", "Rent")
            self.tx("loan", d, -200.0, "LOAN FEE", None)
            self.tx("brk", d, -200.0, "FUND FEE", None)
        s = self.suggestions()
        self.assertEqual(set(s), {"streamco", "rent co"})
        self.assertEqual((s["rent co"]["account_id"], s["rent co"]["amount"]), ("chk", -900.0))


class AmountSignTests(LedgerCase):
    """The form sends the signed amount (negative for money out); the API stores what it's given."""
    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 3000.0)

    def add(self, amount):
        r = api_recurring.api_recurring_add(self.conn, None, {"name": "x", "account_id": "chk", "amount": amount,
                                                              "anchor_date": "2026-09-06"})
        return self.conn.execute(select(Recurring.amount).where(Recurring.id == r["id"])).fetchone()[0]

    def test_signed_amounts_are_stored_as_given(self):
        self.assertEqual(self.add(-120), -120.0)
        self.assertEqual(self.add("-120.50"), -120.5)
        self.assertEqual(self.add(3100), 3100.0)
        self.assertEqual(self.add("3100.00"), 3100.0)

    def test_a_missing_amount_is_an_error(self):
        for amount in (None, "", "abc"):
            with self.assertRaises(ApiError):
                self.add(amount)


if __name__ == "__main__":
    unittest.main()
