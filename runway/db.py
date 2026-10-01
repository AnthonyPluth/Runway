"""Runway's database: SQLite (a file in RUNWAY_DATA) by default, or Postgres when DATABASE_URL is set.

SQLAlchemy provides the engine for both, and Alembic keeps the schema (runway/schema.py) up to date: migrations run
when Runway starts. The rest of Runway queries through the small Connection wrapper here, which works the same on
either database (rows read by name or position, `lastrowid`, ...): with SQLAlchemy statements built from the ORM
models in runway/models.py (docs/src/content/docs/contributing/orm.md), or through its ORM Session (`conn.orm`). SQL
text isn't taken: a statement is compiled for whichever database the connection is on.
"""
from __future__ import annotations

import hashlib
import contextlib
import math
import os
import re
import threading
from contextlib import contextmanager
from urllib.parse import urlsplit

from alembic import command
from alembic.config import Config
from sqlalchemy import Integer, MetaData, Table, and_, case, create_engine, event, func, insert, inspect, select
from sqlalchemy.dialects import postgresql as pg_dialect
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateColumn
from sqlalchemy.sql.expression import FunctionElement

from . import monitoring, schema, secretbox
from .models import Account, Category, Setting, Transaction

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
    os.makedirs(d, mode=0o700, exist_ok=True)   # the database and (without RUNWAY_SECRET_KEY) its key: Runway's user only
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
        u = urlsplit(database_url() or "")
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
    from psycopg.adapt import Dumper   # the Postgres driver: only loaded when DATABASE_URL is set

    class Untyped(Dumper):
        """Send values as "unknown" so Postgres fits them to the column, as SQLite's loose typing would
        (a Python int into a TEXT column, '5' compared with an INTEGER, ...)."""
        oid = 0

        def dump(self, obj):
            if isinstance(obj, bool):
                return b"1" if obj else b"0"
            return str(obj).encode()

    # Only a short, stable name for a test's schema, not a secret; changing the hash would orphan existing test schemas.
    test_schema = None if path is None else "t_" + hashlib.sha1(path.encode(), usedforsecurity=False).hexdigest()[:12]
    # Tests make an engine per database; they don't keep connections open, so they don't run Postgres out of them.
    eng = (create_engine(url, poolclass=NullPool) if test_schema
           else create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=60))   # up to 64 requests plus syncs at once

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

    def scalar(self):
        """The first column of the first row, or None if there are no rows (`SELECT COUNT(*) ...` -> the count)."""
        row = self.fetchone()
        return row[0] if row is not None else None

    def scalars(self) -> list:
        """The first column of every row."""
        return [r[0] for r in self.fetchall()]


