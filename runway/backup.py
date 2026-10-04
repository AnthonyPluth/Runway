"""Backups that work across databases: one gzip'd JSON file with every table's rows.

Use it to move Runway (Mac -> server, SQLite -> Postgres) or just to keep a copy. It includes your settings, and so
your SimpleFIN access, API keys and Plaid tokens: those stay encrypted in the file, as they are in the database
(runway/secretbox.py), so a backup on its own doesn't give them away. Restoring on a machine with the same
RUNWAY_SECRET_KEY (or a copy of secret.key) reads them again; under another key they can't be read (the restore says
which): set the key the backup was made with as RUNWAY_SECRET_KEY_OLD for one start, and they're re-encrypted with the
current one, or enter them again in Settings. Sign-in sessions and assistants connected with OAuth aren't included, so
you sign in (and reconnect them) again after restoring.

A backup records the schema it was made with (its Alembic revision). Restoring one made by an older version loads its
rows into a database of their own at that revision, runs the migrations since over them (so what they change in the
data, a payee renamed or a setting moved, is changed in the backup's data too) and then copies the result in. That
database is a throwaway: in memory on SQLite, and on Postgres a schema made inside a transaction that's rolled back.
A backup from before backups recorded their revision is taken to be at the last revision its shape fits (unversioned()):
the migrations after it run, the ones before don't, and the restore says some older data may not be updated. A backup from
a newer version than this one is refused.
"""
from __future__ import annotations

import gzip
import json
import os
import threading
import uuid
import zlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from alembic import command
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import Connection as SAConnection
from sqlalchemy import Column, MetaData, column, create_engine, delete, false, func, insert, inspect, select, table, update
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateColumn

from . import db, monitoring, schema, secretbox
from . import settings_keys as sk
from .models import PlaidItem, Setting

FORMAT = "runway-backup"
VERSION = 2   # 2: it records its schema (revision); a Runway that reads only 1 refuses it rather than lose what's new
# Sign-ins, and the assistants connected with OAuth: neither travels (reconnect them after a restore).
SKIP = {"auth_sessions", "auth_pending", "oauth_clients", "oauth_grants", "oauth_codes", "oauth_tokens", "oauth_consents"}
UNVERSIONED = "0036"   # backups made before they recorded their revision were made at this revision or an earlier one...
UNVERSIONED_PLAID_SIDES = "0037"   # ... or at this one, which gave a Plaid connection's investment sync its own columns
OLD_BACKUP = ("This backup is from an older version of Runway that didn’t record its database version, so some of "
              "its older data may not have been brought up to date. Check your accounts, payees and budgets.")
NEWER = "That backup is from a newer version of Runway. Update Runway first."


def tables() -> list[str]:
    return [t.name for t in schema.metadata.sorted_tables if t.name not in SKIP]


def table_columns(conn, table: str) -> list[str]:
    return [c["name"] for c in inspect(conn.sa).get_columns(table)]


def _table(name: str, cols: list[str]):
    """The table (schema.py's), for statements on these columns. The database is migrated to match schema.py, but a
    column only the database has (one from long ago) still goes along, as with SQL naming it: then a bare table()."""
    t = schema.metadata.tables[name]
    return t if all(c in t.c for c in cols) else table(name, *(column(c) for c in cols))


def _secret_columns(table: str, cols: list[str]):
    """Which values in a row are stored encrypted: returns a test (row -> list of column indexes)."""
    if table == "settings" and "key" in cols and "value" in cols:
        k, v = cols.index("key"), cols.index("value")
        return lambda row: [v] if row[k] in secretbox.SECRET_SETTINGS else []
    if table == "plaid_items" and "access_token" in cols:
        i = cols.index("access_token")
        return lambda row: [i]
    return None


def _convert(table: str, cols: list[str], rows: list[list], fn) -> list[list]:
    which = _secret_columns(table, cols)
    if not which:
        return rows
    for row in rows:
        for i in which(row):
            row[i] = fn(row[i])
    return rows


def _scripts() -> ScriptDirectory:
    return ScriptDirectory.from_config(db.alembic_config())


def head() -> str:
    """The revision this version of Runway migrates databases to."""
    return str(_scripts().get_current_head())


