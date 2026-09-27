"""Runway's database schema, for SQLite and Postgres alike. Alembic migrations (runway/migrations) create and change it."""
from sqlalchemy import Column, Float, Index, Integer, MetaData, PrimaryKeyConstraint, Table, Text, text
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import FunctionElement

metadata = MetaData()


class now_text(FunctionElement):
    """The current time as 'YYYY-MM-DD HH:MM:SS' text (UTC, or the server's local time), on either database."""
    type = Text()
    inherit_cache = True

    def __init__(self, local: bool = False):
        self.local = local
        super().__init__()


@compiles(now_text, "sqlite")
def _now_sqlite(element, compiler, **kw):
    return "(datetime('now', 'localtime'))" if element.local else "CURRENT_TIMESTAMP"


@compiles(now_text, "postgresql")
def _now_postgres(element, compiler, **kw):
    return "to_char(now()%s, 'YYYY-MM-DD HH24:MI:SS')" % ("" if element.local else " at time zone 'utc'")

accounts = Table(
    'accounts', metadata,
    Column('id', Text, primary_key=True),
    Column('name', Text, nullable=False),
    Column('display_name', Text),
    Column('org', Text),
    Column('currency', Text, server_default=text("'USD'")),
    Column('balance', Float, server_default=text('0')),
    Column('available', Float),
    Column('balance_date', Text),
    Column('kind', Text, server_default=text("'checking'"), doc='checking | savings | credit | loan | investment'),
    Column('pay_from', Text, doc='credit cards: account id that pays the statement'),
    Column('owed_positive', Integer, server_default=text('0'), doc='credit/loan: 1 if the bank reports the amount owed as a positive number'),
    Column('in_forecast', Integer, server_default=text('1'), doc='cash accounts: include in the projection'),
    Column('daily_spend', Integer, server_default=text('0'), doc='cash accounts: also subtract average everyday spending (opt-in)'),
    Column('hidden', Integer, server_default=text('0')),
    Column('owner', Text, doc='a signed-in person\'s first name, "Joint", or NULL'),
    Column('provider', Text, server_default=text("'simplefin'"), doc='where balances and transactions come from: simplefin | plaid'),
    Column('plaid_account_id', Text, doc='the same account in a Plaid connection, if any'),
    Column('provider_since', Text, doc='when the provider last changed (YYYY-MM-DD); overlapping history is matched up'),
)

transactions = Table(
    'transactions', metadata,
    Column('id', Text, primary_key=True, doc="account_id + '|' + provider transaction id"),
    Column('account_id', Text, nullable=False),
    Column('posted', Text, nullable=False, doc='YYYY-MM-DD'),
    Column('amount', Float, nullable=False, doc='positive = money in'),
    Column('description', Text),
    Column('payee', Text),
    Column('category', Text),
    Column('category_source', Text, doc='rule | ai | manual | auto'),
    Column('confidence', Float),
    Column('needs_review', Integer, server_default=text('0')),
    Column('pending', Integer, server_default=text('0')),
    Column('created_at', Text, server_default=now_text()),
    Column('recurring_id', Integer, doc='NULL = not matched, 0 = never match'),
    Column('is_split', Integer, server_default=text('0'), doc='split across categories: the parts are in tx_splits, and they, not this row, count'),
)

tx_splits = Table(
    'tx_splits', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('tx_id', Text, nullable=False),
    Column('amount', Float, nullable=False, doc='same sign as the transaction; the parts add up to it'),
    Column('category', Text),
    Column('note', Text),
    Column('position', Integer, server_default=text('0')),
    sqlite_autoincrement=True,
    info={'doc': 'one transaction spread across categories ($100 at Target: $60 Groceries, $40 Shopping)'},
)

categories = Table(
    'categories', metadata,
    Column('name', Text, primary_key=True),
    Column('is_transfer', Integer, server_default=text('0'), doc='excluded from spending (card payments, moves between own accounts)'),
    Column('is_income', Integer, server_default=text('0')),
    Column('parent', Text, doc='subcategories: name of the top-level category'),
)

rules = Table(
    'rules', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('match', Text, nullable=False, unique=True, doc='lowercase text found in the payee or description'),
    Column('category', Text, nullable=False),
    Column('created_at', Text, server_default=now_text()),
    sqlite_autoincrement=True,
)

