"""Runway's database: SQLite (a file in RUNWAY_DATA) by default, or Postgres when DATABASE_URL is set.

SQLAlchemy provides the engine for both, and Alembic keeps the schema (runway/schema.py) up to date: migrations run
when Runway starts. The rest of Runway writes plain SQL with `?` placeholders against the small Connection
wrapper here, which works the same on either database (rows read by name or position, `lastrowid`, ...).
Queries stick to SQL both databases understand (`ON CONFLICT`, `COALESCE`, ...); `instr()` is added to Postgres.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from . import schema, secretbox

BASELINE = "0001"   # the first migration: the schema as it was before Runway used migrations

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


def engine_url(path: str | None = None) -> str:
    url = database_url()
    if url:   # postgres://... and postgresql://... both mean "Postgres through psycopg 3"
        return re.sub(r"^postgres(ql)?(\+\w+)?://", "postgresql+psycopg://", url)
    return f"sqlite:///{path or db_path()}"


def describe() -> str:
    if using_postgres():
        from urllib.parse import urlsplit
        u = urlsplit(database_url())
        return f"Postgres {u.hostname or 'local'}{':' + str(u.port) if u.port else ''}/{u.path.lstrip('/')}"
    return db_path()


# ------------------------------------------------------------------------------------------------ engines

_engines: dict[tuple, Engine] = {}
_engines_lock = threading.Lock()


def engine(path: str | None = None) -> Engine:
    """One engine per database. On Postgres, a path (only ever passed by tests) picks a schema of its own, so each
    test gets an empty database."""
    key = (engine_url(path), path if using_postgres() else None)
    with _engines_lock:
        if key not in _engines:
            _engines[key] = _postgres_engine(key[0], path) if using_postgres() else _sqlite_engine(key[0])
        return _engines[key]


def _sqlite_engine(url: str) -> Engine:
    # A fresh connection per request, as before; SQLite connections are cheap and this keeps locks short.
    eng = create_engine(url, poolclass=NullPool, connect_args={"timeout": 30, "check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _setup(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return eng


def _postgres_engine(url: str, path: str | None) -> Engine:
    from psycopg.adapt import Dumper

    class Untyped(Dumper):
        """Send values as "unknown" so Postgres fits them to the column, as SQLite's loose typing would
        (a Python int into a TEXT column, '5' compared with an INTEGER, ...)."""
        oid = 0

        def dump(self, obj):
            if isinstance(obj, bool):
                return b"1" if obj else b"0"
            return str(obj).encode()

    test_schema = None if path is None else "t_" + hashlib.sha1(path.encode()).hexdigest()[:12]
    # Tests make an engine per database; they don't keep connections open, so they don't run Postgres out of them.
    eng = (create_engine(url, poolclass=NullPool) if test_schema
           else create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10))

    @event.listens_for(eng, "connect")
    def _setup(dbapi_conn, _record):
        for t in (str, int, float, bool):
            dbapi_conn.adapters.register_dumper(t, Untyped)
        if test_schema:
            dbapi_conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{test_schema}"')
            dbapi_conn.execute(f'SET search_path TO "{test_schema}"')
            dbapi_conn.commit()
    return eng


# ------------------------------------------------------------------------------------------------ connections

class Row(tuple):
    """A result row, read by name (row["id"]) or position (row[0]); dict(row) works too."""
    __slots__ = ()
    _idx: dict = {}

    def __getitem__(self, k):
        if isinstance(k, str):
            return tuple.__getitem__(self, self._idx[k])
        return tuple.__getitem__(self, k)

    def keys(self):
        return list(self._idx)


class Result:
    def __init__(self, res=None, lastrowid=None, rows=None):
        self._res, self.lastrowid = res, lastrowid
        self._make = None
        if res is not None and res.returns_rows:
            cls = type("Row", (Row,), {"__slots__": (), "_idx": {k: i for i, k in enumerate(res.keys())}})
            self._make = cls
        self._rows = rows

    @property
    def rowcount(self) -> int:
        return self._res.rowcount if self._res is not None else 0

    def fetchone(self):
        if self._rows is not None:
            return self._rows.pop(0) if self._rows else None
        if self._make is None:
            return None
        r = self._res.fetchone()
        return self._make(r) if r is not None else None

    def fetchall(self) -> list:
        if self._rows is not None:
            out, self._rows = self._rows, []
            return out
        if self._make is None:
            return []
        return [self._make(r) for r in self._res.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


def _outside_quotes(sql: str, fn) -> str:
    parts = re.split(r"('(?:[^']|'')*')", sql)
    return "".join(p if i % 2 else fn(p) for i, p in enumerate(parts))


_pg_sql: dict[tuple, tuple[str, bool]] = {}


def _postgres_sql(sql: str, has_params: bool) -> tuple[str, bool]:
    """`?` placeholders become psycopg's `%s`; inserts into tables with an auto-numbered id return it."""
    key = (sql, has_params)
    if key not in _pg_sql:
        # psycopg reads % as the start of a placeholder even without parameters (SQLAlchemy always passes some).
        q = sql.replace("%", "%%")
        if has_params:
            q = _outside_quotes(q, lambda p: p.replace("?", "%s"))
        m = re.match(r"\s*INSERT INTO (\w+)", q, re.I)
        want_id = bool(m and m.group(1) in schema.AUTO_ID and "RETURNING" not in q.upper())
        if want_id:
            q = q.rstrip().rstrip(";") + " RETURNING id"
        _pg_sql[key] = (q, want_id)
    return _pg_sql[key]


