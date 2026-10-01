"""The ORM session layer (runway/db.py): statements run with Connection.execute() and the ORM Session share the
Connection's transaction, results read as rows did, and the portable helpers (upsert, insert_ignore, instr,
account_label_expr, splits.parts, not_investment) give the rows they should."""
import unittest

from sqlalchemy import func, insert, select, update

from runway import db, schema, splits
from runway.models import Account, Asset, AssetValue, Rule, Setting, Transaction, TxSplit
from tests.shared import DbCase


class SessionLayerTests(DbCase):
    def others_see(self, stmt):
        with db.session(self.path) as other:
            return other.execute(stmt).fetchall()

    def test_statement_results_read_like_legacy_rows(self):
        self.c.execute(insert(Account).values(id="a", name="Checking", kind="checking"))
        row = self.c.execute(select(Account.id, Account.name.label("n"))).fetchone()
        self.assertEqual((row[0], row["id"], row["n"], dict(row)), ("a", "a", "Checking", {"id": "a", "n": "Checking"}))
        self.assertEqual(db.rows(self.c.execute(select(Account.id))), [{"id": "a"}])
        self.assertEqual(db.rows(self.c.orm.execute(select(Account.id))), [{"id": "a"}])
        whole = db.rows(self.c.execute(select(Account)))[0]   # select(Model): every column, as SELECT * gave
        self.assertEqual(list(whole), [c.name for c in schema.accounts.c])
        self.assertEqual((whole["id"], whole["name"], whole["kind"], whole["display_name"]), ("a", "Checking", "checking", None))
        self.assertEqual(db.as_dict(self.c.orm.get(Account, "a")), whole)
        self.assertRaisesRegex(TypeError, "SQL text isn't supported", self.c.execute, "SELECT * FROM accounts")   # only statements

    def test_insert_gives_lastrowid_and_rowcount(self):
        r = self.c.execute(insert(Rule).values(match="coffee", category="Coffee & Snacks"))
        self.assertTrue(r.lastrowid)
        self.assertEqual(self.c.execute(select(Rule.match).where(Rule.id == r.lastrowid)).fetchone()[0], "coffee")
        self.assertEqual(self.c.execute(update(Rule).where(Rule.match == "coffee").values(category=None)).rowcount, 1)
        self.c.execute(insert(Rule), [])   # no rows: nothing inserted, not a row of defaults
        self.c.execute(insert(Rule), [{"match": "a"}, {"match": "b"}])
        self.assertEqual(self.c.execute(select(func.count()).select_from(Rule)).scalar(), 3)
        self.assertEqual(self.c.execute(select(Rule.match).order_by(Rule.id)).scalars(), ["coffee", "a", "b"])
        self.assertIsNone(self.c.execute(select(Rule.match).where(Rule.id == -1)).scalar())

    def test_orm_and_statements_share_one_transaction(self):
        self.c.execute(insert(Account).values(id="a", name="Checking"))   # a statement first: the Session joins
        self.c.orm.add(Asset(name="House", kind="home", value=100.0))
        # pending ORM changes are written before a statement runs, so it sees them
        self.assertEqual(self.c.execute(select(Asset.name)).fetchone()[0], "House")
        self.assertEqual(self.others_see(select(Asset)), [])   # nothing committed yet
        self.c.commit()
        self.assertEqual(len(self.others_see(select(Asset))), 1)
        self.assertEqual(len(self.others_see(select(Account))), 1)

    def test_orm_first_then_statements_then_commit(self):
        car = Asset(name="Car", kind="vehicle", value=5.0)
        self.c.orm.add(car)
        self.c.orm.flush()   # the Session began the transaction
        self.c.execute(insert(AssetValue).values(asset_id=car.id, date="2024-01-01", value=5))
        self.c.commit()
        self.assertEqual(self.others_see(select(AssetValue.asset_id))[0][0], car.id)
        # and both keep working after the commit (code commits before a slow network call, then carries on)
        car.value = 6.0
        self.c.execute(update(Account).values(hidden=1))
        self.c.commit()
        self.assertEqual(self.others_see(select(Asset.value))[0][0], 6.0)

    def test_rollback_undoes_both(self):
        self.c.execute(insert(Account).values(id="a", name="Checking"))
        self.c.orm.add(Asset(name="House", kind="home"))
        self.c.orm.flush()
        self.c.rollback()
        self.assertEqual(self.c.execute(select(func.count()).select_from(Account)).fetchone()[0], 0)
        self.assertEqual(self.c.execute(select(func.count()).select_from(Asset)).fetchone()[0], 0)
        with self.assertRaises(RuntimeError), db.session(self.path) as conn:
            conn.orm.add(Asset(name="Boat", kind="other"))
            conn.execute(insert(Account).values(id="b", name="Savings"))
            raise RuntimeError
        self.assertEqual(self.others_see(select(Asset)) + self.others_see(select(Account)), [])

    def test_statement_writes_refresh_loaded_objects(self):
        self.c.orm.add(Asset(id=1, name="House", kind="home", value=1.0))
        self.c.orm.flush()
        house = self.c.orm.get(Asset, 1)
        self.c.execute(update(Asset).where(Asset.id == 1).values(value=2))
        self.assertEqual(house.value, 2.0)
        self.c.execute(update(Asset).where(Asset.id == 1).values(value=Asset.value + 1))
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
        self.assertEqual(self.c.execute(select(func.count()).select_from(AssetValue)).fetchone()[0], 1)

    def test_shared_fragments(self):
        self.c.execute(insert(Account), [{"id": "a", "name": "Checking", "kind": "checking"},
                                         {"id": "i", "name": "Brokerage", "kind": "investment"}])
        self.c.execute(insert(Transaction), [
            {"id": i, "account_id": a, "posted": d, "amount": amount, "category": cat, "is_split": split}
            for i, a, d, amount, cat, split in [("t1", "a", "2024-01-01", -100, "Shopping", 1),
                                                ("t2", "a", "2024-01-02", -5, "Coffee & Snacks", 0),
                                                ("t3", "i", "2024-01-03", -7, None, None)]])
        self.c.execute(insert(TxSplit), [{"tx_id": "t1", "amount": -60, "category": "Groceries"},
                                         {"tx_id": "t1", "amount": -40, "category": "Shopping"}])
        p = splits.parts()
        got = sorted((r["id"], r["posted"], r["amount"], r["category"], r["category_source"])
                     for r in self.c.execute(select(p).where(db.not_investment(p.c.account_id))))
        self.assertEqual(got, [("t1", "2024-01-01", -60.0, "Groceries", "split"), ("t1", "2024-01-01", -40.0, "Shopping", "split"),
                               ("t2", "2024-01-02", -5.0, "Coffee & Snacks", None)])   # t1 by its parts; t3 an investment
        self.assertEqual(len(self.c.execute(select(p)).fetchall()), 4)
        self.assertEqual(list(self.c.execute(select(p)).fetchone().keys()),
                         ["id", "account_id", "posted", "amount", "payee", "description", "category", "category_source",
                          "needs_review", "pending", "recurring_id"])
        self.assertEqual({r[0] for r in self.c.execute(select(Transaction.id).where(db.not_investment()))}, {"t1", "t2"})

    def test_instr_and_account_label_expr(self):
        self.assertEqual(self.c.execute(select(db.instr("hello", "ll"), db.instr("hello", "%"))).fetchone()[:], (3, 0))
        self.c.execute(insert(Account), [{"id": i, "name": n, "display_name": d, "owner": o} for i, n, d, o in [
            ("a", "Card", None, "Sam"), ("b", "Card", "Sam's card", "sam"), ("c", "Card", "Mine", ""),
            ("d", "Card", None, None), ("e", "50% card", None, "_")]])
        got = {r["id"]: r["name"] for r in self.c.execute(select(Account.id, db.account_label_expr().label("name")))}
        want = {r["id"]: db.account_label(r) for r in self.c.execute(select(Account))}
        self.assertEqual(got, want)
        self.assertEqual(got, {"a": "Card (Sam)", "b": "Sam's card", "c": "Mine", "d": "Card", "e": "50% card (_)"})
        self.assertEqual(self.c.execute(select(Account.id).where(Account.name.like("50%"))).scalars(), ["e"])


if __name__ == "__main__":
    unittest.main()
