import gzip
import json
import os
import tempfile
import unittest
from datetime import date

from runway import backup, db, networth


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a, self.b = os.path.join(self.tmp.name, "a.db"), os.path.join(self.tmp.name, "b.db")
        db.init(self.a)
        db.init(self.b)

    def tearDown(self):
        self.tmp.cleanup()

    def fill(self, path):
        c = db.connect(path)
        c.execute("INSERT INTO accounts(id, name, kind, balance) VALUES ('chk', 'Checking', 'checking', 1234.5)")
        c.executemany("INSERT INTO transactions(id, account_id, posted, amount, description, payee, category) VALUES (?,?,?,?,?,?,?)",
                      [(f"chk|{i}", "chk", f"2026-09-{i + 1:02d}", -10.25 * i, f"SHOP {i}", "Shop", "Shopping") for i in range(20)])
        c.execute("INSERT INTO rules(match, category) VALUES ('shop', 'Shopping')")
        networth.save_asset(c, {"name": "House", "kind": "home", "value": 400000}, today=date(2026, 9, 1))
        db.set_setting(c, "openrouter_api_key", "sk-secret")
        c.execute("INSERT INTO auth_sessions(token_hash, sub, created, expires) VALUES ('h', 's', 0, 9e9)")
        c.commit()
        return c

    def test_round_trip(self):
        src = self.fill(self.a)
        raw = backup.dump(src)
        data = backup.load(raw)
        self.assertEqual(data["format"], "runway-backup")
        self.assertNotIn("auth_sessions", data["tables"])          # sessions don't travel
        dst = db.connect(self.b)
        dst.execute("INSERT INTO accounts(id, name) VALUES ('old', 'Old stuff')")   # replaced, not merged
        counts = backup.restore(dst, data)
        dst.commit()
        self.assertEqual(counts["transactions"], 20)
        self.assertEqual([r[0] for r in dst.execute("SELECT id FROM accounts")], ["chk"])
        self.assertEqual(dst.execute("SELECT SUM(amount) FROM transactions").fetchone()[0],
                         src.execute("SELECT SUM(amount) FROM transactions").fetchone()[0])
        self.assertEqual(db.get_setting(dst, "openrouter_api_key"), "sk-secret")
        # auto-numbered ids carry on after the restored ones
        dst.execute("INSERT INTO rules(match, category) VALUES ('grocer', 'Groceries')")
        ids = [r[0] for r in dst.execute("SELECT id FROM rules ORDER BY id")]
        self.assertEqual(len(set(ids)), 2)
        src.close(); dst.close()

    def test_rejects_other_files(self):
        for raw in (b"hello", gzip.compress(b'{"format": "something-else"}'), json.dumps({"format": "runway-backup", "version": 99, "tables": {}}).encode()):
            with self.assertRaises(ValueError):
                backup.load(raw)

    def test_backup_from_older_version_with_missing_columns(self):
        src = self.fill(self.a)
        data = backup.load(backup.dump(src))
        t = data["tables"]["accounts"]
        drop = t["columns"].index("daily_spend")                 # pretend an older version didn't have it
        t["columns"].pop(drop)
        t["rows"] = [r[:drop] + r[drop + 1:] for r in t["rows"]]
        t["columns"].append("column_from_the_future"); [r.append(1) for r in t["rows"]]
        dst = db.connect(self.b)
        backup.restore(dst, data)
        self.assertEqual(dst.execute("SELECT name FROM accounts").fetchone()[0], "Checking")
        src.close(); dst.close()

    def test_a_column_only_the_database_has_travels(self):
        src = self.fill(self.a)
        src.execute("ALTER TABLE accounts ADD COLUMN legacy_note TEXT")
        src.execute("UPDATE accounts SET legacy_note='kept'")
        data = backup.load(backup.dump(src))
        self.assertIn("legacy_note", data["tables"]["accounts"]["columns"])
        dst = db.connect(self.b)
        dst.execute("ALTER TABLE accounts ADD COLUMN legacy_note TEXT")
        backup.restore(dst, data)
        self.assertEqual(dst.execute("SELECT name, legacy_note FROM accounts").fetchall(), [("Checking", "kept")])
        src.close(); dst.close()
