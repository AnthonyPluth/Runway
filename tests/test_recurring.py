"""Recurring: matching recurring payments and their amounts."""
import unittest
from datetime import date
from unittest import mock

from sqlalchemy import insert, select, update

from runway import db, forecast, recurring
from runway.models import Override, Recurring, Transaction
from runway.server.common import ApiError
from tests.shared import TODAY, LedgerCase


class RecurringTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 3000.0)
        # Electric bill: same merchant, different amount every month, plus a refund from them.
        for d, amt in [("2026-06-14", -91.20), ("2026-07-15", -143.77), ("2026-08-14", -160.02), ("2026-09-15", -118.40)]:
            self.tx("chk", d, amt, "COMED ELECTRIC PAYMENT 88812")
        self.tx("chk", "2026-09-02", 12.00, "COMED ELECTRIC REFUND")
        self.tx("chk", "2026-09-10", -60.0, "TARGET")
        self.conn.execute(insert(Recurring).values(name="Electric", account_id="chk", amount=-120, frequency="monthly",
                                                   anchor_date="2026-06-15", match="comed electric",
                                                   amount_mode="avg3"))
        self.rid = self.conn.execute(select(Recurring.id)).fetchone()[0]

    def test_auto_match_by_merchant_any_amount(self):
        n = recurring.auto_match(self.conn)
        self.assertEqual(n, 4)  # all four bills, not the refund, not Target
        linked = [r[0] for r in self.conn.execute(select(Transaction.amount)
                                                  .where(Transaction.recurring_id == self.rid)
                                                  .order_by(Transaction.posted))]
        self.assertEqual(linked, [-91.20, -143.77, -160.02, -118.40])

    def test_never_match_is_respected(self):
        recurring.link(self.conn, "chk|0", None)  # "not recurring"
        recurring.auto_match(self.conn)
        self.assertEqual(self.conn.execute(select(Transaction.recurring_id)
                                           .where(Transaction.id == "chk|0")).fetchone()[0], 0)

    def test_amount_modes_and_forecast(self):
        recurring.auto_match(self.conn)
        item = dict(self.conn.execute(select(Recurring)).fetchone())
        hist = recurring.matched(self.conn, self.rid)
        self.assertEqual(recurring.expected_amount(item, hist), round((-118.40 - 160.02 - 143.77) / 3, 2))
        self.assertEqual(recurring.expected_amount({**item, "amount_mode": "last"}, hist), -118.40)
        self.assertEqual(recurring.expected_amount({**item, "amount_mode": "fixed"}, hist), -120.0)
        # An item without an amount learns it, money in or out, from the payments.
        income = {"amount": 0, "amount_mode": "last", "frequency": "monthly", "anchor_date": "2026-06-01"}
        self.assertEqual(recurring.expected_amount(income, [{"posted": "2026-09-01", "amount": 40.0, "pending": 0}]), 40.0)
        fc = forecast.build(self.conn, TODAY, 60)
        elec = [e for e in fc["events"] if e.get("recurring_id") == self.rid]
        # Sep 15 already posted, so the next ones are Oct 15 and Nov 15 (a Sunday: Monday the 16th) at the 3-month average
        self.assertEqual([e["date"] for e in elec], ["2026-10-15", "2026-11-16"])
        self.assertEqual(elec[0]["amount"], round((-118.40 - 160.02 - 143.77) / 3, 2))

    def test_early_payment_not_counted_twice(self):
        self.tx("chk", "2026-10-12", -130.0, "COMED ELECTRIC PAYMENT")  # paid 3 days early
        recurring.auto_match(self.conn)
        fc = forecast.build(self.conn, date(2026, 10, 13), 40)
        dates = [e["date"] for e in fc["events"] if e.get("recurring_id") == self.rid]
        self.assertEqual(dates, ["2026-11-16"])   # Nov 15 is a Sunday

    def test_override_one_occurrence(self):
        recurring.auto_match(self.conn)
        self.conn.execute(insert(Override).values(key=f"rec:{self.rid}:2026-10-15", amount=-250.0))
        fc = forecast.build(self.conn, TODAY, 60)
        e = next(e for e in fc["events"] if e["date"] == "2026-10-15")
        self.assertEqual((e["amount"], e["overridden"]), (-250.0, True))
        self.assertAlmostEqual(fc["total"][-1], 3000 + sum(x["amount"] for x in fc["events"]), places=2)

    def test_create_from_transaction_and_link_teaches_match(self):
        self.tx("chk", "2026-09-05", -15.99, "NETFLIX.COM 8665797172")
        tid = self.conn.execute(select(Transaction.id).where(Transaction.description.like("NETFLIX%"))).fetchone()[0]
        rid = recurring.create_from_transaction(self.conn, tid, "monthly")
        item = self.conn.execute(select(Recurring).where(Recurring.id == rid)).fetchone()
        self.assertEqual((item["amount"], item["frequency"], item["match"]), (-15.99, "monthly", "netflix"))   # the brand's name the payee has
        # an item without merchant text learns it from the first link
        self.conn.execute(insert(Recurring).values(name="Store", account_id="chk", amount=-60, frequency="monthly",
                                                   anchor_date="2026-09-10"))
        sid = self.conn.execute(select(Recurring.id).where(Recurring.name == "Store")).fetchone()[0]
        recurring.link(self.conn, self.conn.execute(select(Transaction.id)
                                                    .where(Transaction.description == "TARGET")).fetchone()[0], sid)
        self.assertEqual(self.conn.execute(select(Recurring.match).where(Recurring.id == sid)).fetchone()[0], "target")


class RecurringAmountTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.acct("cc", "credit", 0.0)
        self.tx("cc", "2026-09-01", -14.99, "AMAZON PRIME")
        for d, amt in (("2026-09-03", -86.40), ("2026-09-08", -5.29), ("2026-09-12", -212.00)):
            self.tx("cc", d, amt, "AMAZON")
        self.tx("cc", "2026-08-01", -14.99, "AMAZON")
        self.conn.execute(insert(Recurring).values(name="Prime", account_id="cc", amount=-14.99, frequency="monthly",
                                                   anchor_date="2026-08-01"))
        self.rid = self.conn.execute(select(Recurring.id)).fetchone()[0]

    def linked(self):
        return sorted(r[0] for r in self.conn.execute(select(Transaction.amount).where(Transaction.recurring_id == self.rid)))

    def test_linking_one_charge_doesnt_link_the_whole_merchant_with_a_range(self):
        self.conn.execute(update(Recurring).where(Recurring.id == self.rid).values(amount_min=10, amount_max=20))
        self.assertIsNone(recurring.link(self.conn, "cc|0", self.rid))   # learns "amazon prime"...
        self.conn.execute(update(Recurring).where(Recurring.id == self.rid).values(match="amazon"))   # ...or plain "amazon"
        recurring.auto_match(self.conn, [self.rid])
        self.assertEqual(self.linked(), [-14.99, -14.99])

    def test_without_a_range_any_amount_with_the_text_matches(self):
        self.conn.execute(update(Recurring).where(Recurring.id == self.rid).values(match="amazon"))
        self.assertEqual(recurring.auto_match(self.conn, [self.rid]), 5)
        self.assertEqual(self.linked(), [-212.0, -86.4, -14.99, -14.99, -5.29])

    def test_either_end_of_the_range_can_be_left_open(self):
        self.conn.execute(update(Recurring).where(Recurring.id == self.rid).values(match="amazon", amount_min=14.99))
        recurring.auto_match(self.conn, [self.rid])
        self.assertEqual(self.linked(), [-212.0, -86.4, -14.99, -14.99])   # at least $14.99 (to the cent)
        self.conn.execute(update(Transaction).values(recurring_id=None))
        self.conn.execute(update(Recurring).where(Recurring.id == self.rid).values(amount_min=None, amount_max=14.99))
        recurring.auto_match(self.conn, [self.rid])
        self.assertEqual(self.linked(), [-14.99, -14.99, -5.29])

    def test_how_each_transaction_was_linked_is_kept(self):
        self.conn.execute(update(Recurring).where(Recurring.id == self.rid).values(match="amazon prime"))
        self.tx("cc", "2026-09-20", -14.99, "AMAZON PRIME")
        recurring.link(self.conn, "cc|4", self.rid)   # the $14.99 "AMAZON" one, by hand
        recurring.auto_match(self.conn, [self.rid])
        by = dict(self.conn.execute(select(Transaction.id, Transaction.recurring_linked_by)
                                    .where(Transaction.recurring_id == self.rid)).fetchall())
        self.assertEqual(by, {"cc|0": "auto", "cc|4": "you", "cc|5": "auto"})
        rid = recurring.create_from_transaction(self.conn, "cc|3", "monthly")   # the $212 one
        self.assertEqual(self.conn.execute(select(Transaction.recurring_linked_by).where(Transaction.id == "cc|3")).scalar(), "you")
        self.assertEqual(rid, self.conn.execute(select(Transaction.recurring_id).where(Transaction.id == "cc|3")).scalar())

    def test_linking_by_hand_offers_the_transactions_text_when_none_of_the_items_is_on_it(self):
        self.conn.execute(update(Recurring).where(Recurring.id == self.rid).values(match="amazon prime"))
        self.assertEqual(recurring.link(self.conn, "cc|4", self.rid), "amazon")
        self.assertIsNone(recurring.link(self.conn, "cc|0", self.rid))   # "amazon prime" is on this one
        # One on another account: matching never looks there, so its text isn't offered.
        self.acct("other", "credit", 0.0)
        self.tx("other", "2026-09-20", -14.99, "SOME OTHER SHOP")
        other = self.conn.execute(select(Transaction.id).where(Transaction.account_id == "other")).scalar()
        self.assertIsNone(recurring.link(self.conn, other, self.rid))
        self.conn.execute(update(Transaction).where(Transaction.id == other).values(recurring_id=None))
        from runway.server.api import recurring as api
        self.assertEqual(api.api_tx_recurring(self.conn, None, {"recurring_id": self.rid}, "cc|1"),
                         {"ok": True, "suggest_text": "amazon"})
        self.assertEqual(api.api_tx_recurring(self.conn, None, {"recurring_id": self.rid}, "cc|0"), {"ok": True})
        # Taking it: the item matches either text from now on (here with a range, so not every order).
        self.conn.execute(update(Recurring).where(Recurring.id == self.rid).values(amount_max=20))
        self.assertEqual(api.api_recurring_add_text(self.conn, None, {"text": " Amazon "}, str(self.rid)), {"ok": True, "linked": 1})
        self.assertEqual(self.conn.execute(select(Recurring.match).where(Recurring.id == self.rid)).scalar(), "amazon prime\namazon")
        self.assertEqual(self.linked(), [-86.4, -14.99, -14.99, -5.29])   # the $86.40 linked by hand; $5.29 matched now
        api.api_recurring_add_text(self.conn, None, {"text": "amazon"}, str(self.rid))   # adding it again changes nothing
        self.assertEqual(self.conn.execute(select(Recurring.match).where(Recurring.id == self.rid)).scalar(), "amazon prime\namazon")
        for bad in ("", "ab"):
            with self.subTest(text=bad), self.assertRaises(ApiError):
                api.api_recurring_add_text(self.conn, None, {"text": bad}, str(self.rid))
        with self.assertRaises(ApiError):
            api.api_recurring_add_text(self.conn, None, {"text": "amazon"}, "99")

    def test_no_suggestion_when_recent_payments_straddle_the_amount(self):
        item = {"amount": 100.0, "amount_mode": "fixed", "frequency": "monthly", "anchor_date": "2026-07-01"}
        history = [{"posted": "2026-09-01", "amount": 60.0, "pending": 0}, {"posted": "2026-08-01", "amount": 140.0, "pending": 0}]
        self.assertIsNone(recurring.stale_amount(item, history, date(2026, 10, 1)))
        history[1]["amount"] = 65.0   # both under it, and close together: offered
        self.assertEqual(recurring.stale_amount(item, history, date(2026, 10, 1)), 62.5)
        history[1]["amount"] = 30.0   # both under it, but too far apart to say what it is now
        self.assertIsNone(recurring.stale_amount(item, history, date(2026, 10, 1)))

    def test_a_drift_counts_past_5_percent_or_2_dollars_whichever_is_more(self):
        def stale(amount, paid):
            item = {"amount": -amount, "amount_mode": "fixed", "frequency": "monthly", "anchor_date": "2026-07-01"}
            history = [{"posted": d, "amount": -p, "pending": 0} for d, p in zip(("2026-09-01", "2026-08-01"), paid, strict=True)]
            return recurring.stale_amount(item, history, date(2026, 10, 1))
        self.assertEqual(stale(80, (87.40, 87.40)), -87.4)       # $7.40 off an $80 bill, to the cent
        self.assertIsNone(stale(80, (81.90, 81.90)))              # under $2 off: the same bill
        self.assertEqual(stale(30, (32.10, 32.10)), -32.1)        # $2.10 off: more than $2 (5% would be $1.50)
        self.assertIsNone(stale(1000, (1040, 1045)))              # $45 off $1,000 is under 5%
        self.assertEqual(stale(1000, (1060, 1055)), -1057.5)      # over 5%, both the same way
        self.assertIsNone(stale(1000, (1060, 940)))               # one each way


