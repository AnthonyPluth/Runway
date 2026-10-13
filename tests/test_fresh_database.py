"""db.create_fresh makes a new database from schema.py in one step instead of replaying the migrations: it has to be the
same database the migrations make, on whichever database the tests run against (SQLite, or Postgres with DATABASE_URL)."""
import re
import unittest
from unittest import mock

import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory

from runway.storage import db
from tests.shared import database_path


def migrated(path) -> None:
    """The database as replaying every migration makes it (what db.migrate did for an empty database before
    create_fresh), then db.init's own steps (the categories)."""
    with db.engine(path).connect() as c:
        if c.dialect.name == "sqlite":   # as db.migrate does: batch mode's remade tables mustn't cascade
            c.exec_driver_sql("PRAGMA foreign_keys=OFF")
            c.commit()
        with c.begin():
            command.upgrade(db.alembic_config(c), "head")
    db.init(path)


def _default(value):
    """A column's default as the database reports it, without parentheses around the whole of it: SQLite keeps the
    text it was given, and batch mode's copy of a table writes "(90)" for the 90 the original was made with."""
    if value is None:
        return None
    value = str(value)
    while re.fullmatch(r"\((.*)\)", value) and _balanced(value[1:-1]):
        value = value[1:-1]
    return value


def _balanced(s) -> bool:
    depth = 0
    for ch in s:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth < 0:
            return False
    return depth == 0


def shape(path) -> dict:
    """Everything about the database's tables that a query or a later migration could tell apart: each table's columns
    in order (type, nullable, default, autoincrement), its primary key, foreign keys (with their ON DELETE and
    deferral), unique constraints, checks and indexes, and, on SQLite, whether it says AUTOINCREMENT; on Postgres, the
    schema's sequences and functions too."""
    out: dict = {}
    with db.engine(path).connect() as c:
        insp = sa.inspect(c)
        for t in sorted(insp.get_table_names()):
            pk = insp.get_pk_constraint(t)
            out[t] = {
                "columns": [(col["name"], str(col["type"].compile(dialect=c.dialect)), col["nullable"],
                             _default(col.get("default")), col.get("autoincrement"), col.get("identity"))
                            for col in insp.get_columns(t)],
                "primary key": (pk.get("name"), pk["constrained_columns"]),
                "foreign keys": sorted((fk["name"], fk["constrained_columns"], fk["referred_table"], fk["referred_columns"],
                                        sorted(fk.get("options", {}).items())) for fk in insp.get_foreign_keys(t)),
                "unique": sorted((u["name"], u["column_names"]) for u in insp.get_unique_constraints(t)),
                "checks": sorted((ck["name"], ck["sqltext"]) for ck in insp.get_check_constraints(t)),
                "indexes": sorted((ix["name"], ix["column_names"], bool(ix["unique"]), sorted(ix.get("dialect_options", {}).items()))
                                  for ix in insp.get_indexes(t)),
            }
        if c.dialect.name == "sqlite":
            for name, sql in c.exec_driver_sql("SELECT name, sql FROM sqlite_master WHERE type = 'table'"):
                if name in out:
                    out[name]["autoincrement keyword"] = "AUTOINCREMENT" in (sql or "").upper()
        else:
            out["(sequences)"] = sorted(tuple(r) for r in c.exec_driver_sql(
                "SELECT sequencename, data_type::text, start_value, increment_by, cycle FROM pg_sequences "
                "WHERE schemaname = current_schema()"))
            out["(sequence owners)"] = sorted(tuple(r) for r in c.exec_driver_sql(
                "SELECT s.relname, t.relname, a.attname FROM pg_depend d JOIN pg_class s ON s.oid = d.objid "
                "JOIN pg_class t ON t.oid = d.refobjid JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid "
                "WHERE s.relkind = 'S' AND s.relnamespace = current_schema()::regnamespace"))
            out["(functions)"] = sorted(r[0] for r in c.exec_driver_sql(
                "SELECT proname FROM pg_proc WHERE pronamespace = current_schema()::regnamespace"))
    return out


def contents(path) -> dict:
    """Every table's rows (sorted, as text), alembic_version's included."""
    out = {}
    with db.engine(path).connect() as c:
        meta = sa.MetaData()
        meta.reflect(c)
        for name, table in sorted(meta.tables.items()):
            out[name] = sorted(tuple(map(repr, r)) for r in c.execute(sa.select(table)))
    return out


class FreshDatabaseTests(unittest.TestCase):
    """The check that holds create_fresh to the migrations: a change to schema.py, or a new migration, that makes the
    two differ fails here (add the migration, or match schema.py to what it leaves, column order included)."""

    @classmethod
    def setUpClass(cls):
        cls.made = database_path(cls, "fresh.db")
        db.init(cls.made)   # an empty database: create_fresh
        cls.replayed = database_path(cls, "migrated.db")
        migrated(cls.replayed)

    def test_the_tables_are_the_same_column_by_column_in_order(self):
        made, replayed = shape(self.made), shape(self.replayed)
        self.assertEqual(sorted(made), sorted(replayed))
        for table in replayed:
            with self.subTest(table=table):
                self.assertEqual(made[table], replayed[table])

    def test_the_rows_are_the_same_categories_and_stamp_included(self):
        made, replayed = contents(self.made), contents(self.replayed)
        self.assertEqual(made, replayed)
        self.assertEqual(len(made["categories"]), len(db.DEFAULT_CATEGORIES))
        head = ScriptDirectory.from_config(db.alembic_config()).get_current_head()
        self.assertEqual(made["alembic_version"], [(repr(head),)])

    def test_a_database_with_tables_still_goes_through_the_migrations(self):
        path = database_path(self, "old.db")
        with db.engine(path).connect() as c:
            if c.dialect.name == "sqlite":
                c.exec_driver_sql("PRAGMA foreign_keys=OFF")
                c.commit()
            with c.begin():
                command.upgrade(db.alembic_config(c), "0042")
        with mock.patch.object(db, "create_fresh", side_effect=AssertionError("not for a database with tables")):
            db.init(path)
        self.assertEqual(shape(path), shape(self.replayed))


if __name__ == "__main__":
    unittest.main()
