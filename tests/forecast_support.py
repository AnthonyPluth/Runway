"""What the forecast tests share across modules: the card they build on and a few helpers (plain functions: a test
class takes one as a method with `name = fs.name`, so none of the classes is imported into another module, where it
would run twice)."""
from unittest import mock

from runway import db
from runway import settings_keys as sk


def card_setup(self):
    self.acct("chk", "checking", 5000.0)
    self.acct("cc", "credit", -900.0, pay_from="chk")
    self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=40.0)
    self.tx("cc", "2026-09-12", -100.0, "COFFEE", "Restaurants")
    self.tx("cc", "2026-09-20", -200.0, "GROCER", "Groceries")
    self.tx("cc", "2026-09-15", 200.0, "PAYMENT THANK YOU", "Credit Card Payment")
    self.tx("cc", "2026-09-01", -800.0, "STUFF", "Shopping")


def drop(self, fc, day):
    """How much the forecast's total falls on a day (what goes out, less what comes in)."""
    i = fc["dates"].index(day)
    return fc["total"][i - 1] - fc["total"][i]


def no_writes(self, fail: Exception):
    """Make every INSERT, UPDATE or DELETE on self.conn raise `fail`. Returns the patch, to stop it early."""
    execute = self.conn.execute

    def guarded(stmt, params=None):
        if getattr(stmt, "is_dml", False):
            raise fail
        return execute(stmt, params)
    patch = mock.patch.object(self.conn, "execute", guarded)
    patch.start()
    self.addCleanup(patch.stop)
    return patch


def pay(self, mode, amount=None, apr=None):
    db.set_setting(self.conn, sk.card_pay_mode("cc"), mode)
    db.set_setting(self.conn, sk.card_pay_amount("cc"), amount)
    db.set_setting(self.conn, sk.card_apr("cc"), apr)


def minimum(statement, interest=0.0):
    """The minimum without one from the issuer: the larger of $25 and 1% of the statement plus its interest."""
    return round(min(statement, max(25.0, statement * 0.01 + interest)), 2)