class Connection:
    """A database connection and its open transaction: one per request or sync, never shared between threads.

    `execute()` takes a SQLAlchemy statement (`select(Account.id).where(...)`, `update(Asset)...`; see
    docs/src/content/docs/contributing/orm.md). `orm` is an ORM Session on this same connection and transaction, for
    loading and changing model objects. Either way, `commit()` commits everything so far (and is what code calls before
    a slow network request, so the write lock isn't held through it); `rollback()` undoes it. `sa` is the SQLAlchemy
    connection underneath.
    """

    def __init__(self, sa_conn):
        self.sa = sa_conn
        self.postgres = sa_conn.dialect.name == "postgresql"
        self._orm: Session | None = None

    @property
    def orm(self) -> Session:
        """An ORM Session sharing this connection and its transaction, made on first use.

        If the connection is already in a transaction, the Session joins it ("rollback_only": its commit() doesn't
        commit the connection's transaction; a rollback does roll it back); otherwise it begins the transaction itself.
        Either way, only Connection.commit()/rollback() should end it: they flush the Session and commit or roll back
        both together. Objects stay readable after a commit (expire_on_commit=False)."""
        if self._orm is None:
            self._orm = Session(bind=self.sa, join_transaction_mode="rollback_only", expire_on_commit=False,
                                autoflush=True)
        return self._orm

    def _before(self) -> None:
        # A statement run here doesn't go through the Session: write out its pending changes first, so it sees them.
        if self._orm is not None:
            self._orm.flush()

    def _after_write(self) -> None:
        # ... and objects the Session already loaded may be out of date after an UPDATE or DELETE run here.
        if self._orm is not None and self._orm.identity_map:
            self._orm.expire_all()

    def execute(self, stmt, params=None) -> Result:
        """Run a SQLAlchemy statement (params: a dict, or a list of dicts for many rows)."""
        if isinstance(stmt, str):
            raise TypeError("SQL text isn't supported; build a statement (docs/src/content/docs/contributing/orm.md)")
        if isinstance(params, list) and not params:   # no rows: nothing to do (not one row of defaults)
            return Result(rows=[])
        self._before()
        res = self.sa.execute(stmt, params) if params is not None else self.sa.execute(stmt)
        lastrowid = None
        ctx = getattr(res, "context", None)
        if ctx is not None and ctx.isinsert and not ctx.executemany:
            try:
                pk = res.inserted_primary_key
                lastrowid = pk[0] if pk is not None and len(pk) == 1 else None
            except InvalidRequestError:   # e.g. an INSERT ... SELECT: there's no one new row
                lastrowid = None
        if getattr(stmt, "is_dml", False):
            self._after_write()
        return Result(res, lastrowid)

    def commit(self) -> None:
        if self._orm is not None:
            self._orm.commit()   # flushes; commits the transaction if the Session began it, else leaves that to us
        self.sa.commit()         # nothing to do if the Session just committed it

    def rollback(self) -> None:
        if self._orm is not None:
            self._orm.rollback()
        self.sa.rollback()

    def close(self) -> None:
        if self._orm is not None:
            self._orm.close()
            self._orm = None
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


# ------------------------------------------------------------------------------------------------ SQL helpers
# Portable pieces for SQLAlchemy statements (docs/src/content/docs/contributing/orm.md): each works the same on SQLite
# and Postgres.

def dialect_insert(conn: Connection, entity):
    """An INSERT for this connection's database that can take .on_conflict_do_update()/.on_conflict_do_nothing()
    (SQLite and Postgres both have ON CONFLICT, but SQLAlchemy builds it with each one's own insert())."""
    return (pg_dialect if conn.postgres else sqlite_dialect).insert(entity)


def upsert(conn: Connection, entity, values: dict | list[dict], key: list[str], update=None) -> Result:
    """INSERT ... ON CONFLICT(key) DO UPDATE SET col=excluded.col, for one row (a dict of column name -> value) or
    many (a list of dicts, all with the same keys).

    `update` picks what a conflict changes: None = every column given except the key; a list of column names; or a
    function taking the would-be row (`excluded`) and returning {column name: expression}, for anything else
    (`lambda ex: {"value": func.coalesce(ex.value, Asset.value)}`). Nothing to update means ON CONFLICT DO NOTHING."""
    many = isinstance(values, list)
    if many and not values:
        return Result(rows=[])
    stmt = dialect_insert(conn, entity)
    if not many:
        stmt = stmt.values(values)
    cols = values[0] if many else values
    if callable(update):
        set_ = update(stmt.excluded)
    else:
        set_ = {c: stmt.excluded[c] for c in (update if update is not None else [c for c in cols if c not in key])}
    stmt = stmt.on_conflict_do_update(index_elements=key, set_=set_) if set_ else stmt.on_conflict_do_nothing(index_elements=key)
    return conn.execute(stmt, values if many else None)


def insert_ignore(conn: Connection, entity, values: dict | list[dict], key: list[str] | None = None) -> Result:
    """INSERT ... ON CONFLICT DO NOTHING: rows already there (by `key`, or by any unique constraint) are left alone."""
    many = isinstance(values, list)
    if many and not values:
        return Result(rows=[])
    stmt = dialect_insert(conn, entity)
    if not many:
        stmt = stmt.values(values)
    return conn.execute(stmt.on_conflict_do_nothing(index_elements=key), values if many else None)