recurring = Table(
    'recurring', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('name', Text, nullable=False),
    Column('account_id', Text, nullable=False),
    Column('amount', Float, nullable=False, doc='negative = money out'),
    Column('frequency', Text, nullable=False, doc='weekly | biweekly | semimonthly | monthly | quarterly | semiannual | yearly | dates'),
    Column('anchor_date', Text, nullable=False, doc='a known occurrence (YYYY-MM-DD)'),
    Column('match', Text, doc='payee text, so history of this item is not double counted'),
    Column('end_date', Text),
    Column('active', Integer, server_default=text('1')),
    Column('amount_mode', Text, server_default=text("'fixed'"), doc='fixed | last | avg3'),
    Column('dates', Text, doc='semimonthly days or yearly dates'),
    sqlite_autoincrement=True,
)

recurring_dismissed = Table(
    'recurring_dismissed', metadata,
    Column('key', Text, primary_key=True),
    info={'doc': 'missed-payment alerts you\'ve dismissed ("rec:<id>:<date>")'},
)

budgets = Table(
    'budgets', metadata,
    Column('category', Text, primary_key=True),
    Column('amount', Float, nullable=False, doc='monthly limit, positive'),
    Column('pay_with', Text, doc='account id this category is usually paid with'),
)

overrides = Table(
    'overrides', metadata,
    Column('key', Text, primary_key=True, doc='rec:<id>:<date> or card:<account id>:<date>'),
    Column('amount', Float, nullable=False, doc='replaces the forecast amount for that one occurrence'),
)

plaid_items = Table(
    'plaid_items', metadata,
    Column('item_id', Text, primary_key=True),
    Column('access_token', Text, nullable=False),
    Column('institution_id', Text),
    Column('institution_name', Text),
    Column('env', Text),
    Column('created_at', Text, server_default=now_text()),
    Column('last_sync', Text),
    Column('error', Text, doc='e.g. ITEM_LOGIN_REQUIRED'),
    Column('products', Text, server_default=text("'investments'"), doc='comma-separated: investments, transactions, liabilities'),
    Column('cursor', Text, doc='/transactions/sync position'),
)

plaid_accounts = Table(
    'plaid_accounts', metadata,
    Column('plaid_account_id', Text, primary_key=True),
    Column('item_id', Text, nullable=False),
    Column('name', Text),
    Column('official_name', Text),
    Column('mask', Text, doc='last 4 digits'),
    Column('type', Text, doc='depository | credit | loan | investment | other'),
    Column('subtype', Text),
    Column('current', Float, doc="Plaid's current balance (amount owed, for cards and loans)"),
    Column('available', Float),
    Column('ignored', Integer, server_default=text('0'), doc="you chose not to use it"),
    info={'doc': "bank and card accounts from Plaid connections; each is matched to a Runway account (accounts.plaid_account_id)"},
)

card_statements = Table(
    'card_statements', metadata,
    Column('plaid_account_id', Text, primary_key=True),
    Column('item_id', Text, nullable=False),
    Column('last_statement_balance', Float),
    Column('last_statement_date', Text, doc='the day the last statement closed (YYYY-MM-DD)'),
    Column('next_due_date', Text),
    Column('minimum_payment', Float),
    Column('last_payment_amount', Float),
    Column('last_payment_date', Text),
    Column('is_overdue', Integer),
    Column('updated', Text),
    info={'doc': "credit card statements from the bank, via Plaid Liabilities"},
)

inv_accounts = Table(
    'inv_accounts', metadata,
    Column('id', Text, primary_key=True, doc='Plaid account_id'),
    Column('item_id', Text, nullable=False),
    Column('name', Text),
    Column('official_name', Text),
    Column('type', Text),
    Column('subtype', Text),
    Column('mask', Text),
    Column('balance', Float),
    Column('currency', Text, server_default=text("'USD'")),
    Column('hidden', Integer, server_default=text('0')),
    Column('source', Text, server_default=text("'plaid'"), doc='plaid | simplefin'),
    Column('institution', Text),
    Column('account_id', Text, doc="Plaid accounts: the Runway account it is (accounts.id; 'pl:<id>' when it's its own), "
                                   "'ignore', or NULL while undecided"),
)

securities = Table(
    'securities', metadata,
    Column('id', Text, primary_key=True, doc='Plaid security_id'),
    Column('ticker', Text),
    Column('name', Text),
    Column('type', Text, doc='cash | cryptocurrency | derivative | equity | etf | fixed income | loan | mutual fund | other'),
    Column('subtype', Text),
    Column('close_price', Float),
    Column('close_as_of', Text),
    Column('is_cash', Integer, server_default=text('0')),
    Column('sector', Text),
    Column('industry', Text),
    Column('currency', Text),
    Column('cusip', Text),
    Column('isin', Text),
)

