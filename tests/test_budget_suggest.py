"""Suggested budgets (runway/domain/budget_suggest.py) and GET /api/budget/suggestions, on made-up figures."""
import unittest
from datetime import date

from sqlalchemy import insert

from runway.domain import budget_suggest, categories, demo
from runway.server.api import budget_suggest as api
from runway.server.common import ApiError
from runway.storage.models import Account, Budget, Transaction
from tests.shared import TODAY, DbCase, freeze_today


def cat(name, parent=None):
    path = [parent, name] if parent else [name]
    return {"name": name, "parent": parent, "path": path, "depth": len(path) - 1, "top": path[0]}


# Food > Groceries, Food > Dining; Home; Fun > Games
CATS = [cat("Food"), cat("Groceries", "Food"), cat("Dining", "Food"), cat("Home"), cat("Fun"), cat("Games", "Fun")]


class RoundUp(unittest.TestCase):
    def test_up_to_the_next_step(self):
        self.assertEqual(budget_suggest.round_up(0), 0.0)
        self.assertEqual(budget_suggest.round_up(-12), 0.0)
        self.assertEqual(budget_suggest.round_up(0.004), 0.0)      # under half a cent is nothing
        self.assertEqual(budget_suggest.round_up(0.01), 5.0)
        self.assertEqual(budget_suggest.round_up(120), 120.0)      # on a step already
        self.assertEqual(budget_suggest.round_up(120.01), 125.0)
        self.assertEqual(budget_suggest.round_up(123.45, step=10), 130.0)


class HistoryMonths(unittest.TestCase):
    def test_full_months_before_this_one(self):
        months = budget_suggest.history_months(date(2025, 1, 5), date(2026, 9, 23))
        self.assertEqual(months, [date(2026, m, 1) for m in range(3, 9)])

    def test_not_before_the_first_transaction(self):
        self.assertEqual(budget_suggest.history_months(date(2026, 7, 30), date(2026, 9, 23)),
                         [date(2026, 7, 1), date(2026, 8, 1)])
        self.assertEqual(budget_suggest.history_months(date(2026, 9, 2), date(2026, 9, 23)), [])
        self.assertEqual(budget_suggest.history_months(None, date(2026, 9, 23)), [])

    def test_across_a_year(self):
        months = budget_suggest.history_months(date(2020, 1, 1), date(2027, 2, 10), months=3)
        self.assertEqual(months, [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)])


class Suggest(unittest.TestCase):
    def by_cat(self, *args):
        return {s["category"]: s for s in budget_suggest.suggest(CATS, *args)}

    def test_median_of_the_months_with_subcategories_counted_in(self):
        monthly = [{"Groceries": -300.0, "Dining": -100.0, "Food": -20.0}, {"Groceries": -410.0},
                   {"Groceries": -350.0, "Dining": -15.5}]
        out = self.by_cat(monthly, [], {})
        # Food's months: 420, 410 and 365.50
        self.assertEqual(out["Food"], {"category": "Food", "suggested": 410.0, "typical": 410.0, "recurring": 0.0, "budget": None})
        self.assertEqual(self.by_cat(monthly[:2], [], {})["Food"]["typical"], 415.0)   # an even count: the middle two's mean
        self.assertNotIn("Groceries", out)   # no budget on it, and it isn't top-level

    def test_a_month_without_spending_counts_as_nothing(self):
        monthly = [{"Home": -900.0}, {}, {}, {"Home": -50.0}]
        self.assertEqual(self.by_cat(monthly, [], {})["Home"]["typical"], 25.0)
        self.assertEqual(self.by_cat(monthly, [], {})["Home"]["suggested"], 25.0)

    def test_refunds_outweighing_spending_count_as_nothing(self):
        monthly = [{"Home": 80.0}, {"Home": -40.0}, {"Home": -60.0}]
        self.assertEqual(self.by_cat(monthly, [], {})["Home"]["typical"], 40.0)

    def test_recurring_payments_set_the_floor_and_are_not_added(self):
        monthly = [{"Home": -100.0}, {"Home": -100.0}]
        out = self.by_cat(monthly, [("Home", 140.0), ("Games", 12.0)], {})
        self.assertEqual((out["Home"]["typical"], out["Home"]["recurring"], out["Home"]["suggested"]), (100.0, 140.0, 140.0))
        self.assertEqual((out["Fun"]["recurring"], out["Fun"]["suggested"]), (12.0, 15.0))   # a subcategory's counts up
        below = self.by_cat([{"Home": -200.0}], [("Home", 140.0)], {})
        self.assertEqual(below["Home"]["suggested"], 200.0)

    def test_which_categories_get_one(self):
        monthly = [{"Groceries": -300.0, "Dining": -80.0, "Games": -30.0, "Home": -500.0}]
        out = budget_suggest.suggest(CATS, monthly, [], {"Groceries": 250.0, "Games": 0.5})
        # The budgeted subcategories, in tree order; Food and Fun have a budget in their family, so not one of their own.
        self.assertEqual([s["category"] for s in out], ["Groceries", "Home", "Games"])
        self.assertEqual(out[0]["budget"], 250.0)
        self.assertIsNone(out[1]["budget"])

    def test_nothing_spent_and_nothing_coming_suggests_nothing(self):
        out = budget_suggest.suggest(CATS, [{}, {}], [], {"Home": 300.0})
        self.assertEqual(out, [])   # an existing budget isn't suggested away
        self.assertEqual(budget_suggest.suggest(CATS, [], [], {}), [])

    def test_no_history_leaves_the_recurring_payments(self):
        out = self.by_cat([], [("Groceries", 61.2)], {})
        self.assertEqual(out["Food"], {"category": "Food", "suggested": 65.0, "typical": 0.0, "recurring": 61.2, "budget": None})


