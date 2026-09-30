"""Backups that work across databases: one gzip'd JSON file with every table's rows.

Use it to move Runway (Mac -> server, SQLite -> Postgres) or just to keep a copy. It includes your settings, so
it holds your SimpleFIN access, API keys and Plaid tokens: keep backup files private. Sign-in sessions and assistants
connected with OAuth aren't included, so you sign in (and reconnect them) again after restoring.
"""
from __future__ import annotations

import gzip
import json
import os
import zlib
from datetime import datetime
from typing import Any

from sqlalchemy import column, delete, false, func, insert, inspect, select, table

from . import db, schema, secretbox

FORMAT = "runway-backup"
VERSION = 1
# Sign-ins, and the assistants connected with OAuth: neither travels (reconnect them after a restore).
SKIP = {"auth_sessions", "auth_pending", "oauth_clients", "oauth_grants", "oauth_codes", "oauth_tokens", "oauth_consents"}


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


def export(conn) -> dict:
    """Secrets are written decrypted, so the backup restores under any key (on another machine, say)."""
    out: dict[str, Any] = {"format": FORMAT, "version": VERSION, "created": datetime.now().isoformat(timespec="seconds"),
           "source": "postgres" if db.using_postgres() else "sqlite", "tables": {}}
    for t in tables():
        cols = table_columns(conn, t)
        src = _table(t, cols)
        rows = [list(r) for r in conn.execute(select(*(src.c[c] for c in cols)))]
        out["tables"][t] = {"columns": cols, "rows": _convert(t, cols, rows, _decrypt_or_drop)}
    return out


def _decrypt_or_drop(value):
    try:
        return secretbox.decrypt(value)
    except secretbox.SecretError:
        return value   # unreadable under the current key: kept as it is rather than failing the whole backup


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
        raise ValueError("That backup is from a newer version of Runway. Update Runway first.")
    return data


SUMMARY = ("accounts", "transactions", "recurring", "budgets")   # what a person recognises their data by


def _summary_counts(rows: dict[str, int]) -> dict[str, int]:
    return {**{t: rows.get(t, 0) for t in SUMMARY}, "total": sum(rows.values())}


def preview(data: dict) -> dict:
    """What a loaded backup holds, shown before restoring it: when and where it was made, and how many rows (the tables
    you'd recognise, and all rows in the tables this version restores)."""
    known = set(tables())
    rows = {t: len(p.get("rows") or []) for t, p in data["tables"].items() if t in known and isinstance(p, dict)}
    return {"created": data.get("created"), "source": data.get("source"), "version": data.get("version", 0),
            "counts": _summary_counts(rows)}


def counts(conn) -> dict[str, int]:
    """The same counts for the database as it is now: what a restore would replace."""
    return _summary_counts({t: conn.execute(select(func.count()).select_from(schema.metadata.tables[t])).scalar() or 0
                            for t in tables()})


def safety_copy(conn, directory: str | None = None) -> str | None:
    """Before a restore replaces everything: a backup of what's here now, saved next to the database (the data
    directory) as runway-before-restore-<time>.json.gz, private to Runway's user. None when there's nothing to keep
    (no accounts, transactions, recurring or budgets). Returns the file's path."""
    if not any(v for k, v in counts(conn).items() if k != "total"):
        return None
    directory = directory or db.data_dir()
    path = os.path.join(directory, f"runway-before-restore-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.json.gz")
    data = dump(conn)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def restore(conn, data: dict) -> dict:
    """Replace everything with the backup's contents (in one transaction). Returns rows restored per table."""
    known = set(tables())
    counts = {}
    for t in tables():
        conn.execute(delete(schema.metadata.tables[t]))
    for t, payload in data["tables"].items():
        if t not in known:
            continue   # a table this version doesn't have
        have = table_columns(conn, t)
        cols = [c for c in payload["columns"] if c in have]
        keep = [payload["columns"].index(c) for c in cols]
        rows = _convert(t, cols, [[r[i] for i in keep] for r in payload["rows"]], secretbox.encrypt)
        if rows and cols:
            conn.execute(insert(_table(t, cols)), [dict(zip(cols, r, strict=True)) for r in rows])
        counts[t] = len(rows)
    if conn.postgres:   # auto-numbered ids continue after the restored ones
        for t in sorted(schema.AUTO_ID):
            last = select(func.max(schema.metadata.tables[t].c.id)).scalar_subquery()
            conn.execute(select(func.setval(func.pg_get_serial_sequence(t, "id"), func.coalesce(last, 0) + 1, false())))
    return counts