holdings = Table(
    'holdings', metadata,
    Column('account_id', Text, nullable=False),
    Column('security_id', Text, nullable=False),
    Column('quantity', Float),
    Column('price', Float),
    Column('price_as_of', Text),
    Column('value', Float),
    Column('cost_basis', Float),
    Column('currency', Text),
    PrimaryKeyConstraint('account_id', 'security_id'),
)

inv_transactions = Table(
    'inv_transactions', metadata,
    Column('id', Text, primary_key=True),
    Column('account_id', Text, nullable=False),
    Column('security_id', Text),
    Column('date', Text, nullable=False),
    Column('name', Text),
    Column('type', Text, doc='buy | sell | cancel | cash | fee | transfer'),
    Column('subtype', Text),
    Column('quantity', Float, doc='positive = bought, negative = sold'),
    Column('amount', Float, doc='positive = cash left the account, negative = cash came in'),
    Column('price', Float),
    Column('fees', Float),
    Column('currency', Text),
)

inv_snapshots = Table(
    'inv_snapshots', metadata,
    Column('date', Text, nullable=False),
    Column('account_id', Text, nullable=False),
    Column('value', Float),
    PrimaryKeyConstraint('date', 'account_id'),
    info={'doc': 'value of each account on each day we synced'},
)

auth_pending = Table(
    'auth_pending', metadata,
    Column('state', Text, primary_key=True),
    Column('nonce', Text),
    Column('verifier', Text),
    Column('next', Text),
    Column('created', Float),
    info={'doc': 'sign-ins in progress at the OIDC provider'},
)

auth_sessions = Table(
    'auth_sessions', metadata,
    Column('token_hash', Text, primary_key=True),
    Column('sub', Text),
    Column('email', Text),
    Column('name', Text),
    Column('created', Float),
    Column('expires', Float),
    Column('id_token', Text),
    info={'doc': 'signed-in browsers (only a hash of each session token is kept)'},
)

push_subscriptions = Table(
    'push_subscriptions', metadata,
    Column('endpoint', Text, primary_key=True),
    Column('p256dh', Text, nullable=False),
    Column('auth', Text, nullable=False),
    Column('device', Text),
    Column('user_sub', Text),
    Column('created', Float),
    Column('last_ok', Float),
    Column('last_error', Text),
    info={'doc': 'devices that get notifications'},
)

notify_log = Table(
    'notify_log', metadata,
    Column('key', Text, primary_key=True),
    Column('sent', Float),
    Column('title', Text),
    info={'doc': 'alerts already sent, so each is sent once'},
)

users = Table(
    'users', metadata,
    Column('sub', Text, primary_key=True),
    Column('email', Text),
    Column('name', Text),
    Column('first_name', Text),
    Column('last_seen', Float),
    info={'doc': 'people who have signed in (for account owners)'},
)

ai_log = Table(
    'ai_log', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('at', Text, server_default=now_text(local=True)),
    Column('purpose', Text, doc='review (the button) | sync (automatic)'),
    Column('model', Text),
    Column('merchants', Integer),
    Column('answered', Integer),
    Column('new_cats', Integer),
    Column('ok', Integer),
    Column('seconds', Float),
    Column('message', Text),
    Column('reply', Text, doc='the start of what the model said, for troubleshooting'),
    info={'doc': 'one row per request to the AI, shown on the Review tab'},
    sqlite_autoincrement=True,
)

assets = Table(
    'assets', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('name', Text, nullable=False),
    Column('kind', Text, nullable=False, doc='home | vehicle | other'),
    Column('value', Float),
    Column('as_of', Text, doc='date the value was set'),
    Column('source', Text, server_default=text("'manual'"), doc='manual | rentcast'),
    Column('yearly_change', Float, doc='optional % per year applied since as_of (e.g. -15 for a car)'),
    Column('address', Text),
    Column('url', Text, doc='e.g. the Zillow or KBB page, to check by hand'),
    Column('loan_account_id', Text, doc='the mortgage / auto loan against it, for equity'),
    Column('auto_update', Integer, server_default=text('0'), doc='homes: refresh from RentCast monthly'),
    Column('low', Float),
    Column('high', Float),
    Column('last_lookup', Text),
    Column('notes', Text),
    Column('created_at', Text, server_default=now_text()),
    info={'doc': 'things you own that no bank reports: a home, a car'},
    sqlite_autoincrement=True,
)

