"""For tests that share one database with tests in other processes (on Postgres every test module uses the same schema
unless it passes its own path, and CI runs the modules in parallel with unittest-parallel).

A test that writes settings, or runs something that does (a sync, the AI categorizer, a request through the server),
takes a database of its own with own_database(), so nothing it writes reaches another module's test: a test left
last_sync_ok in the shared schema and failed another module's on 2026-09-30.

The "Let assistants change churning" and "Let assistants categorize" switches are settings rows: a test that sets
one, or depends on one, holds mcp_switch() (for both) so another process doesn't flip it in the middle. Everything else a test makes, it should find and remove
by its own names and ids, never by clearing a table.
"""
import fcntl
import os
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime
from unittest import mock

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
        grants = [r[0] for r in conn.execute("SELECT id FROM oauth_grants WHERE client_id=?", (cid,)).fetchall()]
        for gid in grants:
            conn.execute("DELETE FROM oauth_tokens WHERE grant_id=?", (gid,))
            conn.execute("DELETE FROM oauth_codes WHERE grant_id=?", (gid,))
        conn.execute("DELETE FROM oauth_grants WHERE client_id=?", (cid,))
        conn.execute("DELETE FROM oauth_consents WHERE params LIKE ?", (f'%"{cid}"%',))
        conn.execute("DELETE FROM oauth_clients WHERE id=?", (cid,))


def own_database(case, **env) -> str:
    """In setUp (or a test): a database for this test alone, with db.session() pointed at it and RUNWAY_DATA (plus any
    other environment variables given) set, all undone when the test is cleaned up. On Postgres its schema is named
    after its path. Returns the path, for db.connect()."""
    from runway import db
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    environ = mock.patch.dict(os.environ, {"RUNWAY_DATA": tmp.name, **env})
    environ.start()
    case.addCleanup(environ.stop)
    path = os.path.join(tmp.name, "runway.db")
    db.init(path)
    opened = db.session
    session = mock.patch.object(db, "session", lambda p=None: opened(p or path))
    session.start()
    case.addCleanup(session.stop)
    return path


class DbCase(unittest.TestCase):
    """A test with a database of its own (own_database) and a connection to it, self.c."""

    def setUp(self):
        from runway import db
        self.path = own_database(self)
        self.c = db.connect(self.path)
        self.addCleanup(self.c.close)   # cleanups run last first: the connection closes before the directory goes


TODAY = date(2026, 9, 23)


def ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 12, tzinfo=UTC).timestamp())


class LedgerCase(DbCase):
    """A database (DbCase) with helpers that add accounts, card statements and transactions, dated around TODAY;
    self.conn is the connection."""

    def setUp(self):
        super().setUp()
        self.conn = self.c

    def acct(self, id, kind, balance, **kw):
        cols = {"id": id, "name": id, "kind": kind, "balance": balance, "balance_date": TODAY.isoformat(), **kw}
        self.conn.execute(
            f"INSERT INTO accounts({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values())
        )

    def stmt(self, card, balance, closed, due, minimum=None):
        """The card issuer's latest statement, as Plaid Liabilities reports it."""
        self.conn.execute("UPDATE accounts SET plaid_account_id=? WHERE id=?", (f"p-{card}", card))
        self.conn.execute("DELETE FROM card_statements WHERE plaid_account_id=?", (f"p-{card}",))
        self.conn.execute("INSERT INTO card_statements(plaid_account_id, item_id, last_statement_balance, last_statement_date, "
                          "next_due_date, minimum_payment) VALUES (?,?,?,?,?,?)", (f"p-{card}", "item", balance, closed, due, minimum))

    def cycle(self, card_id, today=None):
        from runway import forecast
        card = dict(self.conn.execute("SELECT * FROM accounts WHERE id=?", (card_id,)).fetchone())
        return forecast.card_cycle(self.conn, card, today or TODAY, forecast.bank_statement(self.conn, card, today or TODAY))

    def tx(self, acct, posted, amount, desc="x", category=None, pending=0):
        from runway import categorize
        n = self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        self.conn.execute(
            "INSERT INTO transactions(id, account_id, posted, amount, description, payee, category, pending) VALUES (?,?,?,?,?,?,?,?)",
            (f"{acct}|{n}", acct, posted, amount, desc, categorize.clean_payee(desc), category, pending),
        )
