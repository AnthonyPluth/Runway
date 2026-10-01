"""Churning: credit cards found on your accounts (runway/churn_found.py) and the AI's suggestions for them."""
import json
import unittest
from unittest import mock

from sqlalchemy import insert, select

from runway import churn_found, churning, db
from runway import settings_keys as sk
from runway.models import Account, ChurnCard, PlaidAccount, PlaidItem, Transaction
from runway.server.api import churning as api
from runway.server.common import ApiError
from tests.shared import DbCase


class CleanProductTests(unittest.TestCase):
    def test_real_account_names(self):
        for name, issuer, display, want in [
            ("Chase Sapphire Reserve (1034)", "chase", None, "Sapphire Reserve"),
            ("Citi®/AAdvantage® Platinum Select® World Elite Mastercard®-2136 (2136)", "citi", None, "AAdvantage Platinum Select"),
            ("Blue Cash Everyday® (3009)", "amex", None, "Blue Cash Everyday"),
            ("Premium Rewards Visa Signature- 5773 (5773)", "chase", None, "Premium Rewards"),
            ("Venture X (3030)", "capital_one", None, "Venture X"),
            ("CREDIT CARD (8668)", "chase", "CSP", "CSP"),
            ("CREDIT CARD (8668)", "chase", None, ""),
            ("Costco Anywhere Visa® Card by Citi-3152 (3152)", "citi", None, "Costco Anywhere"),
            ("Capital One Venture Rewards ••4321", "capital_one", None, "Venture Rewards"),
            ("Freedom Unlimited x9876", "other", None, "Freedom Unlimited"),
            ("Venture X 3030", "capital_one", None, "Venture X"),   # an X that is part of the name stays
            ("WORLD OF HYATT (1234)", "chase", None, "WORLD OF HYATT"),   # "World" in a name stays...
            ("Chase World of Hyatt Credit Card", "chase", None, "World of Hyatt"),
            ("Ink Business Preferred World Mastercard (5555)", "chase", None, "Ink Business Preferred"),   # ...not as a tier
            ("Southwest Rapid Rewards Plus Visa World", "chase", None, "Southwest Rapid Rewards Plus"),
        ]:
            with self.subTest(name=name):
                self.assertEqual(churn_found.clean_product(name, issuer, display), want)


class IssuerTests(unittest.TestCase):
    def test_matches_known_issuers_by_name(self):
        for org, want in [("Chase Bank Anthony", "chase"), ("Citibank Online", "citi"), ("Capital One Sara", "capital_one"),
                          ("American Express", "amex"), ("Bank of America", "bank_of_america"), ("U.S. Bank", "us_bank"),
                          ("Barclaycard US", "barclays"), ("Citizens Bank", "other"), (None, "other"), ("Some Credit Union", "other")]:
            with self.subTest(org=org):
                self.assertEqual(churn_found.match_issuer(org), want)

    def test_falls_back_to_the_next_text(self):
        self.assertEqual(churn_found.match_issuer("", "Capital One"), "capital_one")