def revision(sa_conn) -> str | None:
    """The revision a database is at (None: not one Alembic has migrated)."""
    if not inspect(sa_conn).has_table("alembic_version"):
        return None
    return sa_conn.execute(select(column("version_num")).select_from(table("alembic_version"))).scalar()


def export(conn) -> dict:
    """Secrets are written as they're stored, encrypted (a value saved by a version before encryption is encrypted on
    the way out), so the file holds nothing readable without the key. Works at any revision (a migration saves a copy
    before it removes anything): a table the database doesn't have yet is left out."""
    have = set(inspect(conn.sa).get_table_names())
    out: dict[str, Any] = {"format": FORMAT, "version": VERSION, "revision": revision(conn.sa),
                           "created": datetime.now().isoformat(timespec="seconds"),
                           "source": "postgres" if conn.postgres else "sqlite", "tables": {}}
    for t in tables():
        if t not in have:
            continue
        cols = table_columns(conn, t)
        src = _table(t, cols)
        rows = [list(r) for r in conn.execute(select(*(src.c[c] for c in cols)))]
        out["tables"][t] = {"columns": cols, "rows": _convert(t, cols, rows, secretbox.encrypt)}
    return out


def dump(conn) -> bytes:
    return gzip.compress(json.dumps(export(conn), separators=(",", ":"), default=str).encode(), compresslevel=6)


MAX_UNPACKED = 256 * 1024 * 1024   # a real backup unpacks to far less; a crafted one could be many GB (held in memory)


def _gunzip(raw: bytes) -> bytes:
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = d.decompress(raw, MAX_UNPACKED + 1)
    if len(out) > MAX_UNPACKED:
        raise ValueError("That backup is too large to restore.")
    if not d.eof:
        raise ValueError("That file isn't a Runway backup.")
    return out


def _check_revision(data: dict) -> None:
    """A backup's revision must be one this version knows; one it doesn't is from a newer version."""
    rev = data.get("revision")
    if rev is None:
        return
    if not isinstance(rev, str):
        raise ValueError("That file isn't a Runway backup.")
    try:
        known = _scripts().get_revision(rev) is not None
    except CommandError:
        known = False
    if not known:
        raise ValueError(NEWER)


def load(raw: bytes) -> dict:
    try:
        if raw[:2] == b"\x1f\x8b":
            raw = _gunzip(raw)
    except zlib.error as e:
        raise ValueError("That file isn't a Runway backup.") from e
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ValueError("That file isn't a Runway backup.") from e
    if not isinstance(data, dict) or data.get("format") != FORMAT or not isinstance(data.get("tables"), dict):
        raise ValueError("That file isn't a Runway backup.")
    if not isinstance(data.get("version", 0), int):
        raise ValueError("That file isn't a Runway backup.")
    if data.get("version", 0) > VERSION:
        raise ValueError(NEWER)
    _check_revision(data)
    return data


def warning(data: dict) -> str | None:
    """What to tell the person about restoring this backup: a backup that didn't record its revision may have older
    data the migrations it predates never brought up to date."""
    return OLD_BACKUP if data.get("revision") is None else None


SUMMARY = ("accounts", "transactions", "recurring", "budgets")   # what a person recognises their data by


def _summary_counts(rows: dict[str, int]) -> dict[str, int]:
    return {**{t: rows.get(t, 0) for t in SUMMARY}, "total": sum(rows.values())}


def preview(data: dict) -> dict:
    """What a loaded backup holds, shown before restoring it: when and where it was made, and how many rows (the tables
    you'd recognise, and all rows in the tables this version restores)."""
    known = set(tables())
    rows = {t: len(p.get("rows") or []) for t, p in data["tables"].items() if t in known and isinstance(p, dict)}
    return {"created": data.get("created"), "source": data.get("source"), "version": data.get("version", 0),
            "revision": data.get("revision"), "warning": warning(data), "counts": _summary_counts(rows)}


def counts(conn) -> dict[str, int]:
    """The same counts for the database as it is now: what a restore would replace."""
    return _summary_counts({t: conn.execute(select(func.count()).select_from(schema.metadata.tables[t])).scalar() or 0
                            for t in tables()})


