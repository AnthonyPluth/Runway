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
        # An early Runway database: a couple of tables, missing columns added later, no alembic_version.
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
            row = conn.execute(select(Account.name, Account.owner, Account.daily_spend)).fetchone()
            self.assertEqual((row["name"], row["owner"], row["daily_spend"]), ("Checking", None, 0))
            self.assertGreater(conn.execute(select(func.count()).select_from(Category)).fetchone()[0], 10)
        db.init(self.path)   # starting again changes nothing

    def test_database_from_before_migrations_keeps_daily_spend_switched_back_on(self):
        # One that had v4's one-time switch-off (and noted it) before migrations came in: it isn't done again.
        with db.engine(self.path).begin() as c:
            c.exec_driver_sql("CREATE TABLE accounts (id TEXT PRIMARY KEY, name TEXT NOT NULL, daily_spend INTEGER DEFAULT 0)")
            c.exec_driver_sql("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
            c.exec_driver_sql("INSERT INTO accounts(id, name, daily_spend) VALUES ('a1', 'Checking', 1)")
            c.exec_driver_sql("INSERT INTO settings(key, value) VALUES ('migrated_daily_spend_off', '1')")
        db.init(self.path)
        self.assertEqual(drift(self.path), [])
        with db.session(self.path) as conn:
            self.assertEqual(conn.execute(select(Account.daily_spend)).fetchone()[0], 1)
            self.assertIsNone(conn.execute(select(Setting.key)
                                           .where(Setting.key == "migrated_daily_spend_off")).fetchone())

    def test_0026_switches_daily_spend_off_once(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0025")
        with db.engine(self.path).begin() as c:
            # Going back notes the switch-off as done, so the older version doesn't do it again at start-up
            self.assertEqual(c.exec_driver_sql("SELECT value FROM settings WHERE key='migrated_daily_spend_off'").scalar(), "1")
            c.exec_driver_sql("INSERT INTO accounts(id, name, daily_spend) VALUES ('a1', 'Checking', 1), ('a2', 'Savings', 0)")
        for flag, want in (("1", {"a1": 1, "a2": 0}),     # done before (and a1 switched back on since): left alone
                           (None, {"a1": 0, "a2": 0}),    # never done: switched off
                           ("", {"a1": 0, "a2": 0})):     # an empty note reads as not done, as db.get_setting has it
            with self.subTest(flag=flag):
                with db.engine(self.path).begin() as c:
                    c.exec_driver_sql("UPDATE accounts SET daily_spend=1 WHERE id='a1'")
                    c.exec_driver_sql("DELETE FROM settings WHERE key='migrated_daily_spend_off'")
                    if flag is not None:
                        # raw SQL: on the raw connection, with a parameter either database's driver takes
                        c.execute(sa.text("INSERT INTO settings(key, value) VALUES ('migrated_daily_spend_off', :v)"), {"v": flag})
                    command.upgrade(db.alembic_config(c), "head")
                with db.engine(self.path).begin() as c:
                    self.assertEqual(dict(c.exec_driver_sql("SELECT id, daily_spend FROM accounts").fetchall()), want)
                    self.assertIsNone(c.exec_driver_sql("SELECT key FROM settings WHERE key='migrated_daily_spend_off'").scalar())
                    command.downgrade(db.alembic_config(c), "0025")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
            c.exec_driver_sql("UPDATE accounts SET daily_spend=1 WHERE id='a1'")
        db.init(self.path)   # starting again leaves an account switched back on alone
        with db.session(self.path) as conn:
            self.assertEqual(conn.execute(select(Account.daily_spend).where(Account.id == "a1")).fetchone()[0], 1)
        self.assertEqual(drift(self.path), [])

    def test_plaid_account_counted_twice_is_retired(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            c.exec_driver_sql("DROP INDEX accounts_plaid_account")
            c.exec_driver_sql("UPDATE alembic_version SET version_num='0011'")
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
        # Rates, cards, tasks and point values from before plans, benefits and portal-only rates came in.
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
            # A portal rate beside the normal one in the same category is allowed now
            conn.execute(insert(ChurnRate).values(card_id=1, category="Travel", multiplier=10, portal_only=1))
            conn.execute(insert(ChurnBenefit).values(card_id=1, name="Lounge"))
            conn.execute(insert(ChurnWish).values(owner="Alex", product="Gold", issuer="amex"))
            conn.execute(insert(ChurnScore).values(owner="Alex", as_of="2026-09-01", score=720))
        db.init(self.path)   # starting again changes nothing

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
            self.assertEqual(left, {"mcp_allow_writes": "1"})                     # the old key is gone; the switch stays
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
        with db.engine(self.path).begin() as c:   # and back down
            command.downgrade(db.alembic_config(c), "0029")
            self.assertFalse({"manual_statements", "deleted_accounts"} & set(sa.inspect(c).get_table_names()))

    def test_0031_writes_down_the_amount_range_existing_recurring_items_matched(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0030")
        with db.engine(self.path).begin() as c:
            self.assertNotIn("amount_min", {col["name"] for col in sa.inspect(c).get_columns("recurring")})
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
        target, loan = "DIRECT DEBIT TARGET DEBIT CACH TRAN (Cash)", "DIRECT DEBIT FIFTH THIRD BAWEB PAY (Cash)"
        with db.session(self.path) as conn:
            txs = [
                {"id": "chk|1", "account_id": "chk", "posted": "2026-09-01", "amount": -35.91, "description": target,
                 "payee": "Target Cach Tran Cash"},
                {"id": "chk|2", "account_id": "chk", "posted": "2026-09-02", "amount": -47.02,
                 "description": "DIRECT DEBIT TARGET DEBIT CPURCHASE (Cash)", "payee": "Target C Cash"},
                {"id": "chk|3", "account_id": "chk", "posted": "2026-09-03", "amount": -12.00, "description": target,
                 "payee": "Groceries Run"},                                   # a name you gave it
                {"id": "chk|4", "account_id": "chk", "posted": "2026-08-08", "amount": -698.38, "description": loan,
                 "payee": "Fifth Third Baweb Pay Cash", "recurring_id": 1},
                {"id": "chk|5", "account_id": "chk", "posted": "2026-09-05", "amount": -9.00, "description": "ACME ACH",
                 "payee": "Acme Ach"},                                        # a rule renames to it
                {"id": "chk|pl:6", "account_id": "chk", "posted": "2026-09-06", "amount": -5.00, "description": target,
                 "payee": "Target Cach Tran Cash", "merchant_id": "ent-x"},  # Plaid named the merchant
                {"id": "chk|7", "account_id": "chk", "posted": "2026-09-07", "amount": -5.00, "description": "APPLE CASH",
                 "payee": "Apple Cash"},
            ]
            conn.execute(insert(Transaction), [{"recurring_id": None, "merchant_id": None, **t} for t in txs])
            conn.execute(insert(Rule).values(match="acme", rename="Acme Ach"))
            items = [
                {"id": 1, "name": "Fifth Third Baweb Pay Cash", "account_id": "chk", "amount": -698.38, "frequency": "monthly",
                 "anchor_date": "2026-08-08", "match": "fifth third baweb pay cash", "amount_min": 488.87, "amount_max": 907.89},
                {"id": 2, "name": "Car", "account_id": "chk", "amount": -698.38, "frequency": "monthly",
                 "anchor_date": "2026-08-08", "match": "fifth third baweb pay cash\nloan"},
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
                "chk|1": "Target", "chk|2": "Target", "chk|3": "Groceries Run", "chk|4": "Fifth Third", "chk|5": "Acme Ach",
                "chk|pl:6": "Target Cach Tran Cash", "chk|7": "Apple Cash"})
            self.assertEqual([tuple(r) for r in conn.execute(select(Recurring.name, Recurring.match).order_by(Recurring.id))], [
                ("Fifth Third", "fifth third baweb pay cash\nfifth third"), ("Car", "fifth third baweb pay cash\nloan\nfifth third"),
                ("Groceries Run", "groceries run")])
            self.assertEqual(dict(conn.execute(select(schema.merchant_logos.c.key, schema.merchant_logos.c.website)).fetchall()),
                             {"target cach tran cash": "target.com", "target": "target.com"})
            self.assertEqual(json.loads(db.get_setting(conn, "recurring_suggestions_dismissed")),
                             ["chk|apple cash|monthly", "chk|target c cash|weekly", "chk|target|weekly"])
            # The next loan payment, synced with the shorter name, still finds its recurring item.
            conn.execute(insert(Transaction).values(id="chk|8", account_id="chk", posted="2026-09-08", amount=-698.38,
                                                    description=loan, payee="Fifth Third"))
            recurring.auto_match(conn, [1])
            self.assertEqual(conn.execute(select(Transaction.recurring_id).where(Transaction.id == "chk|8")).scalar(), 1)

    def test_0033_shows_accounts_left_out_on_investments_again_but_not_duplicates(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            command.downgrade(db.alembic_config(c), "0032")
        with db.engine(self.path).begin() as c:
            c.exec_driver_sql("INSERT INTO plaid_items(item_id, access_token, institution_name, products) VALUES "
                              "('et', 't', 'E*TRADE from Morgan Stanley', 'investments')")
            c.exec_driver_sql("INSERT INTO inv_accounts(id, item_id, name, hidden, source, institution, account_id) VALUES "
                              "('p1', 'et', 'Left out by me', 1, 'plaid', NULL, NULL), "
                              "('p2', 'et', 'Shown', 0, 'plaid', NULL, NULL), "
                              "('sf:dup', 'sf', 'Brokerage (6702)', 1, 'simplefin', 'E*Trade', NULL), "
                              "('sf:mine', 'sf', 'Left out by me too', 1, 'simplefin', 'Robinhood', NULL)")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        with db.session(self.path) as conn:
            self.assertEqual(dict(conn.execute(select(InvAccount.id, InvAccount.hidden)).fetchall()),
                             {"p1": 0, "p2": 0, "sf:dup": 1, "sf:mine": 0})   # the SimpleFIN copy of a Plaid account stays out

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
                 "payee": "Birthday Gift"},                                   # a name you gave it
                {"id": "chk|5", "account_id": "chk", "posted": "2026-09-05", "amount": -9.00, "description": "TARGET T-1234",
                 "payee": "Target T-1234"},                                   # a rule renames to it
                {"id": "chk|pl:6", "account_id": "chk", "posted": "2026-09-06", "amount": -5.00, "description": mktp,
                 "payee": "Amzn Mktp Us", "merchant_id": "ent-x"},           # Plaid named the merchant
                {"id": "chk|7", "account_id": "chk", "posted": "2026-09-07", "amount": -50.00, "description": "COSTCO GAS #0123",
                 "payee": "Costco Gas"},                                      # not a brand's name
                {"id": "chk|8", "account_id": "chk", "posted": "2026-09-08", "amount": -6.50, "description": "UBER *EATS",
                 "payee": "Uber Eats"},                                       # the brand's name already
                {"id": "chk|9", "account_id": "chk", "posted": "2026-09-09", "amount": -11.99, "description": "SPOTIFY*USA 877-778-1161",
                 "payee": "Spotify Usa"},                                     # the bank's words, not its run of text
                {"id": "chk|12", "account_id": "chk", "posted": "2026-09-10", "amount": -40.00, "description": "WAL-MART SUPERCENTER #1234",
                 "payee": "Walmart Supercenter"},                             # the provider's own name, not the bank's text
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
            conn.execute(insert(schema.merchant_logos).values(key="walmart", website="example.com", hidden=0))   # chosen already
            conn.execute(insert(Rule).values(match="amzn digital", match_mode="exact", category="Subscriptions"))
            db.set_setting(conn, "recurring_suggestions_dismissed", '["chk|amzn digital|monthly", "chk|costco gas|weekly"]')
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "0034")
        with db.session(self.path) as conn:
            self.assertEqual(dict(conn.execute(select(Transaction.id, Transaction.payee)).fetchall()), {
                "chk|1": "Amazon", "chk|2": "Amazon", "chk|3": "Walmart", "chk|4": "Birthday Gift", "chk|5": "Target T-1234",
                "chk|pl:6": "Amzn Mktp Us", "chk|7": "Costco Gas", "chk|8": "Uber Eats", "chk|9": "Spotify",
                "chk|12": "Walmart Supercenter"})
            # Each description still has the bank's text: an item matching it keeps its text (written out when it matched
            # by name), without taking in every Amazon order. Spotify's bank text doesn't have "spotify usa": it matches
            # the brand's name too.
            self.assertEqual([tuple(r) for r in conn.execute(select(Recurring.name, Recurring.match).order_by(Recurring.id))], [
                ("Amazon", "amzn digital"), ("Music", "spotify usa\nspotify"), ("Birthday Gift", "birthday gift")])
            logos = schema.merchant_logos.c
            self.assertEqual({k: (w, h) for k, w, h in conn.execute(select(logos.key, logos.website, logos.hidden)).fetchall()},
                             {"amzn mktp us": ("amazon.com", 1), "amazon": ("amazon.com", 1), "wm supercenter": ("walmart.com", 0),
                              "walmart": ("example.com", 0)})
            self.assertEqual(json.loads(db.get_setting(conn, "recurring_suggestions_dismissed")),
                             ["chk|amazon|monthly", "chk|amzn digital|monthly", "chk|costco gas|weekly"])
            # The next Kindle payment, synced with the brand's name, still finds its recurring item, and an Amazon order doesn't.
            conn.execute(insert(Transaction), [
                {"id": "chk|10", "account_id": "chk", "posted": "2026-10-02", "amount": -9.99, "description": digital, "payee": "Amazon"},
                {"id": "chk|11", "account_id": "chk", "posted": "2026-10-02", "amount": -9.99, "description": "AMAZON.COM*ZZ1",
                 "payee": "Amazon"}])
            recurring.auto_match(conn, [1])
            self.assertEqual(dict(conn.execute(select(Transaction.id, Transaction.recurring_id).where(
                Transaction.id.in_(["chk|10", "chk|11"]))).fetchall()), {"chk|10": 1, "chk|11": None})
            # The rule made from the bank's name still matches it (exactly, as made), not the other Amazon order.
            rule = {"match": "amzn digital", "match_mode": "exact"}
            self.assertTrue(rules.matches(rule, {"payee": "Amazon", "description": digital, "amount": -9.99}))
            self.assertFalse(rules.matches(rule, {"payee": "Amazon", "description": "AMAZON.COM*ZZ1", "amount": -9.99}))

    @unittest.skipUnless(db.using_postgres(), "Postgres only: SQLite has one writer at a time anyway")
    def test_processes_starting_together_take_turns_migrating(self):
        # Several copies of Runway (or parallel tests) starting on one empty Postgres database used to collide creating
        # alembic_version ("duplicate key value violates unique constraint pg_type_typname_nsp_index").
        import threading
        errors = []

        def start(path):
            try:
                db.migrate(path)
            except Exception as e:
                errors.append(e)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "together.db")   # on Postgres the path only picks the test's own schema
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
        # Each test's own schema has its own lock, so parallel tests don't all queue on one (the whole Postgres run
        # used to wait on it). Two migrations of the same schema still take turns (the test above).
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            held, free = os.path.join(tmp, "held.db"), os.path.join(tmp, "free.db")
            db.migrate(held)
            with db.engine(held).connect() as other:                            # hold held's lock, as a migration would
                other.exec_driver_sql(f"SELECT pg_advisory_xact_lock({db.SCHEMA_LOCK}, hashtext(current_schema()))")
                waiting = threading.Thread(target=db.migrate, args=(held,))
                waiting.start()
                db.migrate(free)                                                 # another schema: doesn't wait
                self.assertEqual(drift(free), [])
                waiting.join(1)
                self.assertTrue(waiting.is_alive())                             # the same schema: waits for its turn
                other.rollback()                                                  # the lock goes with the transaction
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
            conn.execute(insert(Setting).values(key="n", value=5))   # loose typing, as in SQLite
            self.assertEqual(db.get_setting(conn, "n"), "5")


if __name__ == "__main__":
    unittest.main()
