import unittest
from datetime import date

from sqlalchemy import func, select

from runway.storage import db
from runway.domain import demo, forecast
from runway.storage.models import Account, Recurring
from tests.shared import DbCase


@unittest.skipIf(db.using_postgres(), "needs an empty database of its own")
class DemoTests(DbCase):
    def test_seeds_an_empty_database_once(self):
        n = demo.seed(self.c, today=date(2026, 9, 28))
        self.assertGreater(n, 100)
        self.assertEqual(self.c.execute(select(func.count()).select_from(Account)).fetchone()[0], 4)
        self.assertEqual(self.c.execute(select(func.count()).select_from(Recurring)).fetchone()[0], len(demo.BILLS))
        self.assertTrue(db.get_setting(self.c, "simplefin_access_url").endswith(".invalid/simplefin"))
        card = forecast.build(self.c, date(2026, 9, 28), 30)["cards"][0]
        self.assertEqual((card["statement_source"], card["last_close"], card["due_date"], card["statement_stale"]),
                         ("manual", "2026-09-28", "2026-10-23", False))
        self.assertGreater(card["statement_balance"], 0)
        with self.assertRaises(SystemExit):
            demo.seed(self.c)


if __name__ == "__main__":
    unittest.main()