def save_copy(conn, name: str, directory: str | None = None) -> str:
    """Everything in the database, as a backup, in `directory` (the data directory) as <name>-<time>.json.gz, private
    to Runway's user. Returns the file's path."""
    directory = directory or db.data_dir()
    path = os.path.join(directory, f"{name}-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.json.gz")
    data = dump(conn)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def safety_copy(conn, directory: str | None = None) -> str | None:
    """Before a restore replaces everything: a backup of what's here now, saved next to the database (the data
    directory) as runway-before-restore-<time>.json.gz. None when there's nothing to keep (no accounts, transactions,
    recurring or budgets). Returns the file's path."""
    if not any(v for k, v in counts(conn).items() if k != "total"):
        return None
    return save_copy(conn, "runway-before-restore", directory)


def unreadable_secrets(conn) -> list[str]:
    """The settings that hold a secret this Runway's key can't read (a backup from a machine with another key): what
    to enter again in Settings, by name, then Plaid connections (one entry each)."""
    out = []
    for r in conn.execute(select(Setting.key, Setting.value).where(Setting.key.in_(sorted(secretbox.SECRET_SETTINGS)))
                          .order_by(Setting.key)).fetchall():
        try:
            secretbox.decrypt(r["value"])
        except secretbox.SecretError:
            out.append(r["key"])
    for r in conn.execute(select(PlaidItem.item_id, PlaidItem.access_token).order_by(PlaidItem.item_id)).fetchall():
        try:
            secretbox.decrypt(r["access_token"])
        except secretbox.SecretError:
            out.append(f"plaid:{r['item_id']}")
    return out


# What each secret is called where it's entered again (Settings), for saying which ones a restore couldn't read.
SECRET_LABELS = {
    sk.SIMPLEFIN_ACCESS_URL: "SimpleFIN access", sk.PLAID_SECRET: "Plaid secret", sk.PLAID_PENDING_LINK: "a Plaid connection in progress",
    sk.OPENROUTER_API_KEY: "OpenRouter API key", sk.REALIE_API_KEY: "Realie API key", sk.FINNHUB_API_KEY: "Finnhub API key",
    sk.LOGODEV_TOKEN: "Logo.dev publishable key", sk.LOGODEV_SECRET: "Logo.dev secret key",
    sk.VAPID_PRIVATE_KEY: "notifications' signing key (devices sign up for notifications again)",
    sk.CARTA_CLIENT_SECRET: "Carta client secret", sk.CARTA_ACCESS_TOKEN: "Carta sign-in", sk.CARTA_REFRESH_TOKEN: "Carta sign-in",
    sk.CARTA_WEB_CAPTURE: "what was read from Carta",
}


def unreadable_summary(unreadable: list[str]) -> str:
    """unreadable_secrets() for people: each setting by its name in Settings, and Plaid connections as a count. Only
    these fixed labels and a count are said, never anything read from the rows (a connection's id)."""
    labels = list(dict.fromkeys(label for key, label in SECRET_LABELS.items() if key in unreadable))
    plaid = sum(1 for u in unreadable if u.startswith("plaid:"))
    if plaid:
        labels.append(f"{plaid} Plaid connection{'s' if plaid != 1 else ''}")
    return ", ".join(labels)


# ------------------------------------------------------------------------------------------------ restoring

Rows = dict[str, tuple[list[str], list[list]]]   # table -> (columns, rows)


@contextmanager
def _scratch(conn) -> Iterator[SAConnection]:
    """An empty database to bring a backup up to date in, on the same kind of database as this one (the migrations
    are written for each), thrown away afterwards: on SQLite one in memory; on Postgres a schema of its own, made in a
    transaction that's rolled back, so nothing of it is ever committed."""
    if not conn.postgres:
        eng = create_engine("sqlite://", poolclass=StaticPool)   # foreign keys off, as while migrating (db.migrate)
        try:
            with eng.connect() as sc, sc.begin():
                yield sc
        finally:
            eng.dispose()
        return
    with conn.sa.engine.connect() as sc:
        trans = sc.begin()
        try:
            name = f"runway_restore_{uuid.uuid4().hex[:12]}"
            sc.exec_driver_sql(f'CREATE SCHEMA "{name}"')   # nosemgrep: runway-sql-from-string -- a name made here, which DDL can't bind
            sc.exec_driver_sql(f'SET LOCAL search_path TO "{name}"')   # nosemgrep: runway-sql-from-string -- a name made here, which DDL can't bind
            yield sc
        finally:
            trans.rollback()


