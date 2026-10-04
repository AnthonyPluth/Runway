"""db.is_busy: a database error that only means something else was writing (so try again), on SQLite and Postgres."""
import os
import sqlite3
import tempfile
import unittest

import psycopg.errors
from sqlalchemy.exc import IntegrityError, OperationalError

from runway import db


def wrapped(orig: BaseException) -> OperationalError:
    """The driver's error as SQLAlchemy raises it."""
    return OperationalError("UPDATE account SET balance=?", {}, orig)


class BusyTests(unittest.TestCase):
    def test_sqlite_locked_is_busy(self):
        for text in ("database is locked", "database table is locked", "database table is locked: account"):
            with self.subTest(text=text):
                self.assertTrue(db.is_busy(wrapped(sqlite3.OperationalError(text))))
                self.assertTrue(db.is_busy(sqlite3.OperationalError(text)))   # the driver's own, unwrapped

    def test_sqlite_by_its_error_name(self):
        e = sqlite3.OperationalError("something else")
        e.sqlite_errorname = "SQLITE_BUSY_SNAPSHOT"
        self.assertTrue(db.is_busy(wrapped(e)))

    def test_a_real_sqlite_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.db")
            a, b = sqlite3.connect(path, timeout=0), sqlite3.connect(path, timeout=0)
            try:
                a.execute("CREATE TABLE t (x)")
                a.commit()
                a.execute("BEGIN IMMEDIATE")
                with self.assertRaises(sqlite3.OperationalError) as caught:
                    b.execute("BEGIN IMMEDIATE")
                self.assertTrue(db.is_busy(caught.exception))
            finally:
                a.close()
                b.close()

    def test_other_sqlite_errors_are_not(self):
        for text in ("no such table: account", "disk I/O error", "the database is locked up tight"):
            with self.subTest(text=text):
                self.assertFalse(db.is_busy(wrapped(sqlite3.OperationalError(text))))

    def test_postgres_lock_timeout_deadlock_and_serialization_failure(self):
        for cls in (psycopg.errors.LockNotAvailable, psycopg.errors.DeadlockDetected, psycopg.errors.SerializationFailure):
            with self.subTest(error=cls.__name__):
                self.assertTrue(db.is_busy(wrapped(cls("could not obtain lock"))))
                self.assertTrue(db.is_busy(cls("x")))

    @unittest.skipUnless(db.using_postgres(), "needs Postgres (DATABASE_URL)")
    def test_a_real_postgres_lock_timeout(self):
        key = 0x7E57B05   # an advisory lock only this test takes
        with db.engine().connect() as a, db.engine().connect() as b:
            a.exec_driver_sql(f"SELECT pg_advisory_xact_lock({key})")
            b.exec_driver_sql("SET lock_timeout = '50ms'")
            with self.assertRaises(OperationalError) as caught:
                b.exec_driver_sql(f"SELECT pg_advisory_xact_lock({key})")
            self.assertTrue(db.is_busy(caught.exception))
            a.rollback()

    def test_other_postgres_errors_are_not(self):
        # A statement timeout, a missing column and a broken constraint aren't worth trying again, even when their text
        # says "locked" (only the SQLSTATE counts on Postgres).
        for e in (psycopg.errors.QueryCanceled("canceling statement"), psycopg.errors.UndefinedColumn("column locked"),
                  psycopg.errors.UniqueViolation("database is locked")):
            with self.subTest(error=type(e).__name__):
                self.assertFalse(db.is_busy(wrapped(e)))
        self.assertFalse(db.is_busy(IntegrityError("INSERT", {}, psycopg.errors.UniqueViolation("dup"))))

    def test_anything_else_is_not(self):
        self.assertFalse(db.is_busy(ValueError("database is locked")))
        self.assertFalse(db.is_busy(KeyError("locked")))


if __name__ == "__main__":
    unittest.main()
