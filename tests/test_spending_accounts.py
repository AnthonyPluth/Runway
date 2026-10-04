"""The accounts spending is counted on (db.SPENDING_ACCOUNTS), which Budget, Reports, the forecast's budgets,
notifications and the planner all use."""
from sqlalchemy import insert, select

from runway import db
from runway.models import Account
from tests.shared import DbCase


class SpendingAccountsTests(DbCase):
    def test_checking_savings_and_cards_that_arent_hidden(self):
        for id, kind, hidden in (("chk", "checking", 0), ("sav", "savings", 0), ("cc", "credit", 0), ("old", "checking", 1),
                                 ("loan", "loan", 0), ("brk", "investment", 0), ("odd", None, 0)):
            self.c.execute(insert(Account).values(id=id, name=id, kind=kind, hidden=hidden, balance=0.0))
        got = sorted(self.c.execute(select(Account.id).where(*db.SPENDING_ACCOUNTS)).scalars())
        self.assertEqual(got, ["cc", "chk", "sav"])
