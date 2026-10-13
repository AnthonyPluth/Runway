"""For tests that share one database with tests in other processes (on Postgres every test module uses the same schema
unless it passes its own path, and CI runs the modules in parallel with unittest-parallel).

A test that writes settings, or runs something that does (a sync, the AI categorizer, a request through the server),
takes a database of its own with own_database(), so nothing it writes reaches another module's test: a test left
last_sync_ok in the shared schema and failed another module's on 2026-09-30.

The "Let assistants change churning", "Let assistants categorize" and "Let assistants change anything" switches are
settings rows: a test that sets one, or depends on one, in the shared schema holds mcp_switch() (for all three) so another process doesn't flip it in the middle (one with a database of its own needn't). Everything else a test makes, it should find and remove
by its own names and ids, never by clearing a table.
"""
import fcntl
import hashlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
import weakref
from contextlib import contextmanager
from datetime import UTC, date, datetime
from unittest import mock

from sqlalchemy import delete, func, insert, select, update

from runway.storage.models import (Account, CardStatement, ManualStatement, OAuthClient, OAuthCode, OAuthConsent,
                                   OAuthGrant, OAuthToken, Transaction)

LOCK = os.path.join(tempfile.gettempdir(), "runway-tests-mcp-switch.lock")

# Seconds this process has spent in each phase of making and giving back databases ("schema", "teardown"), and the
# connections the schema pool had to end to give a schema back: what tests/timing_result.py reports.
PHASES: dict[str, float] = {}
ENDED = [0]


@contextmanager
def phase(name):
    """Count the block's time towards PHASES[name]."""
    started = time.perf_counter()
    try:
        yield
    finally:
        PHASES[name] = PHASES.get(name, 0.0) + time.perf_counter() - started


@contextmanager
def mcp_switch():
    """Hold the switch (across processes) until the block ends."""
    with open(LOCK, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def hold_mcp_switch(case) -> None:
    """In setUp (or setUpClass, with the class): hold the switch until the test (or class) is cleaned up."""
    cm = mcp_switch()
    cm.__enter__()
    (case.addClassCleanup if isinstance(case, type) else case.addCleanup)(cm.__exit__, None, None, None)


def tag() -> str:
    """A name no other test (or run) uses."""
    return uuid.uuid4().hex[:10]


def forget_oauth(conn, client_ids) -> None:
    """Remove these OAuth clients and everything under them (their grants, codes, tokens and consent pages)."""
    for cid in client_ids:
        grants = [r[0] for r in conn.execute(select(OAuthGrant.id).where(OAuthGrant.client_id == cid)).fetchall()]
        for gid in grants:
            conn.execute(delete(OAuthToken).where(OAuthToken.grant_id == gid))
            conn.execute(delete(OAuthCode).where(OAuthCode.grant_id == gid))
        conn.execute(delete(OAuthGrant).where(OAuthGrant.client_id == cid))
        conn.execute(delete(OAuthConsent).where(OAuthConsent.params.like(f'%"{cid}"%')))
        conn.execute(delete(OAuthClient).where(OAuthClient.id == cid))


LOCK_WAIT = "200ms"     # how long giving a schema back waits for a connection the test left open before ending it


def _pg():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)


def _end_holders(conn, name) -> None:
    """End the connections holding locks in this schema (a test left a transaction open): they belong to the test that
    is done with it."""
    from psycopg import sql
    ended = conn.execute(sql.SQL(
        "SELECT pg_terminate_backend(pid) FROM (SELECT DISTINCT l.pid FROM pg_locks l JOIN pg_class c ON c.oid = l.relation "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = {} AND l.pid <> pg_backend_pid()) holders"
    ).format(sql.Literal(name))).fetchall()
    ENDED[0] += len(ended)


def _run_ending_holders(conn, name, *statements) -> None:
    """Run statements (in one transaction) that need the schema's tables to themselves, ending whatever connection
    still holds one after a short wait (waiting for it would wait forever)."""
    import psycopg
    from psycopg import sql
    conn.execute(sql.SQL("SET lock_timeout = {}").format(sql.Literal(LOCK_WAIT)))

    def run():
        with conn.transaction():
            for statement in statements:
                conn.execute(statement)
    try:
        run()
    except psycopg.errors.LockNotAvailable:
        _end_holders(conn, name)
        run()


