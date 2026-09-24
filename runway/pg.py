"""Postgres support: a thin layer that lets the rest of Runway keep talking to the database the SQLite way.

Enabled by setting DATABASE_URL (postgresql://user:password@host:5432/dbname). It needs the psycopg driver
(`pip install "psycopg[binary]"`; the Docker image includes it). Without DATABASE_URL, Runway uses SQLite.

What it smooths over:
  - `?` placeholders become `%s`
  - `INSERT OR REPLACE` / `INSERT OR IGNORE` become `INSERT ... ON CONFLICT`
  - `IS NOT 'x'` (SQLite's null-safe comparison) becomes `IS DISTINCT FROM 'x'`
  - `datetime('now')` becomes the same text timestamp Postgres-side; `instr()` and `round(float, n)` are added
  - SQLite's relaxed typing: parameters are sent untyped so Postgres fits them to each column
  - rows can be read by name or position, like sqlite3.Row
  - `cursor.lastrowid` for tables with an auto-numbered id
"""
from __future__ import annotations

import re

_translated: dict[str, str] = {}
_table_columns: dict[str, list[str]] = {}

# Primary keys, filled in from the schema so INSERT OR REPLACE knows what "the same row" means.
PRIMARY_KEYS: dict[str, list[str]] = {}
SERIAL_TABLES: set[str] = set()


def learn_schema(schema: str) -> None:
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", schema, re.S):
        table, body = m.group(1), m.group(2)
        pk = re.search(r"PRIMARY KEY \(([^)]+)\)", body)
        if pk:
            PRIMARY_KEYS[table] = [c.strip() for c in pk.group(1).split(",")]
        else:
            col = re.search(r"^\s*(\w+)\s+\w+[^,\n]*PRIMARY KEY", body, re.M)
            if col:
                PRIMARY_KEYS[table] = [col.group(1)]
        if "AUTOINCREMENT" in body:
            SERIAL_TABLES.add(table)


def translate_ddl(schema: str) -> str:
    s = re.sub(r"--[^\n]*", "", schema)
    s = s.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    s = re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)
    s = s.replace("(datetime('now', 'localtime'))", "(to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))")
    return s


def _outside_quotes(sql: str, fn) -> str:
    """Apply fn to the parts of sql that aren't inside single-quoted strings."""
    parts = re.split(r"('(?:[^']|'')*')", sql)
    return "".join(p if i % 2 else fn(p) for i, p in enumerate(parts))


def translate(sql: str, conn=None) -> str:
    key = sql
    if key in _translated:
        return _translated[key]
    s = sql.replace("%", "%%")
    s = _outside_quotes(s, lambda p: p.replace("?", "%s"))
    s = s.replace("datetime('now', 'localtime')", "to_char(now(), 'YYYY-MM-DD HH24:MI:SS')")
    s = s.replace("datetime('now')", "to_char(now() at time zone 'utc', 'YYYY-MM-DD HH24:MI:SS')")
    s = re.sub(r"\bIS NOT (?!NULL\b)", "IS DISTINCT FROM ", s)
    m = re.match(r"\s*INSERT OR (REPLACE|IGNORE) INTO (\w+)\s*(\(([^)]*)\))?", s, re.I)
    if m:
        kind, table = m.group(1).upper(), m.group(2)
        cols = [c.strip() for c in m.group(4).split(",")] if m.group(4) else (columns(conn, table) if conn else [])
        head = f"INSERT INTO {table}({', '.join(cols)})"
        s = head + s[m.end():]
        if kind == "IGNORE":
            s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        else:
            pk = PRIMARY_KEYS.get(table) or []
            rest = [c for c in cols if c not in pk]
            s = s.rstrip().rstrip(";") + (f" ON CONFLICT ({', '.join(pk)}) DO UPDATE SET " + ", ".join(f"{c}=EXCLUDED.{c}" for c in rest)
                                          if pk and rest else " ON CONFLICT DO NOTHING")
    _translated[key] = s
    return s


