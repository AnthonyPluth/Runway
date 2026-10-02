"""Planned cards and bank bonuses (runway/churn_wishlist.py): what's in the way of applying and the earliest day,
credit scores, Upcoming items and alerts, and turning a plan into a card or a bank bonus when you apply."""
import unittest
from datetime import date

from runway import churn_wishlist as wl
from runway import bank_bonuses, churning, notify
from runway.churning import ChurnError
from tests.shared import DbCase

TODAY = date(2026, 9, 29)
QUIET = {**notify.DEFAULTS, "card_due": False, "low_balance": False, "missed": False, "big_charge": False,
         "sync_failed": False, "churn_fee": False, "churn_bonus": False, "churn_plan": False, "churn_benefit": False}


class WishlistTests(DbCase):
    def card(self, opened, issuer="citi", product="Card", **kw):
        return churning.save_card(self.c, {"owner": "Alex", "issuer": issuer, "product": product, "opened_on": opened, **kw})

    def wish(self, **kw):
        return wl.save(self.c, {"owner": "Alex", "kind": "card", "issuer": "chase", "product": "Sapphire Preferred", **kw})

    def items(self, today=TODAY):
        return {w["id"]: w for w in churning.overview(self.c, today)["wishlist"]}

    def test_chase_at_524_and_the_day_back_under(self):
        for i, opened in enumerate(["2024-11-01", "2025-01-15", "2025-06-01", "2025-09-01", "2026-02-01"]):
            self.card(opened, product=f"Card {i}")
        w = self.wish(family="Sapphire")
        got = self.items()[w]
        self.assertFalse(got["ready"])
        self.assertEqual(got["blockers"], [{"kind": "five24", "date": "2026-11-01",
                                            "text": "Alex is at 5/24; under 5/24 on 2026-11-01"}])
        self.assertEqual(got["earliest_apply"], "2026-11-01")
        up = [u for u in churning.overview(self.c, TODAY)["upcoming"] if u["kind"] == "apply"]
        self.assertEqual((up[0]["date"], up[0]["title"]), ("2026-11-01", "You can apply for Sapphire Preferred on Nov 1"))
        # the 5/24 count isn't changed by a planned card
        self.assertEqual(churning.overview(self.c, TODAY)["five24"]["Alex"]["count"], 5)
        # A Citi card isn't held back by 5/24
        citi = self.wish(issuer="citi", product="Strata Premier")
        self.assertTrue(self.items()[citi]["ready"])

    def test_bonus_rules_holding_and_notes(self):
        self.card("2018-01-01", issuer="amex", product="Gold", bonus=60000, bonus_earned_on="2018-04-01", status="closed")
        gold = self.wish(issuer="amex", product="Gold")
        got = self.items()[gold]
        self.assertEqual((got["blockers"][0]["kind"], got["earliest_apply"], got["ready"]), ("bonus_rule", None, False))
        self.assertIn("Once per lifetime", got["blockers"][0]["text"])
        # Chase: 48 months after the last Sapphire bonus, and not while holding one
        self.card("2024-01-01", issuer="chase", product="Sapphire Reserve", family="Sapphire", bonus=60000,
                  bonus_earned_on="2024-03-01")
        csp = self.wish(family="Sapphire")
        got = self.items()[csp]
        self.assertEqual([b["kind"] for b in got["blockers"]], ["held"])
        # a bonus still being earned from the same issuer: a note, not a blocker
        self.card("2026-09-01", issuer="amex", product="Platinum", bonus=80000, bonus_spend=8000)
        plat = self.wish(issuer="amex", product="Business Platinum", business=True)
        got = self.items()[plat]
        self.assertTrue(got["ready"])
        self.assertEqual(got["hints"], ["Still spending toward Platinum's bonus, $8,000 left"])
        # the date you know better, on a card of the same family
        self.card("2019-01-01", issuer="citi", product="Premier", bonus=60000, bonus_earned_on="2025-01-01",
                  eligible_on="2027-02-01", status="closed")
        prem = self.wish(issuer="citi", product="Premier")
        self.assertEqual(self.items()[prem]["earliest_apply"], "2027-02-01")

    def test_score_and_waiting(self):
        w = self.wish(issuer="citi", product="Double Cash", min_score=740, wait_until="2026-12-01",
                      notes="After the move")
        got = self.items()[w]
        self.assertEqual(got["blockers"], [{"kind": "wait", "text": "You're waiting until 2026-12-01", "date": "2026-12-01"}])
        self.assertEqual(got["hints"], ["No credit score entered for Alex to compare with 740"])
        self.assertEqual(got["notes"], "After the move")   # yours, beside the page's hints
        wl.set_score(self.c, {"owner": "Alex", "score": 705, "as_of": "2026-09-01", "source": "Experian FICO 8"}, TODAY)
        got = self.items()[w]
        self.assertEqual(got["blockers"][1], {"kind": "score", "date": None, "text": "Score 705 of 740 wanted (as of 2026-09-01)"})
        self.assertEqual(got["earliest_apply"], "2026-12-01")   # the dated blockers only
        # an undated blocker: no "you can apply on" item
        self.assertEqual([u for u in churning.overview(self.c, TODAY)["upcoming"] if u["kind"] == "apply"], [])
        wl.set_score(self.c, {"owner": "alex", "score": "745"}, TODAY)
        s = churning.overview(self.c, TODAY)["scores"]["Alex"]
        self.assertEqual((s["score"], s["as_of"], len(s["history"])), (745, "2026-09-29", 2))
        self.assertTrue(self.items(date(2026, 12, 1))[w]["ready"])
        wl.set_score(self.c, {"owner": "Alex", "score": ""}, TODAY)   # removes today's
        self.assertEqual(wl.scores(self.c)["Alex"]["score"], 705)
        for bad, msg in (({"owner": "Alex", "score": 200}, "between"), ({"owner": "", "score": 700}, "person"),
                         ({"owner": "Alex", "score": 700, "as_of": "x"}, "date")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ChurnError, msg):
                wl.set_score(self.c, bad, TODAY)

    def test_application_link(self):
        w = self.wish(apply_url=" https://creditcards.chase.com/apply?x=1 ")
        self.assertEqual(self.items()[w]["apply_url"], "https://creditcards.chase.com/apply?x=1")
        wl.save(self.c, {"apply_url": ""}, w)
        self.assertIsNone(self.items()[w]["apply_url"])
        wl.save(self.c, {"apply_url": "http://example.com/apply"}, w)
        for bad in ("javascript:alert(1)", "data:text/html,x", "ftp://example.com", "example.com/apply", "https://", "https://a b.com",
                    "https://example.com/" + "x" * 500):
            with self.subTest(link=bad), self.assertRaises(churning.ChurnError):
                wl.save(self.c, {"apply_url": bad}, w)
        self.assertEqual(self.items()[w]["apply_url"], "http://example.com/apply")

    def test_prior_planned_cards_can_count(self):
        for i, opened in enumerate(["2025-01-15", "2025-06-01", "2025-09-01", "2026-02-01"]):
            self.card(opened, product=f"Card {i}")
        self.wish(issuer="citi", product="Strata Premier", priority=1)
        plain = self.wish(priority=2)
        counted = self.wish(product="Freedom Flex", priority=3, assume_prior_planned=True)
        got = self.items()
        self.assertTrue(got[plain]["ready"])
        self.assertEqual(got[counted]["blockers"][0]["kind"], "five24")
        self.assertIn("counting the cards planned before this one", got[counted]["blockers"][0]["text"])

    def test_bank_rule_and_an_offer_that_ends_first(self):
        b = bank_bonuses.save(self.c, {"owner": "Alex", "bank": "Chase", "opened_on": "2025-12-01", "bonus": 300,
                                       "received_on": "2026-03-01", "repeat_months": 24})
        w = wl.save(self.c, {"owner": "Alex", "kind": "bank_bonus", "bank": "chase", "bonus": 400,
                             "offer_expires_on": "2026-10-10", "requirements": "$500 direct deposit"})
        got = self.items()[w]
        self.assertEqual([(x["kind"], x["date"]) for x in got["blockers"]], [("bonus_rule", "2028-03-01"), ("offer", None)])
        self.assertEqual(got["blockers"][1]["text"], "The offer ends on 2026-10-10, before you could apply")
        up = [u for u in churning.overview(self.c, TODAY)["upcoming"] if u.get("wish_id") == w]
        self.assertEqual([(u["kind"], u["title"], u["warn"]) for u in up], [("offer_ends", "Offer for chase ends Oct 10", True)])
        keys = [a["key"] for a in notify.alerts(self.c, TODAY, QUIET)]
        self.assertEqual(keys, [f"churnoffer:{w}:2026-10-10"])
        # A bank whose bonus you're earning now: a note; a bank you haven't had: ready
        bank_bonuses.save(self.c, {"owner": "Alex", "bank": "Citi", "opened_on": "2026-09-01", "bonus": 200})
        citi = wl.save(self.c, {"owner": "Alex", "kind": "bank_bonus", "bank": "Citi", "bonus": 300})
        got = self.items()[citi]
        self.assertEqual((got["ready"], got["hints"]), (True, ["A Citi bonus is still being earned"]))
        ended = wl.save(self.c, {"owner": "Alex", "kind": "bank_bonus", "bank": "SoFi", "bonus": 300, "offer_expires_on": "2026-09-01"})
        self.assertEqual(self.items()[ended]["blockers"][0]["text"], "The offer ended on 2026-09-01")
        # The rule on your last bonus from the bank applies when the plan has none
        bank_bonuses.save(self.c, {"repeat_months": ""}, b)
        self.assertEqual(self.items()[w]["hints"], ["You've had a bonus from this bank: add its rule (months between bonuses) "
                                                    "to know when"])

    def test_ready_alerts_ordering_and_applied(self):
        first = self.wish(issuer="citi", product="Strata Premier", annual_fee=95, bonus=60000, currency="ty",
                          bonus_spend=4000, bonus_months=3, family="Strata")
        second = self.wish(issuer="amex", product="Gold")
        self.wish(owner="Sam", issuer="amex", product="Green")
        got = churning.overview(self.c, TODAY)
        self.assertEqual([(w["owner"], w["priority"]) for w in got["wishlist"]], [("Alex", 1), ("Alex", 2), ("Sam", 3)])   # one order for everyone
        now = [u for u in got["upcoming"] if u["kind"] == "apply"]
        self.assertIn("You can apply for Strata Premier now", [u["title"] for u in now])
        keys = [a["key"] for a in notify.alerts(self.c, TODAY, QUIET)]
        self.assertIn(f"churnapply:{first}", keys)
        self.assertEqual(notify.alerts(self.c, TODAY, {**QUIET, "churn_apply": False}), [])
        wl.save(self.c, {"priority": 1}, second)
        wl.save(self.c, {"priority": 2}, first)
        alex = [w["id"] for w in churning.overview(self.c, TODAY)["wishlist"] if w["owner"] == "Alex"]
        self.assertEqual(alex, [second, first])

        r = wl.applied(self.c, first, {"opened_on": "2026-09-28"}, TODAY)
        self.assertEqual(r["kind"], "card")
        card = next(c for c in churning.overview(self.c, TODAY)["cards"] if c["id"] == r["id"])
        self.assertEqual((card["product"], card["issuer"], card["annual_fee"], card["bonus"], card["currency"], card["bonus_spend"],
                          card["opened_on"], card["family"]),
                         ("Strata Premier", "citi", 95.0, 60000.0, "ty", 4000.0, "2026-09-28", "Strata"))
        w = self.items()[first]
        self.assertEqual((w["status"], w["applied_id"], w["applied_on"], w["ready"]), ("applied", r["id"], "2026-09-28", False))
        self.assertEqual(churning.overview(self.c, TODAY)["wishlist"][-1]["id"], first)   # done ones last
        self.assertEqual(churning.overview(self.c, TODAY)["five24"]["Alex"]["count"], 1)
        with self.assertRaisesRegex(ChurnError, "already"):
            wl.applied(self.c, first, {}, TODAY)

        bank = wl.save(self.c, {"owner": "Alex", "kind": "bank_bonus", "bank": "SoFi", "account_type": "savings",
                                "requirements": "$5,000 deposit", "repeat_months": 12})
        with self.assertRaisesRegex(ChurnError, "bonus first"):
            wl.applied(self.c, bank, {}, TODAY)
        wl.save(self.c, {"bonus": 300}, bank)
        r = wl.applied(self.c, bank, {}, TODAY)
        b = next(x for x in churning.overview(self.c, TODAY)["bank"] if x["id"] == r["id"])
        self.assertEqual((r["kind"], b["bank"], b["account_type"], b["bonus"], b["other_reqs"], b["repeat_months"], b["opened_on"]),
                         ("bank_bonus", "SoFi", "savings", 300.0, "$5,000 deposit", 12, TODAY.isoformat()))
        wl.save(self.c, {"status": "dropped"}, second)
        self.assertEqual([u for u in churning.overview(self.c, TODAY)["upcoming"] if u.get("wish_id") == second], [])
        wl.remove(self.c, second)
        self.assertNotIn(second, self.items())

    def test_validation(self):
        for bad, msg in (({"kind": "loan"}, "card or a bank bonus"), ({"issuer": "nope"}, "bank"), ({"product": ""}, "name"),
                         ({"status": "maybe"}, "status"), ({"min_score": 1000}, "between"), ({"currency": "gold"}, "paid in"),
                         ({"owner": "Joint"}, "one person"), ({"priority": 0}, "between"), ({"wait_until": "later"}, "date")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ChurnError, msg):
                self.wish(**bad)
        with self.assertRaisesRegex(ChurnError, "Enter the bank"):
            wl.save(self.c, {"owner": "Alex", "kind": "bank_bonus"})
        with self.assertRaisesRegex(ChurnError, "checking, savings"):
            wl.save(self.c, {"owner": "Alex", "kind": "bank_bonus", "bank": "X", "account_type": "brokerage"})
        with self.assertRaisesRegex(ChurnError, "not found"):
            wl.save(self.c, {"product": "X"}, 999)
        with self.assertRaisesRegex(ChurnError, "not found"):
            wl.applied(self.c, 999, {}, TODAY)


if __name__ == "__main__":
    unittest.main()
