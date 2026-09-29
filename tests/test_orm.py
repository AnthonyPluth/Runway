"""The ORM session layer (runway/db.py): statements and the ORM Session share the Connection's transaction with the
legacy SQL text, and the portable helpers (upsert, insert_ignore, instr, account_label_expr) behave as the SQL did."""
import os
import tempfile
import unittest

from sqlalchemy import func, insert, select, update

from runway import db
from runway.models import Account, Asset, AssetValue, Rule, Setting


class SessionLayerTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "orm.db")
        db.init(self.path)
        self.c = db.connect(self.path)

    def tearDown(self):
        self.c.close()

    def others_see(self, sql):
        with db.session(self.path) as other:
            return other.execute(sql).fetchall()

    def test_statement_results_read_like_legacy_rows(self):
        self.c.execute("INSERT INTO accounts(id, name, kind) VALUES ('a', 'Checking', 'checking')")
        row = self.c.execute(select(Account.id, Account.name.label("n"))).fetchone()
        self.assertEqual((row[0], row["id"], row["n"], dict(row)), ("a", "a", "Checking", {"id": "a", "n": "Checking"}))
        self.assertEqual(db.rows(self.c.execute(select(Account.id))), [{"id": "a"}])
        self.assertEqual(db.rows(self.c.orm.execute(select(Account.id))), [{"id": "a"}])
        whole = db.rows(self.c.execute(select(Account)))[0]   # select(Model): every column, as SELECT * gave
        self.assertEqual(whole, dict(self.c.execute("SELECT * FROM accounts").fetchone()))
        self.assertEqual(db.as_dict(self.c.orm.get(Account, "a")), whole)

    def test_insert_gives_lastrowid_and_rowcount(self):
        r = self.c.execute(insert(Rule).values(match="coffee", category="Coffee & Snacks"))
        self.assertTrue(r.lastrowid)
        self.assertEqual(self.c.execute(select(Rule.match).where(Rule.id == r.lastrowid)).fetchone()[0], "coffee")
        self.assertEqual(self.c.execute(update(Rule).where(Rule.match == "coffee").values(category=None)).rowcount, 1)
        self.c.execute(insert(Rule), [])   # no rows: nothing inserted, not a row of defaults
        self.c.execute(insert(Rule), [{"match": "a"}, {"match": "b"}])
        self.assertEqual(self.c.execute(select(func.count()).select_from(Rule)).fetchone()[0], 3)

    def test_orm_and_legacy_sql_share_one_transaction(self):
        self.c.execute("INSERT INTO accounts(id, name) VALUES ('a', 'Checking')")   # legacy first: the Session joins
        self.c.orm.add(Asset(name="House", kind="home", value=100.0))
        # pending ORM changes are written before legacy SQL runs, so it sees them
        self.assertEqual(self.c.execute("SELECT name FROM assets").fetchone()[0], "House")
        self.assertEqual(self.others_see("SELECT * FROM assets"), [])   # nothing committed yet
        self.c.commit()
        self.assertEqual(len(self.others_see("SELECT * FROM assets")), 1)
        self.assertEqual(len(self.others_see("SELECT * FROM accounts")), 1)

    def test_orm_first_then_legacy_then_commit(self):
        car = Asset(name="Car", kind="vehicle", value=5.0)
        self.c.orm.add(car)
        self.c.orm.flush()   # the Session began the transaction
        self.c.execute("INSERT INTO asset_values(asset_id, date, value) VALUES (?, '2024-01-01', 5)", (car.id,))
        self.c.commit()
        self.assertEqual(self.others_see("SELECT asset_id FROM asset_values")[0][0], car.id)
        # and both keep working after the commit (code commits before a slow network call, then carries on)
        car.value = 6.0
        self.c.execute("UPDATE accounts SET hidden=1")
        self.c.commit()
        self.assertEqual(self.others_see("SELECT value FROM assets")[0][0], 6.0)

    def test_rollback_undoes_both(self):
        self.c.execute("INSERT INTO accounts(id, name) VALUES ('a', 'Checking')")
        self.c.orm.add(Asset(name="House", kind="home"))
        self.c.orm.flush()
        self.c.rollback()
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 0)
        self.assertEqual(self.c.execute(select(func.count()).select_from(Asset)).fetchone()[0], 0)
        with self.assertRaises(RuntimeError), db.session(self.path) as conn:
            conn.orm.add(Asset(name="Boat", kind="other"))
            conn.execute("INSERT INTO accounts(id, name) VALUES ('b', 'Savings')")
            raise RuntimeError
        self.assertEqual(self.others_see("SELECT * FROM assets") + self.others_see("SELECT * FROM accounts"), [])

    def test_legacy_writes_refresh_loaded_objects(self):
        self.c.orm.add(Asset(id=1, name="House", kind="home", value=1.0))
        self.c.orm.flush()
        house = self.c.orm.get(Asset, 1)
        self.c.execute("UPDATE assets SET value=2 WHERE id=1")
        self.assertEqual(house.value, 2.0)
        self.c.execute(update(Asset).where(Asset.id == 1).values(value=3.0))
        self.assertEqual(house.value, 3.0)

    def test_upsert_and_insert_ignore(self):
        db.upsert(self.c, Setting, {"key": "k", "value": "1"}, key=["key"])
        db.upsert(self.c, Setting, {"key": "k", "value": "2"}, key=["key"])
        self.assertEqual(db.get_setting(self.c, "k"), "2")
        db.upsert(self.c, Setting, [{"key": "k", "value": "3"}, {"key": "j", "value": "4"}], key=["key"])
        self.assertEqual(db.rows(self.c.execute(select(Setting).where(Setting.key.in_(["j", "k"])).order_by(Setting.key))),
                         [{"key": "j", "value": "4"}, {"key": "k", "value": "3"}])
        db.upsert(self.c, Setting, {"key": "k", "value": "5"}, key=["key"], update=[])   # DO NOTHING
        self.assertEqual(db.get_setting(self.c, "k"), "3")
        db.upsert(self.c, Setting, {"key": "k", "value": "x"}, key=["key"],
                  update=lambda ex: {"value": Setting.value + ex.value})
        self.assertEqual(db.get_setting(self.c, "k"), "3x")
        db.insert_ignore(self.c, Setting, {"key": "k", "value": "y"})
        db.insert_ignore(self.c, AssetValue, [{"asset_id": 1, "date": "2024-01-01", "value": 1.0}] * 2,
                         key=["asset_id", "date"])
        self.assertEqual(db.get_setting(self.c, "k"), "3x")
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM asset_values").fetchone()[0], 1)

    def test_instr_and_account_label_expr(self):
        self.assertEqual(self.c.execute(select(db.instr("hello", "ll"), db.instr("hello", "%"))).fetchone()[:], (3, 0))
        self.c.executemany("INSERT INTO accounts(id, name, display_name, owner) VALUES (?,?,?,?)", [
            ("a", "Card", None, "Sara"), ("b", "Card", "Sara's card", "sara"), ("c", "Card", "Mine", ""),
            ("d", "Card", None, None), ("e", "50% card", None, "_")])
        got = {r["id"]: r["name"] for r in self.c.execute(select(Account.id, db.account_label_expr().label("name")))}
        want = {r["id"]: db.account_label(r) for r in self.c.execute("SELECT * FROM accounts")}
        self.assertEqual(got, want)
        legacy = {r["id"]: r["name"] for r in self.c.execute(f"SELECT a.id, {db.label_sql('a')} AS name FROM accounts a")}
        self.assertEqual(got, legacy)


if __name__ == "__main__":
    unittest.main()