class RecurringEditTests(LedgerCase):
    """The merchant texts and amount range, as the editor sends them."""

    def setUp(self):
        super().setUp()
        from runway.server.api import recurring as api
        self.api = api
        self.acct("chk", "checking", 0.0)
        self.tx("chk", "2026-09-15", 2150.4, "DIRECT DEPOSIT ACME CORP PAYROLL")
        self.tx("chk", "2026-09-16", 2849.6, "ONLINE TRANSFER FROM SAVINGS XXXXXX0000")
        self.tx("chk", "2026-09-25", 375.2, "ACME CORP EXPENSES")
        self.body = {"name": "Paycheck", "account_id": "chk", "amount": 5000, "frequency": "semimonthly", "dates": "15,31",
                     "anchor_date": "2026-06-15", "match": "Acme\n online transfer from SAVINGS \n\nacme"}
        self.rid = self.api.api_recurring_add(self.conn, None, self.body)["id"]

    def item(self):
        return dict(self.conn.execute(select(Recurring).where(Recurring.id == self.rid)).fetchone())

    def linked(self):
        return sorted(r[0] for r in self.conn.execute(select(Transaction.amount).where(Transaction.recurring_id == self.rid)))

    def test_several_texts_are_kept_one_per_line_and_any_of_them_matches(self):
        self.assertEqual(self.item()["match"], "acme\nonline transfer from savings")
        self.assertEqual(recurring.match_texts(self.item()), ["acme", "online transfer from savings"])
        self.assertEqual(self.linked(), [375.2, 2150.4, 2849.6])
        self.assertEqual(recurring.clean_texts(["A", " a ", "", "b"]), "a\nb")
        self.assertIsNone(recurring.clean_texts(" \n "))

    def test_a_range_leaves_out_what_it_excludes_and_drops_automatic_links_outside_it(self):
        self.assertEqual(self.linked(), [375.2, 2150.4, 2849.6])
        self.api.api_recurring_update(self.conn, None, {**self.body, "amount_min": "1500", "amount_max": 7000}, str(self.rid))
        self.assertEqual((self.item()["amount_min"], self.item()["amount_max"]), (1500, 7000))
        self.assertEqual(self.linked(), [2150.4, 2849.6])           # linked automatically: it goes
        # One you linked stays, and so does one from before Runway kept how (it may be yours).
        recurring.link(self.conn, "chk|2", self.rid)
        self.tx("chk", "2026-08-25", 300.0, "ACME CORP BONUS")
        self.conn.execute(update(Transaction).where(Transaction.id == "chk|3").values(recurring_id=self.rid))
        self.api.api_recurring_update(self.conn, None, {**self.body, "amount_min": 2000, "amount_max": 7000}, str(self.rid))
        self.assertEqual(self.linked(), [300.0, 375.2, 2150.4, 2849.6])
        # Changing something else (the name) leaves every link alone.
        self.conn.execute(update(Transaction).where(Transaction.id == "chk|2").values(recurring_linked_by="auto"))
        self.api.api_recurring_update(self.conn, None, {**self.body, "name": "Pay", "amount_min": 2000, "amount_max": 7000}, str(self.rid))
        self.assertEqual(self.linked(), [300.0, 375.2, 2150.4, 2849.6])
        # Taking a text away drops what only it matched, unless you linked it.
        recurring.link(self.conn, "chk|2", self.rid)
        self.api.api_recurring_update(self.conn, None, {**self.body, "match": "acme", "amount_min": 2000}, str(self.rid))
        self.assertEqual(self.linked(), [300.0, 375.2, 2150.4])
        # Another account: what matched on the old one goes; the one you linked by hand stays.
        self.acct("sav", "savings", 0.0)
        self.api.api_recurring_update(self.conn, None, {**self.body, "account_id": "sav"}, str(self.rid))
        self.assertEqual(self.linked(), [375.2])

    def test_a_part_linked_by_hand_from_another_account_survives_edits(self):
        self.acct("sav", "savings", 0.0)
        self.tx("sav", "2026-09-16", 900.0, "MOVED FROM PAYROLL")
        part = self.conn.execute(select(Transaction.id).where(Transaction.account_id == "sav")).scalar()
        recurring.link(self.conn, part, self.rid)
        for change in ({"name": "Pay"}, {"amount": 5100}, {"frequency": "monthly", "dates": ""}):
            with self.subTest(change=change):
                self.api.api_recurring_update(self.conn, None, {**self.body, **change}, str(self.rid))
                self.assertIn(900.0, self.linked())

    def test_the_range_is_checked(self):
        for lo, hi, msg in ((7000, 1500, "The smallest amount is bigger than the largest"), ("lots", None, "The amount range must be numbers")):
            with self.subTest(lo=lo), self.assertRaisesRegex(ApiError, msg):
                self.api.api_recurring_update(self.conn, None, {**self.body, "amount_min": lo, "amount_max": hi}, str(self.rid))
        self.api.api_recurring_update(self.conn, None, {**self.body, "amount_min": -1500, "amount_max": ""}, str(self.rid))
        self.assertEqual((self.item()["amount_min"], self.item()["amount_max"]), (1500, None))   # either way the money goes


