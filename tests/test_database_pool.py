"""tests/shared.py's SchemaPool: on Postgres a test's database is a schema borrowed from its process's pool, emptied when
it's given back, so what one test leaves must never reach the next, and two held at once must share nothing."""
import contextlib
import unittest

import sqlalchemy as sa
from sqlalchemy import delete, func, insert, select, update

from runway.storage import db
from runway.storage.models import Account, Category, Rule
from tests import shared


@unittest.skipUnless(db.using_postgres(), "Postgres only: on SQLite each test's database is a file of its own, made "
                                          "and removed with its directory, so there is no pool to test")
class SchemaPoolTests(unittest.TestCase):
    def borrow(self, path) -> str:
        name = shared.POOL.acquire(path)
        self.addCleanup(lambda: path in db.TEST_SCHEMAS and shared.POOL.release(path))
        return name

    def count(self, conn, entity, *where) -> int:
        return conn.execute(select(func.count()).select_from(entity).where(*where)).scalar()

    def test_a_schema_comes_back_empty_with_its_numbering_restarted_and_its_categories_whole(self):
        first = self.borrow("/pool/a.db")
        with db.session("/pool/a.db") as conn:
            categories = sorted(map(tuple, conn.execute(select(Category.name, Category.is_transfer, Category.is_income))))
            conn.execute(insert(Account).values(id="left-behind", name="Left behind", kind="checking", balance=12.5))
            for match in ("one", "two", "three"):
                conn.execute(insert(Rule).values(match=match, category="Shopping"))
            conn.execute(delete(Category).where(Category.name == "Groceries"))
            conn.execute(update(Category).where(Category.name == "Travel").values(icon="x", is_income=1))
            conn.execute(insert(Category).values(name="Made up"))
        shared.POOL.release("/pool/a.db")
        self.assertEqual(self.borrow("/pool/b.db"), first)     # the same schema, lent again
        with db.session("/pool/b.db") as conn:
            self.assertEqual(self.count(conn, Account), 0)
            self.assertEqual(self.count(conn, Rule), 0)
            self.assertEqual(conn.execute(insert(Rule).values(match="fresh", category="Shopping")).lastrowid, 1)
            self.assertEqual(sorted(map(tuple, conn.execute(select(Category.name, Category.is_transfer, Category.is_income)))),
                             categories)
            self.assertIsNone(conn.execute(select(Category.icon).where(Category.name == "Travel")).scalar())
            self.assertEqual(self.count(conn, Category, Category.name == "Made up"), 0)
            self.assertEqual(self.count(conn, sa.table("alembic_version")), 1)

    def test_two_held_at_once_share_nothing(self):
        a, b = self.borrow("/pool/one.db"), self.borrow("/pool/two.db")
        self.assertNotEqual(a, b)
        with db.session("/pool/one.db") as conn:
            conn.execute(insert(Account).values(id="only-in-one", name="One", kind="checking"))
            conn.execute(delete(Category).where(Category.name == "Groceries"))
        with db.session("/pool/two.db") as conn:
            self.assertEqual(conn.execute(select(Account.id)).scalars(), [])
            self.assertEqual(self.count(conn, Category, Category.name == "Groceries"), 1)
            conn.execute(insert(Account).values(id="only-in-two", name="Two", kind="checking"))
        with db.session("/pool/one.db") as conn:
            self.assertEqual(conn.execute(select(Account.id)).scalars(), ["only-in-one"])
        with db.session("/pool/two.db") as conn:
            self.assertEqual(conn.execute(select(Account.id)).scalars(), ["only-in-two"])

    def test_a_connection_left_open_doesnt_stop_it_being_given_back(self):
        name = self.borrow("/pool/open.db")
        left_open = db.engine("/pool/open.db").connect()

        def close():
            with contextlib.suppress(sa.exc.SQLAlchemyError):
                left_open.close()
        self.addCleanup(close)
        left_open.execute(select(Account.id)).fetchall()     # a transaction begun and never ended: it holds a lock
        ended = shared.ENDED[0]
        shared.POOL.release("/pool/open.db")
        self.assertEqual(shared.ENDED[0], ended + 1)
        self.assertEqual(self.borrow("/pool/after.db"), name)    # emptied and lent again, not dropped
        with db.session("/pool/after.db") as conn:
            self.assertEqual(self.count(conn, Account), 0)

    def test_a_path_given_back_no_longer_reaches_the_schema(self):
        name = self.borrow("/pool/gone.db")
        shared.POOL.release("/pool/gone.db")
        self.borrow("/pool/next.db")
        self.assertNotEqual(db.schema_for("/pool/gone.db"), name)
        self.addCleanup(shared.drop_schema, "/pool/gone.db")
        with db.session("/pool/gone.db") as conn:     # a late connection gets an empty schema of its own
            self.assertEqual(conn.execute(select(func.current_schema())).scalar(), db.schema_for("/pool/gone.db"))

    def test_a_schema_whose_tables_a_test_changed_is_dropped_not_lent_again(self):
        name = self.borrow("/pool/changed.db")
        with db.session("/pool/changed.db") as conn:
            conn.sa.exec_driver_sql("ALTER TABLE rules ADD COLUMN extra TEXT")
        shared.POOL.release("/pool/changed.db")
        self.assertNotIn(name, shared.POOL.free)
        from psycopg import sql
        with shared._pg() as conn:
            self.assertIsNone(conn.execute(sql.SQL("SELECT 1 FROM pg_namespace WHERE nspname = {}").format(
                sql.Literal(name))).fetchone())


if __name__ == "__main__":
    unittest.main()
