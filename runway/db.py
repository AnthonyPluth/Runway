from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    display_name  TEXT,
    org           TEXT,
    currency      TEXT DEFAULT 'USD',
    balance       REAL DEFAULT 0,
    available     REAL,
    balance_date  TEXT,
    kind          TEXT DEFAULT 'checking',   -- checking | savings | credit | loan | investment
    closing_day   INTEGER,                   -- credit cards: statement closing day of month
    due_day       INTEGER,                   -- credit cards: payment due day of month
    pay_from      TEXT,                      -- credit cards: account id that pays the statement
    owed_positive INTEGER DEFAULT 0,         -- credit/loan: 1 if the bank reports the amount owed as a positive number
    in_forecast   INTEGER DEFAULT 1,         -- cash accounts: include in the projection
    daily_spend   INTEGER DEFAULT 0,         -- cash accounts: also subtract average everyday spending (opt-in)
    hidden        INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id              TEXT PRIMARY KEY,        -- account_id + '|' + provider transaction id
    account_id      TEXT NOT NULL,
    posted          TEXT NOT NULL,           -- YYYY-MM-DD
    amount          REAL NOT NULL,           -- positive = money in
    description     TEXT,
    payee           TEXT,
    category        TEXT,
    category_source TEXT,                    -- rule | ai | manual | auto
    confidence      REAL,
    needs_review    INTEGER DEFAULT 0,
    pending         INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS tx_account_posted ON transactions(account_id, posted);
CREATE INDEX IF NOT EXISTS tx_review ON transactions(needs_review);