class ChangedAmountTests(LedgerCase):
    """The real case: a paycheck set at $5,000 that dropped, with smaller deposits from the same payroll company."""
    PAY = "DIRECT DEPOSIT ACME CORP PAYROLL"

    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 3000.0)
        for d in ("2026-06-29", "2026-07-14", "2026-07-30"):
            self.tx("chk", d, 5004.1, self.PAY)
        self.tx("chk", "2026-08-30", 2450.25, self.PAY)
        self.tx("chk", "2026-09-14", 1700.75, self.PAY)
        self.conn.execute(insert(Recurring).values(name="Paycheck", account_id="chk", amount=5000, frequency="semimonthly",
                                                   dates="15,31", anchor_date="2026-06-15", match="acme",
                                                   amount_mode="fixed"))
        self.rid = self.conn.execute(select(Recurring.id)).fetchone()[0]
        self.assertEqual(recurring.auto_match(self.conn), 5)   # whatever the amount

    def linked(self):
        return [r[0] for r in self.conn.execute(select(Transaction.amount).where(Transaction.recurring_id == self.rid)
                                                .order_by(Transaction.posted))]

    def item(self):
        return dict(self.conn.execute(select(Recurring).where(Recurring.id == self.rid)).fetchone())

    def test_the_deposit_links_and_a_range_keeps_out_the_small_one(self):
        self.tx("chk", "2026-09-25", 375.2, "ACME CORP EXPENSES")
        self.tx("chk", "2026-09-30", 2150.4, self.PAY + " (Cash)")
        self.assertEqual(recurring.auto_match(self.conn), 2)   # with no range, both
        self.assertEqual(self.linked(), [5004.1] * 3 + [2450.25, 1700.75, 375.2, 2150.4])
        self.conn.execute(update(Transaction).where(Transaction.posted >= "2026-09-25").values(recurring_id=None))
        self.conn.execute(update(Recurring).values(amount_min=1500, amount_max=7000))
        self.assertEqual(recurring.auto_match(self.conn), 1)
        self.assertEqual(self.linked(), [5004.1] * 3 + [2450.25, 1700.75, 2150.4])
        # The forecast still uses the amount you set: a fixed amount isn't changed behind your back.
        self.assertEqual(recurring.expected_amount(self.item(), recurring.matched(self.conn, self.rid)), 5000)

    def test_taking_the_suggested_amount_moves_a_range_that_no_longer_holds_it(self):
        # An item the migration gave the old implicit range (±30% of $5,000); "use $2,100" must let $2,500 deposits in.
        from runway.server.api import recurring as api
        self.conn.execute(update(Recurring).values(amount_min=3500, amount_max=6500))
        body = {k: self.item()[k] for k in ("name", "account_id", "frequency", "anchor_date", "match", "amount_mode", "dates",
                                            "amount_min", "amount_max")}
        api.api_recurring_update(self.conn, None, {**body, "amount": 2100}, str(self.rid))
        self.assertEqual((self.item()["amount_min"], self.item()["amount_max"]), (round(3500 * 2100 / 5000, 2), round(6500 * 2100 / 5000, 2)))
        self.tx("chk", "2026-09-30", 2550.0, self.PAY)
        recurring.auto_match(self.conn)
        self.assertIn(2550.0, self.linked())
        # Moving the range with the amount keeps the links made at the old one: they were those paydays' payments.
        self.assertIn(5004.1, self.linked())
        # A range that still holds the new amount, or one you change in the same save, is left as it is.
        api.api_recurring_update(self.conn, None, {**body, "amount": 2600, "amount_min": 1500, "amount_max": 7000}, str(self.rid))
        self.assertEqual((self.item()["amount_min"], self.item()["amount_max"]), (1500, 7000))
        api.api_recurring_update(self.conn, None, {**body, "amount": 2700, "amount_min": 1500, "amount_max": 7000}, str(self.rid))
        self.assertEqual((self.item()["amount_min"], self.item()["amount_max"]), (1500, 7000))

    def test_suggests_the_recent_amount_once_the_last_payments_all_differ(self):
        self.tx("chk", "2026-09-30", 2150.4, self.PAY)
        recurring.auto_match(self.conn)
        hist = recurring.matched(self.conn, self.rid)
        later = date(2026, 10, 10)
        self.assertEqual(recurring.stale_amount(self.item(), hist, later), round((2150.4 + 1700.75 + 2450.25) / 3, 2))
        # Not while Sep 30's window is open (more may come), which leaves one that fits in the last three...
        self.assertIsNone(recurring.stale_amount(self.item(), hist, date(2026, 10, 1)))
        # ...nor while one of the last three still fits the amount, nor for an amount learned from the payments.
        self.assertIsNone(recurring.stale_amount(self.item(), hist[2:], later))
        self.assertIsNone(recurring.stale_amount({**self.item(), "amount": 2500}, hist, later))
        self.assertIsNone(recurring.stale_amount({**self.item(), "amount_mode": "avg3"}, hist, later))
        self.assertIsNone(recurring.stale_amount(self.item(), hist[:1], later))   # one payment isn't a pattern yet
        from runway.server.api import recurring as api
        with mock.patch.object(api, "date", wraps=date) as d:
            d.today.return_value = later
            pay = next(i for i in api.api_recurring(self.conn, None, None) if i["id"] == self.rid)
        self.assertEqual((pay["suggested_amount"], pay["expected_amount"]), (2100.47, 5000))

    def test_after_you_change_the_amount_older_payments_dont_suggest_going_back(self):
        from runway.server.api import recurring as api
        body = {"name": "Paycheck", "account_id": "chk", "amount": 2500, "frequency": "semimonthly", "dates": "15,31",
                "anchor_date": "2026-06-15", "match": "acme"}
        self.conn.execute(update(Transaction).where(Transaction.posted >= "2026-08").values(recurring_id=0))
        hist = recurring.matched(self.conn, self.rid)   # three at $5,004.10
        self.assertIsNone(recurring.stale_amount(self.item(), hist, date(2026, 10, 10)))   # they fit $5,000
        with mock.patch.object(api, "date", wraps=date) as d:
            d.today.return_value = date(2026, 10, 1)
            api.api_recurring_update(self.conn, None, body, str(self.rid))
        self.assertEqual(self.item()["amount_since"], "2026-10-01")
        self.assertIsNone(recurring.stale_amount(self.item(), hist, date(2026, 10, 10)))   # not "about $6,106, not $2,500"
        # Changing something else keeps the date.
        with mock.patch.object(api, "date", wraps=date) as d:
            d.today.return_value = date(2026, 10, 5)
            api.api_recurring_update(self.conn, None, {**body, "name": "Pay"}, str(self.rid))
        self.assertEqual(self.item()["amount_since"], "2026-10-01")

    def test_the_31st_is_the_last_day_of_a_30_day_month(self):
        self.tx("chk", "2026-09-30", 2150.4, self.PAY)
        recurring.auto_match(self.conn)
        item = self.item()
        self.assertEqual(forecast.occurrences(item, date(2026, 9, 1), date(2026, 10, 31)),
                         [date(2026, 9, 15), date(2026, 9, 30), date(2026, 10, 15), date(2026, 10, 30)])   # Sat Oct 31
        def september():   # (no deposit here on Aug 14)
            return [m["date"] for m in recurring.missed(self.conn, date(2026, 10, 10)) if m["date"] >= "2026-09"]
        self.assertEqual(september(), [])
        # Without the Sep 30 deposit, that's the payment that's missing.
        self.conn.execute(update(Transaction).where(Transaction.posted == "2026-09-30").values(recurring_id=None))
        self.assertEqual(september(), ["2026-09-30"])