class Connection:
    """A database connection (and its open transaction) that takes SQL with `?` placeholders."""

    def __init__(self, sa_conn):
        self.sa = sa_conn
        self.postgres = sa_conn.dialect.name == "postgresql"

    def execute(self, sql: str, params=()) -> Result:
        params = tuple(params) if params is not None else ()
        if not self.postgres:
            res = self.sa.exec_driver_sql(sql, params)
            return Result(res, res.lastrowid if res.lastrowid else None)
        if sql.lstrip()[:6].upper() == "PRAGMA":   # SQLite settings: nothing to do on Postgres
            return Result(rows=[])
        q, want_id = _postgres_sql(sql, bool(params))
        res = self.sa.exec_driver_sql(q, params)
        if want_id:
            row = res.fetchone()
            return Result(res, row[0] if row else None, rows=[])
        return Result(res)

    def executemany(self, sql: str, seq) -> None:
        seq = [tuple(p) for p in seq]
        if not seq:
            return
        q = _postgres_sql(sql, True)[0].replace(" RETURNING id", "") if self.postgres else sql
        self.sa.exec_driver_sql(q, seq)

    def commit(self) -> None:
        self.sa.commit()

    def rollback(self) -> None:
        self.sa.rollback()

    def close(self) -> None:
        self.sa.close()


def connect(path: str | None = None) -> Connection:
    return Connection(engine(path).connect())


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


# ------------------------------------------------------------------------------------------------ schema

def alembic_config(connection=None):
    from alembic.config import Config
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini")) if os.path.exists(os.path.join(root, "alembic.ini")) else Config()
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations"))
    cfg.attributes["connection"] = connection
    return cfg


def _baseline_columns() -> dict[str, set[str]]:
    """The tables and columns as of the baseline migration (later migrations add the rest)."""
    from alembic import command
    with create_engine("sqlite://").begin() as c:
        command.upgrade(alembic_config(c), BASELINE)
        insp = inspect(c)
        return {t: {col["name"] for col in insp.get_columns(t)} for t in insp.get_table_names() if t != "alembic_version"}


