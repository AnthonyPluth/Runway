"""Card benefits (runway/churn_benefits.py): periods on the calendar and the cardmember year, what's left of a credit,
what the benefits are worth a year against the annual fee, reminders, and the quick-add presets."""
import unittest
from datetime import date

from sqlalchemy import func, select

from runway import churn_benefits as cb
from runway import churning, notify
from runway.churning import ChurnError
from runway.models import ChurnBenefit, ChurnBenefitUse
from tests.shared import DbCase

TODAY = date(2026, 9, 29)   # not shared.TODAY, the 29th: its card and benefit dates are written around it


def per(period, basis="calendar", opened="2025-03-15", day=TODAY, **kw):
    start, end = cb.period({"period": period, "basis": basis, **kw}, opened, day)
    return start.isoformat(), end.isoformat() if end else None


class PeriodTests(unittest.TestCase):
    def test_calendar(self):
        self.assertEqual(per("monthly"), ("2026-09-01", "2026-09-30"))
        self.assertEqual(per("quarterly"), ("2026-07-01", "2026-09-30"))
        self.assertEqual(per("semiannual"), ("2026-07-01", "2026-12-31"))
        self.assertEqual(per("annual"), ("2026-01-01", "2026-12-31"))
        self.assertEqual(per("monthly", day=date(2028, 2, 10)), ("2028-02-01", "2028-02-29"))   # a leap February
        self.assertEqual(per("quarterly", day=date(2026, 12, 31)), ("2026-10-01", "2026-12-31"))
        self.assertEqual(per("annual", day=date(2027, 1, 1)), ("2027-01-01", "2027-12-31"))       # the year turns
        # every 4 years: from Jan 1 of the year the card was opened
        self.assertEqual(per("every_4_years"), ("2025-01-01", "2028-12-31"))
        self.assertEqual(per("every_4_years", day=date(2029, 1, 1)), ("2029-01-01", "2032-12-31"))

    def test_cardmember_year(self):
        self.assertEqual(per("annual", "anniversary"), ("2026-03-15", "2027-03-14"))
        self.assertEqual(per("annual", "anniversary", day=date(2026, 3, 14)), ("2025-03-15", "2026-03-14"))
        self.assertEqual(per("annual", "anniversary", day=date(2025, 3, 15)), ("2025-03-15", "2026-03-14"))   # opening day
        self.assertEqual(per("monthly", "anniversary", opened="2026-01-31", day=date(2026, 3, 5)), ("2026-02-28", "2026-03-30"))
        self.assertEqual(per("monthly", "anniversary", opened="2026-01-31", day=date(2026, 3, 31)), ("2026-03-31", "2026-04-29"))
        self.assertEqual(per("every_4_years", "anniversary"), ("2025-03-15", "2029-03-14"))

    def test_leap_day_opening(self):
        opened = "2024-02-29"
        self.assertEqual(per("annual", "anniversary", opened, date(2025, 2, 27)), ("2024-02-29", "2025-02-27"))
        self.assertEqual(per("annual", "anniversary", opened, date(2025, 2, 28)), ("2025-02-28", "2026-02-27"))
        self.assertEqual(per("annual", "anniversary", opened, date(2027, 3, 1)), ("2027-02-28", "2028-02-28"))
        self.assertEqual(per("annual", "anniversary", opened, date(2028, 2, 29)), ("2028-02-29", "2029-02-27"))

    def test_one_time(self):
        self.assertEqual(per("one_time"), ("2025-03-15", None))
        self.assertEqual(per("one_time", expires_on="2026-12-31"), ("2025-03-15", "2026-12-31"))

    def test_value_and_lead(self):
        self.assertEqual(cb.value_per_year({"kind": "credit", "amount": 15, "period": "monthly"}), 180)
        self.assertEqual(cb.value_per_year({"kind": "credit", "amount": 120, "period": "every_4_years"}), 30)
        self.assertEqual(cb.value_per_year({"kind": "credit", "amount": 50, "period": "one_time"}), 0)
        self.assertEqual(cb.value_per_year({"kind": "access", "amount": None, "period": "annual"}), 0)
        self.assertEqual(cb.value_per_year({"kind": "access", "annual_value": 150, "period": "annual"}), 150)
        self.assertEqual(cb.value_per_year({"kind": "credit", "amount": 300, "annual_value": 200, "period": "annual"}), 200)
        self.assertEqual(cb.lead_days({"period": "monthly"}), 14)
        self.assertEqual(cb.lead_days({"period": "quarterly"}), 14)
        self.assertEqual(cb.lead_days({"period": "annual"}), 30)
        self.assertEqual(cb.lead_days({"period": "annual", "remind_days": 5}), 5)

    def test_presets(self):
        keys = [p["key"] for p in cb.PRESETS]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertGreaterEqual(len(keys), 13)
        for p in cb.PRESETS:
            with self.subTest(p=p["key"]):
                self.assertIn(p["kind"], cb.KINDS)
                self.assertIn(p["period"], cb.PERIODS)
                self.assertIn(p["basis"], cb.BASES)
                self.assertNotIn("amount", p)   # amounts differ by card: you fill them in
                self.assertTrue(p["name"])
                self.assertTrue(p["group"])
        # Each lounge network is a benefit of its own, so a card with two (Priority Pass and its bank's) lists both.
        lounges = {p["key"] for p in cb.PRESETS if p["group"] == "Lounges"}
        self.assertLessEqual({"priority_pass", "capital_one_lounge", "sapphire_lounge", "centurion_lounge", "lounge"}, lounges)
        self.assertTrue(all(cb.PRESET_KEYS[k]["kind"] == "access" for k in lounges))


