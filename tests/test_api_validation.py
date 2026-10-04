"""What the API's handlers do with values they can't use: each is refused with a message saying which (ApiError, a 400
or a 404), never left to fail as a bug would. Switches sent as text ("false", "0", "no") are off; amounts that aren't
numbers, or are too large, are refused; so are days that aren't days and whole numbers that aren't whole."""

from sqlalchemy import select

from runway import db, demo, mcp_access, validate
from runway import settings_keys as sk
from runway.models import Account, Budget, Category, Override, Recurring, Rule, Transaction
from runway.server.api import (accounts, budget, categories, mcp, merchants, recurring, reports, state, transactions)
from runway.server.common import ApiError, clamped_int, query_int, row_id
from tests.shared import TODAY, DbCase, freeze_today

# (date.today() is frozen at shared.TODAY in each test's setUp: the handlers read the clock too)
OFF = ("false", "0", "no", "off", "", None, 0, False, [], {})
NOT_AMOUNTS = ("nan", "inf", "-inf", "NaN", float("nan"), float("inf"), "1e9", 1e9, -1e9, "1e300", 1e300, 10 ** 30, True,
               "abc", [], {})


def q(**kw):
    return {k: [str(v)] for k, v in kw.items()}


class FlagTests(DbCase):
    def setUp(self):
        super().setUp()
        freeze_today(self)
        demo.seed(self.c, TODAY)
        self.tx = self.c.execute(select(Transaction.id).where(Transaction.account_id == "demo-card")
                                 .order_by(Transaction.id).limit(1)).scalar()

    def test_off_is_off(self):
        for off in OFF:
            with self.subTest(v=off):
                self.assertEqual(validate.flag(off), 0)
                self.assertFalse(validate.on(off))
        for on in (True, 1, "1", "true", "on"):
            self.assertTrue(validate.on(on))

    def test_remembering_a_category(self):
        rules = lambda: self.c.execute(select(Rule.id)).scalars()
        before = rules()
        for off in ("false", "0", "no"):
            out = transactions.api_tx_category(self.c, {}, {"category": "Shopping", "remember": off}, self.tx)
            self.assertIsNotNone(out["offer_rule"])
        self.assertEqual(rules(), before)
        transactions.api_tx_category(self.c, {}, {"category": "Shopping", "remember": "true"}, self.tx)
        self.assertEqual(len(rules()), len(before) + 1)

    def test_bulk_reviewed(self):
        self.c.execute(Transaction.__table__.update().where(Transaction.id == self.tx).values(needs_review=1))
        with self.assertRaisesRegex(ApiError, "Choose what to change"):
            transactions.api_tx_bulk(self.c, {}, {"ids": [self.tx], "reviewed": "false"})
        transactions.api_tx_bulk(self.c, {}, {"ids": [self.tx], "reviewed": "1"})
        self.assertEqual(self.c.execute(select(Transaction.needs_review).where(Transaction.id == self.tx)).scalar(), 0)

    def test_new_category_switches(self):
        categories.api_category_add(self.c, {}, {"name": "Side Gig", "is_income": "false", "is_transfer": "0"})
        row = self.c.execute(select(Category.is_income, Category.is_transfer).where(Category.name == "Side Gig")).fetchone()
        self.assertEqual(tuple(row), (0, 0))

    def test_settings_switches(self):
        state.api_settings(self.c, {}, {"auto_ai_on_sync": "false", "churn_ai_web": "no", "setup_dismissed": "0"})
        self.assertEqual(db.get_setting(self.c, sk.AUTO_AI_ON_SYNC), "0")
        self.assertEqual(db.get_setting(self.c, sk.CHURN_AI_WEB), "0")
        self.assertIsNone(db.get_setting(self.c, sk.SETUP_DISMISSED))
        state.api_settings(self.c, {}, {"auto_ai_on_sync": True, "setup_dismissed": "true"})
        self.assertEqual(db.get_setting(self.c, sk.AUTO_AI_ON_SYNC), "1")
        self.assertEqual(db.get_setting(self.c, sk.SETUP_DISMISSED), "1")

    def test_assistant_switches(self):
        for off in ("false", "0", "no"):
            self.assertEqual(mcp.api_mcp_writes(self.c, {}, {"allow": off}), {"allow_writes": False})
            self.assertEqual(mcp.api_mcp_categorize(self.c, {}, {"allow": off}), {"allow_categorize": False})
            self.assertEqual(mcp.api_mcp_all(self.c, {}, {"allow": off}), {"allow_all": False})
        self.assertEqual(mcp.api_mcp_all(self.c, {}, {"allow": "true"}), {"allow_all": True})
        self.assertTrue(mcp_access.allow_all(self.c))

    def test_account_switches(self):
        accounts.api_account_update(self.c, {}, {"hidden": "false", "in_forecast": "true", "owed_positive": "0"}, "demo-card")
        row = self.c.execute(select(Account.hidden, Account.in_forecast, Account.owed_positive)
                             .where(Account.id == "demo-card")).fetchone()
        self.assertEqual(tuple(row), (0, 1, 0))

    def test_hiding_a_merchants_logo(self):
        merchants.api_merchant_logo(self.c, {}, {"name": "Fit Club", "hidden": "false"})
        self.assertIsNone(merchants.api_merchant_logo_options(self.c, q(name="Fit Club"), {})["choice"])