CREATE TABLE IF NOT EXISTS categories (
    name        TEXT PRIMARY KEY,
    is_transfer INTEGER DEFAULT 0,           -- excluded from spending (card payments, moves between own accounts)
    is_income   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    match      TEXT NOT NULL UNIQUE,         -- lowercase text found in the payee or description
    category   TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS recurring (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    account_id  TEXT NOT NULL,
    amount      REAL NOT NULL,               -- negative = money out
    frequency   TEXT NOT NULL,               -- weekly | biweekly | semimonthly | monthly | quarterly | semiannual | yearly | dates
    anchor_date TEXT NOT NULL,               -- a known occurrence (YYYY-MM-DD)
    match       TEXT,                        -- payee text, so history of this item is not double counted
    end_date    TEXT,
    active      INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS recurring_dismissed (   -- missed-payment alerts you've dismissed ("rec:<id>:<date>")
    key TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS budgets (
    category TEXT PRIMARY KEY,
    amount   REAL NOT NULL                   -- monthly limit, positive
);

CREATE TABLE IF NOT EXISTS overrides (
    key    TEXT PRIMARY KEY,                 -- rec:<id>:<date> or card:<account id>:<date>
    amount REAL NOT NULL                     -- replaces the forecast amount for that one occurrence
);

-- Investments (via Plaid) ---------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plaid_items (
    item_id          TEXT PRIMARY KEY,
    access_token     TEXT NOT NULL,
    institution_id   TEXT,
    institution_name TEXT,
    env              TEXT,
    created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
    last_sync        TEXT,
    error            TEXT                     -- e.g. ITEM_LOGIN_REQUIRED
);

CREATE TABLE IF NOT EXISTS inv_accounts (
    id            TEXT PRIMARY KEY,           -- Plaid account_id
    item_id       TEXT NOT NULL,
    name          TEXT,
    official_name TEXT,
    type          TEXT,
    subtype       TEXT,
    mask          TEXT,
    balance       REAL,
    currency      TEXT DEFAULT 'USD',
    hidden        INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS securities (
    id          TEXT PRIMARY KEY,             -- Plaid security_id
    ticker      TEXT,
    name        TEXT,
    type        TEXT,                         -- cash | cryptocurrency | derivative | equity | etf | fixed income | loan | mutual fund | other
    subtype     TEXT,
    close_price REAL,
    close_as_of TEXT,
    is_cash     INTEGER DEFAULT 0,
    sector      TEXT,
    industry    TEXT,
    currency    TEXT,
    cusip       TEXT,
    isin        TEXT
);

CREATE TABLE IF NOT EXISTS holdings (
    account_id  TEXT NOT NULL,
    security_id TEXT NOT NULL,
    quantity    REAL,
    price       REAL,
    price_as_of TEXT,
    value       REAL,
    cost_basis  REAL,
    currency    TEXT,
    PRIMARY KEY (account_id, security_id)
);

CREATE TABLE IF NOT EXISTS inv_transactions (
    id          TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL,
    security_id TEXT,
    date        TEXT NOT NULL,
    name        TEXT,
    type        TEXT,                          -- buy | sell | cancel | cash | fee | transfer
    subtype     TEXT,
    quantity    REAL,                          -- positive = bought, negative = sold
    amount      REAL,                          -- positive = cash left the account, negative = cash came in
    price       REAL,
    fees        REAL,
    currency    TEXT
);
CREATE INDEX IF NOT EXISTS inv_tx_account_date ON inv_transactions(account_id, date);

CREATE TABLE IF NOT EXISTS inv_snapshots (   -- value of each account on each day we synced
    date       TEXT NOT NULL,
    account_id TEXT NOT NULL,
    value      REAL,
    PRIMARY KEY (date, account_id)
);

CREATE TABLE IF NOT EXISTS auth_pending (       -- sign-ins in progress at the OIDC provider
    state    TEXT PRIMARY KEY,
    nonce    TEXT,
    verifier TEXT,
    next     TEXT,
    created  REAL
);

CREATE TABLE IF NOT EXISTS auth_sessions (       -- signed-in browsers (only a hash of each session token is kept)
    token_hash TEXT PRIMARY KEY,
    sub        TEXT,
    email      TEXT,
    name       TEXT,
    created    REAL,
    expires    REAL,
    id_token   TEXT
);

CREATE TABLE IF NOT EXISTS users (              -- people who have signed in (for account owners)
    sub        TEXT PRIMARY KEY,
    email      TEXT,
    name       TEXT,
    first_name TEXT,
    last_seen  REAL
);

CREATE TABLE IF NOT EXISTS ai_log (              -- one row per request to the AI, shown on the Review tab
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT DEFAULT (datetime('now', 'localtime')),
    purpose     TEXT,                         -- review (the button) | sync (automatic)
    model       TEXT,
    merchants   INTEGER,
    answered    INTEGER,
    new_cats    INTEGER,
    ok          INTEGER,
    seconds     REAL,
    message     TEXT,
    reply       TEXT                          -- the start of what the model said, for troubleshooting
);

CREATE TABLE IF NOT EXISTS assets (              -- things you own that no bank reports: a home, a car
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL,                -- home | vehicle | other
    value           REAL,
    as_of           TEXT,                         -- date the value was set
    source          TEXT DEFAULT 'manual',        -- manual | rentcast
    yearly_change   REAL,                         -- optional % per year applied since as_of (e.g. -15 for a car)
    address         TEXT,
    url             TEXT,                         -- e.g. the Zillow or KBB page, to check by hand
    loan_account_id TEXT,                         -- the mortgage / auto loan against it, for equity
    auto_update     INTEGER DEFAULT 0,            -- homes: refresh from RentCast monthly
    low             REAL,
    high            REAL,
    last_lookup     TEXT,
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS asset_values (
    asset_id INTEGER NOT NULL,
    date     TEXT NOT NULL,
    value    REAL,
    source   TEXT,
    PRIMARY KEY (asset_id, date)
);

CREATE TABLE IF NOT EXISTS networth_snapshots (
    date        TEXT PRIMARY KEY,
    assets      REAL,
    liabilities REAL,
    net         REAL,
    detail      TEXT                              -- JSON: total per group
);

CREATE TABLE IF NOT EXISTS manual_positions (   -- what a balance-only account holds, entered by you
    account_id  TEXT NOT NULL,
    security_id TEXT NOT NULL,
    shares      REAL,
    pct         REAL,                             -- share of each new contribution, %
    last_value  REAL,                             -- for funds without a ticker
    updated     TEXT,
    PRIMARY KEY (account_id, security_id)
);

CREATE TABLE IF NOT EXISTS manual_contributions (  -- new money Runway spotted and invested per your election
    account_id TEXT,
    date       TEXT,
    amount     REAL
);

CREATE TABLE IF NOT EXISTS manual_state (
    account_id   TEXT PRIMARY KEY,
    drift        REAL,                            -- how far the tracked funds are from the synced balance (share of it)
    checked      TEXT,
    last_balance REAL
);

CREATE TABLE IF NOT EXISTS cost_overrides (      -- cost basis you entered yourself; wins over what the institution reports
    account_id  TEXT NOT NULL,
    security_id TEXT NOT NULL,
    cost_basis  REAL NOT NULL,
    PRIMARY KEY (account_id, security_id)
);

CREATE TABLE IF NOT EXISTS holding_snapshots (   -- positions on each day we synced (SimpleFIN has no trade history)
    date        TEXT NOT NULL,
    account_id  TEXT NOT NULL,
    security_id TEXT NOT NULL,
    quantity    REAL,
    value       REAL,
    PRIMARY KEY (date, account_id, security_id)
);

CREATE TABLE IF NOT EXISTS prices (           -- daily closes from the price service (split-adjusted)
    ticker   TEXT NOT NULL,
    date     TEXT NOT NULL,
    close    REAL,
    adjclose REAL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS price_meta (
    ticker     TEXT PRIMARY KEY,
    fetched_at TEXT,
    ok         INTEGER,
    splits     TEXT                            -- JSON list of [date, ratio]
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sync_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT DEFAULT CURRENT_TIMESTAMP,
    ok      INTEGER,
    message TEXT
);
"""

DEFAULT_CATEGORIES = [
    # name, is_transfer, is_income
    ("Groceries", 0, 0),
    ("Restaurants", 0, 0),
    ("Coffee & Snacks", 0, 0),
    ("Shopping", 0, 0),
    ("Travel", 0, 0),
    ("Public Transit", 0, 0),
    ("Rideshare & Taxi", 0, 0),
    ("Auto & Gas", 0, 0),
    ("Parking & Tolls", 0, 0),
    ("Utilities", 0, 0),
    ("Subscriptions", 0, 0),
    ("Technology", 0, 0),
    ("Medical", 0, 0),
    ("Pharmacy", 0, 0),
    ("Home Improvement", 0, 0),
    ("Mortgage", 0, 0),
    ("Loans", 0, 0),
    ("Taxes", 0, 0),
    ("Entertainment", 0, 0),
    ("Extra-Curriculars", 0, 0),
    ("Gifts & Donations", 0, 0),
    ("Fees & Interest", 0, 0),
    ("Other", 0, 0),
    ("Income", 0, 1),
    ("Refunds", 0, 1),
    ("Credit Card Payment", 1, 0),
    ("Transfer", 1, 0),
    ("Ignore", 1, 0),
]


def data_dir() -> str:
    d = os.environ.get("RUNWAY_DATA") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(d, exist_ok=True)
    return d


def db_path() -> str:
    return os.path.join(data_dir(), "runway.db")


def database_url() -> str | None:
    """Postgres connection string, if Runway should use Postgres instead of its SQLite file."""
    return os.environ.get("DATABASE_URL") or None


def using_postgres() -> bool:
    return bool(database_url())


def describe() -> str:
    if using_postgres():
        from urllib.parse import urlsplit
        u = urlsplit(database_url())
        return f"Postgres {u.hostname or 'local'}{':' + str(u.port) if u.port else ''}/{u.path.lstrip('/')}"
    return db_path()


def connect(path: str | None = None):
    if using_postgres():
        from . import pg
        # A path only comes in from tests; each gets its own schema so they don't share data.
        schema = None if path is None else "t_" + __import__("hashlib").sha1(path.encode()).hexdigest()[:12]
        return pg.Connection(database_url(), schema)
    conn = sqlite3.connect(path or db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def session(path: str | None = None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_column(conn, table: str, column: str, decl: str) -> None:
    if using_postgres():
        from . import pg
        pg._table_columns.pop(table, None)
        if column not in pg.columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {pg.translate_ddl(decl)}")
            pg._table_columns.pop(table, None)
        return
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init(path: str | None = None) -> None:
    with session(path) as conn:
        if using_postgres():
            from . import pg
            pg.learn_schema(SCHEMA)
            conn.executescript(pg.translate_ddl(SCHEMA))
            pg.install_helpers(conn)
        else:
            conn.executescript(SCHEMA)
        # Columns added after the first release (existing databases get them on start-up).
        _ensure_column(conn, "transactions", "recurring_id", "INTEGER")   # NULL = not matched, 0 = never match
        _ensure_column(conn, "recurring", "amount_mode", "TEXT DEFAULT 'fixed'")  # fixed | last | avg3
        _ensure_column(conn, "categories", "parent", "TEXT")  # subcategories: name of the top-level category
        _ensure_column(conn, "accounts", "owner", "TEXT")      # a signed-in person's first name, "Joint", or NULL
        _ensure_column(conn, "budgets", "pay_with", "TEXT")    # account id this category is usually paid with
        _ensure_column(conn, "inv_accounts", "source", "TEXT DEFAULT 'plaid'")   # plaid | simplefin
        _ensure_column(conn, "inv_accounts", "institution", "TEXT")
        _ensure_column(conn, "price_meta", "instrument_type", "TEXT")          # EQUITY | ETF | MUTUALFUND | ...
        _ensure_column(conn, "price_meta", "long_name", "TEXT")
        _ensure_column(conn, "recurring", "dates", "TEXT")
        _ensure_column(conn, "manual_state", "baseline", "REAL")   # gap between entered funds and the balance, at entry
        _ensure_column(conn, "cost_overrides", "per_share", "REAL")   # set: cost basis = per_share x shares held
        conn.execute("CREATE INDEX IF NOT EXISTS tx_recurring ON transactions(recurring_id)")
        # v4: the smooth daily "everyday spending" drain became opt-in; switch it off for existing accounts once.
        if not conn.execute("SELECT 1 FROM settings WHERE key='migrated_daily_spend_off'").fetchone():
            conn.execute("UPDATE accounts SET daily_spend=0")
            conn.execute("INSERT INTO settings(key, value) VALUES ('migrated_daily_spend_off', '1')")
        if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO categories(name, is_transfer, is_income) VALUES (?,?,?)", DEFAULT_CATEGORIES
            )


# Categories the app itself relies on; they can't be renamed or removed.
PROTECTED_CATEGORIES = {"Credit Card Payment", "Transfer", "Ignore", "Income", "Refunds"}


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def set_setting(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]