class FoundTests(DbCase):
    def account(self, id, name, **kw):
        self.c.execute(insert(Account).values(id=id, name=name, kind="credit", owner="Anthony", org="Chase Bank Anthony", **kw))

    def tx(self, id, account, posted, amount, payee="Shop", description=""):
        self.c.execute(insert(Transaction).values(id=id, account_id=account, posted=posted, amount=amount, payee=payee,
                                                  description=description))

    def drafts(self):
        return {d["account_id"]: d for d in churn_found.found(self.c)["drafts"]}

    def test_prefills_a_draft_from_the_account(self):
        self.account("a1", "Chase Sapphire Reserve (1034)")
        self.tx("t1", "a1", "2024-03-02", -10)
        self.tx("t2", "a1", "2025-03-05", -550, "ANNUAL MEMBERSHIP FEE")
        self.tx("t3", "a1", "2026-03-07", -795, description="Annual Fee")
        self.tx("t4", "a1", "2026-04-01", 795, description="Annual fee refund")   # a refund isn't the fee
        d = self.drafts()["a1"]
        self.assertEqual((d["issuer"], d["product"], d["owner"], d["business"]), ("chase", "Sapphire Reserve", "Anthony", 0))
        self.assertEqual((d["annual_fee"], d["fee_month"]), (795.0, 3))   # the latest charge
        self.assertEqual((d["opened_on"], d["opened_on_estimate"]), ("2024-03-02", True))
        self.assertNotIn("family", d)

    def test_no_transactions_and_no_fee(self):
        self.account("a1", "Venture X (3030)")
        d = self.drafts()["a1"]
        self.assertEqual((d["opened_on"], d["annual_fee"], d["fee_month"]), (None, None, None))

    def test_joint_owner_is_left_for_you_and_business_is_by_name(self):
        self.c.execute(insert(Account).values(id="j", name="Ink Business Cash (1111)", kind="credit", owner="Joint"))
        self.c.execute(insert(Account).values(id="k", name="Ink Cash (2222)", kind="credit", owner=""))
        d = self.drafts()
        self.assertEqual((d["j"]["owner"], d["j"]["business"], d["k"]["owner"], d["k"]["business"]), ("", 1, "", 0))

    def test_issuer_from_the_plaid_institution(self):
        self.c.execute(insert(PlaidItem).values(item_id="i1", access_token="x", institution_name="Capital One"))
        self.c.execute(insert(PlaidAccount).values(plaid_account_id="p1", item_id="i1"))
        self.c.execute(insert(Account).values(id="a2", name="Quicksilver", kind="credit", org="Unknown", plaid_account_id="p1"))
        self.assertEqual(self.drafts()["a2"]["issuer"], "capital_one")

    def test_leaves_out_hidden_linked_dismissed_and_other_kinds(self):
        self.account("shown", "Shown (1)")
        self.account("hidden", "Hidden (2)", hidden=1)
        self.account("linked", "Linked (3)")
        self.account("gone", "Dismissed (4)")
        self.c.execute(insert(Account).values(id="chk", name="Checking", kind="checking"))
        churning.save_card(self.c, {"owner": "Anthony", "issuer": "chase", "product": "Linked", "opened_on": "2024-01-01",
                                    "account_id": "linked"})
        churn_found.dismiss(self.c, "gone")
        out = churn_found.found(self.c)
        self.assertEqual([d["account_id"] for d in out["drafts"]], ["shown"])
        self.assertEqual([x["account_id"] for x in out["dismissed"]], ["gone"])

    def test_dismiss_is_remembered_and_undone(self):
        self.account("a1", "One (1)")
        api.api_churn_found_dismiss(self.c, {}, {}, "a1")
        self.assertEqual(json.loads(db.get_setting(self.c, sk.CHURN_FOUND_DISMISSED)), ["a1"])
        self.assertEqual(api.api_churning_found(self.c, {}, {})["drafts"], [])
        api.api_churn_found_undismiss(self.c, {}, {}, "a1")
        self.assertEqual([d["account_id"] for d in api.api_churning_found(self.c, {}, {})["drafts"]], ["a1"])
        with self.assertRaises(ApiError):
            api.api_churn_found_dismiss(self.c, {}, {}, "nope")

    def test_a_draft_saves_as_a_card(self):
        self.account("a1", "Chase Sapphire Reserve (1034)")
        self.tx("t1", "a1", "2024-03-02", -10)
        d = self.drafts()["a1"]
        card_id = churning.save_card(self.c, {k: d[k] for k in ("owner", "issuer", "product", "business", "account_id", "opened_on")})
        self.assertEqual(self.c.execute(select(ChurnCard.product).where(ChurnCard.id == card_id)).scalar(), "Sapphire Reserve")
        self.assertEqual(churn_found.found(self.c)["drafts"], [])