def columns(conn, table: str) -> list[str]:
    if table not in _table_columns:
        cur = conn.raw.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() "
                               "AND table_name=%s ORDER BY ordinal_position", (table,))
        _table_columns[table] = [r[0] for r in cur.fetchall()]
    return _table_columns[table]


class Row(tuple):
    """Read by name (row['id']) or by position (row[0]), and dict(row) works, like sqlite3.Row."""
    __slots__ = ()
    _index: dict = {}

    def __new__(cls, values, index):
        r = super().__new__(cls, values)
        return r

    def __getitem__(self, k):
        if isinstance(k, str):
            return tuple.__getitem__(self, self._idx[k])
        return tuple.__getitem__(self, k)

    def keys(self):
        return list(self._idx)


def _row_factory(cursor):
    names = [d.name for d in cursor.description] if cursor.description else []
    idx = {n: i for i, n in enumerate(names)}
    cls = type("Row", (Row,), {"_idx": idx, "__slots__": ()})

    def make(values):
        return cls(values, idx)
    return make


class Cursor:
    def __init__(self, cur, lastrowid=None):
        self._cur, self.lastrowid = cur, lastrowid
        self._buffered = None

    @property
    def rowcount(self):
        return self._cur.rowcount

    def fetchone(self):
        if self._buffered is not None:
            return self._buffered.pop(0) if self._buffered else None
        return self._cur.fetchone() if self._cur.description else None

    def fetchall(self):
        if self._buffered is not None:
            out, self._buffered = self._buffered, []
            return out
        return self._cur.fetchall() if self._cur.description else []

    def __iter__(self):
        return iter(self.fetchall())


class _Empty:
    rowcount, lastrowid = 0, None

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __iter__(self):
        return iter(())


class Connection:
    """Enough of sqlite3.Connection for Runway, on top of psycopg."""

    def __init__(self, url: str, schema: str | None = None):
        import psycopg
        from psycopg.adapt import Dumper

        class Untyped(Dumper):
            oid = 0   # "unknown": Postgres works out the type from where the value is used, as SQLite would

            def dump(self, obj):
                if isinstance(obj, bool):
                    return b"1" if obj else b"0"
                return str(obj).encode()

        self.raw = psycopg.connect(url, row_factory=_row_factory)
        for t in (str, int, float, bool):
            self.raw.adapters.register_dumper(t, Untyped)
        if schema:
            self.raw.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            self.raw.execute(f'SET search_path TO "{schema}"')
        self.row_factory = None

    def execute(self, sql: str, params=()):
        if sql.lstrip().upper().startswith("PRAGMA"):   # SQLite settings: nothing to do here
            return _Empty()
        q = translate(sql, self)
        m = re.match(r"\s*INSERT INTO (\w+)", q, re.I)
        want_id = bool(m and m.group(1) in SERIAL_TABLES and "RETURNING" not in q.upper())
        if want_id:
            q = q.rstrip().rstrip(";") + " RETURNING id"
        cur = self.raw.execute(q, tuple(params) if params is not None else ())
        if want_id:
            row = cur.fetchone()
            return Cursor(cur, row[0] if row else None)
        return Cursor(cur)

    def executemany(self, sql: str, seq):
        q = translate(sql, self)
        with self.raw.cursor() as c:
            c.executemany(q, [tuple(p) for p in seq])

    def executescript(self, script: str):
        for stmt in [x.strip() for x in script.split(";") if x.strip()]:
            self.raw.execute(stmt)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


def install_helpers(conn: Connection) -> None:
    """SQLite functions Runway's queries use."""
    conn.raw.execute("CREATE OR REPLACE FUNCTION instr(text, text) RETURNS integer AS 'SELECT strpos($1, $2)' LANGUAGE sql IMMUTABLE")
    conn.raw.execute("CREATE OR REPLACE FUNCTION round(double precision, integer) RETURNS double precision "
                     "AS 'SELECT round($1::numeric, $2)::double precision' LANGUAGE sql IMMUTABLE")
