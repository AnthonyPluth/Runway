---
title: Queries with SQLAlchemy
description: Writing queries as SQLAlchemy statements over the ORM models.
sidebar:
  order: 2
---

Runway's database code, tests included, builds its queries as SQLAlchemy statements from the ORM models in
`runway/models.py`. `Connection.execute()` doesn't take SQL text (it raises `TypeError`). This is the guide to
writing queries: what to use, what to watch, and examples from the code (`runway/db.py`, `runway/networth.py`,
`runway/equity.py`, `runway/planner.py`, and their API handlers).

When you rewrite an existing query, the rule is **no change in behavior**: same rows, same order, same dict keys in
API responses, same commit points.

## The pieces

| What | Where | Notes |
|---|---|---|
| Schema (the one source of truth) | `runway/schema.py` | Core `Table`s; Alembic migrations keep the database matching it. |
| Models | `runway/models.py` | One class per table, mapping schema.py's own `Table` (`__table__ = schema.assets`), so no migration. `Account`, `Transaction`, `TxSplit`, `Asset`, `AssetValue`, `Setting`, ... |
| Connection | `db.connect()`, `db.session()` | `conn.execute()` takes a statement; SQL text raises `TypeError`. `conn.sa` is the SQLAlchemy connection underneath. |
| ORM Session | `conn.orm` | A `sqlalchemy.orm.Session` on the same connection and transaction. |
| Helpers | `runway/db.py` | `upsert`, `insert_ignore`, `dialect_insert`, `instr`, `account_label_expr`, `not_investment`, `rows`, `as_dict`, `Result.scalar()/scalars()` |
| Shared fragments | `splits.parts()`, `db.not_investment()`, `db.account_label_expr()` | A split transaction as its parts, leaving out investment accounts, an account's name as lists show it |
| Guard | `tests/test_orm_guard.py` | Fails on SQL text passed to `execute()` (or a `text()` that doesn't say why) anywhere in `runway/` or `tests/`. |

## Two ways to run a statement

**1. `conn.execute(statement)`: the default.** Build a Core-style statement from the model attributes and run it on
the connection. The result is a `db.Result`: rows read by name or position (`row["id"]`, `row[0]`, `dict(row)`),
`fetchone()`, `fetchall()`, iteration, `rowcount`, `lastrowid`, plus `scalar()` and `scalars()`. `db.rows(...)`
turns it into a list of dicts.

**2. `conn.orm`: when objects help.** Load an object, change its attributes, add new ones: `conn.orm.get(Asset, 3)`,
`conn.orm.add(EquityGrant(...))`, `conn.orm.scalars(select(Rule).where(...)).all()`. Useful for edit handlers that
look a row up, check it exists and change a few columns. Not for building API responses from many rows (use 1 and
`db.rows()`), and never in loops that would lazy-load per row.

Both share one connection and one transaction:

- Pending ORM changes (`add()`, attribute changes) are written ("flushed") before any `conn.execute()` runs, so
  statements always see them; they're also written at `conn.commit()`.
- After an `UPDATE`/`DELETE`/`INSERT` through `conn.execute()`, objects the Session had loaded are expired and
  re-read when next used, so they're never stale.
- `conn.commit()` commits both; `conn.rollback()` rolls back both; `db.session()` commits on success and rolls back
  on an exception. **Don't call `conn.orm.commit()` or `conn.sa.commit()` yourself**: always `conn.commit()`.
- Objects stay readable after a commit (`expire_on_commit=False`).
- One Connection (and so one Session) per request or sync, never shared between threads.

### Commit points and network calls

Some code commits before a slow network request so SQLite's write lock isn't held through it
(`conn.commit()`, then `plaid...`/`simplefin...`, then carries on with the same `conn`). After `conn.commit()`, both
the connection and `conn.orm` start a new transaction on their next use. Keep every existing `conn.commit()` where it
is. When you use `conn.orm`, remember changes are written lazily: if a commit before a network call must include
them, it will (commit flushes), but a *read through another connection* won't see them until then.

## Reads

Imports: `from sqlalchemy import select, func, ...` and `from .models import Account, ...` (from
`runway/server/api/*`: `from ...models import ...`).

### Columns, filters, order

From `runway/planner.py`:

```python
owed = {a["id"]: forecast.owed(a) for a in db.rows(conn.execute(
    select(Account.id, Account.kind, Account.balance, Account.owed_positive).where(Account.kind.in_(["credit", "loan"]))))}
```

- Several conditions: `.where(a, b)`; or: `or_(a, b)`; not: `~x` or `not_(x)`.
- Null checks: `X.col.is_(None)` / `.is_not(None)`. Never `== None` (ruff flags it anyway).
- `.in_([...])` (an empty list is fine: it's false); `.not_in(...)`; a subquery: `.in_(select(Account.id).where(...))`.
- `.order_by(A.a, A.b.desc())`, `.limit(n)`, `.offset(n)`.
- A computed column's name is `.label("name")`. The label is the dict key in the result.
- Every model attribute is named as its column.

### Every column

`select(Model)` through `conn.execute()` gives every column, named as the table's columns, in schema.py's order
(`runway/networth.py`):

```python
out = db.rows(conn.execute(select(Asset).order_by(Asset.kind, Asset.name)))
```

To leave out a big column (a `raw` blob), select the rest instead of loading it (`runway/equity.py`):

```python
GRANT_COLUMNS = [c for c in EquityGrant.__table__.c if c.key != "raw"]   # a grant, less what Carta sent
grants = db.rows(conn.execute(
    select(*GRANT_COLUMNS).order_by(func.coalesce(EquityGrant.granted_on, EquityGrant.vest_start), EquityGrant.id)))
```

### Joins and aliases

```python
select(Transaction.id, Account.name).join(Account, Account.id == Transaction.account_id)   # INNER JOIN
select(...).outerjoin(Account, Account.id == Transaction.account_id)                     # LEFT JOIN
select(Account.id).join(Account.transactions)                                           # via a relationship
```

A table in two roles (an account and the account it's paid from): `ra = aliased(Account)` (`from sqlalchemy.orm
import aliased`), then `ra.id`, `ra.name`. A relationship loaded on objects is `lazy="raise"`: load it explicitly
with `.options(selectinload(RetailOrder.items))`, or you get an error rather than one query per row.

### Aggregates and GROUP BY

```python
select(Transaction.category, func.count().label("n"), func.sum(Transaction.amount).label("total"))
    .where(Transaction.posted >= start).group_by(Transaction.category).having(func.count() > 1)
```

`func.max/min/avg/coalesce/lower/upper/abs/round/length/substr` render as the same SQL functions. For one number:
`conn.execute(select(func.count()).select_from(Category)).scalar()`.

`GROUP BY` / `ORDER BY` a computed column: label it once and reuse the label object
(`runway/server/api/networth.py`):

```python
name = func.coalesce(Account.display_name, Account.name).label("name")
select(Account.id, name, Account.kind).where(Account.kind == "loan", Account.hidden == 0).order_by(name)
```

### Split transactions, investment accounts, account names

Use the shared versions, never a copy:

| What | Statement |
|---|---|
| Spending by category, a split transaction as its parts | `p = splits.parts()` then `select(p.c.category, func.sum(p.c.amount)).group_by(p.c.category)` |
| Leaving out investment accounts' transactions | `.where(db.not_investment())` (on `Transaction.account_id`) or `db.not_investment(p.c.account_id)` |
| An account's name as lists show it (`db.account_label()`) | `db.account_label_expr().label("name")`, or `db.account_label_expr(a)` for an `aliased(Account)` |

### Strings, dates and `CASE`

- Substring position: `db.instr(haystack, needle) > 0` (SQLite `instr`, Postgres `strpos`). Exact, case-sensitive,
  no wildcards; wrap both sides in `func.lower()` for a case-insensitive match.
- `.like(value)` treats `%`/`_` in the value as wildcards. To match text literally, use
  `.contains(text, autoescape=True)` / `.startswith(..., autoescape=True)`. A search box does: `func.lower(col).contains(text.lower(), autoescape=True)`.
- `func.lower()` lowers every letter on both databases ("CAFÉ" is "café"): SQLite's own only lowers ASCII, so
  `db.py` puts Python's `str.lower` in its place on every SQLite connection. It doesn't case-fold (ß stays ß, as on
  Postgres).
- Concatenation: `A.a + B.b` on text columns (or `func.coalesce(...) + " (" + A.owner + ")"`).
- `case((c, x), else_=y)` for `CASE WHEN c THEN x ELSE y END`.
- Dates are ISO text in this schema (`posted`, `date`, `as_of`): compare them as strings (`Transaction.posted >=
  start.isoformat()`). `schema.now_text()` is the portable "now" as text.
- A literal constant in the select list that must stay SQL, not a parameter (e.g. inside a `UNION`):
  `literal_column("'split'", Text)`.

## Writes

### Insert

From `runway/networth.py`:

```python
cur = conn.execute(insert(Asset).values(name=fields.pop("name"), kind=fields.pop("kind"), value=new_value,
                                        as_of=today.isoformat(), source=body.get("source") or "manual"))
asset_id = cur.lastrowid
```

`lastrowid` is the new row's primary key on both databases (from SQLAlchemy's `inserted_primary_key`). Columns you
leave out get their server default.

Many rows: `conn.execute(insert(Category), [{"name": n, "is_transfer": t, "is_income": i} for
...])`. Every dict must have the same keys. An empty list does nothing.

### Update and delete

```python
conn.execute(update(Asset).where(Asset.id == asset_id).values(**fields))
conn.execute(delete(AssetValue).where(AssetValue.asset_id == asset_id))
```

`.values(**fields)` is safe only because `fields`' keys are column names the code chose; never pass keys that came
from a request body unchecked (SQLAlchemy rejects unknown columns, but a known one you didn't mean to allow would be
written). `rowcount` gives the rows affected. Expressions: `.values(balance=Account.balance + delta)`.

### Upserts (`ON CONFLICT`)

`db.upsert()` builds `INSERT ... ON CONFLICT ... DO UPDATE` with the SQLite or Postgres `insert()` to match the
connection (`runway/db.py`):

```python
db.upsert(conn, Setting, {"key": key, "value": value}, key=["key"])
```

- `update=None` (default): every column given, except the key, is updated from `excluded`. To update only some,
  pass `update=["a", "b"]`.
- Anything else in the SET clause: `update=lambda ex: {"value": func.coalesce(ex.value, AssetValue.value)}`
  (`ex` is `excluded`; the model's attributes are the existing row).
- `update=[]` gives `ON CONFLICT DO NOTHING`; or `db.insert_ignore(conn, Model, values, key=[...])`
  (`key=None`: any unique constraint). Don't use SQLite's `INSERT OR IGNORE`: it doesn't run on Postgres.
- A list of dicts upserts many rows in one call.
- Something else (`ON CONFLICT ... DO UPDATE ... WHERE`): `stmt = db.dialect_insert(conn, Model).values(...)`, then
  SQLAlchemy's own `stmt.on_conflict_do_update(index_elements=[...], set_={...}, where=...)`; both dialects accept the
  same arguments.

## Using the ORM Session

From `runway/equity.py`:

```python
grant = conn.orm.get(EquityGrant, gid)
if grant is None:
    raise EquityError("Grant not found")
for k, v in g.items():
    setattr(grant, k, v)
grant.vested_reported = None
```

and `conn.orm.add(EquityCompany(id=cid, **fields))` for a new row. Notes:

- Changes are written at the next `conn.execute()`, `conn.orm` query, or `conn.commit()`. If you need a generated id
  now, `conn.orm.flush()` then read `obj.id`.
- Attributes not set on a new object are left out of the INSERT, so server defaults apply.
- `conn.orm.get()` loads every column (including big `raw` JSON); for a mere existence check use
  `conn.execute(select(Model.id).where(...)).fetchone()`.
- To return an object as a dict: `db.as_dict(obj)` (columns in table order). For lists, prefer
  `db.rows(conn.execute(select(...)))`.
- Deleting: prefer `conn.execute(delete(Model).where(...))`; the relationships are view-only, so nothing cascades.

## API responses

Handlers return dicts that become JSON; the frontend reads them by key.

- `db.rows(conn.execute(stmt))` gives a list of dicts.
- Dict keys are the selected columns' names or labels. `select(Model.col)` is keyed `col`; a computed column needs
  `.label()`. When you change a query, check every key the frontend reads survived.
- Types: SQLite gives back what's stored. Row order: keep every `ORDER BY`, and don't rely on an order the query
  doesn't ask for.
- When you rewrite a query, compare its output before and after on the same data: run the previous version (from
  `git show HEAD:runway/x.py`, loaded as a module) and the new one on a demo database (`demo.seed(conn)`) plus the
  edge cases the module handles, and compare `json.dumps(...)` of both (key order included).

## When SQLAlchemy can't express it: `text()`

Almost everything can be written with SQLAlchemy (window functions: `func.row_number().over(...)`; CTEs:
`.cte()`; `UNION`: `union_all()`; correlated subqueries: `.scalar_subquery()`; `EXISTS`: `.exists()`). If something
genuinely can't, use `sqlalchemy.text()` with named parameters, and say why on the line above:

```python
# raw SQL: <why this can't be a statement>
conn.execute(text("... WHERE x = :x"), {"x": x})
```

The guard counts `text()` calls without that comment. SQL must still run on both databases. Dynamic table names
(runway/backup.py) aren't a reason: use `schema.metadata.tables[name]` and `insert(table)`.

Not statements, on purpose: Alembic migrations (`runway/migrations`, with `op.execute`), the driver-level setup in
`db.py`'s engine functions (`dbapi_conn.execute("PRAGMA ...")`) and its schema upgrade (`exec_driver_sql` DDL), and
tests that set up an older schema, a column the models don't have, or a SQLite setting: they run SQL with
`exec_driver_sql` on a SQLAlchemy connection (`conn.sa.exec_driver_sql(...)` for a `db.Connection`; a `PRAGMA` only
`if not db.using_postgres()`).

## SQLite and Postgres

Tests here run on SQLite; CI also runs everything on Postgres 16 (`DATABASE_URL`, see
`.github/workflows/docker.yml`). To run it locally: start Postgres, then
`DATABASE_URL=postgresql://user:pass@localhost/db python -m unittest discover tests` (each test gets its own schema).

- Statements are compiled for the connection's database, so portable constructs are portable automatically. Avoid
  `func.<something>` that exists on only one (`func.instr` -> `db.instr`; `func.strftime`, `func.datetime`,
  `func.julianday`, `func.group_concat`, `func.ifnull` are SQLite-only: use `func.coalesce`, string comparison of ISO
  dates, or do it in Python). `sqlite_*`/`postgresql_*` imports only via `db.dialect_insert`.
- On Postgres, SQLAlchemy sends parameters with a cast to the column's type (`%(x)s::VARCHAR`, `::INTEGER`), and
  Runway's Postgres setup sends Python values as untyped text; a value that doesn't fit the column's type (a string of
  letters for an integer column, `1.5` for an integer column) fails on Postgres where SQLite would have stored it.
  Pass values of the column's type (`int(...)` for 0/1 flags).
- `func.round(x, 2)` on a float column fails on Postgres (it needs numeric): round in Python, as the code mostly does.
- `LIMIT -1` is SQLite-only (see `oidc.py`): use `.offset(n)` alone.
- `GROUP BY`: Postgres requires every selected non-aggregate column to be grouped (SQLite doesn't), so a query that
  relies on SQLite picking "some row" per group fails on Postgres.
- Booleans are 0/1 integers in this schema: compare with `== 1` / `== 0`, not `.is_(True)`.

## Testing a change

1. `python -W ignore -m unittest discover tests`: all pass. The module's own tests should cover the queries you
   changed; add a test for any query path that isn't covered before you change it.
2. When rewriting a query, compare its output before and after (above).
3. `ruff check .` and `mypy`.
4. `python -m unittest tests.test_orm_guard`: it fails on SQL text anywhere in `runway/` or `tests/`.
5. Try the pages it feeds (`python run.py demo`, then `python run.py --no-sync`).

## Checklist

- [ ] Every `conn.execute(...)` runs a statement (or a commented `text()`).
- [ ] Rewritten queries keep the same rows, order, dict keys and types, and the same `lastrowid`/`rowcount` use.
- [ ] Every `conn.commit()` kept where it was; no `conn.orm.commit()`.
- [ ] No per-row queries (use joins, `in_()` or `selectinload`).
- [ ] Shared fragments from `splits.parts()` / `db.not_investment()` / `db.account_label_expr()`.
- [ ] Nothing SQLite- or Postgres-only.
- [ ] Tests, ruff and the guard pass.