class SplitPaymentTests(LedgerCase):
    """One occurrence paid in parts: the $5,000 paycheck as a $2,150.40 deposit and a $2,849 transfer from another bank."""

    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 3000.0)
        self.tx("chk", "2026-09-15", 5000.0, "DIRECT DEPOSIT ACME CORP PAYROLL", "Income")
        self.tx("chk", "2026-09-30", 2150.4, "DIRECT DEPOSIT ACME CORP PAYROLL", "Income")
        self.conn.execute(insert(Recurring).values(name="Paycheck", account_id="chk", amount=5000, frequency="semimonthly",
                                                   dates="15,31", anchor_date="2026-06-15", match="acme", amount_mode="fixed"))
        self.rid = self.conn.execute(select(Recurring.id)).fetchone()[0]
        recurring.auto_match(self.conn)

    def events(self, today):
        return [(e["date"], e["amount"], e.get("late_from"), e.get("paid_so_far"))
                for e in forecast.build(self.conn, today, 31)["events"] if e.get("recurring_id") == self.rid]

    def test_the_rest_is_expected_until_the_window_closes(self):
        self.assertEqual(self.events(date(2026, 10, 1)), [("2026-10-01", 2849.6, "2026-09-30", 2150.4),
                                                          ("2026-10-15", 5000, None, None), ("2026-10-30", 5000, None, None)])
        # Once Sep 30's window has closed (Oct 4), the rest isn't expected any more: no late money is made up...
        self.assertEqual(self.events(date(2026, 10, 5))[0], ("2026-10-15", 5000, None, None))
        # ...and it isn't missed either: something came.
        self.assertNotIn("2026-09-30", [m["date"] for m in recurring.missed(self.conn, date(2026, 10, 10))])

    def test_the_transfer_completes_it_whatever_its_category(self):
        self.tx("chk", "2026-10-01", 2849.0, "ONLINE TRANSFER FROM SAVINGS XXXXXX0000", "Transfer")
        self.assertEqual(recurring.link(self.conn, "chk|2", self.rid), "online transfer from savings")
        self.assertEqual(self.events(date(2026, 10, 1))[0], ("2026-10-15", 5000, None, None))   # $4,999.40 is close enough
        # An amount learned from the payments counts the parts together.
        hist = recurring.matched(self.conn, self.rid)
        self.assertEqual(recurring.expected_amount({**dict(self.conn.execute(select(Recurring)).fetchone()), "amount_mode": "last"}, hist),
                         4999.4)
        self.assertIsNone(recurring.stale_amount(dict(self.conn.execute(select(Recurring)).fetchone()), hist, date(2026, 10, 10)))

    def test_a_part_that_comes_first_leaves_the_rest_on_the_date(self):
        self.tx("chk", "2026-10-13", 1000.0, "ACME CORP ADVANCE", "Income")
        recurring.auto_match(self.conn)
        self.assertEqual(self.events(date(2026, 10, 13))[0], ("2026-10-15", 4000, None, 1000))

    def test_an_edit_is_the_occurrence_in_all_so_what_came_in_comes_off_it(self):
        # You knew Sep 30 would be smaller and set it to $2,140 before the deposit came in: with $2,150.40 in, nothing's left.
        self.conn.execute(insert(Override).values(key=f"rec:{self.rid}:2026-09-30", amount=2140.0))
        self.assertEqual([e[0] for e in self.events(date(2026, 10, 1))], ["2026-10-15", "2026-10-30"])
        # Set to $4,900 instead: the rest is $4,900 less what came, marked as edited from the usual rest.
        self.conn.execute(update(Override).values(amount=4900.0))
        e = next(e for e in forecast.build(self.conn, date(2026, 10, 1), 31)["events"] if e.get("key") == f"rec:{self.rid}:2026-09-30")
        self.assertEqual((e["amount"], e["original_amount"], e["overridden"], e["paid_so_far"]), (2749.6, 2849.6, True, 2150.4))

    def test_a_bill_that_comes_in_lower_is_done_not_partly_paid(self):
        def rest(mode, amount, paid):
            item = {"frequency": "monthly", "amount_mode": mode, "amount": amount}
            return recurring.still_due(item, date(2026, 10, 3), {date(2026, 10, 3): paid}, date(2026, 10, 4), amount)
        self.assertIsNone(rest("avg3", -120.0, -95.0))     # learned from the payments: it varies, $95 was this month's
        self.assertIsNone(rest("last", 2500.0, 1500.0))
        self.assertIsNone(rest("fixed", -120.0, -95.0))    # a fixed bill that came in cheaper
        self.assertEqual(rest("fixed", -139.0, -15.0), -124.0)   # a small charge isn't the renewal
        self.assertEqual(rest("fixed", 5000.0, 2150.4), 2849.6)   # money in: the rest of the paycheck
        item = {"frequency": "monthly", "amount_mode": "avg3", "amount": -120.0}
        self.assertEqual(recurring.still_due(item, date(2026, 10, 3), {date(2026, 10, 3): -95.0}, date(2026, 10, 4), -200.0, True),
                         -105.0)   # an amount you set for this one counts as fixed
        # ...and whatever of it hasn't come is still expected, however small: a rest edited to $200, a $10 renewal rest.
        pay = {"frequency": "monthly", "amount_mode": "fixed", "amount": 5000.0}
        self.assertEqual(recurring.still_due(pay, date(2026, 10, 3), {date(2026, 10, 3): 2150.4}, date(2026, 10, 4), 2350.4, True),
                         200.0)
        self.assertEqual(recurring.still_due(item, date(2026, 10, 3), {date(2026, 10, 3): -15.0}, date(2026, 10, 4), -25.0, True),
                         -10.0)
        self.assertIsNone(recurring.still_due(pay, date(2026, 10, 3), {date(2026, 10, 3): 2400.0}, date(2026, 10, 4), 2350.4, True))

    def test_a_small_charge_near_a_yearly_renewal_leaves_the_rest_expected(self):
        self.conn.execute(insert(Recurring).values(name="Prime", account_id="chk", amount=-139, frequency="yearly",
                                                   anchor_date="2025-10-14", match="amazon prime", amount_mode="fixed"))
        prime = self.conn.execute(select(Recurring.id).where(Recurring.name == "Prime")).scalar()
        self.tx("chk", "2026-10-08", -15.0, "AMAZON PRIME VIDEO RENTAL")
        recurring.auto_match(self.conn)
        events = [(e["date"], e["amount"]) for e in forecast.build(self.conn, date(2026, 10, 9), 31)["events"]
                  if e.get("recurring_id") == prime]
        self.assertEqual(events, [("2026-10-14", -124.0)])
        # A refund (money the other way) doesn't count toward it.
        self.tx("chk", "2026-10-09", 15.0, "AMAZON PRIME REFUND")
        recurring.link(self.conn, "chk|3", prime)
        self.assertEqual(recurring.paid_by_occurrence(dict(self.conn.execute(select(Recurring).where(Recurring.id == prime)).fetchone()),
                                                      recurring.matched(self.conn, prime)), {date(2026, 10, 14): -15.0})


