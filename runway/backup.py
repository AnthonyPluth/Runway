"""Backups that work across databases: one gzip'd JSON file with every table's rows.

Use it to move Runway (Mac -> server, SQLite -> Postgres) or just to keep a copy. It includes your settings, so
it holds your SimpleFIN access, API keys and Plaid tokens: keep backup files private. Sign-in sessions aren't
included, so you sign in again after restoring.
"""
from __future__ import annotations

import gzip
import json
import re
from datetime import datetime

from . import db

FORMAT = "runway-backup"
VERSION = 1
SKIP = {"auth_sessions", "auth_pending"}


def tables() -> list[str]:
    return [t for t in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", db.SCHEMA) if t not in SKIP]


def table_columns(conn, table: str) -> list[str]:
    if db.using_postgres():
        from . import pg
        pg._table_columns.pop(table, None)
        return pg.columns(conn, table)
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def export(conn) -> dict:
    out = {"format": FORMAT, "version": VERSION, "created": datetime.now().isoformat(timespec="seconds"),
           "source": "postgres" if db.using_postgres() else "sqlite", "tables": {}}
    for t in tables():
        cols = table_columns(conn, t)
        out["tables"][t] = {"columns": cols, "rows": [list(r) for r in conn.execute(f"SELECT {', '.join(cols)} FROM {t}")]}
    return out


def dump(conn) -> bytes:
    return gzip.compress(json.dumps(export(conn), separators=(",", ":"), default=str).encode(), compresslevel=6)


def load(raw: bytes) -> dict:
    try:
        data = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
    except (OSError, ValueError) as e:
        raise ValueError("That file isn't a Runway backup.") from e
    if not isinstance(data, dict) or data.get("format") != FORMAT or not isinstance(data.get("tables"), dict):
        raise ValueError("That file isn't a Runway backup.")
    if data.get("version", 0) > VERSION:
        raise ValueError("That backup is from a newer version of Runway. Update Runway first.")
    return data


def restore(conn, data: dict) -> dict:
    """Replace everything with the backup's contents (in one transaction). Returns rows restored per table."""
    known = set(tables())
    counts = {}
    for t in tables():
        conn.execute(f"DELETE FROM {t}")
    for t, payload in data["tables"].items():
        if t not in known:
            continue   # a table this version doesn't have
        have = table_columns(conn, t)
        cols = [c for c in payload["columns"] if c in have]
        keep = [payload["columns"].index(c) for c in cols]
        rows = [[r[i] for i in keep] for r in payload["rows"]]
        if rows and cols:
            conn.executemany(f"INSERT INTO {t}({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)
        counts[t] = len(rows)
    if db.using_postgres():   # auto-numbered ids continue after the restored ones
        from . import pg
        for t in pg.SERIAL_TABLES:
            conn.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), COALESCE((SELECT MAX(id) FROM {t}), 0) + 1, false)")
    return counts
