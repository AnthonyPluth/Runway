"""The schema comes from Alembic migrations; they must match runway/schema.py, and older databases must upgrade."""
import os
import tempfile
import unittest

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from runway import db, schema


def drift(path):
    """Differences between the database and schema.py, as Alembic's autogenerate would see them."""
    with open(os.path.join(os.path.dirname(db.__file__), "migrations", "env.py")) as f:
        src = f.read()
    ns = {}
    exec(src[src.index("def same_type"):src.index("def skip_pk")], ns)
    with db.engine(path).connect() as c:
        diffs = compare_metadata(MigrationContext.configure(c, opts={"compare_type": ns["same_type"]}), schema.metadata)
    pk = lambda t, col: col in {c.name for c in schema.metadata.tables[t].primary_key}
    return [d for d in diffs
            if not (isinstance(d, tuple) and d[0] == "remove_table" and d[1].name == "alembic_version")
            and not (isinstance(d, list) and d[0][0] == "modify_nullable" and pk(d[0][2], d[0][3]))]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "m.db")

    def test_fresh_database_matches_schema(self):
        db.init(self.path)
        self.assertEqual(drift(self.path), [])
        with db.engine(self.path).connect() as c:
            head = ScriptDirectory.from_config(db.alembic_config()).get_current_head()
            self.assertEqual(c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar(), head)

    def test_database_from_before_migrations_is_upgraded(self):
        # An early Runway database: a couple of tables, missing columns added later, no alembic_version.
        with db.engine(self.path).begin() as c:
            c.exec_driver_sql("CREATE TABLE accounts (id TEXT PRIMARY KEY, name TEXT NOT NULL, display_name TEXT, org TEXT, "
                              "currency TEXT DEFAULT 'USD', balance REAL DEFAULT 0, available REAL, balance_date TEXT, "
                              "kind TEXT DEFAULT 'checking', closing_day INTEGER, due_day INTEGER, pay_from TEXT, "
                              "owed_positive INTEGER DEFAULT 0, in_forecast INTEGER DEFAULT 1, daily_spend INTEGER DEFAULT 0, "
                              "hidden INTEGER DEFAULT 0)")
            c.exec_driver_sql("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
            c.exec_driver_sql("INSERT INTO accounts(id, name, daily_spend) VALUES ('a1', 'Checking', 1)")
        db.init(self.path)
        self.assertEqual(drift(self.path), [])
        with db.session(self.path) as conn:
            row = conn.execute("SELECT name, owner, daily_spend FROM accounts").fetchone()
            self.assertEqual((row["name"], row["owner"], row["daily_spend"]), ("Checking", None, 0))
            self.assertGreater(conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0], 10)
        db.init(self.path)   # starting again changes nothing

    def test_plaid_account_counted_twice_is_retired(self):
        from alembic import command
        db.init(self.path)
        with db.engine(self.path).begin() as c:
            c.exec_driver_sql("DROP INDEX accounts_plaid_account")
            c.exec_driver_sql("UPDATE alembic_version SET version_num='0011'")
            c.exec_driver_sql("INSERT INTO accounts(id, name, plaid_account_id) VALUES ('sf', 'Freedom', 'p1'), "
                              "('pl:p1', 'Freedom ••9999', 'p1'), ('pl:p2', 'Savings', 'p2')")
        with db.engine(self.path).begin() as c:
            command.upgrade(db.alembic_config(c), "head")
        with db.session(self.path) as conn:
            got = {r["id"]: (r["plaid_account_id"], r["hidden"]) for r in conn.execute("SELECT id, plaid_account_id, hidden FROM accounts")}
        self.assertEqual(got, {"sf": ("p1", 0), "pl:p1": (None, 1), "pl:p2": ("p2", 0)})
        self.assertEqual(drift(self.path), [])

    def test_connection_wrapper(self):
        db.init(self.path)
        with db.session(self.path) as conn:
            r = conn.execute("INSERT INTO rules(match, category) VALUES (?, ?)", ("50% off", "Shopping"))
            self.assertTrue(r.lastrowid)
            row = conn.execute("SELECT id, match FROM rules WHERE match LIKE '50%'").fetchone()
            self.assertEqual((row[0], row["match"], dict(row)["id"]), (r.lastrowid, "50% off", r.lastrowid))
            self.assertEqual(conn.execute("SELECT instr('hello', 'll')").fetchone()[0], 3)
            conn.execute("INSERT INTO settings(key, value) VALUES (?, ?)", ("n", 5))   # loose typing, as in SQLite
            self.assertEqual(db.get_setting(conn, "n"), "5")


if __name__ == "__main__":
    unittest.main()