class ScheduleTests(LedgerCase):
    """Rent of $1,500 on the 1st, paid Jul 1, Aug 3 (Aug 1 was a Saturday) and Sep 1; today is Wednesday Sep 23."""

    def setUp(self):
        super().setUp()
        from runway.server.api import recurring as api
        self.api = api
        self.acct("chk", "checking", 5000.0)
        self.acct("sav", "savings", 500.0)
        for d in ("2026-07-01", "2026-08-03", "2026-09-01"):
            self.tx("chk", d, -1500.0, "CITY RENT")
        self.body = {"name": "Rent", "account_id": "chk", "amount": -1500, "frequency": "monthly",
                     "anchor_date": "2026-07-01", "match": "city rent"}
        self.rid = self.api.api_recurring_add(self.conn, None, self.body)["id"]

    def events(self, today=TODAY, days=120):
        return [(e["date"], e["amount"]) for e in forecast.build(self.conn, today, days)["events"] if e.get("recurring_id") == self.rid]

    def listed(self, today=TODAY):
        with mock.patch.object(self.api, "date", wraps=date) as d:
            d.today.return_value = today
            return next(i for i in self.api.api_recurring(self.conn, None, None) if i["id"] == self.rid)

    def skip(self, day):
        from runway.server.api.state import api_override_set
        api_override_set(self.conn, None, {"key": f"rec:{self.rid}:{day}", "amount": 0})

    def test_the_forecast_stops_at_its_end_date(self):
        self.assertEqual(self.events(), [("2026-10-01", -1500), ("2026-11-02", -1500), ("2026-12-01", -1500), ("2027-01-04", -1500)])   # New Year’s Day, then a weekend
        # Ending on Nov 1 keeps Nov 1's (paid the Monday after, Nov 2), and nothing after it.
        self.api.api_recurring_update(self.conn, None, {**self.body, "end_date": "2026-11-01"}, str(self.rid))
        self.assertEqual(self.events(), [("2026-10-01", -1500), ("2026-11-02", -1500)])
        self.assertEqual(self.listed(date(2026, 11, 3))["next_date"], None)
        # A day earlier, Oct 1's is the last one.
        self.api.api_recurring_update(self.conn, None, {**self.body, "end_date": "2026-10-31"}, str(self.rid))
        self.assertEqual(self.events(), [("2026-10-01", -1500)])
        # Clearing it carries on as before.
        self.api.api_recurring_update(self.conn, None, {**self.body, "end_date": ""}, str(self.rid))
        self.assertEqual(len(self.events()), 4)

    def test_an_end_date_is_checked(self):
        for end, msg in (("2026-06-30", "It ends before it starts"), ("next week", "Pick an end date (YYYY-MM-DD)")):
            with self.subTest(end=end), self.assertRaises(ApiError) as e:
                self.api.api_recurring_update(self.conn, None, {**self.body, "end_date": end}, str(self.rid))
            self.assertEqual(str(e.exception), msg)
        self.assertIsNone(self.conn.execute(select(Recurring.end_date)).scalar())

    def test_a_paused_item_is_out_of_the_forecast_and_never_missed(self):
        self.api.api_recurring_update(self.conn, None, {**self.body, "active": 0}, str(self.rid))
        self.assertEqual(self.events(), [])
        self.assertEqual(recurring.missed(self.conn, date(2026, 10, 20)), [])
        self.api.api_recurring_update(self.conn, None, {**self.body, "active": 1}, str(self.rid))
        self.assertEqual(len(self.events()), 4)

    def test_skipping_the_next_one_is_a_zero_edit_the_forecast_and_the_list_follow(self):
        self.assertEqual((self.listed()["next_date"], self.listed()["skipped"]), ("2026-10-01", []))
        self.skip("2026-10-01")
        fc = forecast.build(self.conn, TODAY, 120)
        oct1 = next(e for e in fc["events"] if e.get("recurring_id") == self.rid)
        self.assertEqual((oct1["date"], oct1["amount"], oct1["overridden"], oct1["original_amount"]), ("2026-10-01", 0, True, -1500))
        self.assertAlmostEqual(fc["total"][-1], 5000 - 1500 * 3, places=2)   # three payments, not four
        item = self.listed()
        self.assertEqual((item["next_date"], item["skipped"]), ("2026-11-02", ["2026-10-01"]))
        # It never shows up as missed once its window has passed...
        self.assertEqual(recurring.missed(self.conn, date(2026, 10, 20)), [])
        # ...and taking the skip back (the edit's reset) expects it again.
        from runway.server.api.state import api_override_delete
        api_override_delete(self.conn, None, {"key": f"rec:{self.rid}:2026-10-01"})
        self.assertEqual(self.listed()["next_date"], "2026-10-01")
        self.assertEqual([m["date"] for m in recurring.missed(self.conn, date(2026, 10, 20))], ["2026-10-01"])

    def test_one_thats_late_says_since_when_and_skipping_it_clears_that(self):
        item = self.listed(date(2026, 10, 3))   # Oct 1's hasn't come; its window is open until Oct 7
        self.assertEqual((item["late_date"], item["next_date"]), ("2026-10-01", "2026-11-02"))
        self.assertEqual(self.listed()["late_date"], None)
        self.skip("2026-10-01")
        self.assertEqual(self.listed(date(2026, 10, 3))["late_date"], None)
        # One due today is next, not late.
        self.assertEqual(self.listed(date(2026, 11, 2))["next_date"], "2026-11-02")

    def test_from_now_on_makes_one_dates_new_amount_the_items_and_drops_that_dates_edit(self):
        from runway.server.api.state import api_override_set
        self.conn.execute(update(Recurring).values(amount_mode="avg3", amount_min=1400, amount_max=1600))
        api_override_set(self.conn, None, {"key": f"rec:{self.rid}:2026-10-01", "amount": -1650})   # Oct 1's only
        self.assertEqual(self.events()[:2], [("2026-10-01", -1650), ("2026-11-02", -1500)])
        before = dict(self.conn.execute(select(Recurring)).fetchone())
        with mock.patch.object(self.api, "date", wraps=date) as d:
            d.today.return_value = TODAY
            r = self.api.api_recurring_amount(self.conn, None, {"amount": -1650, "key": f"rec:{self.rid}:2026-10-01"}, str(self.rid))
        item = dict(self.conn.execute(select(Recurring)).fetchone())
        # A fixed amount now (it was the average of the last 3), its range moved with it, the hint counting from today
        self.assertEqual((item["amount"], item["amount_mode"], item["amount_since"]), (-1650, "fixed", TODAY.isoformat()))
        self.assertEqual((item["amount_min"], item["amount_max"]), (1540, 1760))
        self.assertIsNone(self.conn.execute(select(Override.key)).scalar())   # no edit left: it's the usual amount now
        fc = forecast.build(self.conn, TODAY, 120)
        self.assertFalse(any(e.get("overridden") for e in fc["events"]))
        self.assertEqual({a for e, a in self.events()}, {-1650})
        self.assertEqual(len(recurring.matched(self.conn, self.rid)), 3)   # what's matched stays
        # Undo puts back exactly what was there.
        self.assertEqual(r["previous"], {k: before[k] for k in self.api.AMOUNT_COLUMNS})
        self.api.api_recurring_amount(self.conn, None, {"restore": r["previous"]}, str(self.rid))
        self.assertEqual(dict(self.conn.execute(select(Recurring)).fetchone()), before)
        for bad in ({"amount": "lots"}, {"restore": {"amount": -1}}, {"restore": {**r["previous"], "amount_mode": "guess"}}):
            with self.subTest(body=bad), self.assertRaises(ApiError):
                self.api.api_recurring_amount(self.conn, None, bad, str(self.rid))
        with self.assertRaises(ApiError):
            self.api.api_recurring_amount(self.conn, None, {"amount": -10}, "999")

    def test_payments_to_link_to_a_missed_one(self):
        self.tx("chk", "2026-10-05", -1500.0, "CITY RNT PMT ONLINE")      # the rent, under another text
        self.tx("chk", "2026-10-02", -1480.0, "SOFA STORE")               # about the same amount
        self.tx("chk", "2026-10-01", -3000.0, "BIG TV")                   # twice as much
        self.tx("chk", "2026-10-01", -1000.0, "SMALLER")                  # a third less
        self.tx("chk", "2026-10-01", 1500.0, "REFUND")                    # money in
        self.tx("chk", "2026-10-20", -1500.0, "CITY RNT PMT ONLINE")      # too long after (12 days for monthly)
        self.tx("sav", "2026-10-01", -1500.0, "CITY RNT PMT ONLINE")      # another account
        self.tx("chk", "2026-09-28", -1500.0, "NOT RENT")
        recurring.link(self.conn, self.conn.execute(select(Transaction.id).where(Transaction.description == "NOT RENT")).scalar(), None)
        found = self.api.api_recurring_candidates(self.conn, {"date": ["2026-10-01"]}, None, str(self.rid))
        self.assertEqual([(t["posted"], t["amount"]) for t in found], [("2026-10-05", -1500.0), ("2026-10-02", -1480.0)])
        self.assertTrue(found[0]["name"])
        for q in ({}, {"date": ["soon"]}):
            with self.subTest(q=q), self.assertRaises(ApiError):
                self.api.api_recurring_candidates(self.conn, q, None, str(self.rid))
        with self.assertRaises(ApiError):
            self.api.api_recurring_candidates(self.conn, {"date": ["2026-10-01"]}, None, "999")