class AmountTests(DbCase):
    def setUp(self):
        super().setUp()
        freeze_today(self)
        demo.seed(self.c, TODAY)

    def test_a_transaction_you_add(self):
        good = {"account": "demo-checking", "posted": TODAY.isoformat(), "payee": "Corner Shop"}
        for bad in NOT_AMOUNTS:
            with self.subTest(amount=bad), self.assertRaisesRegex(ApiError, "^Enter the amount as a number$"):
                transactions.api_tx_create(self.c, {}, {**good, "amount": bad})
        out = transactions.api_tx_create(self.c, {}, {**good, "amount": "-12.345"})
        self.assertEqual(self.c.execute(select(Transaction.amount).where(Transaction.id == out["id"])).scalar(), -12.35)
        self.assertEqual(transactions.api_tx_create(self.c, {}, {**good, "amount": 999_999_999.99})["ok"], True)

    def test_budgets_overrides_and_recurring(self):
        budgeted = lambda: self.c.execute(select(Budget.amount).where(Budget.category == "Groceries")).scalar()
        was = budgeted()
        for bad in NOT_AMOUNTS:
            with self.subTest(amount=bad):
                with self.assertRaises(ApiError):
                    budget.api_budget_set(self.c, {}, {"category": "Groceries", "amount": bad})
                with self.assertRaises(ApiError):
                    state.api_override_set(self.c, {}, {"key": "rec:1:2026-01-01", "amount": bad})
                with self.assertRaises(ApiError):
                    recurring.api_recurring_add(self.c, {}, {"name": "Gym", "account_id": "demo-card", "amount": bad,
                                                             "anchor_date": TODAY.isoformat()})
                with self.assertRaises(ApiError):
                    recurring.api_recurring_add(self.c, {}, {"name": "Gym", "account_id": "demo-card", "amount": -45,
                                                             "anchor_date": TODAY.isoformat(), "amount_min": bad})
        self.assertEqual(budgeted(), was)
        self.assertEqual(self.c.execute(select(Override.key)).fetchall(), [])
        budget.api_budget_set(self.c, {}, {"category": "Groceries", "amount": "-450.5"})
        self.assertEqual(budgeted(), 450.5)

    def test_list_filters(self):
        for bad in ("nan", "inf", "abc", "1e10"):
            with self.subTest(v=bad), self.assertRaises(ApiError) as cm:
                transactions.api_transactions(self.c, q(min=bad), {})
            self.assertIn(str(cm.exception), ("Amounts must be numbers", "Amounts must be less than a billion"))
        self.assertGreater(transactions.api_transactions(self.c, q(min="-10", max="1e6"), {})["total"], 0)

    def test_rule_amounts(self):
        from runway import rules
        for bad in ("nan", "inf", "1e9", True):
            with self.subTest(v=bad), self.assertRaisesRegex(rules.RuleError, "^Amounts must"):
                rules.clean(self.c, {"match": "gym", "category": "Shopping", "amount_min": bad})


class DayTests(DbCase):
    def setUp(self):
        super().setUp()
        freeze_today(self)
        demo.seed(self.c, TODAY)

    def test_bad_days(self):
        for bad in ("2026-02-30", "2026-13-01", "soon", "2026-09-30garbage", 20260930, None, [], {}):
            with self.subTest(day=bad):
                with self.assertRaisesRegex(ApiError, "^Enter a date like 2026-09-30$"):
                    transactions.api_tx_create(self.c, {}, {"account": "demo-checking", "posted": bad, "payee": "X", "amount": 1})
                if isinstance(bad, str) and bad:
                    with self.assertRaisesRegex(ApiError, "^Dates must look like 2026-09-30$"):
                        transactions.api_transactions(self.c, q(**{"from": bad}), {})
                    with self.assertRaisesRegex(ApiError, "^Dates must look like 2026-09-01$"):
                        reports.api_report_breakdown(self.c, q(start=bad), {})
                    with self.assertRaisesRegex(ApiError, r"^Amount and a date \(YYYY-MM-DD\) are required$"):
                        recurring.api_recurring_add(self.c, {}, {"name": "Gym", "account_id": "demo-card", "amount": -45,
                                                                 "anchor_date": bad})

    def test_days_are_kept_as_days(self):
        out = transactions.api_tx_create(self.c, {}, {"account": "demo-checking", "posted": " 2026-09-03 ", "payee": "X",
                                                      "amount": 1})
        self.assertEqual(self.c.execute(select(Transaction.posted).where(Transaction.id == out["id"])).scalar(), "2026-09-03")


