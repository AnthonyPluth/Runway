"""Churning: credit cards found on your accounts (runway/churn_found.py) and the AI's suggestions for them."""
import io
import json
import unittest
import urllib.error
from unittest import mock

from sqlalchemy import insert, select

from runway import categorize, churn_found, churning, db
from runway import settings_keys as sk
from runway.models import Account, ChurnCard, PlaidAccount, PlaidItem, Transaction
from runway.server.api import churning as api
from runway.server.api import state as api_state
from runway.server.common import ApiError
from tests.shared import DbCase


class CleanProductTests(unittest.TestCase):
    def test_real_account_names(self):
        for name, issuer, display, want in [
            ("Chase Sapphire Reserve (8814)", "chase", None, "Sapphire Reserve"),
            ("Citi®/AAdvantage® Platinum Select® World Elite Mastercard®-9201 (9201)", "citi", None, "AAdvantage Platinum Select"),
            ("Blue Cash Everyday® (5508)", "amex", None, "Blue Cash Everyday"),
            ("Premium Rewards Visa Signature- 4417 (4417)", "chase", None, "Premium Rewards"),
            ("Venture X (7731)", "capital_one", None, "Venture X"),
            ("CREDIT CARD (3392)", "chase", "My Visa", "My Visa"),
            ("CREDIT CARD (3392)", "chase", None, ""),
            ("Costco Anywhere Visa® Card by Citi-6620 (6620)", "citi", None, "Costco Anywhere"),
            ("Capital One Venture Rewards ••4321", "capital_one", None, "Venture Rewards"),
            ("Freedom Unlimited x9876", "other", None, "Freedom Unlimited"),
            ("Venture X 7731", "capital_one", None, "Venture X"),   # an X that is part of the name stays
            ("DISCOVER IT CARD (1234)", "discover", None, "DISCOVER IT"),   # a product named for its issuer keeps it
            ("Discover it Chrome", "discover", None, "Discover it Chrome"),
            ("The Platinum Card® from American Express (1009)", "amex", None, "The Platinum"),
            ("Blue Cash Preferred® Card from American Express", "amex", None, "Blue Cash Preferred"),
            ("WORLD OF HYATT (1234)", "chase", None, "WORLD OF HYATT"),   # "World" in a name stays...
            ("Chase World of Hyatt Credit Card", "chase", None, "World of Hyatt"),
            ("Ink Business Preferred World Mastercard (5555)", "chase", None, "Ink Business Preferred"),   # ...not as a tier
            ("Southwest Rapid Rewards Plus Visa World", "chase", None, "Southwest Rapid Rewards Plus"),
        ]:
            with self.subTest(name=name):
                self.assertEqual(churn_found.clean_product(name, issuer, display), want)


class IssuerTests(unittest.TestCase):
    def test_matches_known_issuers_by_name(self):
        for org, want in [("Chase Bank Alex", "chase"), ("Citibank Online", "citi"), ("Capital One Sam", "capital_one"),
                          ("American Express", "amex"), ("Bank of America", "bank_of_america"), ("U.S. Bank", "us_bank"),
                          ("Barclaycard US", "barclays"), ("Citizens Bank", "other"), (None, "other"), ("Some Credit Union", "other")]:
            with self.subTest(org=org):
                self.assertEqual(churn_found.match_issuer(org), want)

    def test_falls_back_to_the_next_text(self):
        self.assertEqual(churn_found.match_issuer("", "Capital One"), "capital_one")


