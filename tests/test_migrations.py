"""The schema comes from Alembic migrations; they must match runway/schema.py, and older databases must upgrade."""
import json
import os
import tempfile
import unittest

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import func, insert, select

from runway import db, schema
from runway.models import (Account, CardStatement, Category, ChurnBenefit, ChurnCard, ChurnRate, ChurnScore, ChurnTask,
                           ChurnWish, DeletedAccount, InvAccount, LoanTerms, ManualStatement, Recurring, Rule, Setting, Transaction)


def drift(path):
    """Differences between the database and schema.py, as Alembic's autogenerate would see them."""
    with open(os.path.join(os.path.dirname(db.__file__), "migrations", "env.py")) as f:
        src = f.read()
    ns = {}
    exec(src[src.index("def same_type"):src.index("def skip_pk")], ns)
    with db.engine(path).connect() as c:
        diffs = compare_metadata(MigrationContext.configure(c, opts={"compare_type": ns["same_type"]}), schema.metadata)
    pk = lambda t, col: col in {c.name for c in schema.metadata.tables[t].primary_key}
    return [d for d in diffs
            if not (isinstance(d, tuple) and d[0] == "remove_table" and d[1].name == "alembic_version")
            and not (isinstance(d, list) and d[0][0] == "modify_nullable" and pk(d[0][2], d[0][3]))]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "m.db")

    def test_fresh_database_matches_schema(self):
        db.init(self.path)
        self.assertEqual(drift(self.path), [])
        with db.engine(self.path).connect() as c:
            head = ScriptDirectory.from_config(db.alembic_config()).get_current_head()
            self.assertEqual(c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar(), head)

    def test_database_from_before_migrations_is_upgraded(self):
        with db.engine(self.path).begin() as c:
            c.exec_driver_sql("CREATE TABLE accounts (id TEXT PRIMARY KEY, name TEXT NOT NULL, display_name TEXT, org TEXT, "
                              "currency TEXT DEFAULT 'USD', balance REAL DEFAULT 0, available REAL, balance_date TEXT, "
                              "kind TEXT DEFAULT 'checking', closing_day INTEGER, due_day INTEGER, pay_from TEXT, "
                              "owed_positive INTEGER DEFAULT 0, in_forecast INTEGER DEFAULT 1, daily_spend INTEGER DEFAULT 0, "
                              "hidden INTEGER DEFAULT 0)")
            c.exec_driver_sql("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
            c.exec_driver_sql("INSERT INTO accounts(id, name, daily_spend) VALUES ('a1', 'Checking', 1)")
        db.init(self.path)
        self.assertEqual(drift(self.path), [])
        with db.session(self.path) as conn:
            row = conn.execute(select(Account.name, Account.owner)).fetchone()
            self.assertEqual((row["name"], row["owner"]), ("Checking", None))
            self.assertGreater(conn.execute(select(func.count()).select_from(Category)).fetchone()[0], 10)
        db.init(self.path)

    def test_database_from_before_migrations_without_accounts_yet_is_upgraded(self):
        with db.engine(self.path).begin() as c:
            c.exec_driver_sql("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        db.init(self.path)
        self.assertEqual(drift(self.path), [])

    def test_database_from_before_migrations_keeps_daily_spend_switched_back_on(self):
        with db.engine(self.path).begin() as c:
            c.exec_driver_sql("CREATE TABLE accounts (id TEXT PRIMARY KEY, name TEXT NOT NULL, daily_spend INTEGER DEFAULT 0)")
            c.exec_driver_sql("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
            c.exec_driver_sql("INSERT INTO accounts(id, name, daily_spend) VALUES ('a1', 'Checking', 1)")
            c.exec_driver_sql("INSERT INTO settings(key, value) VALUES ('migrated_daily_spend_off', '1')")
        from alembic import command
        with db.engine(self.path).begin() as c:
            db._upgrade_legacy(c)
            command.stamp(db.alembic_config(c), db.BASELINE)
            command.upgrade(db.alembic_config(c), "0036")
        with db.engine(self.path).begin() as c:
            self.assertEqual(c.exec_driver_sql("SELECT daily_spend FROM accounts").scalar(), 1)
            self.assertIsNone(c.exec_driver_sql("SELECT key FROM settings WHERE key='migrated_daily_spend_off'").scalar())
        db.init(self.path)
        self.assertEqual(drift(self.path), [])

    def test_0026_switches_daily_spend_off_once(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0025")
        with db.engine(self.path).begin() as c:
            self.assertEqual(c.exec_driver_sql("SELECT value FROM settings WHERE key='migrated_daily_spend_off'").scalar(), "1")
            c.exec_driver_sql("INSERT INTO accounts(id, name, daily_spend) VALUES ('a1', 'Checking', 1), ('a2', 'Savings', 0)")
        for flag, want in (("1", {"a1": 1, "a2": 0}),
                           (None, {"a1": 0, "a2": 0}),
                           ("", {"a1": 0, "a2": 0})):
            with self.subTest(flag=flag):
                with db.engine(self.path).begin() as c:
                    c.exec_driver_sql("UPDATE accounts SET daily_spend=1 WHERE id='a1'")
                    c.exec_driver_sql("DELETE FROM settings WHERE key='migrated_daily_spend_off'")
                    if flag is not None:
                        # raw SQL: on the raw connection, with a parameter either database's driver takes
                        c.execute(sa.text("INSERT INTO settings(key, value) VALUES ('migrated_daily_spend_off', :v)"), {"v": flag})
                    command.upgrade(db.alembic_config(c), "0036")
                with db.engine(self.path).begin() as c:
                    self.assertEqual(dict(c.exec_driver_sql("SELECT id, daily_spend FROM accounts").fetchall()), want)
                    self.assertIsNone(c.exec_driver_sql("SELECT key FROM settings WHERE key='migrated_daily_spend_off'").scalar())
                    command.downgrade(db.alembic_config(c), "0025")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
            self.assertNotIn("daily_spend", {col["name"] for col in sa.inspect(c).get_columns("accounts")})
            self.assertEqual(c.exec_driver_sql("SELECT name FROM accounts WHERE id='a1'").scalar(), "Checking")
        db.init(self.path)
        self.assertEqual(drift(self.path), [])

    def test_plaid_account_counted_twice_is_retired(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0011")
        with db.engine(self.path).begin() as c:
            c.exec_driver_sql("INSERT INTO accounts(id, name, plaid_account_id) VALUES ('sf', 'Freedom', 'p1'), "
                              "('pl:p1', 'Freedom ••9999', 'p1'), ('pl:p2', 'Savings', 'p2')")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        with db.session(self.path) as conn:
            got = {r["id"]: (r["plaid_account_id"], r["hidden"])
                   for r in conn.execute(select(Account.id, Account.plaid_account_id, Account.hidden))}
        self.assertEqual(got, {"sf": ("p1", 0), "pl:p1": (None, 1), "pl:p2": ("p2", 0)})
        self.assertEqual(drift(self.path), [])

    def test_churning_data_survives_0019(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0018")
        with db.engine(self.path).begin() as c:
            self.assertNotIn("portal_only", {col["name"] for col in sa.inspect(c).get_columns("churn_rates")})
            c.exec_driver_sql("INSERT INTO churn_cards(id, owner, issuer, product, opened_on, currency, annual_fee) "
                              "VALUES (1, 'Alex', 'citi', 'AAdvantage Platinum', '2025-11-01', 'airline', 99)")
            c.exec_driver_sql("INSERT INTO churn_rates(card_id, category, multiplier) VALUES (1, 'Travel', 2), (1, 'Gas', 2)")
            c.exec_driver_sql("INSERT INTO churn_currencies(key, name, cents) VALUES ('hotel', 'Hotel points', 0.7)")
            c.exec_driver_sql("INSERT INTO churn_tasks(card_id, due_on, action) VALUES (1, '2026-10-01', 'Call')")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        self.assertEqual(drift(self.path), [])
        with db.session(self.path) as conn:
            rates = [tuple(r) for r in conn.execute(select(ChurnRate.card_id, ChurnRate.category, ChurnRate.multiplier,
                                                           ChurnRate.portal_only)
                                                    .order_by(ChurnRate.category))]
            self.assertEqual(rates, [(1, "Gas", 2.0, 0), (1, "Travel", 2.0, 0)])
            card = conn.execute(select(ChurnCard.currency, ChurnCard.plan, ChurnCard.plan_remind_days,
                                       ChurnCard.hide_upcoming)).fetchone()
            self.assertEqual(tuple(card), ("airline", "undecided", 14, 0))
            self.assertIsNone(conn.execute(select(ChurnTask.snooze_until)).fetchone()[0])
            conn.execute(insert(ChurnRate).values(card_id=1, category="Travel", multiplier=10, portal_only=1))
            conn.execute(insert(ChurnBenefit).values(card_id=1, name="Lounge"))
            conn.execute(insert(ChurnWish).values(owner="Alex", product="Gold", issuer="amex"))
            conn.execute(insert(ChurnScore).values(owner="Alex", as_of="2026-09-01", score=720))
        db.init(self.path)

    def test_downgrade_plans_become_product_changes(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0020")
        with db.engine(self.path).begin() as c:
            c.exec_driver_sql("INSERT INTO churn_cards(id, owner, issuer, product, opened_on, plan) VALUES "
                              "(1, 'Alex', 'chase', 'Sapphire', '2025-01-01', 'downgrade'), "
                              "(2, 'Alex', 'chase', 'Ink', '2025-01-01', 'close')")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        with db.session(self.path) as conn:
            plans = [r[0] for r in conn.execute(select(ChurnCard.plan).order_by(ChurnCard.id))]
        self.assertEqual(plans, ["product_change", "close"])

    def test_0024_adds_the_oauth_tables_and_drops_the_old_key(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0023")
        with db.engine(self.path).begin() as c:
            self.assertNotIn("oauth_grants", sa.inspect(c).get_table_names())
            c.exec_driver_sql("INSERT INTO settings(key, value) VALUES ('mcp_token_hash', 'abc'), ('mcp_token_created', 'x'), "
                              "('mcp_allow_writes', '1')")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        with db.engine(self.path).begin() as c:
            left = dict(c.exec_driver_sql("SELECT key, value FROM settings WHERE key IN "
                                          "('mcp_token_hash', 'mcp_token_created', 'mcp_allow_writes')").fetchall())
            self.assertEqual(left, {"mcp_allow_writes": "1"})
            self.assertLessEqual({"oauth_clients", "oauth_grants", "oauth_codes", "oauth_tokens", "oauth_consents"},
                                 set(sa.inspect(c).get_table_names()))
        self.assertEqual(drift(self.path), [])

    def test_0027_adds_benefit_guests(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0026")
        with db.engine(self.path).begin() as c:
            self.assertNotIn("guests", {col["name"] for col in sa.inspect(c).get_columns("churn_benefits")})
            c.exec_driver_sql("INSERT INTO churn_cards(id, owner, issuer, product, opened_on) VALUES (1, 'Alex', 'amex', 'Gold', '2025-01-01')")
            c.exec_driver_sql("INSERT INTO churn_benefits(id, card_id, name, kind) VALUES (1, 1, 'Lounge access', 'access')")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        with db.session(self.path) as conn:
            self.assertEqual(tuple(conn.execute(select(ChurnBenefit.name, ChurnBenefit.guests)).fetchone()), ("Lounge access", None))
        self.assertEqual(drift(self.path), [])

    def test_0028_adds_loan_terms(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0027")
        with db.engine(self.path).begin() as c:
            self.assertNotIn("loan_terms", sa.inspect(c).get_table_names())
            self.assertNotIn("interest_rate", {col["name"] for col in sa.inspect(c).get_columns("accounts")})
            c.exec_driver_sql("INSERT INTO accounts(id, name, kind, balance) VALUES ('mtg', 'Mortgage', 'loan', -250000)")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        with db.session(self.path) as conn:
            self.assertEqual(tuple(conn.execute(select(Account.kind, Account.balance, Account.interest_rate,
                                                       Account.monthly_payment)).fetchone()), ("loan", -250000, None, None))
            conn.execute(insert(LoanTerms).values(plaid_account_id="p", item_id="i", interest_rate=6.25))
        self.assertEqual(drift(self.path), [])

    def test_0029_adds_a_cards_purchase_apr(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0028")
        with db.engine(self.path).begin() as c:
            self.assertNotIn("purchase_apr", {col["name"] for col in sa.inspect(c).get_columns("card_statements")})
            c.exec_driver_sql("INSERT INTO card_statements(plaid_account_id, item_id, last_statement_balance) VALUES ('p', 'i', 640.5)")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        with db.session(self.path) as conn:
            self.assertEqual(tuple(conn.execute(select(CardStatement.last_statement_balance, CardStatement.purchase_apr)).fetchone()),
                             (640.5, None))
        self.assertEqual(drift(self.path), [])

    def test_0030_adds_manual_statements_and_deleted_accounts(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0029")
        with db.engine(self.path).begin() as c:
            self.assertFalse({"manual_statements", "deleted_accounts"} & set(sa.inspect(c).get_table_names()))
            c.exec_driver_sql("INSERT INTO accounts(id, name, kind) VALUES ('cc', 'Visa', 'credit')")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        with db.session(self.path) as conn:
            conn.execute(insert(ManualStatement).values(account_id="cc", statement_date="2026-09-10", balance=812.4, due_date="2026-10-05"))
            conn.execute(insert(DeletedAccount).values(id="old", name="Old card"))
            row = conn.execute(select(ManualStatement.balance, ManualStatement.minimum_payment, ManualStatement.entered_at)).fetchone()
            self.assertEqual((row["balance"], row["minimum_payment"]), (812.4, None))
            self.assertTrue(row["entered_at"])
            self.assertTrue(conn.execute(select(DeletedAccount.deleted_at)).scalar())
            self.assertEqual(conn.execute(select(Account.name)).scalar(), "Visa")
        self.assertEqual(drift(self.path), [])
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0029")
            self.assertFalse({"manual_statements", "deleted_accounts"} & set(sa.inspect(c).get_table_names()))

    def test_0031_writes_down_the_amount_range_existing_recurring_items_matched(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0030")
        with db.engine(self.path).begin() as c:
            self.assertNotIn("amount_min", {col["name"] for col in sa.inspect(c).get_columns("recurring")})
            c.exec_driver_sql("INSERT INTO accounts(id, name, kind) VALUES ('cc', 'Visa', 'credit'), ('chk', 'Checking', 'checking')")
            c.exec_driver_sql("INSERT INTO recurring(id, name, account_id, amount, frequency, anchor_date, match, amount_mode) VALUES "
                              "(1, 'Prime', 'cc', -14.99, 'monthly', '2026-01-01', 'amazon', 'fixed'), "
                              "(2, 'Electric', 'chk', -120, 'monthly', '2026-01-01', 'comed', 'avg3'), "
                              "(3, 'Paycheck', 'chk', 5000, 'semimonthly', '2026-01-01', 'acme', NULL), "
                              "(4, 'Whatever', 'chk', 0, 'monthly', '2026-01-01', NULL, 'last')")
            c.exec_driver_sql("INSERT INTO transactions(id, account_id, posted, amount, recurring_id) "
                              "VALUES ('cc|1', 'cc', '2026-01-01', -14.99, 1)")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        with db.session(self.path) as conn:
            self.assertEqual([tuple(r) for r in conn.execute(select(Recurring.amount_min, Recurring.amount_max, Recurring.amount_since)
                                                              .order_by(Recurring.id))],
                             [(10.49, 19.49, None), (48.0, 192.0, None), (3500.0, 6500.0, None), (None, None, None)])
            self.assertEqual(tuple(conn.execute(select(Transaction.recurring_id, Transaction.recurring_linked_by)).fetchone()), (1, None))

    def test_0032_shortens_the_banks_payees_and_leaves_yours(self):
        from alembic import command
        from runway import recurring
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0031")
        target, loan = "DIRECT DEBIT TARGET DEBIT CACH TRAN (Cash)", "DIRECT DEBIT LAKESIDE BANK BAWEB PAY (Cash)"
        with db.engine(self.path).begin() as c:
            c.exec_driver_sql("INSERT INTO accounts(id, name, kind) VALUES ('chk', 'Checking', 'checking')")
        with db.session(self.path) as conn:
            txs = [
                {"id": "chk|1", "account_id": "chk", "posted": "2026-09-01", "amount": -35.91, "description": target,
                 "payee": "Target Cach Tran Cash"},
                {"id": "chk|2", "account_id": "chk", "posted": "2026-09-02", "amount": -47.02,
                 "description": "DIRECT DEBIT TARGET DEBIT CPURCHASE (Cash)", "payee": "Target C Cash"},
                {"id": "chk|3", "account_id": "chk", "posted": "2026-09-03", "amount": -12.00, "description": target,
                 "payee": "Groceries Run"},
                {"id": "chk|4", "account_id": "chk", "posted": "2026-08-08", "amount": -512.40, "description": loan,
                 "payee": "Lakeside Bank Baweb Pay Cash", "recurring_id": 1},
                {"id": "chk|5", "account_id": "chk", "posted": "2026-09-05", "amount": -9.00, "description": "ACME ACH",
                 "payee": "Acme Ach"},
                {"id": "chk|pl:6", "account_id": "chk", "posted": "2026-09-06", "amount": -5.00, "description": target,
                 "payee": "Target Cach Tran Cash", "merchant_id": "ent-x"},
                {"id": "chk|7", "account_id": "chk", "posted": "2026-09-07", "amount": -5.00, "description": "APPLE CASH",
                 "payee": "Apple Cash"},
            ]
            conn.execute(insert(Transaction), [{"recurring_id": None, "merchant_id": None, **t} for t in txs])
            conn.execute(insert(Rule).values(match="acme", rename="Acme Ach"))
            items = [
                {"id": 1, "name": "Lakeside Bank Baweb Pay Cash", "account_id": "chk", "amount": -512.40, "frequency": "monthly",
                 "anchor_date": "2026-08-08", "match": "lakeside bank baweb pay cash", "amount_min": 358.68, "amount_max": 666.12},
                {"id": 2, "name": "Car", "account_id": "chk", "amount": -512.40, "frequency": "monthly",
                 "anchor_date": "2026-08-08", "match": "lakeside bank baweb pay cash\nloan"},
                {"id": 3, "name": "Groceries Run", "account_id": "chk", "amount": -12, "frequency": "weekly",
                 "anchor_date": "2026-09-03", "match": "groceries run"},
            ]
            conn.execute(insert(Recurring), [{"amount_min": None, "amount_max": None, **r} for r in items])
            conn.execute(insert(schema.merchant_logos).values(key="target cach tran cash", website="target.com", hidden=0))
            db.set_setting(conn, "recurring_suggestions_dismissed", '["chk|target c cash|weekly", "chk|apple cash|monthly"]')
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        with db.session(self.path) as conn:
            self.assertEqual(dict(conn.execute(select(Transaction.id, Transaction.payee)).fetchall()), {
                "chk|1": "Target", "chk|2": "Target", "chk|3": "Groceries Run", "chk|4": "Lakeside Bank", "chk|5": "Acme Ach",
                "chk|pl:6": "Target Cach Tran Cash", "chk|7": "Apple Cash"})
            self.assertEqual([tuple(r) for r in conn.execute(select(Recurring.name, Recurring.match).order_by(Recurring.id))], [
                ("Lakeside Bank", "lakeside bank baweb pay cash\nlakeside bank"), ("Car", "lakeside bank baweb pay cash\nloan\nlakeside bank"),
                ("Groceries Run", "groceries run")])
            self.assertEqual(dict(conn.execute(select(schema.merchant_logos.c.key, schema.merchant_logos.c.website)).fetchall()),
                             {"target cach tran cash": "target.com", "target": "target.com"})
            self.assertEqual(json.loads(db.get_setting(conn, "recurring_suggestions_dismissed")),
                             ["chk|apple cash|monthly", "chk|target c cash|weekly", "chk|target|weekly"])
            conn.execute(insert(Transaction).values(id="chk|8", account_id="chk", posted="2026-09-08", amount=-512.40,
                                                    description=loan, payee="Lakeside Bank"))
            recurring.auto_match(conn, [1])
            self.assertEqual(conn.execute(select(Transaction.recurring_id).where(Transaction.id == "chk|8")).scalar(), 1)

    def test_0033_clears_hidden_on_every_investment_account(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0032")
        with db.engine(self.path).begin() as c:
            c.exec_driver_sql("INSERT INTO plaid_items(item_id, access_token, institution_name, products) VALUES "
                              "('fid', 't', 'Fidelity Investments', 'investments')")
            c.exec_driver_sql("INSERT INTO inv_accounts(id, item_id, name, hidden, source, institution, account_id) VALUES "
                              "('p1', 'fid', 'Left out by me', 1, 'plaid', NULL, NULL), "
                              "('p2', 'fid', 'Shown', 0, 'plaid', NULL, NULL), "
                              "('sf:dup', 'sf', 'Brokerage (6702)', 1, 'simplefin', 'Fidelity', NULL), "
                              "('sf:401k', 'sf', 'Fidelity 401(k)', 1, 'simplefin', 'Fidelity NetBenefits', NULL)")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        with db.session(self.path) as conn:
            self.assertEqual(dict(conn.execute(select(InvAccount.id, InvAccount.hidden)).fetchall()),
                             {"p1": 0, "p2": 0, "sf:dup": 0, "sf:401k": 0})

    def test_0034_gives_brands_their_names_and_leaves_yours(self):
        from alembic import command
        from runway import recurring, rules
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0034-1")
        mktp, digital = "AMZN Mktp US*2K3AB1", "AMZN Digital*RT4QW2"
        with db.session(self.path) as conn:
            txs = [
                {"id": "chk|1", "account_id": "chk", "posted": "2026-09-01", "amount": -35.91, "description": mktp, "payee": "Amzn Mktp Us"},
                {"id": "chk|2", "account_id": "chk", "posted": "2026-09-02", "amount": -9.99, "description": digital, "payee": "Amzn Digital"},
                {"id": "chk|3", "account_id": "chk", "posted": "2026-09-03", "amount": -12.00, "description": "WM SUPERCENTER #123",
                 "payee": "Wm Supercenter"},
                {"id": "chk|4", "account_id": "chk", "posted": "2026-09-03", "amount": -12.00, "description": mktp,
                 "payee": "Birthday Gift"},
                {"id": "chk|5", "account_id": "chk", "posted": "2026-09-05", "amount": -9.00, "description": "TARGET T-1234",
                 "payee": "Target T-1234"},
                {"id": "chk|pl:6", "account_id": "chk", "posted": "2026-09-06", "amount": -5.00, "description": mktp,
                 "payee": "Amzn Mktp Us", "merchant_id": "ent-x"},
                {"id": "chk|7", "account_id": "chk", "posted": "2026-09-07", "amount": -50.00, "description": "COSTCO GAS #0123",
                 "payee": "Costco Gas"},
                {"id": "chk|8", "account_id": "chk", "posted": "2026-09-08", "amount": -6.50, "description": "UBER *EATS",
                 "payee": "Uber Eats"},
                {"id": "chk|9", "account_id": "chk", "posted": "2026-09-09", "amount": -11.99, "description": "SPOTIFY USA 8777781161",
                 "payee": "Spotify Usa"},
                {"id": "chk|13", "account_id": "chk", "posted": "2026-09-09", "amount": -11.99, "description": "SPOTIFY*USA 877-778-1161",
                 "payee": "Spotify Usa"},
                {"id": "chk|12", "account_id": "chk", "posted": "2026-09-10", "amount": -40.00, "description": "WAL-MART SUPERCENTER #1234",
                 "payee": "Walmart Supercenter"},
            ]
            conn.execute(insert(Transaction), [{"recurring_id": None, "merchant_id": None, **t} for t in txs])
            conn.execute(insert(Rule).values(match="target", rename="Target T-1234"))
            items = [
                {"id": 1, "name": "Amzn Digital", "account_id": "chk", "amount": -9.99, "frequency": "monthly",
                 "anchor_date": "2026-09-02", "match": None},
                {"id": 2, "name": "Music", "account_id": "chk", "amount": -11.99, "frequency": "monthly",
                 "anchor_date": "2026-09-09", "match": "spotify usa"},
                {"id": 3, "name": "Birthday Gift", "account_id": "chk", "amount": -12, "frequency": "yearly",
                 "anchor_date": "2026-09-03", "match": "birthday gift"},
            ]
            conn.execute(insert(Recurring), [{"amount_min": None, "amount_max": None, **r} for r in items])
            conn.execute(insert(schema.merchant_logos).values(key="amzn mktp us", website="amazon.com", hidden=1))
            conn.execute(insert(schema.merchant_logos).values(key="wm supercenter", website="walmart.com", hidden=0))
            conn.execute(insert(schema.merchant_logos).values(key="walmart", website="example.com", hidden=0))
            conn.execute(insert(Rule).values(match="amzn digital", match_mode="exact", category="Subscriptions"))
            db.set_setting(conn, "recurring_suggestions_dismissed", '["chk|amzn digital|monthly", "chk|costco gas|weekly"]')
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "0034")
        with db.session(self.path) as conn:
            self.assertEqual(dict(conn.execute(select(Transaction.id, Transaction.payee)).fetchall()), {
                "chk|1": "Amazon", "chk|2": "Amazon", "chk|3": "Walmart", "chk|4": "Birthday Gift", "chk|5": "Target T-1234",
                "chk|pl:6": "Amzn Mktp Us", "chk|7": "Costco Gas", "chk|8": "Uber Eats", "chk|9": "Spotify",
                "chk|12": "Walmart Supercenter", "chk|13": "Spotify Usa"})
            self.assertEqual([tuple(r) for r in conn.execute(select(Recurring.name, Recurring.match).order_by(Recurring.id))], [
                ("Amazon", "amzn digital"), ("Music", "spotify usa"), ("Birthday Gift", "birthday gift")])
            logos = schema.merchant_logos.c
            self.assertEqual({k: (w, h) for k, w, h in conn.execute(select(logos.key, logos.website, logos.hidden)).fetchall()},
                             {"amzn mktp us": ("amazon.com", 1), "amazon": ("amazon.com", 1), "wm supercenter": ("walmart.com", 0),
                              "walmart": ("example.com", 0)})
            self.assertEqual(json.loads(db.get_setting(conn, "recurring_suggestions_dismissed")),
                             ["chk|amazon|monthly", "chk|amzn digital|monthly", "chk|costco gas|weekly"])
            conn.execute(insert(Transaction), [
                {"id": "chk|10", "account_id": "chk", "posted": "2026-10-02", "amount": -9.99, "description": digital, "payee": "Amazon"},
                {"id": "chk|11", "account_id": "chk", "posted": "2026-10-02", "amount": -9.99, "description": "AMAZON.COM*ZZ1",
                 "payee": "Amazon"}])
            recurring.auto_match(conn, [1])
            self.assertEqual(dict(conn.execute(select(Transaction.id, Transaction.recurring_id).where(
                Transaction.id.in_(["chk|10", "chk|11"]))).fetchall()), {"chk|10": 1, "chk|11": None})
            rule = {"match": "amzn digital", "match_mode": "exact"}
            self.assertTrue(rules.matches(rule, {"payee": "Amazon", "description": digital, "amount": -9.99}))
            self.assertFalse(rules.matches(rule, {"payee": "Amazon", "description": "AMAZON.COM*ZZ1", "amount": -9.99}))

    def test_0036_moves_each_budgets_card_to_its_category(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0035")
        with db.engine(self.path).begin() as c:
            self.assertNotIn("pay_with", {x["name"] for x in sa.inspect(c).get_columns("categories")})
            c.exec_driver_sql("INSERT INTO accounts(id, name, kind) VALUES ('cc', 'Visa', 'credit'), ('chk', 'Checking', 'checking')")
            c.exec_driver_sql("INSERT INTO categories(name, parent) VALUES ('Crafts', NULL), ('Pottery', 'Crafts'), ('Aquarium', NULL)")
            c.exec_driver_sql("INSERT INTO budgets(category, amount, pay_with) VALUES ('Crafts', 300, 'cc'), ('Pottery', 80, 'chk'), "
                              "('Aquarium', 50, NULL), ('Gone', 20, 'cc')")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        self.assertEqual(drift(self.path), [])
        with db.session(self.path) as conn:
            paid = dict(conn.execute(select(Category.name, Category.pay_with)).fetchall())
            self.assertEqual({k: paid[k] for k in ("Crafts", "Pottery", "Aquarium")}, {"Crafts": "cc", "Pottery": "chk", "Aquarium": None})
            self.assertEqual(sum(1 for v in paid.values() if v), 2)
            self.assertEqual(conn.execute(select(func.count()).select_from(schema.budgets)).scalar(), 4)
            conn.execute(sa.update(Category).where(Category.name == "Aquarium").values(pay_with="chk"))
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0035")
            self.assertEqual(dict(c.exec_driver_sql("SELECT category, pay_with FROM budgets").fetchall()),
                             {"Crafts": "cc", "Pottery": "chk", "Aquarium": "chk", "Gone": None})
            self.assertNotIn("pay_with", {x["name"] for x in sa.inspect(c).get_columns("categories")})

    def test_0038_drops_what_nothing_reads(self):
        from alembic import command
        db.init(self.path)
        instr = "SELECT count(*) FROM pg_proc WHERE proname = 'instr' AND pronamespace = current_schema()::regnamespace"
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0037")
        with db.engine(self.path).begin() as c:
            if c.dialect.name == "postgresql":
                self.assertEqual(c.exec_driver_sql(instr).scalar(), 1)
            c.exec_driver_sql("INSERT INTO accounts(id, name, daily_spend) VALUES ('chk', 'Checking', 1)")
            c.exec_driver_sql("INSERT INTO churn_cards(id, owner, issuer, product, opened_on, fee_month) "
                              "VALUES (1, 'Alex', 'chase', 'Sapphire', '2025-03-10', 11)")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        with db.engine(self.path).begin() as c:
            self.assertNotIn("daily_spend", {col["name"] for col in sa.inspect(c).get_columns("accounts")})
            self.assertNotIn("fee_month", {col["name"] for col in sa.inspect(c).get_columns("churn_cards")})
            self.assertEqual(c.exec_driver_sql("SELECT name FROM accounts").scalar(), "Checking")
            self.assertEqual(c.exec_driver_sql("SELECT opened_on FROM churn_cards").scalar(), "2025-03-10")
            if c.dialect.name == "postgresql":
                self.assertEqual(c.exec_driver_sql(instr).scalar(), 0)
            self.assertEqual(c.execute(select(db.instr("hello", "ll"))).scalar(), 3)
        self.assertEqual(drift(self.path), [])

    def test_0039_moves_categories_nested_too_deep_up(self):
        from alembic import command
        from runway import categories
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0038")
            c.exec_driver_sql("INSERT INTO categories(name, parent) VALUES ('Food', NULL), ('Eating Out', 'Food'), "
                              "('Burgers', 'Eating Out'), ('Sliders', 'Burgers'), ('Lost', 'Gone'), ('Lost Too', 'Lost'), "
                              "('Under Lost', 'Lost Too'), ('Loop A', 'Loop B'), ('Loop B', 'Loop A')")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        with db.session(self.path) as conn:
            parents = dict(conn.execute(select(Category.name, Category.parent)).fetchall())
            self.assertEqual({k: parents[k] for k in ("Food", "Eating Out", "Burgers", "Sliders", "Lost", "Lost Too", "Under Lost",
                                                      "Loop A", "Loop B")},
                             {"Food": None, "Eating Out": "Food", "Burgers": "Food", "Sliders": "Food",
                              "Lost": "Gone", "Lost Too": "Lost", "Under Lost": "Lost",
                              "Loop A": "Loop B", "Loop B": "Loop A"})
            self.assertTrue(all(c["depth"] <= 1 for c in categories.all_categories(conn) if not c["name"].startswith("Loop")))

    def test_0040_keeps_a_copy_then_removes_what_refers_to_nothing(self):
        import contextlib
        import io
        import stat
        from unittest import mock
        from alembic import command
        from runway import backup
        data = tempfile.mkdtemp()
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0039")
        with db.engine(self.path).begin() as c:
            for sql in (
                "INSERT INTO accounts(id, name, kind, pay_from) VALUES ('chk', 'Checking', 'checking', NULL), "
                "('cc', 'Visa', 'credit', 'gone'), ('pl:p1', 'Brokerage', 'investment', NULL)",
                "INSERT INTO transactions(id, account_id, posted, amount, recurring_id) VALUES ('chk|1', 'chk', '2026-09-01', -5, 7), "
                "('gone|1', 'gone', '2026-09-01', -9.99, NULL), ('gone|2', 'gone', '2026-09-02', -1, NULL)",
                "INSERT INTO tx_splits(tx_id, amount, category) VALUES ('gone|1', -5, 'Groceries'), ('chk|1', -5, 'Groceries')",
                "INSERT INTO retail_orders(id, retailer, order_number) VALUES ('amazon:1', 'amazon', '1')",
                "INSERT INTO retail_charges(id, order_id, date, amount, tx_id, match_source, applied) VALUES "
                "('amazon:1:a', 'amazon:1', '2026-09-01', -9.99, 'gone|1', 'auto', '{}'), "
                "('amazon:2:a', 'amazon:2', '2026-09-01', -3, NULL, NULL, NULL)",
                "INSERT INTO recurring(id, name, account_id, amount, frequency, anchor_date) VALUES "
                "(7, 'Streaming', 'gone', -9.99, 'monthly', '2026-09-01'), (8, 'Rent', 'chk', -1500, 'monthly', '2026-09-01')",
                "INSERT INTO overrides(key, amount) VALUES ('rec:7:2026-10-01', -12), ('rec:8:2026-10-01', -1400), "
                "('card:gone:2026-10-05', -50), ('stmt:pl:gone:2026-09-10', 60), ('card:cc:2026-10-05', -40), "
                "('card:pl:p1:2026-10-05', -1)",
                "INSERT INTO recurring_dismissed(key) VALUES ('rec:7:2026-08-01'), ('rec:8:2026-08-01')",
                "INSERT INTO rules(match, category, account_id) VALUES ('cafe', 'Coffee & Snacks', 'gone'), ('rent', 'Rent', 'chk')",
                "INSERT INTO manual_statements(account_id, statement_date, balance, due_date) VALUES "
                "('gone', '2026-09-10', 10, '2026-10-05'), ('cc', '2026-09-10', 20, '2026-10-05')",
                "INSERT INTO categories(name, pay_with) VALUES ('Crafts', 'gone'), ('Pottery', 'cc')",
                "INSERT INTO assets(id, name, kind, loan_account_id) VALUES (1, 'Home', 'home', 'gone')",
                "INSERT INTO asset_values(asset_id, date, value) VALUES (1, '2026-09-01', 400000), (99, '2026-09-01', 1)",
                "INSERT INTO churn_cards(id, owner, issuer, product, opened_on, account_id, changed_from) VALUES "
                "(1, 'Alex', 'chase', 'Sapphire', '2025-01-01', 'gone', 999), (2, 'Alex', 'chase', 'Freedom', '2025-01-01', 'cc', NULL)",
                "INSERT INTO churn_rates(card_id, category, multiplier) VALUES (1, 'Travel', 2), (999, 'Travel', 3)",
                "INSERT INTO churn_benefits(id, card_id, name) VALUES (5, 999, 'Lounge'), (6, 1, 'Credit')",
                "INSERT INTO churn_benefit_uses(benefit_id, period_start, used_on) VALUES (5, '2026-01-01', '2026-01-02'), "
                "(6, '2026-01-01', '2026-01-02')",
                "INSERT INTO equity_grants(id, company_id, kind, quantity) VALUES ('g1', 'nobody', 'rsu', 10)",
                "INSERT INTO settings(key, value) VALUES ('card_pay_mode:gone', 'fixed'), ('card_apr:pl:gone', '20'), "
                "('card_apr:cc', '24.99')",
            ):
                c.exec_driver_sql(sql)
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"RUNWAY_DATA": data}), contextlib.redirect_stdout(out), \
                db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        self.assertEqual(drift(self.path), [])
        log = out.getvalue()
        self.assertIn("removed 2 rows from transactions", log)
        self.assertIn("cleared what 1 row in transactions referred to", log)
        self.assertNotRegex(log, r"gone|9\.99|Streaming|amazon|cafe")
        where = data if db.using_postgres() else os.path.dirname(self.path)
        copies = [f for f in os.listdir(where) if f.startswith("runway-before-migration-0040-")]
        self.assertEqual(len(copies), 1)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(where, copies[0])).st_mode), 0o600)
        with open(os.path.join(where, copies[0]), "rb") as f:
            kept = backup.load(f.read())
        self.assertEqual(kept["revision"], "0039")
        self.assertEqual(len(kept["tables"]["transactions"]["rows"]), 3)
        with db.engine(self.path).begin() as c:
            # raw SQL: what the migration left, on the raw connection (text() so Postgres takes LIKE's %)
            q = lambda sql: sorted(tuple(r) for r in c.execute(sa.text(sql)).fetchall())
            self.assertEqual(q("SELECT id, recurring_id FROM transactions"), [("chk|1", None)])
            self.assertEqual(q("SELECT tx_id FROM tx_splits"), [("chk|1",)])
            self.assertEqual(q("SELECT id, tx_id, match_source, applied FROM retail_charges"), [("amazon:1:a", None, None, None)])
            self.assertEqual(q("SELECT id FROM recurring"), [(8,)])
            self.assertEqual(q("SELECT key FROM overrides"), [("card:cc:2026-10-05",), ("card:pl:p1:2026-10-05",), ("rec:8:2026-10-01",)])
            self.assertEqual(q("SELECT key FROM recurring_dismissed"), [("rec:8:2026-08-01",)])
            self.assertEqual(q("SELECT account_id FROM rules"), [("chk",)])
            self.assertEqual(q("SELECT account_id FROM manual_statements"), [("cc",)])
            self.assertEqual(q("SELECT name, pay_with FROM categories WHERE name IN ('Crafts', 'Pottery')"), [("Crafts", None), ("Pottery", "cc")])
            self.assertEqual(q("SELECT id, pay_from FROM accounts WHERE id = 'cc'"), [("cc", None)])
            self.assertEqual(q("SELECT name, loan_account_id FROM assets"), [("Home", None)])
            self.assertEqual(q("SELECT asset_id FROM asset_values"), [(1,)])
            self.assertEqual(q("SELECT id, account_id, changed_from FROM churn_cards"), [(1, None, None), (2, "cc", None)])
            self.assertEqual(q("SELECT card_id FROM churn_rates"), [(1,)])
            self.assertEqual(q("SELECT id FROM churn_benefits"), [(6,)])
            self.assertEqual(q("SELECT benefit_id FROM churn_benefit_uses"), [(6,)])
            self.assertEqual(q("SELECT id FROM equity_grants"), [])
            self.assertEqual(q("SELECT key FROM settings WHERE key LIKE 'card_%'"), [("card_apr:cc",)])
        with db.session(self.path) as conn, self.assertRaises(sa.exc.IntegrityError):
            conn.execute(insert(Transaction).values(id="x|1", account_id="nobody", posted="2026-09-01", amount=-1))
        with db.session(self.path) as conn:
            conn.execute(sa.delete(Account).where(Account.id == "chk"))
            self.assertEqual(conn.execute(select(func.count()).select_from(Transaction)).scalar(), 0)
            self.assertEqual(conn.execute(select(func.count()).select_from(Rule)).scalar(), 0)
            self.assertEqual(conn.execute(select(func.count()).select_from(Recurring)).scalar(), 0)
            conn.execute(sa.delete(Account).where(Account.id == "cc"))
            self.assertIsNone(conn.execute(select(Category.pay_with).where(Category.name == "Pottery")).scalar())
            self.assertIsNone(conn.execute(select(ChurnCard.account_id).where(ChurnCard.id == 2)).scalar())
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0039")
            self.assertEqual([fk for t in ("transactions", "churn_cards") for fk in sa.inspect(c).get_foreign_keys(t)], [])

    def test_0040_saves_no_copy_when_nothing_refers_to_nothing(self):
        from unittest import mock
        from alembic import command
        data = tempfile.mkdtemp()
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0039")
            c.exec_driver_sql("INSERT INTO accounts(id, name) VALUES ('chk', 'Checking')")
            c.exec_driver_sql("INSERT INTO transactions(id, account_id, posted, amount) VALUES ('chk|1', 'chk', '2026-09-01', -5)")
        with mock.patch.dict(os.environ, {"RUNWAY_DATA": data}), db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        for where in (data, os.path.dirname(self.path)):
            self.assertFalse([f for f in os.listdir(where) if f.startswith("runway-before-migration")])
        with db.session(self.path) as conn:
            self.assertEqual(conn.execute(select(Transaction.id)).scalars(), ["chk|1"])

    @unittest.skipIf(db.using_postgres(), "SQLite only: it makes a table again to change it")
    def test_migrating_doesnt_cascade_on_sqlite(self):
        from alembic import command
        db.init(self.path)
        with db.session(self.path) as conn:
            conn.execute(insert(Account).values(id="chk", name="Checking"))
            conn.execute(insert(Transaction).values(id="chk|1", account_id="chk", posted="2026-09-01", amount=-5))
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0036")
        db.migrate(self.path)
        with db.session(self.path) as conn:
            self.assertEqual(conn.execute(select(Transaction.id)).scalars(), ["chk|1"])
            self.assertEqual(conn.sa.exec_driver_sql("PRAGMA foreign_keys").scalar(), 1)

    def test_0037_gives_each_side_of_a_plaid_connection_its_own_error_and_time(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0036")
        with db.engine(self.path).begin() as c:
            self.assertNotIn("inv_error", {x["name"] for x in sa.inspect(c).get_columns("plaid_items")})
            c.exec_driver_sql("INSERT INTO plaid_items(item_id, access_token, products, last_sync, error) VALUES "
                              "('inv', 't', 'investments', '2026-09-01 10:00:00', 'ITEM_LOGIN_REQUIRED'), "
                              "('old', 't', NULL, '2026-09-02 10:00:00', NULL), "
                              "('bank', 't', 'liabilities,transactions', '2026-09-03 10:00:00', 'X'), "
                              "('cards', 't', 'liabilities', NULL, 'Y'), "
                              "('mix', 't', 'investments,transactions', '2026-09-04 10:00:00', 'Z')")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        self.assertEqual(drift(self.path), [])
        cols = "item_id, last_sync, error, inv_last_sync, inv_error"
        with db.engine(self.path).begin() as c:
            got = {r[0]: tuple(r[1:]) for r in c.exec_driver_sql(f"SELECT {cols} FROM plaid_items")}
        self.assertEqual(got, {"inv": (None, None, "2026-09-01 10:00:00", "ITEM_LOGIN_REQUIRED"),
                               "old": (None, None, "2026-09-02 10:00:00", None),
                               "bank": ("2026-09-03 10:00:00", "X", None, None),
                               "cards": (None, "Y", None, None),
                               "mix": ("2026-09-04 10:00:00", "Z", None, None)})
        with db.engine(self.path).begin() as c:
            c.exec_driver_sql("UPDATE plaid_items SET inv_error = 'W', inv_last_sync = '2026-09-05 10:00:00' WHERE item_id = 'mix'")
            command.downgrade(db.alembic_config(c), "0036")
            got = {r[0]: tuple(r[1:]) for r in c.exec_driver_sql("SELECT item_id, last_sync, error FROM plaid_items")}
        self.assertEqual(got, {"inv": ("2026-09-01 10:00:00", "ITEM_LOGIN_REQUIRED"), "old": ("2026-09-02 10:00:00", None),
                               "bank": ("2026-09-03 10:00:00", "X"), "cards": (None, "Y"), "mix": ("2026-09-04 10:00:00", "Z")})

    @unittest.skipUnless(db.using_postgres(), "Postgres only: SQLite has one writer at a time anyway")
    def test_processes_starting_together_take_turns_migrating(self):
        import threading
        errors = []

        def start(path):
            try:
                db.migrate(path)
            except Exception as e:
                errors.append(e)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "together.db")
            threads = [threading.Thread(target=start, args=(path,)) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(60)
            self.assertFalse([t for t in threads if t.is_alive()], "the migrations are stuck waiting on each other")
            self.assertEqual(errors, [])
            self.assertEqual(drift(path), [])

    @unittest.skipUnless(db.using_postgres(), "Postgres only: the lock is Postgres's")
    def test_a_tests_own_schema_migrates_without_waiting_for_another(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            held, free = os.path.join(tmp, "held.db"), os.path.join(tmp, "free.db")
            db.migrate(held)
            with db.engine(held).connect() as other:
                other.exec_driver_sql(f"SELECT pg_advisory_xact_lock({db.SCHEMA_LOCK}, hashtext(current_schema()))")
                waiting = threading.Thread(target=db.migrate, args=(held,))
                waiting.start()
                db.migrate(free)
                self.assertEqual(drift(free), [])
                waiting.join(1)
                self.assertTrue(waiting.is_alive())
                other.rollback()
            waiting.join(30)
            self.assertFalse(waiting.is_alive())

    def test_connection_wrapper(self):
        db.init(self.path)
        with db.session(self.path) as conn:
            r = conn.execute(insert(Rule).values(match="50% off", category="Shopping"))
            self.assertTrue(r.lastrowid)
            row = conn.execute(select(Rule.id, Rule.match).where(Rule.match.like("50%"))).fetchone()
            self.assertEqual((row[0], row["match"], dict(row)["id"]), (r.lastrowid, "50% off", r.lastrowid))
            self.assertEqual(conn.execute(select(db.instr("hello", "ll"))).fetchone()[0], 3)
            conn.execute(insert(Setting).values(key="n", value=5))
            self.assertEqual(db.get_setting(conn, "n"), "5")


if __name__ == "__main__":
    unittest.main()
