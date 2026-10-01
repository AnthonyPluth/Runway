"""Churning (runway/churning.py), the second round: portal-only earning rates in the best-card ranking, rates sent
with a new card, plans for a card (keep, close, product change) with Done and undo, hiding a card from Upcoming, snoozing
a to-do, program currencies with estimated values, and whose card it is."""
import unittest
from datetime import date

from runway import churning, notify
from runway.churning import ChurnError
from tests.shared import DbCase

TODAY = date(2026, 9, 29)
VALS = {"cash": {"name": "Cash back", "cents": 1.0}, "c1": {"name": "Capital One miles", "cents": 1.4},
        "ur": {"name": "UR", "cents": 1.5}}
PARENTS = {"Travel": None, "Hotels": "Travel", "Restaurants": None}


def card(id, product, currency, base=1, owner="Alex", **kw):
    return {"id": id, "owner": owner, "issuer": "other", "product": product, "opened_on": "2025-01-01", "status": "open",
            "currency": currency, "base_rate": base, **kw}


class PortalRankTests(unittest.TestCase):
    cards = [card(1, "Venture X", "c1", 2, portal_name="Capital One Travel"),
             card(2, "Sapphire Reserve", "ur", 1, portal_name="Chase Travel"),
             card(3, "Double Cash", "cash", 2)]
    rates = {2: {"Travel": 3}}
    portal = {1: {"Hotels": 10}, 2: {"Travel": 8}}

    def rank(self, category, **kw):
        return churning.best_cards(self.cards, self.rates, VALS, category, PARENTS, TODAY, portal_rates=self.portal, **kw)

    def test_without_the_portal_normal_rates_rank(self):
        got = self.rank("Hotels", amount=100)
        self.assertEqual([(g["id"], g["multiplier"]) for g in got], [(2, 3), (1, 2), (3, 2)])
        vx = got[1]
        self.assertFalse(vx["needs_portal"])
        self.assertEqual(vx["portal_option"]["multiplier"], 10)
        self.assertEqual(vx["portal_option"]["return_pct"], 14.0)
        self.assertEqual(vx["portal_option"]["value"], 14.0)
        self.assertEqual(vx["note"], "10x if booked through Capital One Travel")
        self.assertEqual(got[0]["note"], "8x if booked through Chase Travel")   # the parent's portal rate
        self.assertIsNone(got[2]["portal_option"])

    def test_with_the_portal_they_count(self):
        got = self.rank("Hotels", portal=True, amount=100)
        self.assertEqual([(g["id"], g["multiplier"], g["needs_portal"]) for g in got],
                         [(1, 10, True), (2, 8, True), (3, 2, False)])
        self.assertEqual((got[0]["return_pct"], got[0]["value"], got[0]["note"]),
                         (14.0, 14.0, "Only when booked through Capital One Travel"))
        self.assertIsNone(got[0]["portal_option"])
        # A category without portal rates is unchanged by the flag
        self.assertEqual([g["id"] for g in self.rank("Restaurants", portal=True)], [1, 3, 2])

    def test_a_portal_rate_no_better_is_ignored_and_ties_prefer_no_portal(self):
        cards = [card(1, "A Card", "cash", 5, portal_name="Portal"), card(2, "B Card", "cash", 1, portal_name="Portal")]
        got = churning.best_cards(cards, {}, VALS, "Hotels", PARENTS, TODAY, portal_rates={1: {"Hotels": 3}, 2: {"Hotels": 5}},
                                  portal=True)
        self.assertEqual([(g["id"], g["needs_portal"]) for g in got], [(1, False), (2, True)])   # 5% each: no portal first
        self.assertIsNone(got[0]["portal_option"])
        unnamed = churning.best_cards([card(1, "X", "cash", 1)], {}, VALS, "Hotels", PARENTS, TODAY,
                                      portal_rates={1: {"Travel": 4}})
        self.assertEqual(unnamed[0]["note"], "4x if booked through the issuer's portal")


