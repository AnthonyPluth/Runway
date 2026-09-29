import os
import tempfile
import unittest
from datetime import date

from runway import db, demo


@unittest.skipIf(db.using_postgres(), "needs an empty database of its own")
class DemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        db.init(self.path)
        self.c = db.connect(self.path)

    def tearDown(self):
        self.c.close(); self.tmp.cleanup()

    def test_seeds_an_empty_database_once(self):
        n = demo.seed(self.c, today=date(2026, 9, 28))
        self.assertGreater(n, 100)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 4)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM recurring").fetchone()[0], len(demo.BILLS))
        self.assertTrue(db.get_setting(self.c, "simplefin_access_url").endswith(".invalid/simplefin"))
        with self.assertRaises(SystemExit):   # never on top of existing data
            demo.seed(self.c)


if __name__ == "__main__":
    unittest.main()