class WholeNumberTests(DbCase):
    def setUp(self):
        super().setUp()
        freeze_today(self)
        demo.seed(self.c, TODAY)

    def test_query_numbers(self):
        for bad in ("abc", "2.5", "nan", "inf", "1e13", "true"):
            with self.subTest(v=bad):
                with self.assertRaisesRegex(ApiError, "^The limit must be a whole number$"):
                    transactions.api_transactions(self.c, q(limit=bad), {})
                with self.assertRaisesRegex(ApiError, "^The number of months must be a whole number$"):
                    reports.api_report_income(self.c, q(months=bad), {})
                with self.assertRaisesRegex(ApiError, "^The number of days must be a whole number$"):
                    state.api_overview(self.c, q(days=bad), {})
        self.assertEqual(query_int(q(limit="5000"), "limit", 200, 1, 1000), 1000)
        self.assertEqual(query_int(q(limit="-3"), "limit", 200, 1, 1000), 1)
        self.assertEqual(query_int(q(limit="3.0"), "limit", 200, 1, 1000), 3)
        self.assertEqual(query_int({}, "limit", 200, 1, 1000), 200)
        self.assertEqual(len(transactions.api_transactions(self.c, q(limit="2", offset="-5"), {})["items"]), 2)

    def test_the_forecast_length_setting(self):
        for bad in ("abc", [], {}, True, "2.5", None, ""):
            with self.subTest(v=bad), self.assertRaises(ApiError):
                state.api_settings(self.c, {}, {"horizon_days": bad, "auto_ai_on_sync": "1"})
        self.assertIsNone(db.get_setting(self.c, sk.AUTO_AI_ON_SYNC))
        state.api_settings(self.c, {}, {"horizon_days": "1000"})
        self.assertEqual(db.get_setting(self.c, sk.HORIZON_DAYS), "365")
        self.assertEqual(clamped_int(7, "days", 90, 14, 365), 14)

    def test_ids_in_the_address(self):
        for bad in ("abc", "-1", "1.5", " 1", "99999999999999999999", "١٢", ""):
            with self.subTest(id=bad):
                with self.assertRaisesRegex(ApiError, "^Recurring item not found$") as cm:
                    recurring.api_recurring_update(self.c, {}, {}, bad)
                self.assertEqual(cm.exception.status, 404)
                with self.assertRaisesRegex(ApiError, "^Rule not found$"):
                    categories.api_rule_delete(self.c, {}, {}, bad)
                with self.assertRaises(ApiError):
                    recurring.api_recurring_delete(self.c, {}, {}, bad)
                with self.assertRaisesRegex(ApiError, "^Not found$"):
                    mcp.api_mcp_revoke(self.c, {}, {}, bad)
        self.assertEqual(row_id("12"), 12)
        self.assertEqual(row_id(12), 12)
        with self.assertRaises(ApiError):
            row_id(True)
        rid = self.c.execute(select(Recurring.id).limit(1)).scalar()
        self.assertEqual(recurring.api_recurring_delete(self.c, {}, {}, str(rid)), {"ok": True})

    def test_a_recurring_link_by_a_body_id(self):
        tx = self.c.execute(select(Transaction.id).limit(1)).scalar()
        for bad in ({"a": 1}, "abc", 10 ** 30, [1]):
            with self.subTest(v=bad), self.assertRaisesRegex(ApiError, "^Unknown recurring item$"):
                recurring.api_tx_recurring(self.c, {}, {"recurring_id": bad}, tx)


class TextTests(DbCase):
    """A field that should be text, sent as something else, is refused like an empty one."""

    def setUp(self):
        super().setUp()
        freeze_today(self)
        demo.seed(self.c, TODAY)

    def test_not_text(self):
        calls = ((categories.api_category_rename, "name", {"new_name": "X"}), (categories.api_category_move, "name", {}),
                 (categories.api_category_remove, "name", {}), (categories.api_category_move, "parent", {"name": "Gym"}),
                 (budget.api_budget_set, "category", {"amount": 5}), (recurring.api_recurring_dismiss, "key", {}),
                 (transactions.api_ai_apply, "tx_ids", {}), (state.api_settings, "primary_account", {}))
        for bad in ({"a": 1}, ["Groceries"], 10 ** 30, True):
            for fn, field, rest in calls:
                with self.subTest(v=bad, call=fn.__name__, field=field), self.assertRaises(ApiError):
                    fn(self.c, {}, {**rest, field: bad})
        self.assertIsNone(db.get_setting(self.c, sk.PRIMARY_ACCOUNT))

    def test_a_rule_with_a_category_that_isnt_text(self):
        with self.assertRaises(ApiError):
            categories.api_rule_add(self.c, {}, {"match": "gym", "category": ["Shopping"]})