class ChurnDbTests(DbCase):
    def setUp(self):
        super().setUp()
        self.c.execute("INSERT INTO accounts(id, name, kind, owner) VALUES ('cc', 'Venture X', 'credit', 'Alex')")
        self.c.execute("INSERT INTO categories(name, parent) VALUES ('Hotels', 'Travel')")

    def add(self, **kw):
        return churning.save_card(self.c, {"owner": "Alex", "issuer": "chase", "product": "Card", "opened_on": "2025-10-10", **kw})

    def out(self, today=TODAY):
        return churning.overview(self.c, today)

    def test_rates_with_a_new_card_and_replaced_at_once(self):
        vx = self.add(issuer="capital_one", product="Venture X", currency="c1", portal_name="Capital One Travel",
                      rates=[{"category": "*", "multiplier": 2}, {"category": "Hotels", "multiplier": 10, "portal_only": True},
                             {"category": "Travel", "multiplier": 2}])
        got = self.out()["cards"][0]
        self.assertEqual((got["base_rate"], got["portal_name"]), (2.0, "Capital One Travel"))
        self.assertEqual(got["rates"], [{"category": "Hotels", "multiplier": 10.0, "portal_only": True},
                                        {"category": "Travel", "multiplier": 2.0, "portal_only": False}])
        best = churning.best(self.c, TODAY, "Hotels")
        self.assertEqual((best[0]["multiplier"], best[0]["portal_option"]["multiplier"]), (2.0, 10.0))
        self.assertEqual(churning.best(self.c, TODAY, "Hotels", portal=True)[0]["multiplier"], 10.0)
        # the same category as a normal and a portal rate is fine; twice as either is not
        churning.save_card(self.c, {"rates": [{"category": "Hotels", "multiplier": 2},
                                              {"category": "Hotels", "multiplier": 10, "portal_only": "1"}]}, vx)
        self.assertEqual(len(self.out()["cards"][0]["rates"]), 2)
        for bad, msg in (([{"category": "Hotels", "multiplier": 2}, {"category": "Hotels", "multiplier": 3}], "Hotels is listed twice"),
                         ([{"category": "Nope", "multiplier": 2}], "no category called Nope"),
                         ([{"category": "Hotels", "multiplier": 101}], "between 0 and 100"),
                         ([{"category": "Hotels", "multiplier": ""}], "Enter the points per dollar on Hotels"),
                         ([{"category": "", "multiplier": 1}], "Pick a category"),
                         ([{"category": "*", "multiplier": 3, "portal_only": True}], "base rate can't be portal-only"),
                         ("Hotels", "must be a list"), ([5], "category and points")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ChurnError, msg):
                churning.save_card(self.c, {"product": "Renamed", "rates": bad}, vx)
            with self.subTest(new=bad), self.assertRaisesRegex(ChurnError, msg):
                self.add(product="Never added", rates=bad)
        # nothing was written by the failed requests
        self.assertEqual([c["product"] for c in self.out()["cards"]], ["Venture X"])
        self.assertEqual(len(self.out()["cards"][0]["rates"]), 2)
        with self.assertRaisesRegex(ChurnError, "given twice"):
            churning.save_card(self.c, {"base_rate": 1, "rates": [{"category": "*", "multiplier": 2}]}, vx)
        churning.save_card(self.c, {"rates": []}, vx)   # an empty list clears them
        self.assertEqual(self.out()["cards"][0]["rates"], [])
        churning.set_rate(self.c, vx, "Hotels", 10, portal_only=True)   # the one-rate endpoint still works
        churning.set_rate(self.c, vx, "Hotels", 2)
        churning.set_rate(self.c, vx, "Hotels", "", portal_only=True)
        self.assertEqual(self.out()["cards"][0]["rates"], [{"category": "Hotels", "multiplier": 2.0, "portal_only": False}])

    def test_points_estimates_use_normal_rates(self):
        vx = self.add(issuer="capital_one", product="Venture X", currency="c1", account_id="cc",
                      rates=[{"category": "*", "multiplier": 2}, {"category": "Hotels", "multiplier": 10, "portal_only": True}])
        self.c.execute("INSERT INTO transactions(id, account_id, posted, amount, category) VALUES "
                       "('t1', 'cc', '2026-03-01', -100, 'Hotels')")
        got = next(c for c in self.out()["cards"] if c["id"] == vx)
        self.assertEqual(got["points_ytd"], 200)
        self.assertIn("normal rates", got["points_note"])

    def test_plans_in_upcoming_and_alerts(self):
        csr = self.add(product="Sapphire Reserve", annual_fee=795, plan="keep")
        aa = self.add(issuer="citi", product="AAdvantage Platinum", annual_fee=99, opened_on="2025-10-20",
                      plan="product_change", plan_target="AAdvantage MileUp")
        undecided = self.add(issuer="amex", product="Gold", annual_fee=325, opened_on="2025-10-25")
        hidden = self.add(issuer="amex", product="Green", annual_fee=150, opened_on="2025-10-15", hide_upcoming=True,
                          bonus=40000, bonus_spend=3000)
        up = self.out()["upcoming"]
        by_card = {}
        for u in up:
            by_card.setdefault(u["card_id"], []).append(u)
        self.assertNotIn(hidden, by_card)
        fee = by_card[csr][0]
        self.assertEqual((fee["kind"], fee["detail"], fee["warn"]), ("fee", "You're keeping it", False))
        # The plan's day is the day before the fee, 20 days out: its reminder starts 14 days before
        self.assertEqual([(u["kind"], u["date"]) for u in by_card[aa]], [("fee", "2026-10-20")])
        self.assertEqual((by_card[aa][0]["detail"], by_card[aa][0]["warn"]),
                         ("Your plan: Product change AAdvantage Platinum to AAdvantage MileUp before it posts", False))
        soon = [u for u in self.out(date(2026, 10, 5))["upcoming"] if u["card_id"] == aa]
        self.assertEqual([(u["kind"], u["date"]) for u in soon], [("plan", "2026-10-19"), ("fee", "2026-10-20")])
        plan = soon[0]
        self.assertEqual(plan["title"], "Product change AAdvantage Platinum to AAdvantage MileUp by Oct 19")
        self.assertEqual((plan["detail"], plan["warn"]), ("Before the $99 annual fee posts", False))
        self.assertEqual(by_card[undecided][0]["detail"], "Keep it, downgrade or close before it posts?")

        p = {**notify.DEFAULTS, "card_due": False, "low_balance": False, "missed": False, "big_charge": False,
             "sync_failed": False}
        keys = [a["key"] for a in notify.alerts(self.c, TODAY, p)]
        self.assertEqual(keys, [f"churnfee:{undecided}:2026-10-25"])   # kept, planned and hidden cards: no fee nagging
        keys = [a["key"] for a in notify.alerts(self.c, date(2026, 10, 10), p)]
        self.assertIn(f"churnplan:{aa}:2026-10-19", keys)
        self.assertNotIn(f"churnfee:{aa}:2026-10-20", keys)
        self.assertEqual(notify.alerts(self.c, date(2026, 10, 10), {**p, "churn_plan": False, "churn_fee": False}), [])
        warn = next(u for u in self.out(date(2026, 10, 14))["upcoming"] if u["kind"] == "plan")
        self.assertTrue(warn["warn"])

    def test_plan_reminder_window_and_date(self):
        aa = self.add(product="Freedom", annual_fee=0, plan="close", plan_date="2026-11-30", plan_remind_days=10)
        self.assertEqual([u for u in self.out()["upcoming"] if u["kind"] == "plan"], [])   # not until 10 days before
        up = [u for u in self.out(date(2026, 11, 20))["upcoming"] if u["kind"] == "plan"]
        self.assertEqual((up[0]["title"], up[0]["detail"], up[0]["warn"]), ("Close Freedom by Nov 30", "Check it off when it's done", False))
        late = [u for u in self.out(date(2026, 12, 5))["upcoming"] if u["kind"] == "plan"]
        self.assertTrue(late[0]["warn"])   # overdue until checked off
        self.assertEqual(self.out()["cards"][0]["plan_due"], "2026-11-30")
        # without a day or a fee there's nothing to remind about
        churning.save_card(self.c, {"plan_date": ""}, aa)
        self.assertIsNone(self.out()["cards"][0]["plan_due"])
        for bad, msg in (({"plan": "sell"}, "plan is"), ({"plan_date": "soon"}, "date"), ({"plan_remind_days": 400}, "between")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ChurnError, msg):
                churning.save_card(self.c, bad, aa)

    def test_done_and_undo(self):
        aa = self.add(issuer="citi", product="AAdvantage Platinum", annual_fee=99, opened_on="2025-10-20", account_id="cc",
                      currency="aa", plan="product_change", plan_target="AAdvantage MileUp")
        self.assertEqual(self.out()["five24"]["Alex"]["count"], 1)
        r = churning.plan_done(self.c, aa, TODAY)
        self.assertEqual((r["status"], r["closed_on"], r["plan_done_on"]), ("product_changed", "2026-09-29", "2026-09-29"))
        self.assertTrue(r["new_card_id"])
        self.assertEqual(len(r["changes"]), 2)
        cards = {c["id"]: c for c in self.out()["cards"]}
        new = cards[r["new_card_id"]]
        self.assertEqual((new["product"], new["changed_from"], new["account_id"], new["currency"], new["annual_fee"]),
                         ("AAdvantage MileUp", aa, "cc", "aa", 0.0))
        self.assertEqual(self.out()["five24"]["Alex"]["count"], 1)   # the change isn't a new card
        self.assertEqual([u for u in self.out()["upcoming"] if u["card_id"] == aa], [])
        with self.assertRaisesRegex(ChurnError, "already"):
            churning.plan_done(self.c, aa, TODAY)
        u = churning.plan_undo(self.c, aa)
        self.assertEqual((u["status"], u["closed_on"], u["plan_done_on"], u["new_card_id"]), ("open", None, None, None))
        self.assertEqual([c["id"] for c in self.out()["cards"]], [aa])
        with self.assertRaisesRegex(ChurnError, "isn't checked off"):
            churning.plan_undo(self.c, aa)

        gold = self.add(issuer="amex", product="Gold", plan="close")
        r = churning.plan_done(self.c, gold, TODAY, "2026-09-20")
        self.assertEqual((r["status"], r["closed_on"], r["new_card_id"]), ("closed", "2026-09-20", None))
        self.assertEqual(self.out()["five24"]["Alex"]["count"], 2)   # closed cards still count
        churning.plan_undo(self.c, gold)
        self.assertEqual(self.out()["cards"][1]["status"], "open")

        keep = self.add(product="Sapphire Reserve", plan="keep")
        self.assertEqual(churning.plan_done(self.c, keep, TODAY)["status"], "open")
        pc = self.add(product="Ink", plan="product_change")   # no target: only marked
        r = churning.plan_done(self.c, pc, TODAY)
        self.assertEqual((r["status"], r["new_card_id"]), ("product_changed", None))
        plain = self.add(product="Freedom")
        with self.assertRaisesRegex(ChurnError, "Pick a plan"):
            churning.plan_done(self.c, plain, TODAY)
        with self.assertRaisesRegex(ChurnError, "not found"):
            churning.plan_done(self.c, 999, TODAY)
        with self.assertRaisesRegex(ChurnError, "before the card was opened"):
            churning.plan_done(self.c, gold, TODAY, "2020-01-01")

    def test_snooze(self):
        cid = self.add(product="Freedom")
        t = churning.save_task(self.c, {"card_id": cid, "due_on": "2026-10-01", "action": "Call retention"})
        self.assertEqual(churning.snooze_task(self.c, t, {"days": 7}, TODAY), "2026-10-06")
        self.assertEqual([u for u in self.out()["upcoming"] if u["kind"] == "task"], [])
        self.assertEqual(len([u for u in self.out(date(2026, 10, 6))["upcoming"] if u["kind"] == "task"]), 1)
        churning.snooze_task(self.c, t, {"until": "2026-12-01"}, TODAY)
        self.assertEqual(self.out()["tasks"][0]["snooze_until"], "2026-12-01")
        self.assertIsNone(churning.snooze_task(self.c, t, {}, TODAY))   # woken
        self.assertEqual(len([u for u in self.out()["upcoming"] if u["kind"] == "task"]), 1)
        with self.assertRaisesRegex(ChurnError, "between"):
            churning.snooze_task(self.c, t, {"days": 0}, TODAY)
        with self.assertRaisesRegex(ChurnError, "not found"):
            churning.snooze_task(self.c, 999, {"days": 1}, TODAY)
        # a hidden card's to-dos still show: you wrote them yourself
        churning.save_card(self.c, {"hide_upcoming": True}, cid)
        self.assertEqual(len([u for u in self.out()["upcoming"] if u["kind"] == "task"]), 1)

    def test_currencies_old_keys_and_estimates(self):
        old = self.add(product="Old hotel card", currency="hotel")
        self.add(product="Old airline card", currency="airline")
        churning.save_currency(self.c, {"key": "hotel", "cents": 0.7})   # a value set before the programs were listed
        v = churning.values(self.c)
        self.assertEqual((v["hotel"]["name"], v["hotel"]["cents"], v["hotel"]["overridden"], v["hotel"]["estimate"]),
                         ("Other hotel points", 0.7, True, False))
        self.assertEqual((v["airline"]["name"], v["airline"]["kind"]), ("Other airline miles", "airline"))
        self.assertEqual((v["aa"]["cents"], v["aa"]["estimate"], v["aa"]["as_of"]), (1.4, True, churning.VALUES_AS_OF))
        self.assertFalse(v["cash"]["estimate"])
        cards = {c["id"]: c for c in self.out()["cards"]}
        self.assertEqual(cards[old]["currency_name"], "Other hotel points")
        out = self.out()
        groups = {g["kind"]: g["keys"] for g in out["currency_groups"]}
        self.assertEqual(list(groups), ["bank", "airline", "hotel", "cash", "other"])
        self.assertIn("united", groups["airline"])
        self.assertIn("hyatt", groups["hotel"])
        self.assertEqual(sum(len(k) for k in groups.values()), len(out["currencies"]))
        self.assertEqual(out["values_as_of"], churning.VALUES_AS_OF)
        for key, cur in churning.CURRENCIES.items():
            with self.subTest(key=key):
                self.assertIn(cur["kind"], churning.CURRENCY_KINDS)
                self.assertTrue(0 < cur["cents"] < 5)
        # a currency you add can say what kind it is
        key = churning.save_currency(self.c, {"name": "Wyndham Rewards", "cents": 0.9, "kind": "hotel"})
        self.assertEqual(churning.values(self.c)[key]["kind"], "hotel")
        with self.assertRaisesRegex(ChurnError, "kind"):
            churning.save_currency(self.c, {"name": "Rocks", "cents": 1, "kind": "mineral"})

    def test_owners(self):
        self.c.execute("INSERT INTO users(sub, first_name, last_seen) VALUES ('u1', 'Alex', 1)")
        self.c.execute("INSERT INTO accounts(id, name, kind, owner) VALUES ('sam', 'Checking', 'checking', 'Sam')")
        cid = churning.save_card(self.c, {"owner": "sam", "issuer": "chase", "product": "Freedom", "opened_on": "2026-01-01"})
        self.assertEqual(self.out()["cards"][0]["owner"], "Sam")   # spelled as the account has it
        churning.save_card(self.c, {"owner": "Robin"}, cid)
        self.assertEqual(churning.known_owners(self.c), ["Alex", "Sam", "Robin"])
        out = churning.overview(self.c, TODAY, ["Alex", "Sam"])
        self.assertEqual(out["owners"], ["Alex", "Sam", "Robin"])
        with self.assertRaisesRegex(ChurnError, "one person"):
            churning.save_card(self.c, {"owner": "joint"}, cid)


if __name__ == "__main__":
    unittest.main()