def _upgrade_legacy(sa_conn) -> None:
    """Databases made before Runway used migrations: add the columns that were added over time, so they match
    the baseline migration, which is then recorded as done (and later migrations run as usual)."""
    from sqlalchemy.schema import CreateColumn
    baseline = _baseline_columns()
    insp = inspect(sa_conn)
    have = set(insp.get_table_names())
    for table in schema.metadata.sorted_tables:
        if table.name not in baseline:
            continue
        if table.name not in have:   # the table as it was then; later migrations add to it
            from sqlalchemy import MetaData, Table
            Table(table.name, MetaData(), *[c._copy() for c in table.columns if c.name in baseline[table.name]],
                  sqlite_autoincrement=table.kwargs.get("sqlite_autoincrement", False)).create(sa_conn)
            continue
        cols = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in cols and col.name in baseline[table.name]:
                ddl = CreateColumn(col).compile(dialect=sa_conn.dialect)
                sa_conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {ddl}")
    for table in schema.metadata.sorted_tables:
        if table.name in baseline:
            for index in table.indexes:
                index.create(sa_conn, checkfirst=True)
    if sa_conn.dialect.name == "postgresql":
        sa_conn.exec_driver_sql(schema.POSTGRES_INSTR)


def migrate(path: str | None = None) -> None:
    """Bring the database's schema up to date."""
    from alembic import command
    with engine(path).begin() as sa_conn:
        tables = set(inspect(sa_conn).get_table_names())
        cfg = alembic_config(sa_conn)
        if tables and "alembic_version" not in tables:
            _upgrade_legacy(sa_conn)
            command.stamp(cfg, BASELINE)
        command.upgrade(cfg, "head")


def init(path: str | None = None) -> None:
    migrate(path)
    with session(path) as conn:
        # v4: the smooth daily "everyday spending" drain became opt-in; switch it off for existing accounts once.
        if not conn.execute("SELECT 1 FROM settings WHERE key='migrated_daily_spend_off'").fetchone():
            conn.execute("UPDATE accounts SET daily_spend=0")
            conn.execute("INSERT INTO settings(key, value) VALUES ('migrated_daily_spend_off', '1')")
        secretbox.encrypt_stored(conn)   # secrets saved by earlier versions, or under an older key
        if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO categories(name, is_transfer, is_income) VALUES (?,?,?)", DEFAULT_CATEGORIES
            )


# Categories the app itself relies on; they can't be renamed or removed.
PROTECTED_CATEGORIES = {"Credit Card Payment", "Transfer", "Ignore", "Income", "Refunds"}


def get_setting(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    value = row["value"] if row and row["value"] is not None else None
    if value is not None and key in secretbox.SECRET_SETTINGS:
        try:
            value = secretbox.decrypt(value)
        except secretbox.SecretError as e:   # the key changed: behave as if it was never entered, and say why
            print(f"Warning: {key}: {e}", flush=True)
            value = None
    return value if value is not None else default


def set_setting(conn, key: str, value: str | None) -> None:
    if value is not None and key in secretbox.SECRET_SETTINGS:
        value = secretbox.encrypt(value)   # secrets are stored encrypted (runway/secretbox.py)
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def account_label(a) -> str:
    """How an account is named in lists: your name for it, plus whose it is ("AAdvantage (Sara)") when an owner is set
    and the name doesn't already say so."""
    name = a["display_name"] or a["name"]
    owner = a["owner"] if "owner" in a.keys() else None
    return f"{name} ({owner})" if owner and owner.lower() not in name.lower() else name


def label_sql(alias: str = "a") -> str:
    """account_label as SQL (SQLite and Postgres)."""
    n = f"COALESCE({alias}.display_name, {alias}.name)"
    return (f"({n} || CASE WHEN {alias}.owner IS NOT NULL AND {alias}.owner <> '' AND instr(lower({n}), lower({alias}.owner)) = 0 "
            f"THEN ' (' || {alias}.owner || ')' ELSE '' END)")


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]
