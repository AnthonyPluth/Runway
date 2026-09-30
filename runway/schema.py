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
    Column('networth_hidden', Integer, server_default=text('0'), doc='1: left out of Net worth (still shown everywhere else)'),
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
    Column('merchant_id', Text, doc='the merchant as Plaid named it (merchants.id)'),
)

merchants = Table(
    'merchants', metadata,
    Column('id', Text, primary_key=True, doc="Plaid's merchant entity id, or 'name:<lowercased name>'"),
    Column('name', Text),
    Column('website', Text),
    Column('logo_url', Text, doc="where Plaid has the logo (plaid.com only)"),
    Column('logo', Text, doc='the logo itself, base64 (downloaded once, served by Runway)'),
    Column('logo_type', Text, doc='image/png, ...'),
    Column('logo_checked', Text, doc='when Runway last tried to download it'),
    info={'doc': 'merchants Plaid knows, and their logos'},
)

merchant_logos = Table(
    'merchant_logos', metadata,
    Column('key', Text, primary_key=True, doc='the merchant name, as merchants.key() has it (lowercase, single spaces)'),
    Column('website', Text, doc="the website whose logo (from Logo.dev) you picked; NULL with hidden=1: no logo"),
    Column('hidden', Integer, server_default=text('0'), doc='1: show no logo for this merchant'),
    info={'doc': 'logos you chose for a merchant, for every transaction from it'},
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

retail_orders = Table(
    'retail_orders', metadata,
    Column('id', Text, primary_key=True, doc="'<retailer>:<order number>'"),
    Column('retailer', Text, nullable=False, doc='amazon | target | costco'),
    Column('order_number', Text, nullable=False),
    Column('channel', Text, doc='online | store'),
    Column('placed', Text, doc='YYYY-MM-DD'),
    Column('total', Float, doc='what the order cost, positive'),
    Column('subtotal', Float),
    Column('tax', Float),
    Column('shipping', Float),
    Column('payment', Text, doc='how it was paid ("Visa 1234"), as the retailer says'),
    Column('details', Integer, server_default=text('0'), doc='1 once its items have been read'),
    Column('attempts', Integer, server_default=text('0'), doc='times its details page could not be read'),
    Column('raw', Text, doc="Target, Costco: what its API sent (JSON), to troubleshoot or re-read later"),
    Column('updated', Text, server_default=now_text()),
    info={'doc': 'orders from Amazon, Target and Costco (online and in store), sent by the browser extension'},
)

retail_items = Table(
    'retail_items', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('order_id', Text, nullable=False),
    Column('position', Integer, server_default=text('0')),
    Column('title', Text),
    Column('quantity', Float),
    Column('amount', Float, doc='the line: unit price x quantity, positive'),
    Column('department', Text, doc="the retailer's own department or product type, if it says"),
    Column('category', Text),
    Column('category_source', Text, doc='manual | memory | ai | department; NULL = not decided yet'),
    Column('confidence', Float),
    sqlite_autoincrement=True,
)

retail_charges = Table(
    'retail_charges', metadata,
    Column('id', Text, primary_key=True),
    Column('order_id', Text, nullable=False),
    Column('date', Text, nullable=False),
    Column('amount', Float, nullable=False, doc='as the bank shows it: negative = a charge, positive = a refund'),
    Column('payment', Text),
    Column('tx_id', Text, doc='the bank transaction it is'),
    Column('match_source', Text, doc='auto | manual'),
    Column('not_tx', Text, doc='transactions you said it is not (JSON list)'),
    Column('applied', Text, doc='JSON: the split or category Runway gave the transaction, and what it had before'),
    info={'doc': 'what a retailer charged to a card for an order (Amazon charges each shipment separately)'},
)

retail_item_memory = Table(
    'retail_item_memory', metadata,
    Column('key', Text, primary_key=True, doc='the item title, lowercased'),
    Column('category', Text, nullable=False),
    info={'doc': 'categories you picked for items, reused when you buy them again'},
)

equity_companies = Table(
    'equity_companies', metadata,
    Column('id', Text, primary_key=True, doc="'m<n>' for one you entered, 'carta:<issuer id>' from Carta"),
    Column('name', Text, nullable=False),
    Column('share_price', Float, doc="what a share is worth (the latest 409A fair market value, or the price you set)"),
    Column('price_as_of', Text),
    Column('in_networth', Integer, server_default=text('1'), doc='count vested equity in net worth'),
    Column('source', Text, server_default=text("'manual'"), doc='manual | carta'),
    Column('raw', Text, doc='what Carta sent (JSON)'),
    Column('updated', Text, server_default=now_text()),
    info={'doc': 'companies you hold stock or options in'},
)

equity_grants = Table(
    'equity_grants', metadata,
    Column('id', Text, primary_key=True),
    Column('company_id', Text, nullable=False),
    Column('kind', Text, nullable=False, doc='iso | nso | rsu | rsa | shares'),
    Column('label', Text, doc='the grant\'s name, e.g. ES-12'),
    Column('granted_on', Text),
    Column('quantity', Float, nullable=False, doc='shares or options granted'),
    Column('strike', Float, doc='exercise price per share (options)'),
    Column('vest_start', Text, doc='vesting start date'),
    Column('vest_months', Integer, doc='months until fully vested (0 or NULL: vested at once)'),
    Column('cliff_months', Integer, doc='nothing vests before this many months, then the months so far vest at once'),
    Column('vest_every', Integer, server_default=text('1'), doc='vests every this many months (1 monthly, 3 quarterly)'),
    Column('exercised', Float, server_default=text('0'), doc='options exercised (now shares you hold)'),
    Column('vested_reported', Float, doc='vested amount as Carta last reported it'),
    Column('vested_reported_on', Text),
    Column('expires_on', Text),
    Column('source', Text, server_default=text("'manual'")),
    Column('raw', Text, doc='what Carta sent (JSON)'),
    info={'doc': 'stock options, RSUs and shares you were granted'},
)

categories = Table(
    'categories', metadata,
    Column('name', Text, primary_key=True),
    Column('is_transfer', Integer, server_default=text('0'), doc='excluded from spending (card payments, moves between own accounts)'),
    Column('is_income', Integer, server_default=text('0')),
    Column('parent', Text, doc='subcategories: name of the top-level category'),
    Column('icon', Text, doc='an emoji you picked; NULL = the default for its name'),
    Column('color', Text, doc='a #rrggbb color you picked; NULL = the default (a subcategory: its parent\'s)'),
)

rules = Table(
    'rules', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('match', Text, nullable=False, doc="lowercase text in the payee or description ('' = any)"),
    Column('category', Text, doc='set this category (NULL: leave it to the other rules, history or the AI)'),
    Column('created_at', Text, server_default=now_text()),
    Column('match_mode', Text, server_default=text("'contains'"), doc='contains | exact | starts'),
    Column('amount_min', Float, doc='only amounts at least this much (dollars, either direction)'),
    Column('amount_max', Float, doc='only amounts at most this much'),
    Column('direction', Text, doc='out | in | NULL (either)'),
    Column('account_id', Text, doc='only this account'),
    Column('rename', Text, doc='show the merchant as this'),
    Column('review', Integer, server_default=text('0'), doc='1: put matching transactions in Review'),
    Column('split', Text, doc='JSON [{"category", "percent"}]: split matching transactions this way'),
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
    Column('rollover_from', Text, doc='YYYY-MM: from this month on, what\'s left over carries into the next month; NULL = off'),
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

oauth_clients = Table(
    'oauth_clients', metadata,
    Column('id', Text, primary_key=True, doc='rwc_...'),
    Column('name', Text),
    Column('redirect_uris', Text, nullable=False, doc='JSON list'),
    Column('auth_method', Text, nullable=False, doc='none | client_secret_post | client_secret_basic'),
    Column('secret_hash', Text, doc='sha256 of the client secret, for the two secret methods'),
    Column('kind', Text, nullable=False, server_default=text("'dcr'"), doc='dcr: registered at /oauth/register'),
    Column('metadata_url', Text, doc='reserved for client ID metadata documents; unused'),
    Column('created', Float, nullable=False),
    Column('last_used', Float),
    info={'doc': 'apps registered to connect to /mcp with OAuth'},
)

oauth_grants = Table(
    'oauth_grants', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('client_id', Text, nullable=False),
    Column('sub', Text, doc='who approved it'),
    Column('email', Text),
    Column('scope', Text, nullable=False, doc='space-separated: read, churning:write, categorize:write'),
    Column('resource', Text, nullable=False, doc='the /mcp address its tokens are for'),
    Column('created', Float, nullable=False),
    Column('last_used', Float),
    Column('revoked', Float),
    Column('revoked_reason', Text),
    sqlite_autoincrement=True,
    info={'doc': 'one approval of an app on the consent page; its codes and tokens die with it'},
)

oauth_codes = Table(
    'oauth_codes', metadata,
    Column('code_hash', Text, primary_key=True),
    Column('client_id', Text, nullable=False),
    Column('grant_id', Integer, nullable=False),
    Column('redirect_uri', Text, nullable=False),
    Column('code_challenge', Text, nullable=False, doc='PKCE, S256'),
    Column('resource', Text, nullable=False),
    Column('created', Float, nullable=False),
    Column('used', Float),
    info={'doc': 'authorization codes (only a hash of each is kept), single use, for 10 minutes'},
)

oauth_tokens = Table(
    'oauth_tokens', metadata,
    Column('token_hash', Text, primary_key=True),
    Column('kind', Text, nullable=False, doc='access | refresh'),
    Column('grant_id', Integer, nullable=False),
    Column('created', Float, nullable=False),
    Column('expires', Float, nullable=False),
    Column('consumed', Float, doc='refresh: when it was traded for a new one'),
    Column('replaced_by', Text, doc='refresh: the hash of the one it was traded for'),
    info={'doc': 'access and refresh tokens (only a hash of each is kept)'},
)

oauth_consents = Table(
    'oauth_consents', metadata,
    Column('token_hash', Text, primary_key=True),
    Column('params', Text, nullable=False, doc='JSON: the checked authorization request, and who was signed in'),
    Column('created', Float, nullable=False),
    info={'doc': 'consent pages shown and not yet answered (10 minutes)'},
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
    Column('source', Text, server_default=text("'manual'"), doc='manual | realie (rentcast: an older estimate)'),
    Column('yearly_change', Float, doc='optional % per year applied since as_of (e.g. -15 for a car)'),
    Column('address', Text),
    Column('url', Text, doc='e.g. the Zillow or KBB page, to check by hand'),
    Column('loan_account_id', Text, doc='the mortgage / auto loan against it, for equity'),
    Column('auto_update', Integer, server_default=text('0'), doc='homes: refresh from Realie monthly'),
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

churn_cards = Table(
    'churn_cards', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('owner', Text, nullable=False, doc="whose card it is: a person's first name (as accounts.owner has it)"),
    Column('issuer', Text, nullable=False, doc='chase | amex | citi | capital_one | bank_of_america | barclays | us_bank | '
                                               'wells_fargo | discover | other (churning.ISSUERS)'),
    Column('product', Text, nullable=False, doc='e.g. Sapphire Preferred'),
    Column('family', Text, doc="cards whose bonuses count as one for the issuer's rules (e.g. Sapphire); NULL = the product"),
    Column('account_id', Text, doc='the Runway account it is (accounts.id), to count its spending'),
    Column('opened_on', Text, nullable=False, doc='YYYY-MM-DD'),
    Column('closed_on', Text, doc='closed, or changed to another product, on this day'),
    Column('status', Text, server_default=text("'open'"), doc='open | closed | product_changed'),
    Column('changed_from', Integer, doc='a product change of this card (churn_cards.id): the same account, so not a new one for 5/24'),
    Column('authorized_user', Integer, server_default=text('0'), doc="1: you're an authorized user on someone else's card"),
    Column('business', Integer, server_default=text('0'), doc="1: a business card (most don't count toward 5/24)"),
    Column('annual_fee', Float, server_default=text('0')),
    Column('fee_month', Integer, doc='the month the annual fee posts (1-12); NULL = the month it was opened'),
    Column('currency', Text, server_default=text("'cash'"),
           doc='what it earns, and its bonus is paid in (a key of churning.CURRENCIES or churn_currencies)'),
    Column('base_rate', Float, server_default=text('1'), doc='points per dollar on everything without a rate of its own'),
    Column('earn_note', Text, doc='e.g. "5x rotating quarterly categories"'),
    Column('bonus', Float, doc='the sign-up bonus, in its currency (points, miles or dollars)'),
    Column('bonus_spend', Float, doc='spending needed for the bonus'),
    Column('bonus_months', Integer, server_default=text('3'), doc='months after opening to spend it'),
    Column('bonus_deadline', Text, doc='the day the spending is due, if not opened_on + bonus_months'),
    Column('bonus_earned_on', Text, doc='the day the bonus posted'),
    Column('manual_spend', Float, doc='spending so far toward the bonus, for a card not linked to an account'),
    Column('eligible_on', Text, doc="you know better: the day its bonus can be earned again (overrides the issuer's rule)"),
    Column('notes', Text),
    Column('created_at', Text, server_default=now_text()),
    Column('portal_name', Text, doc="the issuer's travel portal, for rates earned only there (e.g. Capital One Travel)"),
    Column('plan', Text, server_default=text("'undecided'"),
           doc='what you mean to do before the annual fee: undecided | keep | close | product_change'),
    Column('plan_target', Text, doc='the card to change it to (a downgrade or other product change)'),
    Column('plan_date', Text, doc='do it by this day; NULL = the day before the next annual fee'),
    Column('plan_remind_days', Integer, server_default=text('14'), doc='remind you this many days before plan_date'),
    Column('plan_done_on', Text, doc='the day you checked the plan off'),
    Column('plan_new_id', Integer, doc='the card checking off a product change added (churn_cards.id); undo removes it'),
    Column('hide_upcoming', Integer, server_default=text('0'), doc='1: leave this card out of Upcoming and its alerts'),
    sqlite_autoincrement=True,
    info={'doc': 'credit cards you and your partner opened for their sign-up bonuses and rewards'},
)

churn_rates = Table(
    'churn_rates', metadata,
    Column('card_id', Integer, nullable=False),
    Column('category', Text, nullable=False, doc='a category (its subcategories earn the same unless they have their own)'),
    Column('multiplier', Float, nullable=False, doc='points per dollar'),
    Column('portal_only', Integer, nullable=False, server_default=text('0'),
           doc="1: only when booked through the issuer's portal (churn_cards.portal_name)"),
    PrimaryKeyConstraint('card_id', 'category', 'portal_only'),
    info={'doc': 'what a card earns in a category, instead of its base rate'},
)

churn_currencies = Table(
    'churn_currencies', metadata,
    Column('key', Text, primary_key=True),
    Column('name', Text, nullable=False),
    Column('cents', Float, nullable=False, doc='what one point is worth to you, in cents'),
    Column('kind', Text, doc='a currency you added: bank | airline | hotel | cash | other (NULL = other)'),
    info={'doc': "points values you set, and currencies you added (the rest are churning.CURRENCIES' defaults)"},
)

churn_balances = Table(
    'churn_balances', metadata,
    Column('owner', Text, nullable=False),
    Column('currency', Text, nullable=False),
    Column('points', Float, nullable=False),
    Column('as_of', Text),
    PrimaryKeyConstraint('owner', 'currency'),
    info={'doc': 'points balances you entered, per person and currency'},
)

churn_tasks = Table(
    'churn_tasks', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('card_id', Integer, nullable=False),
    Column('due_on', Text, nullable=False),
    Column('action', Text, nullable=False, doc='e.g. close, downgrade to Freedom, call retention'),
    Column('done', Integer, server_default=text('0')),
    Column('snooze_until', Text, doc='left out of Upcoming until this day'),
    sqlite_autoincrement=True,
    info={'doc': 'things to do about a card, and when'},
)

churn_benefits = Table(
    'churn_benefits', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('card_id', Integer, nullable=False),
    Column('name', Text, nullable=False, doc='e.g. Uber Cash, Priority Pass lounges'),
    Column('kind', Text, server_default=text("'credit'"), doc='credit | access | status | other (churn_benefits.KINDS)'),
    Column('amount', Float, doc='a credit: dollars per period (NULL for access and the like)'),
    Column('period', Text, server_default=text("'annual'"),
           doc='monthly | quarterly | semiannual | annual | every_4_years | one_time'),
    Column('basis', Text, server_default=text("'calendar'"),
           doc="calendar (resets Jan 1, the 1st of the month...) | anniversary (the cardmember year, from opened_on)"),
    Column('annual_value', Float, doc="what it's worth to you a year, instead of amount times periods (access: the only value)"),
    Column('counts', Integer, server_default=text('1'), doc="1: you'll use it, so it counts against the annual fee"),
    Column('remind', Integer, server_default=text('1'), doc='1: remind you before a credit with money left resets'),
    Column('remind_days', Integer, doc='... this many days ahead (NULL = 14 for monthly and quarterly, else 30)'),
    Column('expires_on', Text, doc='a one-time benefit: the last day to use it'),
    Column('preset', Text, doc='the quick-add preset it came from (churn_benefits.PRESETS)'),
    Column('notes', Text),
    Column('active', Integer, server_default=text('1'), doc='0: kept for the record, but no longer on the card'),
    Column('created_at', Text, server_default=now_text()),
    sqlite_autoincrement=True,
    info={'doc': "a card's perks and credits (lounges, Uber, airline and hotel credits...)"},
)

churn_benefit_uses = Table(
    'churn_benefit_uses', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('benefit_id', Integer, nullable=False),
    Column('period_start', Text, nullable=False, doc='the first day of the period it was used in'),
    Column('amount_used', Float, doc='dollars of a credit used (NULL: a benefit without an amount, used)'),
    Column('used_on', Text, nullable=False),
    sqlite_autoincrement=True,
    info={'doc': 'when a benefit was used, per period'},
)

churn_wishlist = Table(
    'churn_wishlist', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('owner', Text, nullable=False, doc="whose it will be: a person's first name (as accounts.owner has it)"),
    Column('kind', Text, server_default=text("'card'"), doc='card | bank_bonus'),
    Column('issuer', Text, doc='a card: churning.ISSUERS key'),
    Column('bank', Text, doc='a bank bonus: the bank'),
    Column('product', Text, doc='a card: e.g. Sapphire Preferred; a bank bonus: the account, if you like'),
    Column('family', Text, doc="cards whose bonuses count as one for the issuer's rules (NULL = the product)"),
    Column('business', Integer, server_default=text('0')),
    Column('annual_fee', Float, doc='expected'),
    Column('bonus', Float, doc='the expected bonus: points or miles in its currency, or dollars'),
    Column('currency', Text, doc='a card: what the bonus is paid in (churning.CURRENCIES or churn_currencies)'),
    Column('bonus_spend', Float, doc='a card: spending needed for the bonus'),
    Column('bonus_months', Integer, doc='a card: months to spend it'),
    Column('account_type', Text, doc='a bank bonus: checking | savings | business'),
    Column('requirements', Text, doc='a bank bonus: deposits, balance and the like, as the offer says'),
    Column('repeat_months', Integer, doc="a bank bonus: the bank's rule, a bonus again this many months after the last"),
    Column('once_per_lifetime', Integer, server_default=text('0'), doc="a bank bonus: 1 if the bank's rule is once per lifetime"),
    Column('offer_expires_on', Text, doc='the offer ends on this day'),
    Column('priority', Integer, doc='1 = the next one to get'),
    Column('status', Text, server_default=text("'wanted'"), doc='wanted | ready (you mean to apply now) | applied | dropped'),
    Column('wait_until', Text, doc='your own "not before" day'),
    Column('min_score', Integer, doc='the credit score you want before applying'),
    Column('assume_prior_planned', Integer, server_default=text('0'),
           doc="1: count your earlier-priority planned cards as opened, for this one's 5/24"),
    Column('notes', Text),
    Column('apply_url', Text, doc='where to apply: the offer\'s page (http or https)'),
    Column('applied_on', Text),
    Column('applied_id', Integer, doc='what applying made: churn_cards.id or churn_bank_bonuses.id (by kind)'),
    Column('created_at', Text, server_default=now_text()),
    sqlite_autoincrement=True,
    info={'doc': "cards and bank bonuses you want next, and what's in the way of applying"},
)

churn_scores = Table(
    'churn_scores', metadata,
    Column('owner', Text, nullable=False),
    Column('as_of', Text, nullable=False, doc='YYYY-MM-DD'),
    Column('score', Integer, nullable=False, doc='a credit score you looked up (Runway never fetches one)'),
    Column('source', Text, doc='e.g. Experian FICO 8'),
    PrimaryKeyConstraint('owner', 'as_of'),
    info={'doc': 'credit scores you entered, per person and day'},
)

churn_bank_bonuses = Table(
    'churn_bank_bonuses', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('owner', Text, nullable=False, doc="whose account it is: a person's first name (as accounts.owner has it)"),
    Column('bank', Text, nullable=False),
    Column('account_type', Text, server_default=text("'checking'"), doc='checking | savings | business'),
    Column('account_id', Text, doc='the Runway account it is (accounts.id), to follow its deposits and balance'),
    Column('opened_on', Text, nullable=False, doc='YYYY-MM-DD'),
    Column('bonus', Float, nullable=False, doc='the bonus, in dollars'),
    Column('dd_total', Float, doc='direct deposits needed, in total'),
    Column('dd_count', Integer, doc='direct deposits needed, how many'),
    Column('debit_count', Integer, doc='debit card purchases needed'),
    Column('min_balance', Float, doc='balance to keep'),
    Column('hold_until', Text, doc='... until this day'),
    Column('other_reqs', Text, doc='anything else the offer asks for'),
    Column('deadline_days', Integer, server_default=text('90'), doc='days after opening to meet the requirements'),
    Column('deadline', Text, doc='the day the requirements are due, if not opened_on + deadline_days'),
    Column('post_days', Integer, server_default=text('60'), doc='the bonus posts within this many days of the deadline'),
    Column('manual_dd', Float, doc='direct deposits so far, for an account not linked'),
    Column('manual_debits', Integer, doc='debit purchases so far, for an account not linked'),
    Column('status', Text, server_default=text("'open'"), doc='open | pending (requirements met) | received | closed'),
    Column('received_on', Text),
    Column('received_amount', Float, doc='what actually posted (NULL = the bonus)'),
    Column('closed_on', Text),
    Column('monthly_fee', Float, server_default=text('0')),
    Column('fee_waiver', Text, doc='how the monthly fee is waived'),
    Column('early_close_fee', Float, doc='charged (or the bonus clawed back) if closed too soon'),
    Column('keep_open_days', Integer, doc='days to keep it open to avoid that'),
    Column('repeat_months', Integer, doc="the bank's rule: a bonus again this many months after the last one"),
    Column('once_per_lifetime', Integer, server_default=text('0'), doc="1: the bank's rule is once per lifetime"),
    Column('eligible_on', Text, doc='you know better: the day the bonus can be earned again'),
    Column('notes', Text),
    Column('created_at', Text, server_default=now_text()),
    sqlite_autoincrement=True,
    info={'doc': 'checking and savings account sign-up bonuses'},
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
Index('equity_grants_company', equity_grants.c.company_id)
Index('retail_items_order', retail_items.c.order_id)
Index('retail_charges_order', retail_charges.c.order_id)
Index('retail_charges_tx', retail_charges.c.tx_id)
Index('churn_tasks_card', churn_tasks.c.card_id)
Index('churn_benefits_card', churn_benefits.c.card_id)
Index('churn_benefit_uses_benefit', churn_benefit_uses.c.benefit_id)
Index('oauth_grants_client', oauth_grants.c.client_id)
Index('oauth_tokens_grant', oauth_tokens.c.grant_id)
Index('oauth_codes_grant', oauth_codes.c.grant_id)
Index('accounts_plaid_account', accounts.c.plaid_account_id, unique=True)   # a Plaid account is one of your accounts, never two

# Tables whose integer id is assigned by the database.
AUTO_ID = {t.name for t in metadata.tables.values() if 'id' in t.c and t.c.id.autoincrement is True}

# SQLite's instr(haystack, needle), which Runway's queries use, for Postgres.
POSTGRES_INSTR = ("CREATE OR REPLACE FUNCTION instr(text, text) RETURNS integer AS 'SELECT strpos($1, $2)' "
                  "LANGUAGE sql IMMUTABLE")