class instr(FunctionElement):
    """SQLite's instr(haystack, needle): where needle first appears in haystack, from 1; 0 if it doesn't.
    (Postgres calls it strpos.) Case-sensitive, and without LIKE's wildcards, so safe for any text."""
    type = Integer()
    inherit_cache = True
    name = "instr"


@compiles(instr)
def _instr(element, compiler, **kw):
    return f"instr({compiler.process(element.clauses, **kw)})"


@compiles(instr, "postgresql")
def _instr_postgres(element, compiler, **kw):
    return f"strpos({compiler.process(element.clauses, **kw)})"


def account_label_expr(a=None):
    """account_label as a SQLAlchemy expression, for `a` (models.Account, or an aliased(Account)); label it yourself:
    `select(Account.id, db.account_label_expr().label("name"))`."""
    a = Account if a is None else a
    name = func.coalesce(a.display_name, a.name)
    return name + case((and_(a.owner.is_not(None), a.owner != "", instr(func.lower(name), func.lower(a.owner)) == 0),
                        " (" + a.owner + ")"), else_="")


# ------------------------------------------------------------------------------------------------ schema

def alembic_config(connection=None):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini")) if os.path.exists(os.path.join(root, "alembic.ini")) else Config()
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations"))
    cfg.attributes["connection"] = connection
    return cfg


def _baseline_columns() -> dict[str, set[str]]:
    """The tables and columns as of the baseline migration (later migrations add the rest)."""
    with create_engine("sqlite://").begin() as c:
        command.upgrade(alembic_config(c), BASELINE)
        insp = inspect(c)
        return {t: {col["name"] for col in insp.get_columns(t)} for t in insp.get_table_names() if t != "alembic_version"}


def _upgrade_legacy(sa_conn) -> None:
    """Databases made before Runway used migrations: add the columns that were added over time, so they match
    the baseline migration, which is then recorded as done (and later migrations run as usual)."""
    baseline = _baseline_columns()
    insp = inspect(sa_conn)
    have = set(insp.get_table_names())
    for table in schema.metadata.sorted_tables:
        if table.name not in baseline:
            continue
        if table.name not in have:   # the table as it was then; later migrations add to it
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
                if all(c.name in baseline[table.name] for c in index.columns):   # later ones come with their migration
                    index.create(sa_conn, checkfirst=True)
    if sa_conn.dialect.name == "postgresql":
        sa_conn.exec_driver_sql(schema.POSTGRES_INSTR)


MIGRATE_LOCK = 0x52554E574159   # "RUNWAY": Postgres advisory lock key, held while one process migrates
SCHEMA_LOCK = 0x52554E57        # "RUNW": with the schema's hash, the lock for migrating a test's own schema


def migrate(path: str | None = None) -> None:
    """Bring the database's schema up to date. On Postgres, processes starting together (several copies of Runway, or the
    tests running in parallel) take turns: the others wait for the first to finish, then find nothing left to do.
    A test's own schema (a path) is a database of its own, so it has a lock of its own: tests migrating different
    schemas don't queue behind each other, while two migrating the same one still take turns."""
    with engine(path).begin() as sa_conn:
        if sa_conn.dialect.name == "postgresql":   # the lock is released when this transaction ends
            if path is None:
                sa_conn.exec_driver_sql(f"SELECT pg_advisory_xact_lock({MIGRATE_LOCK})")
            else:
                sa_conn.exec_driver_sql(f"SELECT pg_advisory_xact_lock({SCHEMA_LOCK}, hashtext(current_schema()))")
        tables = set(inspect(sa_conn).get_table_names())
        cfg = alembic_config(sa_conn)
        if tables and "alembic_version" not in tables:
            _upgrade_legacy(sa_conn)
            command.stamp(cfg, BASELINE)
        command.upgrade(cfg, "head")