class SchemaPool:
    """On Postgres, the databases this test process lends its tests: schemas made once (db.init, so db.create_fresh)
    and emptied when a test gives one back, instead of a schema made and dropped for each test, which was most of the
    Postgres run's time. A schema is lent to one test at a time. Giving it back ends the connections the test left
    holding locks in it, deletes every row (with the foreign keys checked at the end, so in any order), restarts the
    sequences that were used and copies back the rows db.init seeds (the categories) from a template schema. That
    gives what TRUNCATE ... RESTART IDENTITY CASCADE would, several times faster: a TRUNCATE of every table makes
    new files for all of them, even on a throwaway server. One whose tables a test changed is dropped instead.

    Schemas are named p_<host>_<pid>_<n>. A test process doesn't get to clean up after itself (unittest-parallel ends
    its workers), so each new pool drops the ones left by processes on this machine that are gone."""

    def __init__(self):
        self.free: list[str] = []
        self.made = 0
        self.template: str | None = None
        self.prefix = ""
        self.fingerprint = ""
        self.seeded: list[str] = []
        self.tables: list[str] = []

    @staticmethod
    def _fingerprint(conn, name) -> str:
        """Everything about the schema a test could change and the next one would notice: its tables' columns (type,
        nullable, default), constraints (keys, foreign keys, checks), indexes, sequences, functions and the revision
        alembic_version records (a test that downgrades a migration changes the keys and the revision, not the
        columns). The schema's own name is left out, so two schemas built the same way match."""
        from psycopg import sql
        ns = sql.SQL("to_regnamespace({})").format(sql.Literal(name))
        parts = [
            sql.SQL("SELECT c.relname || '.' || a.attname || ':' || format_type(a.atttypid, a.atttypmod) || ':' || "
                    "a.attnotnull || ':' || coalesce(pg_get_expr(d.adbin, d.adrelid), '') FROM pg_class c "
                    "JOIN pg_attribute a ON a.attrelid = c.oid LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum "
                    "WHERE c.relnamespace = {} AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped").format(ns),
            sql.SQL("SELECT t.relname || ':' || k.conname || ':' || pg_get_constraintdef(k.oid) FROM pg_constraint k "
                    "JOIN pg_class t ON t.oid = k.conrelid WHERE k.connamespace = {}").format(ns),
            sql.SQL("SELECT indexname || ':' || indexdef FROM pg_indexes WHERE schemaname = {}").format(sql.Literal(name)),
            sql.SQL("SELECT 'sequence:' || relname FROM pg_class WHERE relnamespace = {} AND relkind = 'S'").format(ns),
            sql.SQL("SELECT 'function:' || p.oid::regprocedure FROM pg_proc p WHERE p.pronamespace = {}").format(ns),
        ]
        rows = [r[0] for part in parts for r in conn.execute(part)]
        if conn.execute(sql.SQL("SELECT to_regclass({})").format(sql.Literal(f'"{name}".alembic_version'))).fetchone()[0]:
            rows += ["revision:" + r[0] for r in conn.execute(sql.SQL("SELECT version_num FROM {}").format(
                sql.Identifier(name, "alembic_version")))]
        return "\n".join(sorted(r.replace(name, "<schema>") for r in rows))

    def _make(self, name, path) -> None:
        from psycopg import sql

        from runway.storage import db
        with _pg() as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))
        db.TEST_SCHEMAS[path] = name
        db.init(path)

    def _drop_leftovers(self) -> None:
        from psycopg import sql
        host = hashlib.sha1(socket.gethostname().encode(), usedforsecurity=False).hexdigest()[:6]
        self.prefix = f"p_{host}_{os.getpid()}_"
        with _pg() as conn:
            pattern = f"p\\_{host}\\_%"
            for (name,) in conn.execute(sql.SQL("SELECT nspname FROM pg_namespace WHERE nspname LIKE {}").format(
                    sql.Literal(pattern))).fetchall():
                try:
                    os.kill(int(name.split("_")[2]), 0)
                except ProcessLookupError:
                    conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))
                except (PermissionError, ValueError, IndexError):
                    pass    # another user's live process, or not one of ours

    def start(self) -> None:
        from psycopg import sql

        from runway.storage import db
        self._drop_leftovers()
        self.template = self.prefix + "template"
        self._make(self.template, self.template)
        db.TEST_SCHEMAS.pop(self.template, None)
        db.forget_engine(self.template)
        with _pg() as conn:
            self.tables = [r[0] for r in conn.execute(sql.SQL("SELECT tablename FROM pg_tables WHERE schemaname = {} "
                                                              "ORDER BY 1").format(sql.Literal(self.template)))
                           if r[0] != "alembic_version"]
            # The rows db.init seeds (the categories): whatever the template has, so a new seed is restored too.
            self.seeded = [t for t in self.tables if conn.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM {})").format(
                sql.Identifier(self.template, t))).fetchone()[0]]
            self.fingerprint = self._fingerprint(conn, self.template)

    def acquire(self, path) -> str:
        """Lend this path a schema (db.session(path) and the rest use it until it's given back); returns its name."""
        from runway.storage import db
        if self.template is None:
            self.start()
        if self.free:
            db.TEST_SCHEMAS[path] = self.free.pop()
        else:
            self.made += 1
            self._make(f"{self.prefix}{self.made}", path)
        return db.TEST_SCHEMAS[path]

    def release(self, path) -> None:
        """Take the path's schema back, empty, for the next test; one whose tables the test changed (or that can't be
        emptied) is dropped instead."""
        from psycopg import sql

        from runway.storage import db
        name = db.TEST_SCHEMAS.pop(path)
        db.forget_engine(path)
        with _pg() as conn:
            try:
                _end_holders(conn, name)    # what the test left running mustn't write into the next one's database
                conn.execute(sql.SQL("SET lock_timeout = {}").format(sql.Literal(LOCK_WAIT)))   # else: dropped, below
                if self._fingerprint(conn, name) != self.fingerprint:
                    raise LookupError(name)
                filled = {r[0] for r in conn.execute(sql.SQL(" UNION ALL ").join(
                    sql.SQL("SELECT {} WHERE EXISTS (SELECT 1 FROM {})").format(sql.Literal(t), sql.Identifier(name, t))
                    for t in self.tables))}
                used = [r[0] for r in conn.execute(sql.SQL("SELECT sequencename FROM pg_sequences WHERE schemaname = {} "
                                                           "AND last_value IS NOT NULL").format(sql.Literal(name)))]
                with conn.transaction():
                    conn.execute(sql.SQL("SET CONSTRAINTS ALL DEFERRED"))   # every foreign key is (schema.refers)
                    for t in [t for t in self.tables if t in filled]:
                        conn.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(name, t)))
                    for seq in used:
                        conn.execute(sql.SQL("ALTER SEQUENCE {} RESTART").format(sql.Identifier(name, seq)))
                    for t in self.seeded:
                        conn.execute(sql.SQL("INSERT INTO {} SELECT * FROM {}").format(sql.Identifier(name, t),
                                                                                       sql.Identifier(self.template, t)))
            except Exception:
                _run_ending_holders(conn, name, sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))
                return
        self.free.append(name)