def _migrate(sc: SAConnection, to: str) -> None:
    cfg = db.alembic_config(sc)
    cfg.attributes["restoring"] = True   # the backup is the copy: migration 0040 needn't save one of it
    command.upgrade(cfg, to)


def _budget_cards(sc: SAConnection, tables: dict) -> None:
    """A backup from before a category kept its own card (migration 0036) has it on the budget: it goes to the
    category. (Only for a backup that didn't record its revision, loaded at unversioned(): one that did gets 0036 itself.)"""
    b = tables.get("budgets") or {}
    if "pay_with" not in (b.get("columns") or []):
        return
    cat, acct = b["columns"].index("category"), b["columns"].index("pay_with")
    c = table("categories", column("name"), column("pay_with"))
    for r in b.get("rows") or []:
        if r[acct]:
            sc.execute(update(c).where(c.c.name == r[cat], c.c.pay_with.is_(None)).values(pay_with=r[acct]))


def unversioned(data: dict) -> str:
    """The revision a backup that didn't record one was made at, as far as can be told: 0037 (its Plaid connections have
    inv_error, which 0037 added; running 0037 again would move their sync times a second time), else 0036."""
    items = data["tables"].get("plaid_items")
    if isinstance(items, dict) and "inv_error" in (items.get("columns") or []):
        return UNVERSIONED_PLAID_SIDES
    return UNVERSIONED


def _upgraded(conn, data: dict) -> Rows:
    """The backup's rows as they'd be had its database been migrated to this version's schema."""
    rev = data.get("revision")
    with _scratch(conn) as sc:
        _migrate(sc, rev or unversioned(data))
        then = MetaData()
        then.reflect(sc)
        order = [t for t in then.sorted_tables if t.name != "alembic_version"]
        for t in reversed(order):
            sc.execute(delete(t))   # anything a migration put there: the backup's rows replace it
        target = inspect(conn.sa)
        given: Rows = {}
        for t in order:
            payload = data["tables"].get(t.name)
            if not isinstance(payload, dict):
                continue
            there = set(t.c.keys())
            # A column only the backup's database had (one from long ago) comes along if this database has it too.
            if target.has_table(t.name):
                for c in target.get_columns(t.name):
                    if c["name"] in payload["columns"] and c["name"] not in there:
                        ddl = CreateColumn(Column(c["name"], c["type"])).compile(dialect=sc.dialect)
                        sc.exec_driver_sql(f'ALTER TABLE "{t.name}" ADD COLUMN {ddl}')   # nosemgrep: runway-sql-from-string -- the model's own table and column names, which DDL can't bind
                        there.add(c["name"])
            cols = [c for c in payload["columns"] if c in there]
            keep = [payload["columns"].index(c) for c in cols]
            given[t.name] = (cols, _convert(t.name, cols, [[r[i] for i in keep] for r in payload["rows"]], secretbox.encrypt))
        _prune(given, then)   # at a revision with foreign keys, a row they'd refuse (the migrations do the rest)
        if sc.dialect.name == "postgresql":
            sc.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")   # rows referring to later ones of their own table
        for t in order:
            cols, rows = given.get(t.name, ([], []))
            if rows and cols:
                sc.execute(insert(table(t.name, *(column(c) for c in cols))), [dict(zip(cols, r, strict=True)) for r in rows])
        if sc.dialect.name == "postgresql":
            sc.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")   # checked now: a migration can't change a table with checks pending
        if rev is None:
            _budget_cards(sc, data["tables"])
        _migrate(sc, "head")
        insp = inspect(sc)
        out: Rows = {}
        for name in tables():
            if not insp.has_table(name):
                continue
            cols = [c["name"] for c in insp.get_columns(name)]
            got: list[Any] = list(sc.execute(select(*(column(c) for c in cols)).select_from(table(name))))
            out[name] = (cols, [list(row) for row in got])
        return out


def _as_given(data: dict) -> Rows:
    """A backup made at this version's revision: its rows as they are."""
    known = set(tables())
    return {t: (list(p["columns"]), [list(r) for r in p["rows"]]) for t, p in data["tables"].items()
            if t in known and isinstance(p, dict)}