class Endpoint(DbCase):
    def setUp(self):
        super().setUp()
        freeze_today(self)

    def test_made_up_history(self):
        self.c.execute(insert(Account).values(id="chk", name="Checking", kind="checking", balance=1000.0))
        self.c.execute(insert(Account).values(id="loan", name="Loan", kind="loan", balance=-5000.0))
        rows = [("chk", "2026-06-10", -210.0, "Groceries"), ("chk", "2026-07-10", -190.0, "Groceries"),
                ("chk", "2026-08-10", -250.0, "Groceries"), ("chk", "2026-09-10", -900.0, "Groceries"),   # this month: not counted
                ("loan", "2026-08-11", -999.0, "Groceries")]   # loans aren't spending
        self.c.execute(insert(Transaction), [{"id": f"t{i}", "account_id": a, "posted": d, "amount": amt, "payee": "Shop",
                                              "description": "SHOP", "category": c, "pending": 0}
                                             for i, (a, d, amt, c) in enumerate(rows)])
        self.c.execute(insert(Budget).values(category="Groceries", amount=150.0))
        out = api.api_budget_suggestions(self.c, {}, {})
        self.assertEqual((out["months"], out["first"], out["last"], out["recurring_month"]), (3, "2026-06", "2026-08", "2026-10"))
        groceries = next(s for s in out["suggestions"] if s["category"] == "Groceries")
        self.assertEqual(groceries, {"category": "Groceries", "suggested": 210.0, "typical": 210.0, "recurring": 0.0,
                                     "budget": 150.0})
        top = categories.all_categories(self.c)
        parent = next(c for c in top if c["name"] == "Groceries")["top"]
        if parent != "Groceries":   # its top-level category has a budget in its family, so gets none
            self.assertNotIn(parent, [s["category"] for s in out["suggestions"]])

    def test_the_month_asked_for(self):
        self.assertEqual(api.api_budget_suggestions(self.c, {"month": ["2026-12"]}, {})["recurring_month"], "2026-12")
        self.assertEqual(api.api_budget_suggestions(self.c, {"month": [f"{TODAY:%Y-%m}"]}, {})["recurring_month"], "2026-10")
        self.assertEqual(api.api_budget_suggestions(self.c, {"month": ["2026-01"]}, {})["recurring_month"], "2026-10")
        with self.assertRaises(ApiError):
            api.api_budget_suggestions(self.c, {"month": ["soon"]}, {})

    def test_a_month_beyond_the_forecast_counts_the_last_month_it_reaches(self):
        demo.seed(self.c, TODAY)
        out = api.api_budget_suggestions(self.c, {"month": ["2028-06"]}, {})
        self.assertEqual(out["recurring_month"], "2027-09")   # TODAY + EXPECTED_DAYS (366) falls in September 2027
        self.assertTrue(any(s["recurring"] >= 2140.0 for s in out["suggestions"]))   # the sample mortgage, then

    def test_an_empty_database(self):
        out = api.api_budget_suggestions(self.c, {}, {})
        self.assertEqual((out["months"], out["first"], out["last"], out["suggestions"]), (0, None, None, []))

    def test_sample_data_counts_the_recurring_bills(self):
        demo.seed(self.c, TODAY)
        out = api.api_budget_suggestions(self.c, {}, {})
        self.assertEqual(out["months"], 6)
        by_cat = {s["category"]: s for s in out["suggestions"]}
        self.assertEqual(set(by_cat) & {"Groceries", "Restaurants", "Coffee & Snacks", "Shopping"},
                         {"Groceries", "Restaurants", "Coffee & Snacks", "Shopping"})   # the sample's budgets
        bills = [s for s in out["suggestions"] if s["recurring"] > 0]
        self.assertTrue(any(s["recurring"] >= 2140.0 for s in bills))   # the sample mortgage, next month
        for s in out["suggestions"]:
            self.assertGreaterEqual(s["suggested"], max(s["typical"], s["recurring"]))
            self.assertEqual(s["suggested"] % budget_suggest.STEP, 0)


if __name__ == "__main__":
    unittest.main()
