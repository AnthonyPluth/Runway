"""The API handlers for the Overview, Accounts, Budget, Transactions and push notifications, and the sync's own
queries, called directly on the sample data (runway/demo.py) plus the cases each one handles."""
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from sqlalchemy import delete, func, insert, select, update

from runway import categories, db, demo, forecast, splits
from runway.server import sync
from runway.server.api import accounts, budget, notifications, state, transactions
from runway.server.common import ApiError
from runway.models import (Account, AiLog, Budget, CardStatement, Category, Holding, InvAccount, InvTransaction, LoanTerms,
                           ManualPosition, NotifyLog, Override, PlaidAccount, PlaidItem, Recurring, Security, SyncLog,
                           Transaction, User)
from tests.shared import TODAY, DbCase, freeze_today

# (date.today() is frozen at shared.TODAY in each test's setUp: the handlers read the clock too)


def q(**kw):
    return {k: [str(v)] for k, v in kw.items()}


class HandlerTests(DbCase):
    def setUp(self):
        super().setUp()
        freeze_today(self)
        demo.seed(self.c, TODAY)

    def one(self, stmt):
        return self.c.execute(stmt).fetchone()

    # ------------------------------------------------------------------------------------------ accounts

    def test_accounts_list(self):
        self.c.execute(insert(PlaidItem).values(item_id="it1", access_token="x", institution_name="Card Bank",
                                                products="transactions,liabilities"))
        self.c.execute(insert(PlaidAccount).values(plaid_account_id="pa1", item_id="it1", mask="1234"))
        self.c.execute(insert(CardStatement).values(plaid_account_id="pa1", item_id="it1",
                                                    last_statement_date="2026-09-01", next_due_date="2026-09-25"))
        self.c.execute(update(Account).where(Account.id == "demo-card")
                       .values(plaid_account_id="pa1", display_name="Zed Card"))
        self.c.execute(insert(Account).values(id="gone", name="Aardvark", kind="checking", hidden=1))
        out = accounts.api_accounts(self.c, {}, {})
        self.assertEqual([a["id"] for a in out], ["demo-checking", "demo-card", "demo-mortgage", "demo-savings", "gone"])
        cols = [c.name for c in db.schema.accounts.columns]
        self.assertEqual(list(out[0]), [*cols, "plaid_link"])
        self.assertIsNone(out[0]["plaid_link"])
        self.assertEqual(out[1]["plaid_link"], {"institution": "Card Bank", "mask": "1234", "transactions": True,
                                                "closed": "2026-09-01", "due": "2026-09-25", "statement_note": None})

    def test_account_update(self):
        with self.assertRaises(ApiError):
            accounts.api_account_update(self.c, {}, {"display_name": "x"}, "nope")
        with self.assertRaises(ApiError):
            accounts.api_account_update(self.c, {}, {"kind": "boat"}, "demo-card")
        accounts.api_account_update(self.c, {}, {"display_name": "  Daily  ", "hidden": "1", "owner": "", "name": "sneaky",
                                                 "in_forecast": 0}, "demo-checking")
        row = self.one(select(Account.name, Account.display_name, Account.hidden, Account.owner, Account.in_forecast)
                       .where(Account.id == "demo-checking"))
        self.assertEqual(tuple(row), ("Everyday Checking", "Daily", 1, None, 0))
        self.assertEqual(accounts.api_account_update(self.c, {}, {}, "demo-checking"), {"ok": True})
        # The account a card is paid from: one of yours (the database refuses another), or none.
        with self.assertRaisesRegex(ApiError, "Pick one of your accounts"):
            accounts.api_account_update(self.c, {}, {"pay_from": "nope"}, "demo-card")
        accounts.api_account_update(self.c, {}, {"pay_from": "demo-checking"}, "demo-card")
        self.assertEqual(self.one(select(Account.pay_from).where(Account.id == "demo-card"))[0], "demo-checking")
        accounts.api_account_update(self.c, {}, {"pay_from": ""}, "demo-card")
        self.assertIsNone(self.one(select(Account.pay_from).where(Account.id == "demo-card"))[0])

    def test_card_payment_plan(self):
        plan = lambda: {k: v for k, v in next(a for a in accounts.api_accounts(self.c, {}, {}) if a["id"] == "demo-card").items()
                        if k in ("pay_mode", "pay_amount", "apr")}
        self.assertEqual(plan(), {"pay_mode": "full", "pay_amount": None, "apr": None})
        self.assertNotIn("pay_mode", next(a for a in accounts.api_accounts(self.c, {}, {}) if a["id"] == "demo-checking"))
        accounts.api_account_update(self.c, {}, {"pay_mode": "fixed", "pay_amount": "312.50", "apr": 24.99}, "demo-card")
        self.assertEqual(plan(), {"pay_mode": "fixed", "pay_amount": 312.5, "apr": 24.99})
        # anything wrong is refused before anything is saved
        for bad in ({"pay_mode": "revolve"}, {"pay_amount": "lots"}, {"pay_amount": -5}, {"apr": "nan"}, {"apr": 150}):
            with self.assertRaises(ApiError):
                accounts.api_account_update(self.c, {}, {"display_name": "Changed", **bad}, "demo-card")
        self.assertIsNone(self.one(select(Account.display_name).where(Account.id == "demo-card"))[0])
        self.assertEqual(plan(), {"pay_mode": "fixed", "pay_amount": 312.5, "apr": 24.99})
        # back to paying in full, and blanks clear the amount and the APR
        accounts.api_account_update(self.c, {}, {"pay_mode": "full", "pay_amount": "", "apr": None}, "demo-card")
        self.assertEqual(plan(), {"pay_mode": "full", "pay_amount": None, "apr": None})
        accounts.api_account_update(self.c, {}, {"pay_mode": "minimum", "apr": "0"}, "demo-card")
        self.assertEqual(plan(), {"pay_mode": "minimum", "pay_amount": None, "apr": 0.0})
        # which is what the forecast reads
        from runway import forecast
        self.assertEqual(forecast.payment_plan(self.c, "demo-card", 24.99),
                         {"pay_mode": "minimum", "pay_amount": None, "apr": 0.0, "apr_source": "you"})
        # with no APR of yours, the list has the issuer's alongside (Settings shows it as the field's placeholder)
        self.c.execute(insert(PlaidItem).values(item_id="it1", access_token="x", institution_name="Card Bank", products="liabilities"))
        self.c.execute(insert(PlaidAccount).values(plaid_account_id="pa1", item_id="it1", mask="1234"))
        self.c.execute(insert(CardStatement).values(plaid_account_id="pa1", item_id="it1", last_statement_date="2026-09-01",
                                                    purchase_apr=24.99))
        self.c.execute(update(Account).where(Account.id == "demo-card").values(plaid_account_id="pa1"))
        accounts.api_account_update(self.c, {}, {"apr": ""}, "demo-card")
        card = next(a for a in accounts.api_accounts(self.c, {}, {}) if a["id"] == "demo-card")
        self.assertEqual((card["apr"], card["issuer_apr"]), (None, 24.99))

    def test_loan_terms(self):
        terms = lambda: tuple(self.one(select(Account.interest_rate, Account.monthly_payment).where(Account.id == "demo-mortgage")))
        accounts.api_account_update(self.c, {}, {"interest_rate": "6.25%", "monthly_payment": "$1,840.50"}, "demo-mortgage")
        self.assertEqual(terms(), (6.25, 1840.5))
        mtg = next(a for a in accounts.api_accounts(self.c, {}, {}) if a["id"] == "demo-mortgage")
        self.assertEqual({k: mtg["loan"][k] for k in ("rate", "payment", "source", "plaid", "set_rate", "set_payment")},
                         {"rate": 6.25, "payment": 1840.5, "source": "manual", "plaid": False, "set_rate": 6.25, "set_payment": 1840.5})
        self.assertNotIn("loan", next(a for a in accounts.api_accounts(self.c, {}, {}) if a["id"] == "demo-checking"))
        # Empty clears one (the payment is then worked out from recent payments); 0 is a rate.
        accounts.api_account_update(self.c, {}, {"interest_rate": "0", "monthly_payment": ""}, "demo-mortgage")
        self.assertEqual(terms(), (0, None))
        for bad in ({"interest_rate": "31"}, {"interest_rate": "-1"}, {"interest_rate": "lots"}, {"monthly_payment": "-5"},
                    {"interest_rate": "nan"}, {"interest_rate": "5", "display_name": "x", "monthly_payment": "-1"}):
            with self.assertRaises(ApiError):
                accounts.api_account_update(self.c, {}, bad, "demo-mortgage")
        self.assertEqual(terms(), (0, None))
        self.assertEqual(self.one(select(Account.display_name).where(Account.id == "demo-mortgage"))[0], None)   # nothing half-saved
        with self.assertRaisesRegex(ApiError, "Only a loan"):
            accounts.api_account_update(self.c, {}, {"interest_rate": "5"}, "demo-checking")
        # The lender's terms through Plaid aren't yours to change.
        self.c.execute(insert(LoanTerms).values(plaid_account_id="pm", item_id="it1", kind="mortgage", interest_rate=5.5,
                                                monthly_payment=2000))
        self.c.execute(update(Account).where(Account.id == "demo-mortgage").values(plaid_account_id="pm"))
        with self.assertRaisesRegex(ApiError, "interest rate comes from Plaid"):
            accounts.api_account_update(self.c, {}, {"interest_rate": "4"}, "demo-mortgage")
        with self.assertRaisesRegex(ApiError, "monthly payment comes from Plaid"):
            accounts.api_account_update(self.c, {}, {"monthly_payment": "100"}, "demo-mortgage")
        self.assertEqual(terms(), (0, None))
        mtg = next(a for a in accounts.api_accounts(self.c, {}, {}) if a["id"] == "demo-mortgage")
        self.assertEqual((mtg["loan"]["rate"], mtg["loan"]["payment"], mtg["loan"]["plaid"], mtg["loan"]["plaid_payment"]),
                         (5.5, 2000, True, True))
        accounts.api_account_update(self.c, {}, {"display_name": "Home loan"}, "demo-mortgage")   # the rest still saves
        # What Plaid leaves out (a new loan's payment, or a deferred student loan's $0) is yours to set, and used.
        self.c.execute(update(LoanTerms).where(LoanTerms.plaid_account_id == "pm").values(monthly_payment=0))
        accounts.api_account_update(self.c, {}, {"monthly_payment": "1,950"}, "demo-mortgage")
        self.assertEqual(terms(), (0, 1950))
        mtg = next(a for a in accounts.api_accounts(self.c, {}, {}) if a["id"] == "demo-mortgage")
        self.assertEqual((mtg["loan"]["rate"], mtg["loan"]["payment"], mtg["loan"]["source"], mtg["loan"]["plaid_payment"]),
                         (5.5, 1950, "manual", False))

    # ------------------------------------------------------------------------------------------ push

    def test_push_recent(self):
        for i in range(10):
            self.c.execute(insert(NotifyLog).values(key=f"k{i}", sent=float(i), title=f"t{i}"))
        out = notifications.api_push(self.c, {}, {})
        self.assertEqual(out["recent"], [{"title": f"t{i}", "sent": float(i)} for i in range(9, 1, -1)])
        self.assertEqual(out["devices"], [])

    # ------------------------------------------------------------------------------------------ state

    def test_state(self):
        self.c.execute(insert(Account).values(id="inv", name="Brokerage", kind="investment"))
        self.c.execute(insert(Transaction), [{"id": "inv|1", "account_id": "inv", "posted": "2026-01-01", "amount": -5,
                                              "needs_review": 1},
                                             {"id": "x|1", "account_id": "demo-card", "posted": "2026-01-01",
                                              "amount": -5, "needs_review": 1}])
        self.c.execute(insert(SyncLog).values(ok=0, message="Latest"))
        st = state.api_state(self.c, {}, {})
        self.assertEqual(st["review_count"], 1)
        self.assertEqual({k: st["last_log"][k] for k in ("ok", "message")}, {"ok": 0, "message": "Latest"})
        self.assertEqual(list(st["last_log"]), ["at", "ok", "message"])
        self.assertEqual(st["setup"], {"bank": True, "primary": True, "recurring": True, "budgets": True, "dismissed": False})

    def test_state_sync_times_carry_their_offset(self):
        # The setting is the server's local time, the log's `at` UTC: both go out with an offset, so the browser shows
        # its own time zone and not the server's.
        db.set_setting(self.c, "last_sync_ok", "2026-09-30T07:02:00")
        self.c.execute(insert(SyncLog).values(at="2026-09-30 12:02:00", ok=1, message="3 new transactions"))
        st = state.api_state(self.c, {}, {})
        self.assertEqual(datetime.fromisoformat(st["last_sync_ok"]), datetime(2026, 9, 30, 7, 2).astimezone())
        self.assertEqual(st["last_log"]["at"], "2026-09-30T12:02:00+00:00")
        self.assertEqual(st["sync_warnings"], [])
        self.assertEqual(state.with_offset("2026-09-30T12:02:00+00:00"), "2026-09-30T12:02:00+00:00")
        self.assertEqual(state.with_offset("2026-09-30T12:02:00-05:00", utc=True), "2026-09-30T12:02:00-05:00")
        self.assertIsNone(state.with_offset(None))
        self.assertEqual(state.with_offset("garbled"), "garbled")

    def test_owner_choices(self):
        self.c.execute(insert(User), [{"sub": "a", "first_name": "Zoe", "last_seen": 2},
                                      {"sub": "b", "first_name": "Adam", "last_seen": 1},
                                      {"sub": "c", "first_name": None, "last_seen": 0},
                                      {"sub": "d", "first_name": "Joint", "last_seen": 3}])
        self.c.execute(update(Account).where(Account.id == "demo-card").values(owner="Adam"))
        self.c.execute(update(Account).where(Account.id == "demo-savings").values(owner="Pat"))
        self.c.execute(update(Account).where(Account.id == "demo-checking").values(owner=""))
        self.assertEqual(state.owner_choices(self.c), ["Adam", "Zoe", "Pat"])

    def test_setup_steps(self):
        self.c.execute(delete(Recurring))
        self.c.execute(update(Budget).values(amount=0))
        self.c.execute(insert(Account).values(id="chk2", name="Second", kind="checking"))
        self.assertEqual(state.setup_steps(self.c), {"bank": True, "primary": False, "recurring": False, "budgets": False,
                                                      "dismissed": False})
        self.c.execute(update(Account).where(Account.id == "chk2").values(hidden=1))
        self.assertTrue(state.setup_steps(self.c)["primary"])

    def test_overview(self):
        self.c.execute(update(Account).where(Account.id == "demo-savings").values(display_name="A Savings"))
        fc = state.api_overview(self.c, q(days=30), {})
        self.assertEqual([a["id"] for a in fc["all_accounts"]], ["demo-checking", "demo-card", "demo-mortgage", "demo-savings"])
        self.assertEqual(list(fc["all_accounts"][0]), ["id", "name", "kind", "balance", "balance_date", "owed_positive", "hidden"])
        self.assertEqual(fc["all_accounts"][3]["name"], "A Savings")
        rec = [e for e in fc["events"] if e.get("recurring_id")]
        self.assertTrue(rec)
        self.assertTrue(all("logo" in e for e in rec))
        self.assertIn("missed", fc)
        self.assertEqual({a["id"]: a["balance_date"] for a in fc["accounts"]},
                         {a["id"]: a["balance_date"] for a in fc["all_accounts"] if a["id"] in {b["id"] for b in fc["accounts"]}})
        self.assertTrue(all(a["balance_date"] for a in fc["accounts"]))
        # nothing is taken out for everyday spending, so nothing about it is sent
        self.assertFalse(any({"daily_spend", "daily_spend_on", "daily_spend_estimate"} & set(a) for a in fc["accounts"]))
        self.assertEqual([w["text"] for w in fc["warning_links"]], fc["warnings"])

    def test_overview_names_sort_by_display_name(self):
        self.c.execute(insert(Account).values(id="c2", name="AAA", display_name="ZZZ", kind="checking"))
        self.c.execute(insert(Account).values(id="c3", name="MMM", kind="checking"))
        fc = state.api_overview(self.c, q(days=14), {})
        self.assertEqual([a["id"] for a in fc["all_accounts"] if a["kind"] == "checking"], ["demo-checking", "c3", "c2"])

    def test_overrides(self):
        with self.assertRaises(ApiError):
            state.api_override_set(self.c, {}, {"key": "other:1", "amount": 1})
        with self.assertRaises(ApiError):
            state.api_override_set(self.c, {}, {"key": "rec:1:2026-09-01", "amount": "lots"})
        state.api_override_set(self.c, {}, {"key": "rec:1:2026-09-01", "amount": "12.5"})
        state.api_override_set(self.c, {}, {"key": "rec:1:2026-09-01", "amount": -20})
        self.assertEqual([tuple(r) for r in self.c.execute(select(Override.key, Override.amount))], [("rec:1:2026-09-01", -20.0)])
        state.api_override_delete(self.c, {}, {"key": "rec:1:2026-09-01"})
        self.assertIsNone(self.one(select(Override.key)))
        state.api_override_set(self.c, {}, {"key": "cardclose:cc:2026-09-10", "amount": -500})   # a card payment, by its closing date
        self.assertEqual(self.one(select(Override.key))[0], "cardclose:cc:2026-09-10")
        state.api_override_delete(self.c, {}, {"key": "cardclose:cc:2026-09-10"})

    def test_settings_primary_account(self):
        with self.assertRaises(ApiError):
            state.api_settings(self.c, {}, {"primary_account": "demo-card"})
        with self.assertRaises(ApiError):
            state.api_settings(self.c, {}, {"primary_account": "nope"})
        state.api_settings(self.c, {}, {"primary_account": "demo-savings"})
        self.assertEqual(db.get_setting(self.c, "primary_account"), "demo-savings")
        state.api_settings(self.c, {}, {"primary_account": ""})
        self.assertIsNone(db.get_setting(self.c, "primary_account"))

    # ------------------------------------------------------------------------------------------ budget

    def test_budget(self):
        self.c.execute(insert(Account).values(id="c2", name="Zeta", display_name="Alpha Card", kind="credit"))
        self.c.execute(insert(Account).values(id="c3", name="Hidden Card", kind="credit", hidden=1))
        start = TODAY.replace(day=1)
        b = budget.api_budget(self.c, {}, {})
        self.assertEqual(b["pay_accounts"], [{"id": "c2", "name": "Alpha Card", "kind": "credit"},
                                             {"id": "demo-card", "name": "Rewards Visa", "kind": "credit"},
                                             {"id": "demo-checking", "name": "Everyday Checking", "kind": "checking"},
                                             {"id": "demo-savings", "name": "High-Yield Savings", "kind": "savings"}])
        groceries = sum(r[0] for r in self.c.execute(
            select(Transaction.amount)
            .where(Transaction.category == "Groceries", Transaction.posted >= start.isoformat())))
        g = next(c for c in b["categories"] if c["name"] == "Groceries")
        self.assertEqual((g["budget"], g["spent"], g["pay_with"], g["usual_account"]), (600, round(-groceries, 2), "demo-card", "demo-card"))

    def test_budget_rollover_carries_unspent(self):
        last = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=1)
        self.c.execute(update(Budget).where(Budget.category == "Coffee & Snacks").values(rollover_from=f"{last:%Y-%m}"))
        spent = sum(r[0] for r in self.c.execute(select(Transaction.amount)
                                                 .where(Transaction.category == "Coffee & Snacks",
                                                        Transaction.posted >= last.isoformat(),
                                                        Transaction.posted < TODAY.replace(day=1).isoformat())))
        b = budget.api_budget(self.c, {}, {})
        c = next(c for c in b["categories"] if c["name"] == "Coffee & Snacks")
        self.assertEqual(c["carried"], max(0.0, round(60 + spent, 2)))

    def test_budget_set(self):
        with self.assertRaises(ApiError):
            budget.api_budget_set(self.c, {}, {"category": "Transfer", "amount": 5})
        with self.assertRaises(ApiError):   # no budget to roll over yet
            budget.api_budget_set(self.c, {}, {"category": "Travel", "rollover": True})
        with self.assertRaises(ApiError):
            budget.api_budget_set(self.c, {}, {"category": "Groceries", "pay_with": "nope"})
        paid = lambda: self.one(select(Category.pay_with).where(Category.name == "Travel"))[0]
        # The card is the category's (still accepted here, for now): no budget needed, and the budget coming and going
        # leaves it alone.
        budget.api_budget_set(self.c, {}, {"category": "Travel", "pay_with": "demo-checking"})
        self.assertEqual(paid(), "demo-checking")
        budget.api_budget_set(self.c, {}, {"category": "Travel", "amount": "-250"})
        budget.api_budget_set(self.c, {}, {"category": "Travel", "rollover": True})
        budget.api_budget_set(self.c, {}, {"category": "Travel", "amount": 300})
        row = self.one(select(Budget.amount, Budget.rollover_from).where(Budget.category == "Travel"))
        self.assertEqual(tuple(row), (300.0, f"{TODAY:%Y-%m}"))
        budget.api_budget_set(self.c, {}, {"category": "Travel", "rollover": False})
        self.assertIsNone(self.one(select(Budget.rollover_from).where(Budget.category == "Travel"))[0])
        budget.api_budget_set(self.c, {}, {"category": "Travel", "amount": "0"})
        self.assertIsNone(self.one(select(Budget.category).where(Budget.category == "Travel")))
        self.assertEqual(paid(), "demo-checking")
        budget.api_budget_set(self.c, {}, {"category": "Travel", "pay_with": ""})
        self.assertIsNone(paid())

    def test_budget_expects_whats_still_coming_this_month(self):
        categories.add(self.c, "Streaming TV", parent="Subscriptions")
        next_month = (TODAY.replace(day=28) + timedelta(days=4)).replace(day=1)
        last_month = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=1)
        ev = lambda day, cat, amount, kind="recurring", acct="demo-checking": {
            "date": day.isoformat(), "category": cat, "amount": amount, "kind": kind, "account_id": acct, "name": "x"}
        fc = {"events": [ev(TODAY, "Subscriptions", -20.0),
                         ev(next_month, "Subscriptions", -99.0),            # another month's
                         ev(TODAY, "Subscriptions", 50.0),                  # money in isn't spending
                         ev(TODAY, "Income", 3000.0),
                         ev(TODAY, None, -500.0, kind="card")],             # a card's statement payment
              # on a card: counted in its subcategory and in the parent above it
              "charges": [ev(TODAY, "Streaming TV", -15.5, acct="demo-card"), ev(next_month, "Streaming TV", -15.5, acct="demo-card"),
                          ev(TODAY, "Income", 40.0, acct="demo-card")]}   # a card doesn't receive income

        def coming(month):
            return {c["name"]: c["expected"] for c in budget.api_budget(self.c, q(month=f"{month:%Y-%m}"), {})["categories"]}

        with mock.patch.object(budget.forecast, "build", return_value=fc) as build:
            now = coming(TODAY)
            self.assertEqual((now["Subscriptions"], now["Streaming TV"], now["Groceries"]), (35.5, 15.5, 0.0))
            # the paycheck still to come is income's, and nothing else counts there
            income = {c["name"]: c["expected"] for c in budget.api_budget(self.c, {}, {})["income_rows"]}
            self.assertEqual(income, {"Income": 3000.0})
            # the forecast is asked for this month's rest: from today to its last day
            self.assertEqual(build.call_args.args[1:], (TODAY, max(1, (next_month - timedelta(days=1) - TODAY).days)))
            later = coming(next_month)
            self.assertEqual((later["Subscriptions"], later["Streaming TV"]), (114.5, 15.5))
            build.reset_mock()
            self.assertEqual(set(coming(last_month).values()), {0.0})   # a month that's over expects nothing
            build.assert_not_called()
        # The rest stays as it was: what's left is still the budget less what's spent.
        g = next(c for c in budget.api_budget(self.c, {}, {})["categories"] if c["name"] == "Groceries")
        self.assertEqual(g["left"], round(600 - g["spent"], 2))

    def test_budget_expects_the_forecasts_recurring_payments_and_card_charges(self):
        fc = forecast.build(self.c, TODAY, max(1, ((TODAY.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1) - TODAY).days))
        due = lambda cat: round(-sum(e["amount"] for e in fc["events"] + fc["charges"] if e.get("category") == cat and e["amount"] < 0
                                     and e["date"][:7] == f"{TODAY:%Y-%m}"), 2)
        b = {c["name"]: c["expected"] for c in budget.api_budget(self.c, {}, {})["categories"]}
        self.assertEqual((b["Subscriptions"], b["Utilities"]), (due("Subscriptions"), due("Utilities")))

    def test_income_budget(self):
        categories.add(self.c, "Bonus", parent="Income")
        start = TODAY.replace(day=1)
        self.c.execute(insert(Transaction), [
            {"id": "bonus-1", "account_id": "demo-checking", "posted": start.isoformat(), "amount": 250.0, "category": "Bonus"},
            {"id": "refund-1", "account_id": "demo-card", "posted": start.isoformat(), "amount": 30.0, "category": "Refunds"}])
        paid = sum(r[0] for r in self.c.execute(select(Transaction.amount).where(
            Transaction.category == "Income", Transaction.posted >= start.isoformat())))
        save = lambda name, **kw: budget.api_budget_set(self.c, {}, {"category": name, **kw})
        self.assertEqual(save("Bonus", amount=300), {"ok": True, "raised": []})   # its parent has no budget
        self.assertEqual(save("Income", amount=6000)["raised"], [])
        # a subcategory's raises its parent's, as for spending
        self.assertEqual(save("Bonus", amount=6500)["raised"], [{"category": "Income", "amount": 6500.0}])
        with self.assertRaises(ApiError) as e:
            save("Income", rollover=True)
        self.assertEqual(str(e.exception), "Income doesn’t roll over")
        self.assertEqual(save("Income", pay_with="demo-card"), {"ok": True})   # no card for income: ignored
        self.assertIsNone(self.one(select(Category.pay_with).where(Category.name == "Income"))[0])
        with mock.patch.object(budget.forecast, "build", return_value={"events": [], "charges": []}):
            b = budget.api_budget(self.c, {}, {})
        rows = {c["name"]: c for c in b["income_rows"]}
        self.assertEqual(list(rows), ["Income", "Bonus"])   # Refunds come off spending: not income
        inc = rows["Income"]
        self.assertEqual((inc["budget"], inc["spent"], inc["own_spent"], inc["left"], inc["carried"], inc["pay_with"]),
                         (6500.0, round(paid + 250, 2), round(paid, 2), round(6500 - paid - 250, 2), 0.0, None))
        self.assertEqual((rows["Bonus"]["spent"], rows["Bonus"]["left"]), (250.0, 6250.0))
        self.assertEqual(b["income"], round(paid + 250, 2))   # the total as before
        self.assertNotIn("Income", [c["name"] for c in b["categories"]])

    def test_saving_a_subcategorys_budget_raises_its_parents(self):
        categories.add(self.c, "Medical Care")
        for name, parent in (("Dental", "Medical Care"), ("Eye Care", "Medical Care"), ("Flights", "Travel")):
            categories.add(self.c, name, parent=parent)
        # a level deeper than the app makes (its walk up the tree doesn't stop at two)
        self.c.execute(insert(Category).values(name="Braces", parent="Dental", is_transfer=0, is_income=0))
        amount = lambda name: self.one(select(Budget.amount).where(Budget.category == name))[0]
        save = lambda name, value: budget.api_budget_set(self.c, {}, {"category": name, "amount": value})
        save("Medical Care", 300)
        self.assertEqual(save("Eye Care", 200), {"ok": True, "raised": []})
        self.assertEqual(save("Dental", "150.004"), {"ok": True, "raised": [{"category": "Medical Care", "amount": 350.0}]})
        # lowering one never lowers the parent; the parent's still enough
        self.assertEqual(save("Eye Care", 100)["raised"], [])
        self.assertEqual(amount("Medical Care"), 350.0)
        # two levels up, each to its own subcategories' total
        self.assertEqual(save("Braces", 400)["raised"], [{"category": "Dental", "amount": 400.0}, {"category": "Medical Care", "amount": 500.0}])
        # removing a budget leaves the parents alone
        self.assertEqual(save("Braces", "0"), {"ok": True, "raised": []})
        self.assertEqual((amount("Dental"), amount("Medical Care")), (400.0, 500.0))
        # a parent without a budget isn't given one
        self.assertEqual(save("Flights", 900)["raised"], [])
        self.assertIsNone(self.one(select(Budget.amount).where(Budget.category == "Travel")))

    # ------------------------------------------------------------------------------------------ transactions

    def ids(self, **kw):
        return [t["id"] for t in transactions.api_transactions(self.c, q(**kw), {})["items"]]

    def test_transaction_filters(self):
        all_ = transactions.api_transactions(self.c, {}, {})
        self.assertEqual(all_["total"], self.one(select(func.count()).select_from(Transaction))[0])
        items = all_["items"]
        self.assertEqual([(t["posted"], t["id"]) for t in items],
                         sorted(((t["posted"], t["id"]) for t in items), key=lambda x: (-date.fromisoformat(x[0]).toordinal(), x[1])))
        t0 = items[0]
        for k in ("account_name", "account_kind", "recurring_name", "splits", "retail", "logo"):
            self.assertIn(k, t0)
        self.assertEqual(list(t0)[:len(db.schema.transactions.columns)], [c.name for c in db.schema.transactions.columns])
        self.c.execute(update(Account).where(Account.id == "demo-card").values(owner="Sam"))
        card = transactions.api_transactions(self.c, q(account="demo-card", limit=3, offset=1), {})
        self.assertEqual(len(card["items"]), 3)
        self.assertEqual({t["account_name"] for t in card["items"]}, {"Rewards Visa (Sam)"})
        self.assertEqual(card["total"], self.one(select(func.count())
                                                 .select_from(Transaction)
                                                 .where(Transaction.account_id == "demo-card"))[0])
        rid = self.one(select(Recurring.id).where(Recurring.name == "Gym"))[0]
        self.c.execute(update(Transaction).where(Transaction.payee == "Fit Club").values(recurring_id=rid))
        gym = transactions.api_transactions(self.c, q(recurring=rid), {})["items"]
        self.assertTrue(gym)
        self.assertEqual({t["recurring_name"] for t in gym}, {"Gym"})
        self.assertEqual(set(self.ids(q="PIZZA")),
                         {r[0] for r in self.c.execute(select(Transaction.id).where(Transaction.payee == "Pizza Palace"))})
        month = f"{TODAY:%Y-%m}"
        self.assertEqual(set(self.ids(month=month, limit=1000)),
                         {r[0] for r in self.c.execute(select(Transaction.id)
                                                       .where(Transaction.posted >= TODAY.replace(day=1).isoformat()))})
        self.assertEqual(set(self.ids(scope="budget", limit=1000)),
                         {r[0] for r in self.c.execute(select(Transaction.id)
                                                       .where(Transaction.account_id != "demo-mortgage"))})

    def test_transaction_ignored_filter(self):
        categories.add(self.c, "Reimbursed", "Ignore")
        a, b, c = (r[0] for r in self.c.execute(select(Transaction.id).order_by(Transaction.id).limit(3)))
        self.c.execute(update(Transaction).where(Transaction.id == a).values(category="Ignore"))
        self.c.execute(update(Transaction).where(Transaction.id == b).values(category="Reimbursed"))
        self.c.execute(update(Transaction).where(Transaction.id == c).values(category=None))
        everything = set(self.ids(limit=1000))
        self.assertTrue({a, b, c} <= everything)   # shown unless asked to hide them
        hidden = transactions.api_transactions(self.c, q(ignored="0", limit=1000), {})
        self.assertEqual({t["id"] for t in hidden["items"]}, everything - {a, b})
        self.assertEqual(hidden["total"], len(everything) - 2)
        self.assertEqual(set(self.ids(ignored="0", category="Ignore")), {a, b})   # asked for by name: shown
        # ignored=only is exactly what ignored=0 hides: a split with an Ignore part stays in the list, so it isn't counted
        d = self.one(select(Transaction.id).where(Transaction.id.notin_([a, b, c])).order_by(Transaction.id))[0]
        amount = self.one(select(Transaction.amount).where(Transaction.id == d))[0]
        half = round(amount / 2, 2)    # whole cents, whatever the demo data's amount is on the day it's seeded
        splits.set_splits(self.c, d, [{"amount": half, "category": "Groceries"},
                                      {"amount": round(amount - half, 2), "category": "Ignore"}])
        self.assertEqual(set(self.ids(ignored="only", limit=1000)), {a, b})
        self.assertIn(d, set(self.ids(ignored="0", limit=1000)))

    def test_transaction_category_filters(self):
        categories.add(self.c, "Farmers Market", "Groceries")
        tid = self.one(select(Transaction.id).where(Transaction.category == "Shopping").order_by(Transaction.id))[0]
        amount = self.one(select(Transaction.amount).where(Transaction.id == tid))[0]
        half = round(amount / 2, 2)
        splits.set_splits(self.c, tid, [{"amount": half, "category": "Farmers Market"},
                                        {"amount": round(amount - half, 2), "category": "Pharmacy"}])
        other = self.one(select(Transaction.id).where(Transaction.category == "Restaurants")
                         .order_by(Transaction.id))[0]
        self.c.execute(update(Transaction).where(Transaction.id == other).values(category=None, needs_review=1))
        groceries = {r[0] for r in self.c.execute(select(Transaction.id).where(Transaction.category == "Groceries"))}
        self.assertEqual(set(self.ids(category="Groceries", limit=1000)), groceries | {tid})
        self.assertNotIn(tid, self.ids(category="Shopping", limit=1000))
        self.assertEqual(self.ids(category="__none__"), [other])
        self.assertEqual(self.ids(review=1), [other])

    def test_ai_log_and_apply(self):
        for i in range(30):
            self.c.execute(insert(AiLog).values(at=f"2026-09-{i % 28 + 1:02d}", purpose="categorize"))
        log = transactions.api_ai_log(self.c, {}, {})
        self.assertEqual(len(log), 25)
        self.assertEqual([r["id"] for r in log], sorted((r["id"] for r in log), reverse=True))
        self.assertEqual(list(log[0]), [c.name for c in db.schema.ai_log.columns])
        tid = self.one(select(Transaction.id).where(Transaction.category == "Shopping").order_by(Transaction.id))[0]
        out = transactions.api_ai_apply(self.c, {}, {"tx_ids": [tid], "new_category": {"name": "  groceries "}, "remember": True})
        self.assertEqual((out["category"], out["created"]), ("Groceries", False))
        out = transactions.api_ai_apply(self.c, {}, {"tx_ids": [tid], "new_category": {"name": "Pet Food", "parent": "No Such"},
                                                     "remember": True})
        self.assertEqual((out["category"], out["created"]), ("Pet Food", True))
        self.assertIsNone(self.one(select(Category.parent).where(Category.name == "Pet Food"))[0])
        out = transactions.api_ai_apply(self.c, {}, {"tx_ids": [tid], "new_category": {"name": "Dog Food", "parent": "Groceries"},
                                                     "remember": True})
        self.assertEqual(self.one(select(Category.parent).where(Category.name == "Dog Food"))[0], "Groceries")

    def test_recategorize(self):
        a, b, c, d = [r[0] for r in self.c.execute(select(Transaction.id)
                                                   .where(Transaction.category == "Shopping")
                                                   .order_by(Transaction.id).limit(4))]
        self.c.execute(update(Transaction).where(Transaction.id.in_([a, b, c])).values(needs_review=1))
        self.c.execute(update(Transaction).where(Transaction.id == b).values(category_source="manual"))
        self.c.execute(update(Transaction).where(Transaction.id == d).values(category=None, category_source=None))
        with mock.patch.object(transactions.categorize, "categorize", return_value={"rules": 0}) as cat:
            transactions.api_recategorize(self.c, {}, {})
        self.assertEqual(sorted(cat.call_args[0][1]), sorted([a, c, d]))
        rows = {r[0]: tuple(r)[1:] for r in self.c.execute(
            select(Transaction.id, Transaction.category, Transaction.category_source, Transaction.confidence)
            .where(Transaction.id.in_([a, b, c])))}
        self.assertEqual(rows[a], (None, None, None))
        self.assertEqual(rows[b], ("Shopping", "manual", None))

    # ------------------------------------------------------------------------------------------ sync

    def test_plaid_banks(self):
        with mock.patch.object(sync.plaid, "configured", return_value=True):
            self.assertFalse(sync.plaid_banks(self.c))
            self.c.execute(insert(PlaidItem).values(item_id="i", access_token="x", products="investments"))
            self.assertFalse(sync.plaid_banks(self.c))
            self.c.execute(insert(PlaidItem).values(item_id="j", access_token="x", products="transactions"))
            self.assertTrue(sync.plaid_banks(self.c))

    def test_refresh_prices_asks_for_the_securities_held(self):
        self.c.execute(insert(Security), [{"id": "s1", "ticker": "AAA", "is_cash": 0},
                                          {"id": "s2", "ticker": "BBB", "is_cash": 0},
                                          {"id": "s3", "ticker": "CCC", "is_cash": 0},
                                          {"id": "s4", "ticker": "CASH", "is_cash": 1},
                                          {"id": "s5", "ticker": None, "is_cash": 0},
                                          {"id": "s6", "ticker": "ZZZ", "is_cash": 0},
                                          {"id": "s7", "ticker": "AAA", "is_cash": 0}])
        self.c.execute(insert(Holding), [{"account_id": "a", "security_id": "s1"},
                                         {"account_id": "a", "security_id": "s4"},
                                         {"account_id": "a", "security_id": "s5"},
                                         {"account_id": "a", "security_id": "s7"}])
        self.c.execute(insert(InvTransaction).values(id="t", account_id="a", security_id="s2", date="2026-01-01"))
        self.c.execute(insert(ManualPosition).values(account_id="a", security_id="s3"))
        with mock.patch.object(sync.prices, "refresh", return_value={}) as refresh, \
                mock.patch.object(sync.sfinvest, "recapture_all"), mock.patch.object(sync.prices, "fill_security_types"):
            sync.refresh_prices(self.c)
        self.assertEqual(sorted(refresh.call_args[0][1]), sorted(["AAA", "BBB", "CCC", sync.prices.BENCHMARK]))

    def test_investment_sync(self):
        self.c.commit()
        with mock.patch.object(sync, "refresh_prices", return_value={"AAA": 1}) as rp:
            self.assertEqual(sync.run_investment_sync(), {"items": 0, "errors": [], "prices": {}})
            rp.assert_not_called()
            self.c.execute(insert(InvAccount).values(id="ia", item_id="x"))
            self.c.commit()
            self.assertEqual(sync.run_investment_sync()["prices"], {"AAA": 1})

    def test_bank_sync_logs_and_refreshes_simplefin_investments(self):
        self.c.execute(insert(InvAccount).values(id="ia", item_id="x", source="plaid"))
        self.c.commit()
        with mock.patch.object(sync.simplefin, "sync", return_value={"new": [], "errors": ["Bank note"]}), \
                mock.patch.object(sync.merchants, "fetch_logos"), mock.patch.object(sync.realie, "refresh_due"), \
                mock.patch.object(sync, "refresh_prices") as rp:
            self.assertEqual(sync.run_sync(), {"new": 0, "categorized": mock.ANY, "bank_messages": ["Bank note"]})
            rp.assert_not_called()
            self.c.execute(update(InvAccount).values(source="simplefin"))
            self.c.commit()
            sync.run_sync()
            rp.assert_called_once()
        log = self.one(select(SyncLog.ok, SyncLog.message).order_by(SyncLog.id.desc()).limit(1))
        self.assertEqual(tuple(log), (1, "0 new transactions · bank messages: Bank note"))
        # It worked, so it counts as a good sync; what the bank said is kept apart for the sidebar, until a clean sync.
        self.assertTrue(db.get_setting(self.c, "last_sync_ok"))
        self.assertEqual(state.api_state(self.c, {}, {})["sync_warnings"], ["Bank note"])
        with mock.patch.object(sync.simplefin, "sync", return_value={"new": [], "errors": []}), \
                mock.patch.object(sync.merchants, "fetch_logos"), mock.patch.object(sync.realie, "refresh_due"), \
                mock.patch.object(sync, "refresh_prices"):
            sync.run_sync()
        self.assertEqual(state.api_state(self.c, {}, {})["sync_warnings"], [])


if __name__ == "__main__":
    unittest.main()
