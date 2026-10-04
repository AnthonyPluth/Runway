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
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
import weakref
from contextlib import contextmanager
from datetime import UTC, date, datetime
from unittest import mock

from sqlalchemy import delete, func, insert, select, update

from runway.models import Account, CardStatement, ManualStatement, OAuthClient, OAuthCode, OAuthConsent, OAuthGrant, OAuthToken, Transaction

LOCK = os.path.join(tempfile.gettempdir(), "runway-tests-mcp-switch.lock")


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


def drop_schema(path) -> None:
    """On Postgres, drop the schema db.init(path) made for this path (db.py names it from the path); SQLite has nothing
    to drop: the file goes with its directory."""
    from runway import db
    if not db.using_postgres():
        return
    import psycopg
    name = "t_" + hashlib.sha1(path.encode(), usedforsecurity=False).hexdigest()[:12]   # as db._postgres_engine names it
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


def add_database(case, path) -> str:
    """db.init(path), and (in setUp, or setUpClass with the class) drop its schema on Postgres when the test is cleaned
    up, so nothing a run makes stays in the database. own_database does this for its own; a test that needs a second
    database (a restore target, an old schema to migrate) makes it with this. Returns the path."""
    from runway import db
    db.init(path)
    (case.addClassCleanup if isinstance(case, type) else case.addCleanup)(drop_schema, path)
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
    other environment variables given) set, all undone when the test is cleaned up. On Postgres its schema is named
    after its path. Returns the path, for db.connect().

    In setUpClass (pass the class): the same for every test in the class, undone after tearDownClass. db.session() is
    patched for the whole process, so a server a class starts on a thread uses it too: start the server after this, and
    stop it in tearDownClass (class cleanups run after that)."""
    from runway import db
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
        from runway import db
        self.path = own_database(self)
        self.c = db.connect(self.path)
        self.addCleanup(self.c.close)   # cleanups run last first: the connection closes before the directory goes


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
    from runway import categorize
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
        from runway import forecast
        card = dict(self.conn.execute(select(Account).where(Account.id == card_id)).fetchone())
        return forecast.card_cycle(self.conn, card, today or TODAY, forecast.bank_statement(self.conn, card, today or TODAY))

    def tx(self, acct, posted, amount, desc="x", category=None, pending=0):
        add_tx(self.conn, acct, posted, amount, description=desc, category=category, pending=pending)
