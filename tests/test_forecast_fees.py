"""The forecast's estimates, and annual fees."""
from datetime import date

from sqlalchemy import insert, update

from runway.domain import churning, forecast
from runway.storage.models import Account, Budget, Category, ChurnCard, Override, Recurring
from tests import forecast_support as fs
from tests.shared import TODAY, LedgerCase


class EstimatePartsTests(LedgerCase):
    """What an estimated statement is made of (forecast.estimate_parts), on ForecastTests' card: its statements close the
    10th and are due the 5th; $300 charged since the Sep 10 close."""
    pay = fs.pay

    def setUp(self):
        super().setUp()
        fs.card_setup(self)

    def estimates(self, days=90, card="cc"):
        """{payment date: estimate} for a card's estimated statements, each checked to add up to the cent."""
        out = {}
        for e in forecast.build(self.conn, TODAY, days)["events"]:
            if e["kind"] != "card" or not e["estimated"] or e["card_id"] != card:
                continue
            est = e["estimate"]
            cents = lambda v: round(v * 100)
            parts = [est.get(k, 0.0) for k in ("charged_so_far", "owed_now", "budgets_total", "recurring_total", "fees_total",
                                                "carried", "interest")]
            self.assertEqual(sum(map(cents, parts)), cents(est["statement"]), est)
            for name in ("budgets", "recurring", "fees"):
                self.assertEqual(name in est, f"{name}_total" in est, est)
                if name in est:
                    self.assertEqual(sum(cents(i["amount"]) for i in est[name]), cents(est[f"{name}_total"]), est)
            self.assertEqual(est["total"], -e["amount"])
            out[e["date"]] = est
        return out

    def test_whats_charged_and_the_budgets_on_the_card(self):
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        self.conn.execute(insert(Budget).values(category="Restaurants", amount=93))
        self.conn.execute(update(Category).where(Category.name == "Restaurants").values(pay_with="cc"))
        est = self.estimates()
        oct10 = est["2026-11-05"]
        self.assertEqual((oct10["close"], oct10["due"], oct10["charged_so_far"]), ("2026-10-10", "2026-11-05", 300.0))
        self.assertEqual([b["category"] for b in oct10["budgets"]], ["Groceries", "Restaurants"])
        self.assertAlmostEqual(oct10["budgets"][0]["amount"], 300 + 500 / 31 * 10, delta=0.01)
        self.assertAlmostEqual(oct10["budgets"][1]["amount"], 93 / 31 * 10, delta=0.01)
        self.assertAlmostEqual(oct10["total"], 300 + 300 + 593 / 31 * 10, delta=0.01)
        for k in ("recurring", "fees", "carried", "interest", "pay_mode", "owed_now", "assumed_cycle"):
            self.assertNotIn(k, oct10)
        nov10 = est["2026-12-07"]
        self.assertNotIn("charged_so_far", nov10)
        self.assertAlmostEqual(nov10["budgets_total"], 593 / 31 * 21 + 593 / 30 * 10, delta=0.01)

    def test_recurring_charges_and_an_annual_fee(self):
        self.conn.execute(insert(Recurring).values(name="Insurance", account_id="cc", amount=-600, frequency="yearly",
                                                   anchor_date="2025-10-20", match="insurer"))
        self.conn.execute(insert(Recurring).values(name="Streaming", account_id="cc", amount=-15, frequency="monthly",
                                                   anchor_date="2026-09-30"))
        AnnualFeeTests.churn(self, opened="2023-11-02", account_id="cc")
        nov10 = self.estimates(days=120)["2026-12-07"]
        self.assertEqual(nov10["recurring"], [{"name": "Insurance", "amount": 600.0}, {"name": "Streaming", "amount": 15.0}])
        self.assertEqual(nov10["fees"], [{"name": "Sapphire annual fee", "amount": 95.0}])
        self.assertEqual(nov10["total"], 710.0)

    def test_paying_the_minimum_with_interest(self):
        self.conn.execute(insert(Recurring).values(name="Insurance", account_id="cc", amount=-450, frequency="monthly",
                                                   anchor_date="2026-09-25"))
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum", apr="24")
        oct10 = self.estimates()["2026-11-05"]
        i1 = (550 + 750 / 2) * 0.02
        self.assertEqual((oct10["charged_so_far"], oct10["recurring_total"]), (300.0, 450.0))
        self.assertEqual((oct10["carried"], oct10["interest"], oct10["apr"]), (550.0, round(i1, 2), 24.0))
        self.assertEqual(oct10["statement"], round(550 + i1 + 750, 2))
        self.assertEqual((oct10["total"], oct10["pay_mode"]), (fs.minimum(oct10["statement"], i1), "minimum"))

    def test_a_card_with_no_statement_yet(self):
        self.acct("cc3", "credit", -40.0, pay_from="chk")
        self.conn.execute(insert(Budget).values(category="Travel", amount=310))
        self.conn.execute(update(Category).where(Category.name == "Travel").values(pay_with="cc3"))
        est = self.estimates(card="cc3")
        sep = est["2026-10-26"]
        self.assertEqual((sep["assumed_cycle"], sep["close"], sep["due"], sep["owed_now"]), (True, "2026-09-30", "2026-10-25", 40.0))
        self.assertEqual((sep["budgets"], sep["total"]), ([{"category": "Travel", "amount": 310.0}], 350.0))
        self.assertNotIn("charged_so_far", sep)
        oct_ = est["2026-11-25"]
        self.assertEqual((oct_["close"], oct_["total"]), ("2026-10-31", 310.0))
        self.assertNotIn("owed_now", oct_)

    def test_an_amount_you_set_is_not_an_estimate(self):
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-10-10", amount=-123.0))
        fc = forecast.build(self.conn, TODAY, 90)
        e = next(e for e in fc["events"] if e.get("key") == "cardclose:cc:2026-10-10")
        self.assertEqual((e["amount"], e["overridden"], e["estimated"]), (-123.0, True, False))
        self.assertNotIn("estimate", e)
        nxt = next(e for e in fc["events"] if e.get("key") == "cardclose:cc:2026-11-10")
        self.assertTrue(nxt["estimated"] and nxt["estimate"])

    def test_an_amount_you_set_on_a_learned_recurring_amount_is_not_an_estimate(self):
        rid = self.conn.execute(insert(Recurring).values(name="Power", account_id="chk", amount=-120, frequency="monthly",
                                                         anchor_date="2026-08-15", match="power co", amount_mode="avg3")).lastrowid
        events = lambda: {e["key"]: e for e in forecast.build(self.conn, TODAY, 60)["events"] if e.get("recurring_id") == rid}
        self.assertTrue(events()[f"rec:{rid}:2026-10-15"]["estimated"])
        self.conn.execute(insert(Override).values(key=f"rec:{rid}:2026-10-15", amount=-140.0))
        got = events()
        self.assertEqual((got[f"rec:{rid}:2026-10-15"]["amount"], got[f"rec:{rid}:2026-10-15"]["estimated"]), (-140.0, False))
        self.assertGreater(len(got), 1)
        self.assertTrue(all(e["estimated"] for k, e in got.items() if k != f"rec:{rid}:2026-10-15"))

    def test_to_cents(self):
        self.assertEqual(forecast.to_cents([1 / 3, 1 / 3, 1 / 3], 1.0), [0.34, 0.33, 0.33])
        self.assertEqual(forecast.to_cents([0.005, 0.005], 0.0), [0.0, 0.0])
        self.assertEqual(forecast.to_cents([10.004, 5.003, -2.0], 13.01), [10.01, 5.0, -2.0])
        self.assertEqual(forecast.to_cents([], 0.0), [])