class SuggestTests(DbCase):
    def setUp(self):
        super().setUp()
        names = {c["name"] for c in churning.spending_categories(self.c)}
        assert {"Travel", "Restaurants"} <= names, names
        db.set_setting(self.c, sk.OPENROUTER_API_KEY, "sk-or-test")
        self.prompts = []

    def caller(self, reply):
        def call(api_key, model, prompt, timeout=0):
            self.prompts.append(prompt)
            if isinstance(reply, Exception):
                raise reply
            return reply
        return call

    REPLY = json.dumps({
        "family": "Sapphire", "currency": "ur", "base_rate": 1, "annual_fee": 550, "portal_name": "Chase Travel",
        "rates": [{"category": "travel", "multiplier": 3}, {"category": "Restaurants", "multiplier": 3},
                  {"category": "Made Up", "multiplier": 5}, {"category": "Travel", "multiplier": 8, "portal_only": True}],
        "benefits": [{"name": "Travel credit", "kind": "credit", "amount": 300, "period": "annual"},
                     {"name": "Lounge", "kind": "bogus", "amount": 50, "period": "weekly"}],
    })

    def test_validates_and_keeps_only_what_runway_has(self):
        out = churn_found.suggest(self.c, "chase", "Sapphire Reserve", self.caller(self.REPLY))
        self.assertEqual((out["family"], out["currency"], out["base_rate"], out["annual_fee"], out["portal_name"]),
                         ("Sapphire", "ur", 1.0, 550.0, "Chase Travel"))
        self.assertEqual(out["rates"], [{"category": "Travel", "multiplier": 3.0, "portal_only": 0},
                                        {"category": "Restaurants", "multiplier": 3.0, "portal_only": 0},
                                        {"category": "Travel", "multiplier": 8.0, "portal_only": 1}])
        self.assertEqual(out["benefits"], [{"name": "Travel credit", "kind": "credit", "amount": 300.0, "period": "annual"},
                                           {"name": "Lounge", "kind": "other", "amount": None, "period": "annual"}])

    def test_drops_unknown_currencies_and_clamps_numbers(self):
        reply = json.dumps({"currency": "bitcoin", "base_rate": 9999, "annual_fee": -5, "family": 12,
                            "benefits": [{"name": "Big", "kind": "credit", "amount": 1e9}, "junk", {"kind": "credit"}]})
        out = churn_found.suggest(self.c, "other", "Mystery", self.caller("Sure!\n```json\n" + reply + "\n```"))
        self.assertIsNone(out["currency"])
        self.assertEqual((out["base_rate"], out["annual_fee"], out["family"]), (churn_found.MAX_RATE, 0.0, "12"))
        self.assertEqual(out["benefits"][0]["amount"], churn_found.MAX_CREDIT)
        self.assertEqual(len(out["benefits"]), 1)
        out = churn_found.suggest(self.c, "chase", "X", self.caller(json.dumps({"currency": "Chase Ultimate Rewards"})))
        self.assertEqual(out["currency"], "ur")   # by name

    def test_the_request_carries_the_bank_and_card_name_only(self):
        self.c.execute(insert(Account).values(id="acct-secret-1", name="Chase Sapphire Reserve (1034)", kind="credit",
                                              owner="Anthony", org="Chase Bank Anthony", balance=-4321.99))
        churn_found.suggest(self.c, "chase", "Sapphire Reserve (1034)", self.caller("{}"))
        (prompt,) = self.prompts
        self.assertIn("Bank: Chase", prompt)
        self.assertIn('"Sapphire Reserve"', prompt)
        for private in ("acct-secret-1", "Anthony", "4321", "1034", "sk-or-test"):
            self.assertNotIn(private, prompt)

    def test_failures(self):
        with self.assertRaisesRegex(RuntimeError, "failed.*boom"):
            churn_found.suggest(self.c, "chase", "X", self.caller(TimeoutError("boom")))
        with self.assertRaisesRegex(RuntimeError, "expected format"):
            churn_found.suggest(self.c, "chase", "X", self.caller("I don't know"))
        with self.assertRaises(churning.ChurnError):
            churn_found.suggest(self.c, "nope", "X", self.caller("{}"))
        with self.assertRaises(churning.ChurnError):
            churn_found.suggest(self.c, "chase", "  ", self.caller("{}"))
        self.assertEqual(len(self.prompts), 2)
        db.set_setting(self.c, sk.OPENROUTER_API_KEY, None)
        with self.assertRaisesRegex(churning.ChurnError, "OpenRouter API key"):
            churn_found.suggest(self.c, "chase", "X", self.caller("{}"))
        self.assertEqual(len(self.prompts), 2)   # no key: nothing was sent

    def test_the_endpoint_reports_errors(self):
        with mock.patch("runway.categorize.call_llm", self.caller(self.REPLY)):
            out = api.api_churn_suggest(self.c, {}, {"issuer": "chase", "product": "Sapphire Reserve"})
        self.assertEqual(out["currency"], "ur")
        with mock.patch("runway.categorize.call_llm", self.caller(OSError("down"))):
            with self.assertRaises(ApiError) as cm:
                api.api_churn_suggest(self.c, {}, {"issuer": "chase", "product": "Sapphire Reserve"})
        self.assertEqual(cm.exception.status, 502)
        with self.assertRaises(ApiError) as cm:
            api.api_churn_suggest(self.c, {}, {"issuer": "chase"})
        self.assertEqual(cm.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