def _prune(rows: Rows, meta: MetaData) -> dict[str, int]:
    """Rows that refer to one the backup doesn't have (a foreign key in `meta` that couldn't hold): the row goes, or
    lets go, as removing what it refers to would do (ON DELETE CASCADE or SET NULL). Returns how many went, by table."""
    gone: dict[str, int] = {}
    for t in meta.sorted_tables:   # what a row refers to is checked (and pruned) before the row
        if t.name not in rows:
            continue
        cols, data = rows[t.name]
        for fk in sorted(t.foreign_keys, key=lambda f: f.parent.name):
            if fk.parent.name not in cols:
                continue
            parent = fk.column.table.name
            pcols, prows = rows.get(parent, ([], []))
            if parent == t.name:
                pcols, prows = cols, data
            if fk.column.name not in pcols:
                continue
            j, i = pcols.index(fk.column.name), cols.index(fk.parent.name)
            there = {r[j] for r in prows}
            if fk.ondelete == "CASCADE" or not fk.parent.nullable:
                kept = [r for r in data if r[i] is None or r[i] in there]
                if len(kept) < len(data):
                    gone[t.name] = gone.get(t.name, 0) + len(data) - len(kept)
                data = kept
            else:
                for r in data:
                    if r[i] is not None and r[i] not in there:
                        r[i] = None
        rows[t.name] = (cols, data)
    return gone


def restore(conn, data: dict) -> dict:
    """Replace everything with the backup's contents (in one transaction). Returns rows restored per table. Secrets
    the backup holds encrypted are kept as they are (see unreadable_secrets for the ones this key can't read); a
    plaintext one from an old backup is encrypted."""
    _check_revision(data)
    rows = _as_given(data) if data.get("revision") == head() else _upgraded(conn, data)
    for t, n in sorted(_prune(rows, schema.metadata).items()):   # counts only: never what the rows held
        monitoring.log(f"Restore: left out {n} row{'s' if n != 1 else ''} of {t} referring to something the backup doesn't have.",
                       "warning")
    for t in reversed(tables()):
        conn.execute(delete(schema.metadata.tables[t]))
    # Rows can refer to rows of their own table restored after them (a card paid from an account listed later):
    # checked when the restore commits, not row by row.
    conn.sa.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED" if conn.postgres else "PRAGMA defer_foreign_keys = ON")
    out = {}
    for t in tables():
        if t not in rows:
            continue
        have = table_columns(conn, t)
        given, data_rows = rows[t]
        cols = [c for c in given if c in have]
        keep = [given.index(c) for c in cols]
        restored = _convert(t, cols, [[r[i] for i in keep] for r in data_rows], secretbox.encrypt)
        if restored and cols:
            conn.execute(insert(_table(t, cols)), [dict(zip(cols, r, strict=True)) for r in restored])
        out[t] = len(restored)
    if conn.postgres:   # auto-numbered ids continue after the restored ones
        for t in sorted(schema.AUTO_ID):
            last = select(func.max(schema.metadata.tables[t].c.id)).scalar_subquery()
            conn.execute(select(func.setval(func.pg_get_serial_sequence(t, "id"), func.coalesce(last, 0) + 1, false())))
    return out


class Busy(RuntimeError):
    """Something in the background (a sync) is writing: a restore now would mix its rows in with the backup's."""


def restore_all(data: dict, locks: Sequence[threading.Lock] = (), directory: str | None = None) -> dict:
    """Restore a loaded backup, the whole way, for the web and the command line alike: hold `locks` (the server's sync
    locks: Busy if one is taken), save a copy of what's here first (safety_copy), replace everything with the backup
    (restore), and see which of its secrets this Runway's key can't read. Returns what to tell the person: rows restored
    per table (counts), the copy's path (safety_copy), those secrets (unreadable_secrets) and a warning (or None).
    Raises ValueError for a backup that can't be restored, OSError when the copy can't be saved (nothing is restored
    then), and the database's errors as they are."""
    held: list[threading.Lock] = []
    try:
        for lock in locks:
            if not lock.acquire(blocking=False):
                raise Busy("A sync is running. Restore once it has finished.")
            held.append(lock)
        with db.session() as conn:
            copy = safety_copy(conn, directory)   # what's here now, in case the backup was the wrong one
            restored = restore(conn, data)
            unreadable = unreadable_secrets(conn)   # from a machine with another key: entered again
    finally:
        for lock in held:
            lock.release()
    return {"counts": restored, "safety_copy": copy, "unreadable_secrets": unreadable, "warning": warning(data)}