def init(path: str | None = None) -> None:
    migrate(path)
    if not using_postgres():
        private_files(path or db_path())
    with session(path) as conn:
        secretbox.encrypt_stored(conn)   # secrets saved by earlier versions, or under an older key
        if conn.execute(select(func.count()).select_from(Category)).fetchone()[0] == 0:
            conn.execute(insert(Category), [{"name": n, "is_transfer": t, "is_income": i} for n, t, i in DEFAULT_CATEGORIES])


def private_files(path: str) -> None:
    """The SQLite database (and its journal files, which SQLite makes with the same mode) readable by Runway's user
    only: it's created with the process's umask, usually readable by everyone on the machine."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        with contextlib.suppress(OSError):
            os.chmod(path + suffix, 0o600)


def not_investment(account_id=None):
    """Transactions in investment accounts (buys, sells, dividends) live on the Investments page, not in Transactions,
    Review or the review count: this is the condition that leaves them out, `.where(db.not_investment())` (or pass
    the account id column, e.g. a subquery's `p.c.account_id`; the default is Transaction.account_id)."""
    col = Transaction.account_id if account_id is None else account_id
    return col.not_in(select(Account.id).where(Account.kind == "investment"))


# Categories the app itself relies on; they can't be renamed or removed.
PROTECTED_CATEGORIES = {"Credit Card Payment", "Transfer", "Ignore", "Income", "Refunds"}


MAX_NUMBER = 1e12   # more than any amount, price or count here; a sum of bigger ones overflows to inf


def number(value) -> float:
    """float(), for a number someone typed or sent: "nan" and "inf" are refused (float() takes them, and one saved
    would spoil every sum it's in, or stop the sync that uses it), and so is anything past MAX_NUMBER (a product of
    two such overflows to inf just the same, and inf isn't JSON)."""
    n = float(value)
    if math.isnan(n) or n in (float("inf"), float("-inf")):
        raise ValueError(f"{value!r} isn't a number")
    if abs(n) > MAX_NUMBER:
        raise ValueError(f"{value!r} is too large")
    return n


def get_setting(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute(select(Setting.value).where(Setting.key == key)).fetchone()
    value = row["value"] if row and row["value"] is not None else None
    if value is not None and key in secretbox.SECRET_SETTINGS:
        try:
            value = secretbox.decrypt(value)
        except secretbox.SecretError as e:   # the key changed: behave as if it was never entered, and say why
            monitoring.log(f"Warning: {key}: {e}", "warning")
            value = None
    return value if value is not None else default


def set_setting(conn, key: str, value: str | None) -> None:
    if value is not None and key in secretbox.SECRET_SETTINGS:
        value = secretbox.encrypt(value)   # secrets are stored encrypted (runway/secretbox.py)
    upsert(conn, Setting, {"key": key, "value": value}, key=["key"])


def account_label(a) -> str:
    """How an account is named in lists: your name for it, plus whose it is ("AAdvantage (Sara)") when an owner is set
    and the name doesn't already say so."""
    name = a["display_name"] or a["name"]
    owner = a["owner"] if "owner" in a.keys() else None   # a row: `in` alone would search its values  # noqa: SIM118
    return f"{name} ({owner})" if owner and owner.lower() not in name.lower() else name


def rows(cur) -> list[dict]:
    """Every row of a result as a dict (column name -> value), from Connection.execute() or a Session's execute()."""
    if hasattr(cur, "mappings"):   # a SQLAlchemy Result (conn.orm.execute(...))
        return [dict(m) for m in cur.mappings()]
    return [dict(r) for r in cur.fetchall()]


def as_dict(obj) -> dict:
    """A model object's columns as a dict, in the table's order: what `SELECT *` gave as a row."""
    return {a.key: getattr(obj, a.key) for a in inspect(obj).mapper.column_attrs}