POOL = SchemaPool()


def drop_schema(path) -> None:
    """On Postgres, drop the schema db.init(path) made for this path (db.schema_for names it from the path); SQLite has
    nothing to drop: the file goes with its directory. A schema the pool lent the path is the pool's to take back."""
    from runway.storage import db
    if not db.using_postgres() or path in db.TEST_SCHEMAS:
        return
    from psycopg import sql
    name = db.schema_for(path)
    db.forget_engine(path)
    with phase("teardown"), _pg() as conn:
        # A connection the test left open (a transaction never ended) holds locks that would make the drop wait forever.
        _run_ending_holders(conn, name, sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))


def release_database(path) -> None:
    with phase("teardown"):
        POOL.release(path)


def add_database(case, path) -> str:
    """A new database at this path, all of it gone when the test (or class, in setUpClass) is cleaned up: on SQLite
    db.init(path) (with the file in the test's own directory), on Postgres a schema borrowed from this process's pool
    (SchemaPool), already made, emptied when it's given back. own_database does this for its own; a test that needs a
    second database (a restore target, say) makes it with this. Returns the path."""
    from runway.storage import db
    later = case.addClassCleanup if isinstance(case, type) else case.addCleanup
    with phase("schema"):
        if db.using_postgres():
            POOL.acquire(path)
            later(release_database, path)
        else:
            db.init(path)
    return path