class BenefitDbTests(DbCase):
    def setUp(self):
        super().setUp()
        self.card = churning.save_card(self.c, {"owner": "Alex", "issuer": "amex", "product": "Platinum",
                                                "opened_on": "2025-11-10", "annual_fee": 695})

    def card_out(self, today=TODAY):
        return next(c for c in churning.overview(self.c, today)["cards"] if c["id"] == self.card)

    def test_remaining_net_fee_and_use(self):
        uber = cb.save(self.c, {"preset": "uber", "amount": 15}, self.card)
        hotel = cb.save(self.c, {"preset": "hotel_credit", "amount": 200, "basis": "calendar"}, self.card)
        lounge = cb.save(self.c, {"preset": "lounge", "annual_value": 100}, self.card)
        cb.save(self.c, {"name": "Saks credit", "kind": "credit", "amount": 50, "period": "semiannual", "counts": False},
                self.card)
        b = {x["id"]: x for x in self.card_out()["benefits"]}
        self.assertEqual((b[uber]["name"], b[uber]["period"], b[uber]["remaining"]), ("Uber / Uber Eats credit", "monthly", 15))
        self.assertEqual(b[lounge]["kind"], "access")
        self.assertIsNone(b[lounge]["remaining"])
        out = self.card_out()
        self.assertEqual(out["benefits_value"], 180 + 200 + 100)   # the Saks credit doesn't count: you won't use it
        self.assertEqual(out["net_fee"], 695 - 480)

        cb.use(self.c, hotel, {"amount": "120"}, TODAY)
        cb.use(self.c, hotel, {"amount": 30, "used_on": "2026-02-01"}, TODAY)   # the same calendar year
        self.assertEqual(self.benefit(hotel)["remaining"], 50)
        with self.assertRaisesRegex(ChurnError, r"Only \$50 is left"):
            cb.use(self.c, hotel, {"amount": 60}, TODAY)
        cb.use(self.c, hotel, {}, TODAY)   # no amount: the rest
        self.assertEqual((self.benefit(hotel)["remaining"], self.benefit(hotel)["used_count"]), (0, 3))
        with self.assertRaisesRegex(ChurnError, "all used"):
            cb.use(self.c, hotel, {}, TODAY)
        cb.unuse(self.c, hotel, {}, TODAY)   # the latest
        self.assertEqual(self.benefit(hotel)["remaining"], 50)
        first = self.benefit(hotel)["uses"][0]["id"]   # the earliest: $30 in February
        cb.unuse(self.c, hotel, {"use_id": first}, TODAY)
        self.assertEqual(self.benefit(hotel)["remaining"], 80)
        # last year's credit is last year's
        cb.use(self.c, hotel, {"used_on": "2025-12-01"}, TODAY)
        self.assertEqual(self.benefit(hotel)["remaining"], 80)
        # access: used, without an amount
        cb.use(self.c, lounge, {}, TODAY)
        self.assertEqual(self.benefit(lounge)["used_count"], 1)
        with self.assertRaisesRegex(ChurnError, "before the card was opened"):
            cb.use(self.c, lounge, {"used_on": "2025-01-01"}, TODAY)
        with self.assertRaisesRegex(ChurnError, "Nothing to undo"):
            cb.unuse(self.c, uber, {}, TODAY)
        cb.remove(self.c, hotel)
        self.assertNotIn(hotel, [x["id"] for x in self.card_out()["benefits"]])
        churning.remove_card(self.c, self.card)
        self.assertEqual(self.c.execute(select(func.count()).select_from(ChurnBenefit)).fetchone()[0], 0)
        self.assertEqual(self.c.execute(select(func.count()).select_from(ChurnBenefitUse)).fetchone()[0], 0)

    def benefit(self, bid):
        return next(x for x in self.card_out()["benefits"] if x["id"] == bid)

    def test_reminders_upcoming_and_alerts(self):
        uber = cb.save(self.c, {"preset": "uber", "amount": 15}, self.card)
        airline = cb.save(self.c, {"preset": "airline_credit", "amount": 200}, self.card)
        quiet = cb.save(self.c, {"name": "Digital credit", "amount": 20, "period": "monthly", "remind": False}, self.card)
        cb.save(self.c, {"preset": "lounge"}, self.card)
        up = [u for u in churning.overview(self.c, TODAY)["upcoming"] if u["kind"] == "benefit"]
        self.assertEqual([(u["benefit_id"], u["date"]) for u in up], [(uber, "2026-09-30")])   # airline: Dec 31, not yet
        self.assertEqual(up[0]["title"], "Use your $15 Uber / Uber Eats credit by Sep 30 ($15 left)")
        self.assertTrue(up[0]["warn"])
        cb.use(self.c, uber, {"amount": 5}, TODAY)
        up = [u for u in churning.overview(self.c, TODAY)["upcoming"] if u["kind"] == "benefit"]
        self.assertEqual(up[0]["title"], "Use your $15 Uber / Uber Eats credit by Sep 30 ($10 left)")
        # December: the airline credit's 30 days, Uber's 14
        dec = [u["benefit_id"] for u in churning.overview(self.c, date(2026, 12, 10))["upcoming"] if u["kind"] == "benefit"]
        self.assertEqual(sorted(dec), sorted([airline]))
        dec = [u["benefit_id"] for u in churning.overview(self.c, date(2026, 12, 20))["upcoming"] if u["kind"] == "benefit"]
        self.assertEqual(sorted(dec), sorted([uber, airline]))
        self.assertNotIn(quiet, dec)
        p = {**notify.DEFAULTS, "card_due": False, "low_balance": False, "missed": False, "big_charge": False,
             "sync_failed": False, "churn_fee": False, "churn_bonus": False, "churn_plan": False}
        keys = [a["key"] for a in notify.alerts(self.c, TODAY, p)]
        self.assertEqual(keys, [f"churnbenefit:{uber}:2026-09-30"])
        self.assertEqual(notify.alerts(self.c, TODAY, {**p, "churn_benefit": False}), [])
        # a card hidden from Upcoming: nothing
        churning.save_card(self.c, {"hide_upcoming": True}, self.card)
        self.assertEqual([u for u in churning.overview(self.c, TODAY)["upcoming"] if u["kind"] == "benefit"], [])
        self.assertEqual(notify.alerts(self.c, TODAY, p), [])

    def test_validation(self):
        for bad, msg in (({"name": ""}, "name"), ({"name": "X", "kind": "perk"}, "kind"),
                         ({"name": "X", "period": "weekly"}, "resets"), ({"name": "X", "basis": "moon"}, "calendar"),
                         ({"name": "X", "amount": -1}, "between"), ({"preset": "nope"}, "preset"),
                         ({"name": "X", "remind_days": 1.5}, "whole"), ({"name": "X", "expires_on": "later"}, "date"),
                         ({"name": "X", "guests": -1}, "guests"), ({"name": "X", "guests": 1.5}, "whole")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ChurnError, msg):
                cb.save(self.c, bad, self.card)
        with self.assertRaisesRegex(ChurnError, "Card not found"):
            cb.save(self.c, {"name": "X"}, 999)
        with self.assertRaisesRegex(ChurnError, "Benefit not found"):
            cb.save(self.c, {"name": "X"}, None, 999)
        with self.assertRaisesRegex(ChurnError, "Benefit not found"):
            cb.use(self.c, 999, {}, TODAY)
        bid = cb.save(self.c, {"preset": "dining", "name": "Resy credit", "amount": 50, "period": "quarterly"}, self.card)
        row = self.c.execute(select(ChurnBenefit.name, ChurnBenefit.kind, ChurnBenefit.period, ChurnBenefit.basis,
                                    ChurnBenefit.preset, ChurnBenefit.counts, ChurnBenefit.remind, ChurnBenefit.active)
                             .where(ChurnBenefit.id == bid)).fetchone()
        self.assertEqual(tuple(row), ("Resy credit", "credit", "quarterly", "calendar", "dining", 1, 1, 1))
        cb.save(self.c, {"active": False, "amount": 60}, None, bid)
        out = self.card_out()
        self.assertEqual(out["benefits_value"], 0)   # inactive: not counted
        self.assertEqual(out["benefits"][0]["amount"], 60)

    def test_lounge_guests(self):
        pp = cb.save(self.c, {"preset": "priority_pass", "guests": 2}, self.card)
        c1 = cb.save(self.c, {"preset": "capital_one_lounge", "guests": 0}, self.card)
        other = cb.save(self.c, {"preset": "lounge"}, self.card)
        guests = {b["id"]: b["guests"] for b in self.card_out()["benefits"]}
        self.assertEqual((guests[pp], guests[c1], guests[other]), (2, 0, None))   # 0: just you; None: not set
        cb.save(self.c, {"guests": None}, None, pp)
        cb.save(self.c, {"guests": "1"}, None, c1)
        guests = {b["id"]: b["guests"] for b in self.card_out()["benefits"]}
        self.assertEqual((guests[pp], guests[c1]), (None, 1))
        # Only access keeps guests: a credit sent with some, or a lounge switched to a credit, has none.
        credit = cb.save(self.c, {"name": "Travel credit", "kind": "credit", "amount": 300, "guests": 2}, self.card)
        cb.save(self.c, {"kind": "credit"}, None, c1)
        cb.save(self.c, {"guests": 3}, None, credit)
        guests = {b["id"]: b["guests"] for b in self.card_out()["benefits"]}
        self.assertEqual((guests[credit], guests[c1]), (None, None))


if __name__ == "__main__":
    unittest.main()
