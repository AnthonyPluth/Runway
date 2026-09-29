"""Churning (runway/churning.py): 5/24, when a bonus can be earned again, annual fee dates, spending toward a bonus,
estimated rewards, the best card for a purchase, points values and the push alerts."""
import os
import tempfile
import unittest
from datetime import date

from runway import churning, db, notify
from runway.churning import ChurnError

TODAY = date(2026, 9, 29)


def card(id, opened, owner="Alex", issuer="chase", product="Freedom", **kw):
    return {"id": id, "owner": owner, "issuer": issuer, "product": product, "opened_on": opened, "status": "open", **kw}


class DateTests(unittest.TestCase):
    def test_next_annual_fee(self):
        fee = lambda opened, today, **kw: churning.next_fee(card(1, opened, annual_fee=95, **kw), today)
        self.assertEqual(fee("2025-12-15", date(2026, 12, 20)), date(2027, 12, 15))   # across the year's end
        self.assertEqual(fee("2025-12-15", date(2026, 12, 15)), date(2026, 12, 15))   # due today
        self.assertEqual(fee("2026-03-01", date(2026, 9, 29)), date(2027, 3, 1))       # first year: the first anniversary
        self.assertEqual(fee("2024-02-29", date(2027, 1, 1)), date(2027, 2, 28))       # a leap day, in a common year
        self.assertEqual(fee("2024-02-29", date(2028, 1, 1)), date(2028, 2, 29))       # ... and in a leap year
        self.assertEqual(fee("2024-01-31", date(2026, 3, 1), fee_month=4), date(2026, 4, 30))   # a fee month you set
        self.assertIsNone(churning.next_fee(card(1, "2024-01-01", annual_fee=0), TODAY))
        self.assertIsNone(churning.next_fee(card(1, "2024-01-01", annual_fee=95, status="closed"), TODAY))

    def test_bonus_deadline(self):
        self.assertIsNone(churning.deadline(card(1, "2026-01-01")))   # no bonus
        self.assertEqual(churning.deadline(card(1, "2025-11-30", bonus_spend=4000)), date(2026, 2, 28))
        self.assertEqual(churning.deadline(card(1, "2026-01-15", bonus=60000, bonus_months=6)), date(2026, 7, 15))
        self.assertEqual(churning.deadline(card(1, "2026-01-15", bonus=1, bonus_deadline="2026-05-01")), date(2026, 5, 1))

    def test_bonus_state(self):
        c = card(1, "2026-08-01", bonus=60000, bonus_spend=4000)
        self.assertEqual(churning.bonus_state(c, 1000, TODAY), "active")
        self.assertEqual(churning.bonus_state(c, 4000, TODAY), "met")
        self.assertEqual(churning.bonus_state(c, 1000, date(2026, 11, 2)), "missed")
        self.assertEqual(churning.bonus_state({**c, "bonus_earned_on": "2026-09-01"}, 0, TODAY), "earned")
        self.assertIsNone(churning.bonus_state(card(1, "2026-08-01"), 0, TODAY))


class Five24Tests(unittest.TestCase):
    def test_counts_personal_cards_and_when_they_fall_off(self):
        cards = [card(1, "2024-10-15"), card(2, "2025-01-10", status="closed"), card(3, "2025-06-01"),
                 card(4, "2025-09-01"), card(5, "2026-02-01"), card(6, "2026-05-20"),
                 card(7, "2026-01-01", authorized_user=1), card(8, "2026-01-01", business=1),
                 card(9, "2026-03-01", changed_from=3),         # a product change: the same account
                 card(10, "2024-09-28"),                        # opened 24 months ago yesterday: gone
                 card(11, "2026-01-01", owner="Sam")]
        f = churning.five24(cards, "Alex", TODAY)
        self.assertEqual(f["count"], 6)
        self.assertEqual([c["id"] for c in f["cards"]], [1, 2, 3, 4, 5, 6])
        self.assertEqual(f["cards"][0]["falls_off"], "2026-10-15")
        self.assertFalse(f["under"])
        self.assertEqual(f["under_on"], "2027-01-10")   # two have to go: the second on Jan 10
        self.assertEqual(f["timeline"][:2], [{"date": "2026-10-15", "count": 5}, {"date": "2027-01-10", "count": 4}])
        s = churning.five24(cards, "Sam", TODAY)
        self.assertEqual((s["count"], s["under"], s["under_on"]), (1, True, None))
        self.assertEqual(churning.five24(cards, "Nobody", TODAY)["count"], 0)