class FoundTests(DbCase):
    def account(self, id, name, **kw):
        self.c.execute(insert(Account).values(id=id, name=name, kind="credit", owner="Alex", org="Chase Bank Alex", **kw))

    def tx(self, id, account, posted, amount, payee="Shop", description=""):
        self.c.execute(insert(Transaction).values(id=id, account_id=account, posted=posted, amount=amount, payee=payee,
                                                  description=description))

    def drafts(self):
        return {d["account_id"]: d for d in churn_found.found(self.c)["drafts"]}

    def test_prefills_a_draft_from_the_account(self):
        self.account("a1", "Chase Sapphire Reserve (8814)")
        self.tx("t1", "a1", "2024-03-02", -10)
        self.tx("t2", "a1", "2025-03-05", -550, "ANNUAL MEMBERSHIP FEE")
        self.tx("t3", "a1", "2026-03-07", -795, description="Annual Fee")
        self.tx("t4", "a1", "2026-04-01", 795, description="Annual fee refund")   # a refund isn't the fee
        d = self.drafts()["a1"]
        self.assertEqual((d["issuer"], d["product"], d["owner"], d["business"]), ("chase", "Sapphire Reserve", "Alex", 0))
        self.assertEqual(d["annual_fee"], 795.0)   # the latest charge; its month isn't kept (the fee follows the opening month)
        self.assertNotIn("fee_month", d)
        self.assertEqual((d["opened_on"], d["opened_on_estimate"]), ("2024-03-02", True))
        self.assertNotIn("family", d)

    def test_no_transactions_and_no_fee(self):
        self.account("a1", "Venture X (7731)")
        d = self.drafts()["a1"]
        self.assertEqual((d["opened_on"], d["annual_fee"]), (None, None))

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
        churning.save_card(self.c, {"owner": "Alex", "issuer": "chase", "product": "Linked", "opened_on": "2024-01-01",
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
        self.account("a1", "Chase Sapphire Reserve (8814)")
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
        self.webs = []

    def caller(self, reply, cited=(), no_tools=False):
        """A stand-in for categorize.chat: the reply (or an exception to raise), the pages it cites. no_tools: the
        model can't call tools, so the web search tool is refused."""
        def call(api_key, model, prompt, web=None):
            self.prompts.append(prompt)
            self.webs.append(web)
            if no_tools and web == "tool":
                raise categorize.ToolsUnsupported("OpenRouter HTTP 404: No endpoints found that support tool use")
            if isinstance(reply, Exception):
                raise reply
            return reply, list(cited)
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
        self.assertEqual(out["benefits"], [{"name": "Travel credit", "kind": "credit", "amount": 300.0, "period": "annual", "basis": None, "guests": None},
                                           {"name": "Lounge", "kind": "other", "amount": None, "period": "annual", "basis": None, "guests": None}])
        self.assertEqual((out["bonus"], out["sources"], out["web"]), (None, [], True))

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
        self.c.execute(insert(Account).values(id="acct-secret-1", name="Chase Sapphire Reserve (8814)", kind="credit",
                                              owner="Alex", org="Chase Bank Alex", balance=-4321.99))
        churn_found.suggest(self.c, "chase", "Sapphire Reserve (8814)", self.caller("{}"))
        (prompt,) = self.prompts
        self.assertIn("Bank: Chase", prompt)
        self.assertIn('"Sapphire Reserve"', prompt)
        for private in ("acct-secret-1", "Alex", "4321", "8814", "sk-or-test"):
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
        with mock.patch("runway.categorize.chat", self.caller(self.REPLY)):
            out = api.api_churn_suggest(self.c, {}, {"issuer": "chase", "product": "Sapphire Reserve"})
        self.assertEqual(out["currency"], "ur")
        with mock.patch("runway.categorize.chat", self.caller(OSError("down"))):
            with self.assertRaises(ApiError) as cm:
                api.api_churn_suggest(self.c, {}, {"issuer": "chase", "product": "Sapphire Reserve"})
        self.assertEqual(cm.exception.status, 502)
        with self.assertRaises(ApiError) as cm:
            api.api_churn_suggest(self.c, {}, {"issuer": "chase"})
        self.assertEqual(cm.exception.status, 400)

    def test_searches_the_web_unless_switched_off(self):
        churn_found.suggest(self.c, "chase", "Sapphire Reserve", self.caller("{}"))
        self.assertEqual(self.webs, ["tool"])
        self.assertIn("Search the web", self.prompts[-1])
        self.assertIn('"sources"', self.prompts[-1])
        # A model that can't call tools: the web plugin instead (it still searches).
        out = churn_found.suggest(self.c, "chase", "Sapphire Reserve", self.caller("{}", no_tools=True))
        self.assertEqual(self.webs[1:], ["tool", "plugin"])
        self.assertTrue(out["web"])
        # Switched off in Settings: no search, and the prompt says so.
        api_state.api_settings(self.c, {}, {"churn_ai_web": False})
        self.assertFalse(api_state.api_state(self.c, {}, None)["churn_ai_web"])
        out = churn_found.suggest(self.c, "chase", "Sapphire Reserve", self.caller("{}"))
        self.assertEqual(self.webs[-1], None)
        self.assertNotIn("Search the web", self.prompts[-1])
        self.assertNotIn('"sources"', self.prompts[-1])
        self.assertFalse(out["web"])

    def test_the_prompt_asks_for_the_bonus_fee_and_benefits(self):
        churn_found.suggest(self.c, "chase", "Sapphire Reserve", self.caller("{}"))
        (prompt,) = self.prompts
        for asked in ('"bonus"', '"spend"', '"months"', '"annual_fee"', '"portal_name"', '"basis"', '"guests"',
                      "bank's own page", "never guess", "Travel", "Restaurants"):
            self.assertIn(asked, prompt)

    def test_reads_the_json_out_of_prose_with_its_bonus_and_sources(self):
        reply = ("I searched Chase's site [1]. Here is what I found:\n\n```json\n" + json.dumps({
            "currency": "ur", "annual_fee": 795,
            "bonus": {"amount": 100000, "spend": 5000, "months": 3},
            "benefits": [{"name": "Priority Pass", "kind": "access", "period": "annual", "basis": "anniversary", "guests": 2},
                         {"name": "Dining credit", "kind": "credit", "amount": 150, "period": "semiannual", "basis": "calendar", "guests": 4}],
            "sources": ["https://creditcards.chase.com/rewards-credit-cards/sapphire/reserve", "javascript:alert(1)",
                        "ftp://chase.com/x", "https://user:pw@evil.example/", "/relative", 42, "https://bad host.com/"],
        }) + "\n```\n\nNote that offers change. [1] https://creditcards.chase.com")
        cited = ["https://creditcards.chase.com/rewards-credit-cards/sapphire/reserve", "https://www.example-news.com/csr", "data:text/html,x"]
        out = churn_found.suggest(self.c, "chase", "Sapphire Reserve", self.caller(reply, cited))
        self.assertEqual((out["currency"], out["annual_fee"]), ("ur", 795.0))
        self.assertEqual(out["bonus"], {"amount": 100000.0, "spend": 5000.0, "months": 3})
        self.assertEqual(out["benefits"], [
            {"name": "Priority Pass", "kind": "access", "amount": None, "period": "annual", "basis": "anniversary", "guests": 2},
            {"name": "Dining credit", "kind": "credit", "amount": 150.0, "period": "semiannual", "basis": "calendar", "guests": None}])
        self.assertEqual(out["sources"], ["https://creditcards.chase.com/rewards-credit-cards/sapphire/reserve", "https://www.example-news.com/csr"])

    def test_a_bonus_without_an_amount_is_dropped_and_numbers_clamped(self):
        reply = json.dumps({"bonus": {"spend": 4000}, "sources": "https://x.com"})
        out = churn_found.suggest(self.c, "chase", "X", self.caller(reply))
        self.assertEqual((out["bonus"], out["sources"]), (None, []))
        reply = json.dumps({"bonus": {"amount": 1e12, "spend": -3, "months": 99}})
        out = churn_found.suggest(self.c, "chase", "X", self.caller(reply))
        self.assertEqual(out["bonus"], {"amount": churn_found.MAX_BONUS, "spend": 0.0, "months": 24})

    def test_a_failed_web_search_says_so(self):
        with self.assertRaisesRegex(RuntimeError, "web search failed.*turn off web search"):
            churn_found.suggest(self.c, "chase", "X", self.caller(RuntimeError("OpenRouter HTTP 400: plugin unavailable")))
        api_state.api_settings(self.c, {}, {"churn_ai_web": False})
        with self.assertRaises(RuntimeError) as cm:
            churn_found.suggest(self.c, "chase", "X", self.caller(RuntimeError("down")))
        self.assertNotIn("web search", str(cm.exception))


def _response(body: dict):
    resp = mock.MagicMock()
    resp.__enter__.return_value.read.return_value = json.dumps(body).encode()
    return resp


class ChatRequestTests(DbCase):
    """What actually goes to OpenRouter (the HTTP call mocked): the web search tool or plugin for card suggestions only,
    and nothing private."""

    REPLY = {"choices": [{"message": {"content": 'Found it. {"annual_fee": 95, "sources": ["https://www.chase.com/card"]}',
                                      "annotations": [{"type": "url_citation", "url_citation": {"url": "https://www.chase.com/card", "title": "Chase"}},
                                                      {"type": "url_citation", "url_citation": {"url": "https://news.example.com/a"}},
                                                      {"type": "other"}]}}]}

    def setUp(self):
        super().setUp()
        db.set_setting(self.c, sk.OPENROUTER_API_KEY, "sk-or-test")

    def sent(self, urlopen) -> dict:
        (req,), _ = urlopen.call_args
        return json.loads(req.data.decode())

    def test_card_suggestions_carry_the_web_search_tool_and_nothing_private(self):
        self.c.execute(insert(Account).values(id="acct-secret-1", name="Chase Sapphire Reserve (8814)", kind="credit",
                                              owner="Alex", org="Chase Bank Alex", balance=-4321.99))
        with mock.patch("urllib.request.urlopen", return_value=_response(self.REPLY)) as urlopen:
            out = churn_found.suggest(self.c, "chase", "Sapphire Reserve (8814)")
        body = self.sent(urlopen)
        self.assertEqual(body["tools"], [{"type": "openrouter:web_search", "parameters": {
            "max_results": categorize.WEB_RESULTS, "max_total_results": categorize.WEB_TOTAL_RESULTS}}])
        self.assertNotIn("plugins", body)
        self.assertEqual(out["annual_fee"], 95.0)
        self.assertEqual(out["sources"], ["https://www.chase.com/card", "https://news.example.com/a"])
        for private in ("acct-secret-1", "Alex", "4321", "8814", "sk-or-test"):
            self.assertNotIn(private, json.dumps(body))

    def test_a_model_without_tools_gets_the_web_plugin(self):
        refused = urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b'{"error":{"message":"No endpoints found that support tool use"}}'))
        with mock.patch("urllib.request.urlopen", side_effect=[refused, _response(self.REPLY)]) as urlopen:
            out = churn_found.suggest(self.c, "chase", "Sapphire Reserve")
        body = self.sent(urlopen)
        self.assertEqual(body["plugins"], [{"id": "web", "max_results": categorize.WEB_RESULTS}])
        self.assertNotIn("tools", body)
        self.assertEqual(out["annual_fee"], 95.0)

    def test_switched_off_and_categorizing_send_no_web_search(self):
        db.set_setting(self.c, sk.CHURN_AI_WEB, "0")
        with mock.patch("urllib.request.urlopen", return_value=_response(self.REPLY)) as urlopen:
            churn_found.suggest(self.c, "chase", "Sapphire Reserve")
            self.assertFalse({"tools", "plugins"} & set(self.sent(urlopen)))
            self.assertIn("Found it", categorize.call_llm("k", "m", "p"))
            self.assertFalse({"tools", "plugins"} & set(self.sent(urlopen)))

    def test_other_errors_are_not_mistaken_for_a_model_without_tools(self):
        down = urllib.error.HTTPError("u", 502, "Bad Gateway", {}, io.BytesIO(b"upstream tool error"))
        with mock.patch("urllib.request.urlopen", side_effect=down) as urlopen, \
                self.assertRaisesRegex(RuntimeError, "web search failed.*502"):
            churn_found.suggest(self.c, "chase", "Sapphire Reserve")
        self.assertEqual(urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