asset_values = Table(
    'asset_values', metadata,
    Column('asset_id', Integer, nullable=False),
    Column('date', Text, nullable=False),
    Column('value', Float),
    Column('source', Text),
    PrimaryKeyConstraint('asset_id', 'date'),
)

networth_snapshots = Table(
    'networth_snapshots', metadata,
    Column('date', Text, primary_key=True),
    Column('assets', Float),
    Column('liabilities', Float),
    Column('net', Float),
    Column('detail', Text, doc='JSON: total per group'),
)

manual_positions = Table(
    'manual_positions', metadata,
    Column('account_id', Text, nullable=False),
    Column('security_id', Text, nullable=False),
    Column('shares', Float),
    Column('pct', Float, doc='share of each new contribution, %'),
    Column('last_value', Float, doc='for funds without a ticker'),
    Column('updated', Text),
    PrimaryKeyConstraint('account_id', 'security_id'),
    info={'doc': 'what a balance-only account holds, entered by you'},
)

manual_contributions = Table(
    'manual_contributions', metadata,
    Column('account_id', Text),
    Column('date', Text),
    Column('amount', Float),
    info={'doc': 'new money Runway spotted and invested per your election'},
)

manual_state = Table(
    'manual_state', metadata,
    Column('account_id', Text, primary_key=True),
    Column('drift', Float, doc='how far the tracked funds are from the synced balance (share of it)'),
    Column('checked', Text),
    Column('last_balance', Float),
    Column('baseline', Float, doc='gap between entered funds and the balance, at entry'),
)

cost_overrides = Table(
    'cost_overrides', metadata,
    Column('account_id', Text, nullable=False),
    Column('security_id', Text, nullable=False),
    Column('cost_basis', Float, nullable=False),
    Column('per_share', Float, doc='set: cost basis = per_share x shares held'),
    PrimaryKeyConstraint('account_id', 'security_id'),
    info={'doc': 'cost basis you entered yourself; wins over what the institution reports'},
)

holding_snapshots = Table(
    'holding_snapshots', metadata,
    Column('date', Text, nullable=False),
    Column('account_id', Text, nullable=False),
    Column('security_id', Text, nullable=False),
    Column('quantity', Float),
    Column('value', Float),
    PrimaryKeyConstraint('date', 'account_id', 'security_id'),
    info={'doc': 'positions on each day we synced (SimpleFIN has no trade history)'},
)

prices = Table(
    'prices', metadata,
    Column('ticker', Text, nullable=False),
    Column('date', Text, nullable=False),
    Column('close', Float),
    Column('adjclose', Float),
    PrimaryKeyConstraint('ticker', 'date'),
    info={'doc': 'daily closes from the price service (split-adjusted)'},
)

price_meta = Table(
    'price_meta', metadata,
    Column('ticker', Text, primary_key=True),
    Column('fetched_at', Text),
    Column('ok', Integer),
    Column('splits', Text, doc='JSON list of [date, ratio]'),
    Column('instrument_type', Text, doc='EQUITY | ETF | MUTUALFUND | ...'),
    Column('long_name', Text),
)

settings = Table(
    'settings', metadata,
    Column('key', Text, primary_key=True),
    Column('value', Text),
)

sync_log = Table(
    'sync_log', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('at', Text, server_default=now_text()),
    Column('ok', Integer),
    Column('message', Text),
    sqlite_autoincrement=True,
)

Index('tx_account_posted', transactions.c.account_id, transactions.c.posted)
Index('tx_review', transactions.c.needs_review)
Index('inv_tx_account_date', inv_transactions.c.account_id, inv_transactions.c.date)
Index('tx_recurring', transactions.c.recurring_id)
Index('tx_splits_tx', tx_splits.c.tx_id)

# Tables whose integer id is assigned by the database.
AUTO_ID = {t.name for t in metadata.tables.values() if 'id' in t.c and t.c.id.autoincrement is True}

# SQLite's instr(haystack, needle), which Runway's queries use, for Postgres.
POSTGRES_INSTR = ("CREATE OR REPLACE FUNCTION instr(text, text) RETURNS integer AS 'SELECT strpos($1, $2)' "
                  "LANGUAGE sql IMMUTABLE")
