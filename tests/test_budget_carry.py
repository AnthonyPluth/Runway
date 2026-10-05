"""What a budget that rolls over carries (budgets.budget_carry): the same as working it out a month at a time, in one
query however many months it's been rolling over."""
from datetime import date
from unittest import mock

from sqlalchemy import insert, select, update

from runway.domain import budgets, categories
from runway.storage.models import Account, Budget
from tests.shared import LedgerCase


def carry_month_by_month(conn, cats, rows, month):
    """budget_carry as it was: each month's totals (month_totals) queried on its own."""
    starts = {n: date.fromisoformat(r["rollover_from"] + "-01") for n, r in rows.items()
              if r.get("rollover_from") and n in {c["name"] for c in cats}}
    carry = {n: 0.0 for n in starts}
    m = min(starts.values(), default=month)
    while m < month:
        nxt = date(m.year + m.month // 12, m.month % 12 + 1, 1)
        spent = budgets.family_spent(cats, budgets.month_totals(conn, m, nxt))
        for n, start in starts.items():
            if start <= m:
                carry[n] = max(0.0, round(rows[n]["amount"] + carry[n] - spent.get(n, 0.0), 2))
        m = nxt
    return carry


class BudgetCarryTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 1000.0)
        self.acct("cc", "credit", -100.0)
        self.acct("gone", "credit", -100.0)
        self.conn.execute(update(Account).where(Account.id == "gone").values(hidden=1))
        self.acct("loan", "loan", -9000.0)
        categories.add(self.conn, "Fast food", parent="Restaurants")
        for cat, amount, since in (("Restaurants", 300, "2025-11"), ("Groceries", 450.5, "2026-03"), ("Shopping", 200, None),
                                   ("Travel", 100, "2026-12"), ("Income", 5000, "2026-01")):
            self.conn.execute(insert(Budget).values(category=cat, amount=amount, rollover_from=since))
        spend = [("cc", "2025-10-31", -80.0, "Restaurants"), ("cc", "2025-11-01", -120.4, "Restaurants"),
                 ("cc", "2025-12-15T18:30:00", -450.0, "Fast food"), ("chk", "2026-01-31", -99.99, "Restaurants"),
                 ("chk", "2026-02-10", 25.0, "Restaurants"), ("gone", "2026-02-11", -500.0, "Restaurants"),
                 ("loan", "2026-02-12", -500.0, "Restaurants"), ("cc", "2026-03-03", -610.25, "Groceries"),
                 ("cc", "2026-04-03", -100.1, "Groceries"), ("chk", "2026-05-20", -0.01, "Groceries"),
                 ("cc", "2026-06-30", -300.0, "Restaurants"), ("cc", "2026-08-01", -12.34, "Fast food"),
                 ("chk", "2026-09-22", -45.67, "Groceries"), ("chk", "2026-01-15", 5200.0, "Income")]
        for acct, posted, amount, cat in spend:
            self.tx(acct, posted, amount, "X", cat)

    def inputs(self):
        cats = [c for c in categories.all_categories(self.conn) if not c["is_transfer"] and not c["is_income"]]
        return cats, {r["category"]: dict(r) for r in self.conn.execute(select(Budget))}

    def test_the_same_as_a_month_at_a_time(self):
        cats, rows = self.inputs()
        for month in ("2025-10-01", "2025-11-01", "2025-12-01", "2026-01-01", "2026-03-01", "2026-04-01", "2026-07-01",
                      "2026-09-01", "2026-10-01", "2026-09-15", "2027-01-01"):
            with self.subTest(month=month):
                got = budgets.budget_carry(self.conn, cats, rows, date.fromisoformat(month))
                self.assertEqual(got, carry_month_by_month(self.conn, cats, rows, date.fromisoformat(month)))
                self.assertEqual(set(got), {"Restaurants", "Groceries", "Travel"})
        self.assertEqual(budgets.budget_carry(self.conn, cats, rows, date(2025, 12, 1))["Restaurants"], 179.6)

    def test_one_query(self):
        cats, rows = self.inputs()
        execute = self.conn.execute
        with mock.patch.object(self.conn, "execute", side_effect=execute) as spy:
            budgets.budget_carry(self.conn, cats, rows, date(2026, 10, 1))
        self.assertEqual(spy.call_count, 1)

    def test_nothing_rolling_over(self):
        cats, rows = self.inputs()
        self.assertEqual(budgets.budget_carry(self.conn, cats, {"Shopping": rows["Shopping"]}, date(2026, 10, 1)), {})
        self.assertEqual(budgets.budget_carry(self.conn, cats, rows, date(2025, 11, 1)),
                         {"Restaurants": 0.0, "Groceries": 0.0, "Travel": 0.0})
        bad = {**rows, "Shopping": {**rows["Shopping"], "rollover_from": "soon"}}
        self.assertNotIn("Shopping", budgets.budget_carry(self.conn, cats, bad, date(2026, 10, 1)))