class EligibilityTests(unittest.TestCase):
    def test_chase_family_48_months_hold_and_524(self):
        csp = card(1, "2019-05-01", product="Sapphire Preferred", family="Sapphire", status="closed",
                   bonus=60000, bonus_earned_on="2019-08-01", closed_on="2023-06-01")
        csr = card(2, "2023-07-01", product="Sapphire Reserve", family="Sapphire", status="closed",
                   bonus=60000, bonus_earned_on="2023-09-15", closed_on="2024-09-01")
        e = churning.eligibility(csp, [csp, csr], TODAY)
        self.assertEqual((e["status"], e["on"]), ("later", "2027-09-15"))   # 48 months after the latest Sapphire bonus
        self.assertIn("Sapphire", e["why"])
        # Still holding a Sapphire: not until it's gone
        held = {**csr, "status": "open", "closed_on": None}
        self.assertEqual(churning.eligibility(csp, [csp, held], TODAY)["status"], "held")
        # Over 5/24 pushes it to the day you're under
        f524 = {"under": False, "under_on": "2028-01-01"}
        self.assertEqual(churning.eligibility(csp, [csp, csr], TODAY, f524)["on"], "2028-01-01")
        # A family never bonused: now
        freedom = card(3, "2026-01-01", product="Freedom Flex", status="closed")
        self.assertEqual(churning.eligibility(freedom, [csp, csr, freedom], TODAY)["status"], "now")

    def test_amex_once_per_lifetime(self):
        gold = card(1, "2018-01-01", issuer="amex", product="Gold", bonus=60000, bonus_earned_on="2018-04-01", status="closed")
        e = churning.eligibility(gold, [gold], TODAY)
        self.assertEqual((e["status"], e["on"]), ("never", None))
        plat = card(2, "2025-01-01", issuer="amex", product="Platinum", status="closed")
        self.assertEqual(churning.eligibility(plat, [gold, plat], TODAY)["status"], "now")   # another product
        # Sam's Gold is Sam's own
        sams = card(3, "2020-01-01", owner="Sam", issuer="amex", product="Gold", status="closed")
        self.assertEqual(churning.eligibility(sams, [gold, sams], TODAY)["status"], "now")

    def test_citi_months_override_au_and_in_progress(self):
        prem = card(1, "2023-01-01", issuer="citi", product="Strata Premier", bonus=60000, bonus_earned_on="2023-03-10")
        self.assertEqual(churning.eligibility(prem, [prem], TODAY)["on"], "2027-03-10")
        self.assertEqual(churning.eligibility({**prem, "eligible_on": "2026-01-01"}, [prem], TODAY),
                         {**churning.eligibility({**prem, "eligible_on": "2026-01-01"}, [prem], TODAY), "status": "now",
                          "on": "2026-01-01", "override": True})
        au = card(2, "2025-01-01", issuer="amex", product="Gold", authorized_user=1)
        self.assertEqual(churning.eligibility(au, [au], TODAY)["status"], "au")
        venture = card(3, "2026-08-01", issuer="capital_one", product="Venture X", bonus=75000, _state="active")
        self.assertEqual(churning.eligibility(venture, [venture], TODAY)["status"], "in_progress")
        self.assertEqual(churning.eligibility({**venture, "_state": None, "bonus_earned_on": "2026-09-01"}, [], TODAY)["on"],
                         "2030-09-01")
        other = card(4, "2020-01-01", issuer="mystery", product="X", bonus_earned_on="2025-01-01")
        self.assertEqual(churning.eligibility(other, [other], TODAY)["on"], "2027-01-01")   # unknown bank: the default