def scratch_dir(case) -> str:
    """A directory removed when the test (or class) is cleaned up."""
    tmp = tempfile.TemporaryDirectory()
    (case.addClassCleanup if isinstance(case, type) else case.addCleanup)(tmp.cleanup)
    return tmp.name


def database_path(case, name="runway.db") -> str:
    """The path of a database a test makes itself (db.init, db.migrate, or tables made by hand), in a directory of its
    own, with its schema dropped on Postgres and the directory removed when the test is cleaned up. Nothing is created
    yet, and db.session() isn't pointed at it (own_database does that)."""
    path = os.path.join(scratch_dir(case), name)
    (case.addClassCleanup if isinstance(case, type) else case.addCleanup)(drop_schema, path)
    return path


def own_database(case, **env) -> str:
    """In setUp (or a test): a database for this test alone, with db.session() pointed at it and RUNWAY_DATA (plus any
    other environment variables given) set, all undone when the test is cleaned up. On Postgres it is a schema
    borrowed from the process's pool (SchemaPool). Returns the path, for db.connect().

    In setUpClass (pass the class): the same for every test in the class, undone after tearDownClass. db.session() is
    patched for the whole process, so a server a class starts on a thread uses it too: start the server after this, and
    stop it in tearDownClass (class cleanups run after that)."""
    from runway.storage import db
    later = case.addClassCleanup if isinstance(case, type) else case.addCleanup
    tmp = tempfile.TemporaryDirectory()
    later(tmp.cleanup)
    environ = mock.patch.dict(os.environ, {"RUNWAY_DATA": tmp.name, **env})
    environ.start()
    later(environ.stop)
    path = os.path.join(tmp.name, "runway.db")
    add_database(case, path)
    opened = db.session
    session = mock.patch.object(db, "session", lambda p=None: opened(p or path))
    session.start()
    later(session.stop)
    return path


class DbCase(unittest.TestCase):
    """A test with a database of its own (own_database) and a connection to it, self.c."""

    def setUp(self):
        from runway.storage import db
        self.path = own_database(self)
        self.c = db.connect(self.path)
        self.addCleanup(self.c.close)


TODAY = date(2026, 9, 23)


def fetch(base, method, path, data=None, headers=None, timeout=20, follow=True):
    """One request to a server: (status, headers, body bytes). An error status (4xx, 5xx) comes back the same way, not
    as an exception. follow=False doesn't follow a redirect (the 3xx is returned)."""
    r = urllib.request.Request(base + path, method=method, data=data, headers=headers or {})
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(r, timeout=timeout) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.headers, e.read()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def serve(case, server_class=None):
    """In setUp (or setUpClass, with the class): a Runway server on a thread of its own, stopped when the test (or class)
    is cleaned up. Returns its base URL (http://127.0.0.1:PORT). Call own_database() first: the server uses whichever
    database db.session() points at, and the class cleanups run last first, so the server stops before the database goes."""
    from runway import server
    later = case.addClassCleanup if isinstance(case, type) else case.addCleanup
    httpd = (server_class or server.ThreadingHTTPServer)(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    later(httpd.server_close)
    later(httpd.shutdown)           # (runs first)
    case.httpd = httpd
    return f"http://127.0.0.1:{httpd.server_port}"


class ServerCase(unittest.TestCase):
    """A real Runway on a thread, on a database of its own for the whole class (own_database, so on Postgres its own
    schema), and req() to call it. Set `env` for environment variables the class needs (put back afterwards), and
    `unset` for ones that must not be set (RUNWAY_PUBLIC_URL, say); `server_class` picks the HTTP server."""
    env: dict = {}
    unset: tuple = ()
    server_class: type | None = None
    app_header = True       # send X-Runway: 1, as the web app does, unless a request says otherwise

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        own_database(cls, **cls.env)    # undone after the server has stopped
        for k in cls.unset:
            os.environ.pop(k, None)
        cls.base = serve(cls, cls.server_class)

    def req(self, method, path, body=None, headers=None, timeout=20):
        """(status, parsed JSON) of a call with a JSON body (an empty response is {})."""
        h = {"Content-Type": "application/json", **({"X-Runway": "1"} if self.app_header else {}), **(headers or {})}
        status, _, raw = fetch(self.base, method, path, json.dumps(body).encode() if body is not None else None, h, timeout)
        return status, json.loads(raw or b"{}")


def freeze_today(case, day=None):
    """In setUp (or setUpClass, with the class): date.today() is `day` (shared.TODAY by default) in Runway's modules and
    the tests', so a test doesn't depend on the day it runs. (datetime.now() isn't frozen, and a module that imports
    date inside a function isn't either.) Undone when the test (or class) is cleaned up."""
    later = case.addClassCleanup if isinstance(case, type) else case.addCleanup
    real = date
    today = day or TODAY

    class Meta(type):
        def __instancecheck__(cls, obj):
            return isinstance(obj, real)

    class FrozenDate(real, metaclass=Meta):
        def __new__(cls, *args, **kwargs):
            return real(*args, **kwargs)        # a real date, so nothing downstream sees a subclass

        @classmethod
        def today(cls):
            return today

    for name, mod in list(sys.modules.items()):
        if (name == "runway" or name.startswith(("runway.", "tests."))) and getattr(mod, "date", None) is real:
            patch = mock.patch.object(mod, "date", FrozenDate)
            patch.start()
            later(patch.stop)
    return today


def ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 12, tzinfo=UTC).timestamp())


