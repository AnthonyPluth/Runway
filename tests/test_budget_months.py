"""A budget's own amount for one month (budget_months): that month only, every other month keeping the usual amount, on
the Budget page, in what rolls over and in the forecast's spending."""
from datetime import date, timedelta

from sqlalchemy import delete, insert, select, update

from runway.dates import add_months, days_in_month, month_key
from runway.domain import budgets, categories, demo, forecast
from runway.server.api import budget
from runway.server.common import ApiError
from runway.storage.models import Budget, MonthBudget, Transaction
from tests.shared import TODAY, DbCase, freeze_today

THIS = month_key(TODAY)
NEXT = month_key(add_months(TODAY.replace(day=1), 1))
LAST = month_key(add_months(TODAY.replace(day=1), -1))


class BudgetMonthTests(DbCase):
    def setUp(self):
        super().setUp()
        freeze_today(self)
        demo.seed(self.c, TODAY)   # made-up data: Restaurants has a budget of 250 a month

    def save(self, name, **kw):
        return budget.api_budget_set(self.c, {}, {"category": name, **kw})

    def row(self, name, month=THIS, key="categories"):
        b = budget.api_budget(self.c, {"month": [month]}, {})
        return next(c for c in b[key] if c["name"] == name)

    def months(self, name):
        return dict(self.c.execute(select(MonthBudget.month, MonthBudget.amount).where(MonthBudget.category == name)).fetchall())

    def test_a_month_of_its_own_and_every_other_month_the_usual_amount(self):
        r = self.row("Restaurants")
        self.assertEqual((r["budget"], r["usual_budget"], r["month_budget"]), (250, 250, None))
        self.assertEqual(self.save("Restaurants", month=NEXT, amount="400"), {"ok": True, "raised": []})
        n = self.row("Restaurants", NEXT)
        self.assertEqual((n["budget"], n["usual_budget"], n["month_budget"], n["available"]), (400, 250, 400, 400))
        self.assertEqual(n["left"], round(400 - n["spent"], 2))
        for month in (LAST, THIS, month_key(add_months(TODAY.replace(day=1), 2))):   # nothing carried forward or back
            self.assertEqual(self.row("Restaurants", month)["budget"], 250, month)
        self.save("Restaurants", amount=300)   # a new usual amount: every month without its own
        self.assertEqual((self.row("Restaurants")["budget"], self.row("Restaurants", NEXT)["budget"]), (300, 400))
        self.assertEqual(self.save("Restaurants", month=NEXT, amount=""), {"ok": True, "raised": []})   # back to the usual
        n = self.row("Restaurants", NEXT)
        self.assertEqual((n["budget"], n["month_budget"]), (300, None))
        self.assertEqual(self.months("Restaurants"), {})

    def test_nothing_budgeted_in_a_month_keeps_the_budget(self):
        self.save("Restaurants", month=THIS, amount="0")
        r = self.row("Restaurants")
        self.assertEqual((r["budget"], r["month_budget"], r["usual_budget"]), (0, 0, 250))
        self.assertEqual(r["left"], round(-r["spent"], 2))
        self.assertEqual(self.row("Restaurants", NEXT)["budget"], 250)

    def test_month_written_any_way_is_kept_as_yyyy_mm(self):
        self.save("Restaurants", month=f"{TODAY.year}-{TODAY.month}", amount=99.5)
        self.assertEqual(self.months("Restaurants"), {THIS: 99.5})

    def test_refused(self):
        for body, msg in (({"category": "Travel", "month": THIS, "amount": 50}, "Set a budget for this category first"),
                          ({"category": "Restaurants", "month": "soon", "amount": 50}, "Month must look like 2026-09"),
                          ({"category": "Restaurants", "month": "", "amount": 50}, "Month must look like 2026-09"),
                          ({"category": "Restaurants", "month": "1990-01", "amount": 50}, "Month must be within 10 years of today"),
                          ({"category": "Restaurants", "month": THIS, "amount": "lots"}, "Enter an amount"),
                          ({"category": "Transfer", "month": THIS, "amount": 50}, "Pick a spending or income category")):
            with self.subTest(body=body), self.assertRaises(ApiError) as e:
                budget.api_budget_set(self.c, {}, body)
            self.assertEqual(str(e.exception), msg)
        with self.assertRaises(ApiError) as e:
            budget.api_budget_set(self.c, {}, {"category": "Restaurants", "month": 202609, "amount": 50})
        self.assertEqual(str(e.exception), 'Send "month" as text')
        self.assertEqual(self.months("Restaurants"), {})

    def test_removing_the_budget_takes_its_months_with_it(self):
        self.save("Restaurants", month=NEXT, amount=400)
        self.save("Restaurants", amount="")
        self.assertIsNone(self.c.execute(select(Budget.amount).where(Budget.category == "Restaurants")).fetchone())
        self.assertEqual(self.months("Restaurants"), {})
        self.save("Restaurants", amount=100)   # a budget set again starts with no months of its own
        self.assertEqual(self.row("Restaurants", NEXT)["budget"], 100)

    def test_a_subcategorys_month_raises_its_parent_that_month_only(self):
        categories.add(self.c, "Fancy", parent="Restaurants")
        self.save("Fancy", amount=100)
        self.assertEqual(self.save("Fancy", month=NEXT, amount=300), {"ok": True, "raised": [{"category": "Restaurants", "amount": 300.0}]})
        self.assertEqual(self.months("Restaurants"), {NEXT: 300.0})
        self.assertEqual(self.c.execute(select(Budget.amount).where(Budget.category == "Restaurants")).scalar(), 250)
        self.assertEqual(self.save("Fancy", month=NEXT, amount=50)["raised"], [])   # never lowered
        self.assertEqual(self.months("Restaurants"), {NEXT: 300.0})

    def test_an_income_month_of_its_own(self):   # a bonus month
        self.save("Income", amount=5000)
        self.save("Income", month=NEXT, amount=8000)
        self.assertEqual(self.row("Income", NEXT, "income_rows")["budget"], 8000)
        self.assertEqual(self.row("Income", THIS, "income_rows")["budget"], 5000)

    def test_renaming_or_removing_the_category(self):
        self.save("Restaurants", month=NEXT, amount=400)
        categories.rename(self.c, "Restaurants", "Eating out")
        self.assertEqual((self.months("Eating out"), self.months("Restaurants")), ({NEXT: 400.0}, {}))
        self.assertEqual(self.row("Eating out", NEXT)["budget"], 400)
        categories.remove(self.c, "Eating out")
        self.assertEqual(self.months("Eating out"), {})

    def test_rollover_carries_each_months_own_amount(self):
        start = add_months(TODAY.replace(day=1), -2)
        self.c.execute(delete(Transaction).where(Transaction.category == "Restaurants"))
        self.c.execute(insert(Transaction), [
            {"id": "r1", "account_id": "demo-card", "posted": start.isoformat(), "amount": -200.0, "category": "Restaurants"},
            {"id": "r2", "account_id": "demo-card", "posted": add_months(start, 1).isoformat(), "amount": -500.0, "category": "Restaurants"}])
        self.c.execute(update(Budget).where(Budget.category == "Restaurants").values(rollover_from=month_key(start)))
        self.assertEqual(self.row("Restaurants")["carried"], 0.0)   # 50 left, then 250 + 50 - 500: nothing
        self.save("Restaurants", month=LAST, amount=600)
        self.assertEqual(self.row("Restaurants")["carried"], 150.0)   # 50 left, then 600 + 50 - 500
        cats = [c for c in categories.all_categories(self.c) if not c["is_transfer"] and not c["is_income"]]
        self.assertEqual(budgets.budget_carry(self.c, cats, budgets.load(self.c), TODAY.replace(day=1))["Restaurants"], 150.0)

    def test_the_forecast_spends_each_months_own_amount(self):
        def spent_by_month():
            plan = forecast.budget_plan(self.c, TODAY)
            days = forecast.budget_days(self.c, TODAY, 120, plan, [], {"demo-checking"})["Restaurants"][0]["days"]
            out: dict[str, float] = {}
            for d, v in days.items():
                out[d[:7]] = out.get(d[:7], 0.0) + v
            return {k: round(v, 2) for k, v in out.items()}
        before = spent_by_month()
        self.assertEqual(before[NEXT], 250.0)
        self.save("Restaurants", month=NEXT, amount=400)
        after = spent_by_month()
        self.assertEqual(after[NEXT], 400.0)
        self.assertEqual({k: v for k, v in after.items() if k != NEXT}, {k: v for k, v in before.items() if k != NEXT})
        first = date.fromisoformat(NEXT + "-01")
        self.assertAlmostEqual(forecast.budget_days(self.c, TODAY, 120, forecast.budget_plan(self.c, TODAY), [], {"demo-checking"})
                               ["Restaurants"][0]["days"][(first + timedelta(days=3)).isoformat()], 400 / days_in_month(first), places=9)

    def test_this_months_own_amount_in_the_forecast(self):
        self.save("Restaurants", month=THIS, amount=1000)
        p = next(p for p in forecast.budget_plan(self.c, TODAY) if p["category"] == "Restaurants")
        self.assertEqual((p["amount"], p["every"], p["months"]), (1000, 250, {THIS: 1000}))
        days = forecast.budget_days(self.c, TODAY, 30, [p], [], {"demo-checking"})["Restaurants"][0]["days"]
        self.assertEqual(round(sum(v for d, v in days.items() if d[:7] == THIS), 2), round(max(0.0, 1000 - p["spent"]), 2))