class RankTests(unittest.TestCase):
    def test_best_card_ranks_by_value_with_bonus_hint(self):
        vals = {"cash": {"name": "Cash back", "cents": 1.0}, "ur": {"name": "UR", "cents": 1.5}, "mr": {"name": "MR", "cents": 2.0}}
        parents = {"Restaurants": None, "Takeout": "Restaurants", "Groceries": None}
        cards = [card(1, "2024-01-01", product="Double Cash", issuer="citi", currency="cash", base_rate=2),
                 card(2, "2024-01-01", product="Sapphire Preferred", currency="ur", base_rate=1),
                 card(3, "2026-08-01", product="Gold", issuer="amex", currency="mr", base_rate=1,
                      bonus_spend=6000, _state="active", _spent=1500, _deadline="2027-02-01"),
                 card(4, "2024-01-01", product="Closed", status="closed", currency="mr", base_rate=10),
                 card(5, "2024-01-01", owner="Sam", product="Savor", issuer="capital_one", currency="cash", base_rate=1)]
        rates = {2: {"Restaurants": 3}, 3: {"Restaurants": 4, "Groceries": 4}, 5: {"Restaurants": 3}}
        got = churning.best_cards(cards, rates, vals, "Takeout", parents, TODAY, amount=100)   # a rate on the parent counts
        self.assertEqual([g["id"] for g in got], [3, 2, 5, 1])
        self.assertEqual((got[0]["multiplier"], got[0]["return_pct"], got[0]["value"]), (4, 8.0, 8.0))
        self.assertEqual(got[0]["bonus"]["remaining"], 4500)
        self.assertIsNone(got[1]["bonus"])
        mine = churning.best_cards(cards, rates, vals, "Gas", parents, TODAY, owner="Alex")
        self.assertEqual([g["id"] for g in mine], [3, 1, 2])   # base rates: 2c MR and 2% cash tie; the bonus card first
        self.assertIsNone(mine[0]["value"])


class DbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self.tmp.name, "t.db")
        db.init(path)
        self.c = db.connect(path)
        self.c.execute("INSERT INTO accounts(id, name, kind, owner) VALUES ('cc', 'Gold ••1234', 'credit', 'Alex')")
        self.c.execute("INSERT INTO accounts(id, name, kind) VALUES ('chk', 'Checking', 'checking')")
        self.c.execute("INSERT INTO categories(name, parent) VALUES ('Takeout', 'Restaurants')")

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def tx(self, id, posted, amount, category, split=0):
        self.c.execute("INSERT INTO transactions(id, account_id, posted, amount, category, is_split) VALUES (?,?,?,?,?,?)",
                       (id, "cc", posted, amount, category, split))

    def test_spending_toward_the_bonus_and_points(self):
        gold = churning.save_card(self.c, {"owner": "Alex", "issuer": "amex", "product": "Gold", "opened_on": "2026-08-01",
                                           "account_id": "cc", "currency": "mr", "bonus": 60000, "bonus_spend": 6000,
                                           "base_rate": 1, "annual_fee": 325})
        churning.set_rate(self.c, gold, "Restaurants", 4)
        self.tx("t0", "2026-07-30", -900, "Groceries")          # before it was opened
        self.tx("t1", "2026-08-05", -1000, "Groceries")
        self.tx("t2", "2026-08-06", -200, "Takeout")            # its parent's rate: 4x
        self.tx("t3", "2026-08-07", 50, "Groceries")            # a refund lowers it
        self.tx("t4", "2026-08-20", 1200, "Credit Card Payment")  # a payment isn't spending
        self.tx("t5", "2026-09-01", -300, None)                 # not categorized yet: still a purchase
        self.tx("t6", "2026-09-02", -500, "Groceries", split=1)  # split: $400 groceries, $100 a transfer
        self.c.execute("INSERT INTO tx_splits(tx_id, amount, category) VALUES ('t6', -400, 'Groceries'), ('t6', -100, 'Transfer')")
        s = churning.overview(self.c, TODAY, ["Alex", "Sam"])
        g = s["cards"][0]
        self.assertEqual(g["spent"], 1000 + 200 - 50 + 300 + 400)
        self.assertEqual((g["bonus_state"], g["deadline"], g["spend_source"]), ("active", "2026-11-01", "account"))
        self.assertEqual(g["points_ytd"], 1000 + 800 - 50 + 300 + 400)
        self.assertEqual(g["value_ytd"], round(2450 * 1.5 / 100, 2))
        self.assertEqual(g["bonus_value"], 900.0)
        self.assertEqual(s["people"], ["Alex", "Sam"])
        self.assertEqual(s["five24"]["Sam"]["count"], 0)
        self.assertEqual(s["rewards"]["Alex"]["currencies"][0]["earned"], 2450)
        bonus = [u for u in s["upcoming"] if u["kind"] == "bonus"]
        self.assertEqual((bonus[0]["date"], bonus[0]["title"]), ("2026-11-01", "Spend $4,150 more on Gold"))
        self.assertEqual(s["accounts"], [{"id": "cc", "name": "Gold ••1234 (Alex)", "owner": "Alex"}])
        # Not linked: what you entered
        churning.save_card(self.c, {"account_id": "", "manual_spend": "5,000"}, gold)
        self.assertEqual(churning.overview(self.c, TODAY)["cards"][0]["spent"], 5000)
        best = churning.best(self.c, TODAY, "Takeout", amount=50)
        self.assertEqual((best[0]["multiplier"], best[0]["value"], best[0]["bonus"]["remaining"]), (4, 3.0, 1000))

    def test_upcoming_is_sorted_and_warns(self):
        a = churning.save_card(self.c, {"owner": "Alex", "issuer": "chase", "product": "Sapphire Preferred", "family": "Sapphire",
                                        "opened_on": "2025-10-10", "annual_fee": 95})
        churning.save_card(self.c, {"owner": "Sam", "issuer": "chase", "product": "Sapphire Reserve", "family": "Sapphire",
                                    "opened_on": "2021-01-05", "status": "closed", "closed_on": "2023-01-05",
                                    "bonus": 50000, "bonus_earned_on": "2021-03-01"})
        churning.save_task(self.c, {"card_id": a, "due_on": "2026-10-01", "action": "Call retention"})
        done = churning.save_task(self.c, {"card_id": a, "due_on": "2026-09-01", "action": "Old"})
        churning.save_task(self.c, {"done": True}, done)
        up = churning.overview(self.c, TODAY)["upcoming"]
        self.assertEqual([(u["date"], u["kind"]) for u in up], [("2026-10-01", "task"), ("2026-10-10", "fee")])
        self.assertTrue(all(u["warn"] for u in up))
        self.assertEqual(churning.overview(self.c, date(2024, 12, 1))["upcoming"][-1]["kind"], "eligible")

    def test_validation(self):
        base = {"owner": "Alex", "issuer": "chase", "product": "Freedom", "opened_on": "2026-01-01"}
        for bad, msg in (({"owner": ""}, "person"), ({"owner": "Joint"}, "one person"), ({"issuer": "bank"}, "bank"),
                         ({"product": " "}, "name"), ({"opened_on": "soon"}, "date"), ({"annual_fee": "-5"}, "between"),
                         ({"fee_month": 13}, "between"), ({"fee_month": 2.5}, "whole"), ({"currency": "gold"}, "earns"),
                         ({"account_id": "chk"}, "credit card"), ({"closed_on": "2025-01-01"}, "before"),
                         ({"status": "lost"}, "Status"), ({"bonus": "nan"}, "number"), ({"changed_from": 99}, "changed from")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ChurnError, msg):
                churning.save_card(self.c, {**base, **bad})
        cid = churning.save_card(self.c, {**base, "authorized_user": True, "fee_month": "", "notes": "  hi "})
        row = self.c.execute("SELECT authorized_user, business, fee_month, notes, status, bonus_months, base_rate, currency "
                             "FROM churn_cards WHERE id=?", (cid,)).fetchone()
        self.assertEqual(tuple(row), (1, 0, None, "hi", "open", 3, 1.0, "cash"))
        with self.assertRaisesRegex(ChurnError, "not found"):
            churning.save_card(self.c, {"product": "X"}, 999)
        with self.assertRaisesRegex(ChurnError, "category"):
            churning.set_rate(self.c, cid, "Nope", 3)
        with self.assertRaisesRegex(ChurnError, "Card"):
            churning.set_rate(self.c, 999, "Groceries", 3)
        with self.assertRaisesRegex(ChurnError, "card"):
            churning.save_task(self.c, {"card_id": 999, "due_on": "2026-10-01", "action": "x"})
        with self.assertRaisesRegex(ChurnError, "what to do"):
            churning.save_task(self.c, {"card_id": cid, "due_on": "2026-10-01", "action": ""})
        with self.assertRaisesRegex(ChurnError, "not found"):
            churning.save_task(self.c, {"done": 1}, 999)

    def test_product_change_and_remove(self):
        csp = churning.save_card(self.c, {"owner": "Alex", "issuer": "chase", "product": "Sapphire Preferred", "opened_on": "2026-01-01",
                                          "status": "product_changed", "closed_on": "2026-06-01"})
        ff = churning.save_card(self.c, {"owner": "Alex", "issuer": "chase", "product": "Freedom Flex", "opened_on": "2026-06-01",
                                         "changed_from": csp})
        with self.assertRaisesRegex(ChurnError, "same person"):
            churning.save_card(self.c, {"owner": "Sam", "issuer": "chase", "product": "X", "opened_on": "2026-06-01", "changed_from": csp})
        self.assertEqual(churning.overview(self.c, TODAY)["five24"]["Alex"]["count"], 1)   # the change isn't a new card
        churning.set_rate(self.c, ff, "Groceries", 5)
        churning.set_rate(self.c, ff, "Groceries", "")   # no multiplier: removed
        churning.set_rate(self.c, ff, "Restaurants", 3)
        churning.save_task(self.c, {"card_id": csp, "due_on": "2026-10-01", "action": "x"})
        churning.remove_card(self.c, csp)
        self.assertIsNone(self.c.execute("SELECT changed_from FROM churn_cards WHERE id=?", (ff,)).fetchone()[0])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM churn_tasks").fetchone()[0], 0)
        self.assertEqual(churning.overview(self.c, TODAY)["cards"][0]["rates"], [{"category": "Restaurants", "multiplier": 3.0}])

    def test_point_values_and_balances(self):
        v = churning.values(self.c)
        self.assertEqual((v["ur"]["cents"], v["cash"]["cents"], v["ur"]["custom"]), (1.5, 1.0, False))
        churning.save_currency(self.c, {"key": "ur", "cents": "2.05"})
        key = churning.save_currency(self.c, {"name": "Bilt Rewards", "cents": 1.8})
        self.assertEqual(key, "x-bilt-rewards")
        v = churning.values(self.c)
        self.assertEqual((v["ur"]["cents"], v["ur"]["default"], v[key]["name"], v[key]["custom"]), (2.05, 1.5, "Bilt Rewards", True))
        with self.assertRaisesRegex(ChurnError, "already"):
            churning.save_currency(self.c, {"name": "bilt rewards", "cents": 1})
        with self.assertRaisesRegex(ChurnError, "cents"):
            churning.save_currency(self.c, {"key": "ur", "cents": ""})
        with self.assertRaisesRegex(ChurnError, "Unknown"):
            churning.save_currency(self.c, {"key": "zzz", "cents": 1})
        churning.set_balance(self.c, "Alex", "ur", "120,000", TODAY)
        churning.set_balance(self.c, "Alex", key, 1000, TODAY)
        churning.save_card(self.c, {"owner": "Alex", "issuer": "other", "product": "Bilt", "opened_on": "2026-01-01", "currency": key})
        with self.assertRaisesRegex(ChurnError, "Bilt earns it"):
            churning.remove_currency(self.c, key)
        r = churning.overview(self.c, TODAY)["rewards"]["Alex"]
        ur = next(x for x in r["currencies"] if x["currency"] == "ur")
        self.assertEqual((ur["balance"], ur["balance_value"], ur["as_of"]), (120000, 2460.0, "2026-09-29"))
        churning.remove_currency(self.c, "ur")   # back to the default
        self.assertEqual(churning.values(self.c)["ur"]["cents"], 1.5)
        churning.set_balance(self.c, "Alex", "ur", "", TODAY)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM churn_balances WHERE currency='ur'").fetchone()[0], 0)
        with self.assertRaisesRegex(ChurnError, "Unknown"):
            churning.set_balance(self.c, "Alex", "nope", 1, TODAY)

    def test_push_alerts(self):
        churning.save_card(self.c, {"owner": "Alex", "issuer": "amex", "product": "Gold", "opened_on": "2025-10-20", "annual_fee": 325})
        churning.save_card(self.c, {"owner": "Sam", "issuer": "citi", "product": "Premier", "opened_on": "2026-07-05",
                                    "bonus": 60000, "bonus_spend": 4000, "manual_spend": 1000})
        p = {**notify.DEFAULTS, "card_due": False, "low_balance": False, "missed": False, "big_charge": False, "sync_failed": False}
        got = notify.alerts(self.c, TODAY, p)
        self.assertEqual([a["key"] for a in got], ["churnfee:1:2026-10-20", "churnbonus:2:2026-10-05"])
        self.assertEqual(got[1]["title"], "$3,000 to spend on Premier by Oct 5")
        self.assertEqual(notify.alerts(self.c, TODAY, {**p, "churn_fee": False, "churn_bonus": False}), [])
        self.assertEqual(notify.alerts(self.c, date(2026, 9, 1), p), [])   # not yet within 30 and 14 days


if __name__ == "__main__":
    unittest.main()