_NEXT_TX: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()   # per connection: the next number add_tx hands out
DERIVE = object()       # add_tx's payee: the description, cleaned up (categorize.clean_payee), as an import would


def add_tx(conn, account, posted, amount, *, id=None, description="x", payee=DERIVE, **cols) -> str:
    """Insert a transaction and return its id. Anything else is a column by name (category=, pending=, needs_review=...);
    description=None leaves it empty. Without an id it's "<account>|<n>", counting up on this connection from the number
    of transactions there, and never the same as one already used or deleted, whatever has been removed since."""
    from runway.domain import categorize
    if id is None:
        n = max(_NEXT_TX.get(conn, 0), conn.execute(select(func.count()).select_from(Transaction)).fetchone()[0])
        while conn.execute(select(Transaction.id).where(Transaction.id == f"{account}|{n}")).fetchone():
            n += 1
        _NEXT_TX[conn] = n + 1
        id = f"{account}|{n}"
    if payee is DERIVE:
        payee = categorize.clean_payee(description) if description is not None else None
    conn.execute(insert(Transaction).values(id=id, account_id=account, posted=posted, amount=amount,
                                            description=description, payee=payee, **cols))
    return id


def add_acct(conn, id, kind, *, name=None, **cols) -> str:
    """Insert an account (named after its id unless name= says otherwise); anything else is a column by name."""
    conn.execute(insert(Account).values(id=id, name=name or id, kind=kind, **cols))
    return id


class LedgerCase(DbCase):
    """A database (DbCase) with helpers that add accounts, card statements and transactions, dated around TODAY;
    self.conn is the connection."""

    def setUp(self):
        super().setUp()
        self.conn = self.c

    def acct(self, id, kind, balance, **kw):
        add_acct(self.conn, id, kind, balance=balance, **{"balance_date": TODAY.isoformat(), **kw})

    def stmt(self, card, balance, closed, due, minimum=None):
        """The card issuer's latest statement, as Plaid Liabilities reports it."""
        self.conn.execute(update(Account).where(Account.id == card).values(plaid_account_id=f"p-{card}"))
        self.conn.execute(delete(CardStatement).where(CardStatement.plaid_account_id == f"p-{card}"))
        self.conn.execute(insert(CardStatement).values(plaid_account_id=f"p-{card}", item_id="item",
                                                       last_statement_balance=balance, last_statement_date=closed,
                                                       next_due_date=due, minimum_payment=minimum))

    def manual(self, card, balance, closed, due, minimum=None):
        """A statement you entered by hand for the card (statements.py)."""
        self.conn.execute(insert(ManualStatement).values(account_id=card, statement_date=closed, balance=balance, due_date=due,
                                                         minimum_payment=minimum))

    def cycle(self, card_id, today=None):
        from runway.domain import forecast
        card = dict(self.conn.execute(select(Account).where(Account.id == card_id)).fetchone())
        return forecast.card_cycle(self.conn, card, today or TODAY, forecast.bank_statement(self.conn, card, today or TODAY))

    def tx(self, acct, posted, amount, desc="x", category=None, pending=0):
        add_tx(self.conn, acct, posted, amount, description=desc, category=category, pending=pending)
