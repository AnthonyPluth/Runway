"""For tests that share one database with tests in other processes (on Postgres every test module uses the same schema
unless it passes its own path, and CI runs the modules in parallel with unittest-parallel).

The "Let assistants change churning" switch is one settings row: a test that sets it, or depends on it, holds
mcp_switch() so another process doesn't flip it in the middle. Everything else a test makes, it should find and remove
by its own names and ids, never by clearing a table.
"""
import fcntl
import os
import tempfile
import uuid
from contextlib import contextmanager

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