class OneTimeTests(LedgerCase):
    """A one-time expected transaction: a $1,240 tax refund due Oct 20 (a Tuesday), with last spring's refund in history."""

    def setUp(self):
        super().setUp()
        from runway.server.api import recurring as api
        self.api = api
        self.acct("chk", "checking", 3000.0)
        self.tx("chk", "2026-05-01", 980.0, "IRS TREAS 310 TAX REF")
        self.body = {"name": "Tax refund", "account_id": "chk", "amount": 1240, "frequency": "once",
                     "anchor_date": "2026-10-20", "match": "irs treas"}
        self.rid = self.api.api_recurring_add(self.conn, None, self.body)["id"]

    def item(self):
        return dict(self.conn.execute(select(Recurring).where(Recurring.id == self.rid)).fetchone())

    def events(self, today, days=400):
        return [(e["date"], e["amount"], e.get("late_from")) for e in forecast.build(self.conn, today, days)["events"]
                if e.get("recurring_id") == self.rid]

    def test_it_is_forecast_once_on_its_date(self):
        self.assertEqual(self.events(TODAY), [("2026-10-20", 1240, None)])
        self.assertEqual(forecast.occurrences(self.item(), TODAY, date(2028, 1, 1)), [date(2026, 10, 20)])
        # On a Saturday, money in comes the business day before.
        self.assertEqual(forecast.occurrences({**self.item(), "anchor_date": "2026-10-24"}, TODAY, date(2028, 1, 1)),
                         [date(2026, 10, 23)])

    def test_only_a_payment_near_its_date_matches_it(self):
        self.assertEqual(recurring.matched(self.conn, self.rid), [])   # not last spring's refund
        self.tx("chk", "2026-10-16", 1240.0, "IRS TREAS 310 TAX REF")
        self.tx("chk", "2027-04-20", 300.0, "IRS TREAS 310 TAX REF")
        recurring.auto_match(self.conn)
        self.assertEqual([t["posted"] for t in recurring.matched(self.conn, self.rid)], ["2026-10-16"])
        self.assertEqual(self.events(date(2026, 10, 16)), [])   # it came: nothing more is expected
        self.assertEqual(recurring.missed(self.conn, date(2026, 11, 10)), [])

    def test_its_window_is_around_the_day_its_money_moves(self):
        # A $2,500 bill due Saturday Oct 24 is paid Monday Oct 26: a payment five days after that still matches it.
        bill = self.api.api_recurring_add(self.conn, None, {**self.body, "name": "Tax bill", "amount": -2500,
                                                            "anchor_date": "2026-10-24", "match": "irs usataxpymt"})["id"]
        self.tx("chk", "2026-10-31", -2500.0, "IRS USATAXPYMT")
        recurring.auto_match(self.conn)
        self.assertEqual([t["posted"] for t in recurring.matched(self.conn, bill)], ["2026-10-31"])

    def test_late_then_missed_if_it_never_comes(self):
        self.assertEqual(self.events(date(2026, 10, 25)), [("2026-10-25", 1240, "2026-10-20")])
        self.assertEqual(self.events(date(2026, 11, 2)), [])   # its window has closed
        self.assertEqual([m["date"] for m in recurring.missed(self.conn, date(2026, 11, 10))], ["2026-10-20"])

    def test_changing_its_amount_on_overview_changes_the_item(self):
        from runway.server.api.state import api_override_set
        key = f"rec:{self.rid}:2026-10-20"
        self.conn.execute(insert(Override).values(key=key, amount=1000.0))   # an edit from before this
        before = self.item()
        r = api_override_set(self.conn, None, {"key": key, "amount": 1310.5})
        self.assertEqual(r["item"], {"id": self.rid, "name": "Tax refund"})
        self.assertEqual(self.item()["amount"], 1310.5)   # Recurring shows it
        self.assertIsNone(self.conn.execute(select(Override.key)).scalar())   # no per-date edit (and nothing to reset)
        e = next(e for e in forecast.build(self.conn, TODAY, 60)["events"] if e.get("recurring_id") == self.rid)
        self.assertEqual((e["amount"], e.get("overridden")), (1310.5, None))
        self.api.api_recurring_amount(self.conn, None, {"restore": r["previous"]}, str(self.rid))   # Undo
        self.assertEqual(self.item(), before)
        # A repeating item's date still gets an edit of its own.
        rent = self.api.api_recurring_add(self.conn, None, {**self.body, "name": "Rent", "amount": -1500, "frequency": "monthly",
                                                            "match": "rent"})["id"]
        self.assertEqual(api_override_set(self.conn, None, {"key": f"rec:{rent}:2026-11-20", "amount": -1600}), {"ok": True})
        self.assertEqual(self.conn.execute(select(Override.amount)).scalar(), -1600)

    def test_moving_its_date_lets_go_of_what_matched_far_from_the_new_one(self):
        self.tx("chk", "2026-10-16", 1240.0, "IRS TREAS 310 TAX REF")
        recurring.auto_match(self.conn)
        self.api.api_recurring_update(self.conn, None, {**self.body, "anchor_date": "2027-03-01"}, self.rid)
        self.assertEqual(recurring.matched(self.conn, self.rid), [])
        # Linked by hand, it stays.
        recurring.link(self.conn, "chk|1", self.rid)
        self.api.api_recurring_update(self.conn, None, {**self.body, "anchor_date": "2027-04-01"}, self.rid)
        self.assertEqual(len(recurring.matched(self.conn, self.rid)), 1)

    def test_a_big_payment_far_from_its_date_is_still_flagged_as_unscheduled(self):
        self.tx("chk", "2026-08-03", -2500.0, "IRS USATAXPYMT")
        self.conn.execute(insert(Recurring).values(name="Tax bill", account_id="chk", amount=-2500, frequency="once",
                                                   anchor_date="2027-04-15", match="irs usataxpymt", amount_mode="fixed"))
        recurring.auto_match(self.conn)
        items = db.rows(self.conn.execute(select(Recurring)))
        self.assertEqual([t["amount"] for t in forecast.large_one_offs(self.conn, ["chk"], TODAY, items)], [-2500.0])
        self.assertEqual(forecast.large_one_offs(self.conn, ["chk"], TODAY, [{**i, "anchor_date": "2026-08-05"} for i in items]), [])

    def test_it_doesnt_hide_a_suggestion_for_a_payee_that_repeats(self):
        for d in ["2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"]:
            self.tx("chk", d, -80.0, "CITY WATER")
        self.api.api_recurring_add(self.conn, None, {**self.body, "name": "Water deposit back", "amount": 150,
                                                     "anchor_date": "2027-01-15", "match": "city water"})
        self.assertIn("city water", {s["match"] for s in forecast.suggest_recurring(self.conn, TODAY)})


if __name__ == "__main__":
    unittest.main()