class AnnualFeeTests(LedgerCase):
    """Churning cards' annual fees, on ForecastTests' card (its statements close the 10th and are due the 5th)."""

    def setUp(self):
        super().setUp()
        fs.card_setup(self)

    def churn(self, opened="2024-10-10", product="Sapphire", annual_fee=95.0, **kw):
        """A churning card; by default opened on the 10th, the fixture card's closing day, so its fee is on the close."""
        self.conn.execute(insert(ChurnCard).values(owner="Alex", issuer="chase", product=product, opened_on=opened,
                                                   annual_fee=annual_fee, **kw))

    def payments(self, fc):
        return {e["date"]: e["amount"] for e in fc["events"] if e["kind"] == "card"}

    def assert_paid_with_their_statements(self, fc):
        """Each fee's paid_on is the payment of its card's statement whose estimate has it: each statement's fees are
        the ones paid on its date, and every fee paid on a date is on a statement."""
        statements = [e for e in fc["events"] if e["kind"] == "card" and e.get("estimate")]
        for e in statements:
            paid = [{"name": f["name"], "amount": -f["amount"]} for f in fc["fees"]
                    if f["account_id"] == e["card_id"] and f["paid_on"] == e["date"]]
            self.assertEqual(e["estimate"].get("fees", []), paid, e)
        for f in fc["fees"]:
            if f["paid_on"]:
                self.assertIn((f["account_id"], f["paid_on"]), [(e["card_id"], e["date"]) for e in statements], f)

    def fees_budget(self, amount=150, pay_with="chk"):
        """A budget for Fees & Interest, paid from checking unless said otherwise, and Groceries ($500) on the card."""
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        self.conn.execute(insert(Budget).values(category="Fees & Interest", amount=amount))
        self.conn.execute(update(Category).where(Category.name == "Fees & Interest").values(pay_with=pay_with))

    def test_a_fee_on_the_close_date_is_on_that_statement_with_a_fees_budget_paid_from_checking(self):
        self.fees_budget()
        before = forecast.build(self.conn, TODAY, 90)
        self.churn(account_id="cc")
        fc = forecast.build(self.conn, TODAY, 90)
        nov5 = next(e for e in fc["events"] if e["date"] == "2026-11-05" and e.get("card_id") == "cc")
        est = nov5["estimate"]
        groceries = 300 + 500 / 31 * 10
        self.assertEqual((est["close"], est["charged_so_far"], est["fees"]), ("2026-10-10", 300.0, [{"name": "Sapphire annual fee", "amount": 95.0}]))
        self.assertEqual([b["category"] for b in est["budgets"]], ["Groceries"])
        self.assertAlmostEqual(est["budgets_total"], groceries, delta=0.005)
        self.assertEqual(est["total"], round(300 + groceries + 95, 2))
        self.assertEqual(-nov5["amount"], est["total"])
        self.assertEqual(EstimatePartsTests.estimates(self)["2026-11-05"], est)
        self.assertAlmostEqual(fs.drop(self, fc, "2026-11-05") - 150 / 30, est["total"], delta=0.011)
        self.assertAlmostEqual(fs.drop(self, before, "2026-10-20"), 150 / 31, delta=0.01)
        self.assertAlmostEqual(fs.drop(self, fc, "2026-10-20"), 55 / 31, delta=0.01)
        october = [v for d, v in fc["spend"].items() if d.startswith("2026-10")]
        self.assertAlmostEqual(sum(october), 55 * 30 / 31, delta=0.005 * len(october))
        self.assertAlmostEqual(fc["total"][-1], before["total"][-1], delta=0.011)
        self.assertEqual((fc["fees"][0]["paid_on"], fc["fees"][0]["paid_from"]), ("2026-11-05", "chk"))
        self.assert_paid_with_their_statements(fc)

    def test_a_fee_the_day_after_the_close_is_on_the_next_statement_with_nothing_else_on_it(self):
        self.fees_budget()
        self.acct("cc2", "credit", 0.0, pay_from="chk")
        self.stmt("cc2", 0.0, "2026-09-10", "2026-10-05")
        cc2 = lambda fc: [(e["date"], e["amount"], e["estimate"]["close"]) for e in fc["events"] if e.get("card_id") == "cc2"]
        self.assertEqual(cc2(forecast.build(self.conn, TODAY, 90)), [])
        self.churn(opened="2024-10-11", account_id="cc2")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual([(f["date"], f["paid_on"]) for f in fc["fees"]], [("2026-10-11", "2026-12-07")])
        self.assertEqual(cc2(fc), [("2026-12-07", -95.0, "2026-11-10")])
        self.assertEqual(next(e for e in fc["events"] if e.get("card_id") == "cc2")["estimate"]["fees"],
                         [{"name": "Sapphire annual fee", "amount": 95.0}])
        self.assert_paid_with_their_statements(fc)
        self.conn.execute(update(ChurnCard).values(opened_on="2024-10-10"))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(cc2(fc), [("2026-11-05", -95.0, "2026-10-10")])
        self.assert_paid_with_their_statements(fc)
        self.stmt("cc2", 0.0, "2026-10-10", "2026-11-05")
        fc = forecast.build(self.conn, date(2026, 10, 11), 90)
        self.assertEqual([(f["date"], f["late_from"], f["paid_on"]) for f in fc["fees"]], [("2026-10-11", "2026-10-10", "2026-12-07")])
        self.assertEqual(cc2(fc), [("2026-12-07", -95.0, "2026-11-10")])
        self.assert_paid_with_their_statements(fc)

    def test_a_fee_after_the_closing_day_is_dated_on_the_anniversary_as_churning_dates_it(self):
        self.acct("cc2", "credit", 0.0, pay_from="chk")
        self.stmt("cc2", 0.0, "2026-09-20", "2026-10-15")
        self.churn(opened="2024-10-21", product="Venture X", annual_fee=395.0, account_id="cc2")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual([(f["date"], f["paid_on"]) for f in fc["fees"]], [("2026-10-21", "2026-12-15")])
        cc2 = [e for e in fc["events"] if e.get("card_id") == "cc2"]
        self.assertEqual([(e["date"], e["amount"], e["estimate"]["close"], e["estimate"]["fees"]) for e in cc2],
                         [("2026-12-15", -395.0, "2026-11-20", [{"name": "Venture X annual fee", "amount": 395.0}])])
        self.assert_paid_with_their_statements(fc)
        s = churning.state(self.conn, TODAY)
        card = next(c for c in s["cards"] if c["product"] == "Venture X")
        self.assertEqual(card["fee_due"], "2026-10-21")
        self.assertIn("2026-10-21", [u["date"] for u in churning.upcoming(s, TODAY) if u["kind"] == "fee"])

    def test_a_fee_that_posts_takes_the_place_of_the_projected_one(self):
        self.fees_budget()
        self.churn(account_id="cc")
        today = date(2026, 10, 10)
        before = forecast.build(self.conn, today, 60)
        self.assertEqual([f["date"] for f in before["fees"]], ["2026-10-10"])
        self.tx("cc", "2026-10-10", -95.0, "SAPPHIRE RENEWAL", "Fees & Interest")
        after = forecast.build(self.conn, today, 60)
        self.assertEqual(after["fees"], [])
        was, now = (next(e for e in fc["events"] if e.get("key") == "cardclose:cc:2026-10-10") for fc in (before, after))
        self.assertEqual((was["estimate"]["charged_so_far"], was["estimate"]["fees_total"]), (300.0, 95.0))
        self.assertEqual(now["estimate"]["charged_so_far"], 395.0)
        self.assertNotIn("fees", now["estimate"])
        self.assertEqual((now["amount"], now["date"]), (was["amount"], was["date"]))
        self.assertEqual((after["total"], after["spend"]), (before["total"], before["spend"]))

    def test_a_fee_charged_under_another_name_counts_as_charged(self):
        self.churn(account_id="cc")
        listed = lambda: [f["name"] for f in forecast.build(self.conn, TODAY, 90)["fees"]]
        self.tx("cc", "2026-09-14", -12.34, "INTEREST CHARGE", "Fees & Interest")
        self.tx("cc", "2026-09-15", -95.0, "OUTDOOR STORE", "Shopping")
        self.tx("cc", "2025-10-10", -95.0, "CARD RENEWAL", "Fees & Interest")
        self.tx("chk", "2026-09-16", -95.0, "CARD RENEWAL", "Fees & Interest")
        self.assertEqual(listed(), ["Sapphire annual fee"])
        self.assertFalse(forecast.fee_posted(self.conn, "cc", date(2026, 10, 10), 95.0))
        self.tx("cc", "2026-09-17", -95.0, "CARD RENEWAL", "Fees & Interest", pending=1)
        self.assertEqual(listed(), [])
        self.assertTrue(forecast.fee_posted(self.conn, "cc", date(2026, 10, 10), 95.0))
        self.assertFalse(forecast.fee_posted(self.conn, "cc", date(2026, 10, 10), 550.0))
        self.assertFalse(forecast.fee_posted(self.conn, "cc", date(2026, 10, 10)))

    def test_each_fee_is_paid_on_the_statement_that_has_it(self):
        self.fees_budget()
        self.acct("cc3", "credit", 0.0, pay_from="chk")
        self.churn(account_id="cc")
        self.churn(opened="2024-12-03", product="Gold", annual_fee=250.0, account_id="cc3")
        fc = forecast.build(self.conn, TODAY, 420)
        self.assertEqual([(f["name"], f["date"], f["paid_on"]) for f in fc["fees"]],
                         [("Sapphire annual fee", "2026-10-10", "2026-11-05"), ("Gold annual fee", "2026-12-03", "2027-01-25"),
                          ("Sapphire annual fee", "2027-10-10", "2027-11-05")])
        self.assert_paid_with_their_statements(fc)

    def test_fee_is_a_charge_on_the_card_paid_with_its_statement(self):
        before = forecast.build(self.conn, TODAY, 90)
        self.churn(account_id="cc")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(fc["fees"], [{
            "date": "2026-10-10", "amount": -95.0, "kind": "fee", "churn_card_id": 1, "name": "Sapphire annual fee",
            "account_id": "cc", "account": "cc", "category": "Fees & Interest", "paid_on": "2026-11-05", "paid_from": "chk"}])
        self.assertNotIn("fee", [e["kind"] for e in fc["events"]])
        was, now = self.payments(before), self.payments(fc)
        self.assertAlmostEqual(now["2026-11-05"] - was["2026-11-05"], -95.0, places=2)
        self.assertEqual({d: a for d, a in now.items() if d != "2026-11-05"}, {d: a for d, a in was.items() if d != "2026-11-05"})
        i = fc["dates"].index("2026-11-05")
        self.assertAlmostEqual(fc["total"][i] - before["total"][i], -95.0, places=2)
        self.assertEqual(fc["total"][i - 1], before["total"][i - 1])
        card = next(c for c in fc["cards"] if c["id"] == "cc")
        self.assertEqual(card["annual_fees"], [{"date": "2026-10-10", "amount": -95.0, "category": "Fees & Interest"}])
        self.assert_paid_with_their_statements(fc)

    def test_a_product_change_keeps_the_accounts_anniversary(self):
        self.churn(opened="2023-10-20", product="Reserve", annual_fee=0.0, status="product_changed")
        self.churn(opened="2026-07-01", product="Preferred", account_id="cc", changed_from=1)
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual([(f["name"], f["date"], f["paid_on"]) for f in fc["fees"]], [("Preferred annual fee", "2026-10-20", "2026-12-07")])
        card = next(c for c in churning.load(self.conn)["cards"] if c["product"] == "Preferred")
        self.assertEqual(churning.next_fee(card, TODAY), date(2026, 10, 20))

    def test_fee_after_the_close_is_on_the_next_statement(self):
        before = self.payments(forecast.build(self.conn, TODAY, 90))
        self.assertNotIn("2026-12-07", before)
        self.churn(opened="2023-10-15", account_id="cc")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual((fc["fees"][0]["date"], fc["fees"][0]["paid_on"]), ("2026-10-15", "2026-12-07"))
        self.assertEqual(self.payments(fc), {**before, "2026-12-07": -95.0})
        self.assert_paid_with_their_statements(fc)

    def test_fee_past_the_horizon_of_its_payment_is_only_listed(self):
        before = forecast.build(self.conn, TODAY, 80)
        self.churn(opened="2022-12-10", account_id="cc")
        fc = forecast.build(self.conn, TODAY, 80)
        self.assertEqual((fc["fees"][0]["date"], fc["fees"][0]["paid_on"]), ("2026-12-10", None))
        self.assertEqual(fc["total"], before["total"])

    def test_no_fee_for_closed_planned_free_or_charged_cards(self):
        self.churn(status="closed", closed_on="2026-01-05", account_id="cc")
        self.churn(status="product_changed", closed_on="2026-01-05", account_id="cc")
        self.churn(closed_on="2026-10-01", account_id="cc")
        self.churn(plan="close", account_id="cc")
        self.churn(plan="product_change", plan_date="2026-10-01", account_id="cc")
        self.churn(annual_fee=0.0, account_id="cc")
        self.churn(annual_fee=None, account_id="cc")
        self.churn(opened="2026-01-15", account_id="cc")
        before = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(before["fees"], [])
        self.churn(plan="close", plan_date="2026-10-20", account_id="cc")
        self.churn(plan="keep", account_id="cc")
        self.assertEqual(len(forecast.build(self.conn, TODAY, 90)["fees"]), 2)

    def test_no_fee_once_charged(self):
        self.churn(account_id="cc")
        self.tx("cc", "2025-10-10", -95.0, "ANNUAL FEE", "Fees & Interest")
        self.assertEqual(len(forecast.build(self.conn, TODAY, 90)["fees"]), 1)
        self.tx("cc", "2026-09-21", 95.0, "ANNUAL FEE REFUND", "Refunds")
        self.assertEqual(len(forecast.build(self.conn, TODAY, 90)["fees"]), 1)
        self.tx("cc", "2026-09-22", -95.0, "ANNUAL MEMBERSHIP FEE", "Fees & Interest", pending=1)
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(fc["fees"], [])
        card = next(c for c in fc["cards"] if c["id"] == "cc")
        self.assertEqual(card["annual_fees"], [])

    def test_what_reads_as_an_annual_fee(self):
        for text in ("ANNUAL FEE", "Annual Membership Fee", "MEMBERSHIP FEE", "annual  fee"):
            self.assertTrue(forecast.FEE_TEXT.search(text), text)
        for text in ("LATE FEE", "FOREIGN TRANSACTION FEE", "ANNUAL PERCENTAGE RATE"):
            self.assertFalse(forecast.FEE_TEXT.search(text), text)

    def test_late_fee_comes_today(self):
        self.churn(account_id="cc")
        self.churn(opened="2024-10-02", product="Unlinked")
        fees = forecast.build(self.conn, date(2026, 10, 15), 60)["fees"]
        self.assertEqual([(f["name"], f["date"], f["late_from"]) for f in fees], [("Sapphire annual fee", "2026-10-15", "2026-10-10")])
        self.tx("cc", "2026-10-12", -95.0, "ANNUAL FEE")
        self.assertEqual(forecast.build(self.conn, date(2026, 10, 15), 60)["fees"], [])
        self.assertEqual(forecast.build(self.conn, date(2026, 11, 1), 60)["fees"], [])

    def test_a_recurring_item_for_the_fee_is_not_doubled(self):
        self.churn(account_id="cc")
        self.conn.execute(insert(Recurring).values(name="Sapphire fee", account_id="cc", amount=-95, frequency="yearly",
                                                   anchor_date="2025-10-12", active=1))
        self.assertEqual(forecast.build(self.conn, TODAY, 90)["fees"], [])
        self.conn.execute(update(Recurring).values(name="Sapphire travel credit"))
        self.assertEqual(len(forecast.build(self.conn, TODAY, 90)["fees"]), 1)
        self.conn.execute(update(Recurring).values(name="Sapphire fee", anchor_date="2026-03-12"))
        self.assertEqual(len(forecast.build(self.conn, TODAY, 90)["fees"]), 1)

    def test_unlinked_card_is_listed_without_touching_cash(self):
        before = forecast.build(self.conn, TODAY, 90)
        self.churn(opened="2020-11-30", product="Gold")
        self.acct("hidden", "credit", -10.0, hidden=1)
        self.churn(opened="2021-12-05", product="Hidden", account_id="hidden")
        self.acct("sav", "savings", 100.0)
        self.churn(opened="2021-11-03", product="Odd", account_id="sav")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual([(f["name"], f["date"], f["account_id"], f["paid_on"]) for f in fc["fees"]],
                         [("Odd annual fee", "2026-11-03", None, None), ("Gold annual fee", "2026-11-30", None, None),
                          ("Hidden annual fee", "2026-12-05", None, None)])
        self.assertEqual((fc["total"], self.payments(fc)), (before["total"], self.payments(before)))

    def test_a_card_without_a_statement_is_listed_on_its_anniversary(self):
        self.acct("cc2", "credit", -50.0, pay_from="chk")
        self.churn(opened="2019-11-17", account_id="cc2")
        fees = forecast.build(self.conn, TODAY, 90)["fees"]
        self.assertEqual([(f["date"], f["account_id"], f["paid_on"]) for f in fees], [("2026-11-17", "cc2", None)])

    def test_short_months_and_leap_days(self):
        card = {"id": 1, "product": "Plat", "annual_fee": 695.0, "opened_on": "2024-02-29", "status": "open"}
        on = lambda today, end: [f["date"] for f in forecast.annual_fees(self.conn, card, today, end, [])]
        self.assertEqual(on(date(2027, 1, 15), date(2027, 3, 31)), ["2027-02-28"])
        self.assertEqual(on(date(2027, 3, 1), date(2028, 3, 31)), ["2028-02-29"])
        self.assertEqual(on(date(2024, 3, 1), date(2025, 1, 31)), [])
        card.update(opened_on="2023-10-31", fee_month=3)
        self.assertEqual(on(date(2026, 9, 23), date(2027, 11, 30)), ["2026-10-31", "2027-10-31"])
        self.assertEqual(on(date(2026, 11, 1), date(2027, 9, 30)), [])

    def test_a_budget_for_fees_has_the_fee_only_when_its_charged_to_the_card(self):
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        before = self.payments(forecast.build(self.conn, TODAY, 90))
        self.churn(account_id="cc")
        self.assertAlmostEqual(self.payments(forecast.build(self.conn, TODAY, 90))["2026-11-05"] - before["2026-11-05"], -95.0, places=2)
        self.conn.execute(insert(Budget).values(category="Fees & Interest", amount=10))
        self.conn.execute(update(Category).where(Category.name == "Fees & Interest").values(pay_with="cc"))
        with_fees = self.payments(forecast.build(self.conn, TODAY, 90))
        self.churn(product="Second", account_id="cc")
        again = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(len(again["fees"]), 2)
        self.assertEqual(self.payments(again), with_fees)
        est = next(e["estimate"] for e in again["events"] if e["date"] == "2026-11-05")
        self.assertNotIn("fees", est)
        self.conn.execute(update(Category).where(Category.name == "Fees & Interest").values(pay_with="chk"))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertAlmostEqual(self.payments(fc)["2026-11-05"], before["2026-11-05"] - 190.0, places=2)
        self.assertEqual(fs.drop(self, fc, "2026-10-20"), 0.0)
        self.assert_paid_with_their_statements(fc)

    def test_the_fee_of_a_card_with_no_statement_is_in_its_assumed_statement(self):
        self.acct("cc3", "credit", 0.0, pay_from="chk")
        self.conn.execute(insert(Budget).values(category="Travel", amount=310))
        self.conn.execute(update(Category).where(Category.name == "Travel").values(pay_with="cc3"))
        self.churn(account_id="cc3")
        fc = forecast.build(self.conn, TODAY, 90)
        card = {e["date"]: e["amount"] for e in fc["events"] if e.get("card_id") == "cc3"}
        self.assertEqual(card, {"2026-10-26": -310.0, "2026-11-25": -405.0})
        self.assertNotIn("fee", [e["kind"] for e in fc["events"]])
        self.assertEqual((fc["fees"][0]["paid_on"], fc["fees"][0]["paid_from"]), ("2026-11-25", "chk"))
        self.conn.execute(insert(Budget).values(category="Fees & Interest", amount=0.01))
        self.conn.execute(update(Category).where(Category.name == "Fees & Interest").values(pay_with="cc3"))
        card = {e["date"]: e["amount"] for e in forecast.build(self.conn, TODAY, 90)["events"] if e.get("card_id") == "cc3"}
        self.assertAlmostEqual(card["2026-11-25"], -310.01, places=2)

    def test_an_overdue_fee_of_a_card_with_no_statement_is_on_its_first_statement(self):
        self.acct("cc3", "credit", 0.0, pay_from="chk")
        self.conn.execute(insert(Budget).values(category="Travel", amount=310))
        self.conn.execute(update(Category).where(Category.name == "Travel").values(pay_with="cc3"))
        self.churn(opened="2024-09-10", account_id="cc3")
        card = {e["date"]: e["amount"] for e in forecast.build(self.conn, TODAY, 90)["events"] if e.get("card_id") == "cc3"}
        self.assertEqual(card["2026-10-26"], -405.0)

    def test_a_card_with_no_statement_and_only_a_fee_has_the_statement_that_pays_it(self):
        self.acct("cc3", "credit", -40.0, pay_from="chk")
        self.conn.execute(insert(Budget).values(category="Fees & Interest", amount=150))
        self.conn.execute(update(Category).where(Category.name == "Fees & Interest").values(pay_with="chk"))
        self.churn(account_id="cc3")
        fc = forecast.build(self.conn, TODAY, 90)
        card = {e["date"]: e for e in fc["events"] if e.get("card_id") == "cc3"}
        self.assertEqual({d: e["amount"] for d, e in card.items()}, {"2026-10-26": -40.0, "2026-11-25": -95.0})
        self.assertEqual((card["2026-11-25"]["estimate"]["fees"], card["2026-11-25"]["assumed_cycle"]),
                         ([{"name": "Sapphire annual fee", "amount": 95.0}], True))
        self.assertEqual([(f["account_id"], f["paid_on"], f["paid_from"]) for f in fc["fees"]], [("cc3", "2026-11-25", "chk")])
        self.assert_paid_with_their_statements(fc)
        self.assertAlmostEqual(fs.drop(self, fc, "2026-10-20"), (150 - 95) / 31, delta=0.01)
        self.conn.execute(update(Account).where(Account.id == "cc3").values(pay_from=None))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual([(f["account_id"], f["paid_on"]) for f in fc["fees"]], [("cc3", None)])
        self.assertFalse(any(e.get("card_id") == "cc3" for e in fc["events"]))
