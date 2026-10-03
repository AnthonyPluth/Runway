import gzip
import json
import os
import stat
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime

from sqlalchemy import func, insert, select

from runway import backup, db, networth, server
from runway import settings_keys as sk
from runway.models import Account, AuthSession, Budget, OAuthClient, OAuthGrant, OAuthToken, Rule, Transaction
from tests.shared import own_database


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
        c.execute(insert(Account).values(id="chk", name="Checking", kind="checking", balance=1234.5))
        c.execute(insert(Transaction), [{"id": f"chk|{i}", "account_id": "chk", "posted": f"2026-09-{i + 1:02d}", "amount": -10.25 * i,
                                         "description": f"SHOP {i}", "payee": "Shop", "category": "Shopping"} for i in range(20)])
        c.execute(insert(Rule).values(match="shop", category="Shopping"))
        networth.save_asset(c, {"name": "House", "kind": "home", "value": 400000}, today=date(2026, 9, 1))
        db.set_setting(c, "openrouter_api_key", "sk-secret")
        c.execute(insert(AuthSession).values(token_hash="h", sub="s", created=0, expires=9e9))
        c.execute(insert(OAuthClient).values(id="rwc_x", name="App", redirect_uris="[]", auth_method="none", created=0))
        c.execute(insert(OAuthGrant).values(client_id="rwc_x", scope="read", resource="https://r/mcp", created=0))
        c.execute(insert(OAuthToken).values(token_hash="t", kind="access", grant_id=1, created=0, expires=9e9))
        c.commit()
        return c

    def test_round_trip(self):
        src = self.fill(self.a)
        raw = backup.dump(src)
        data = backup.load(raw)
        self.assertEqual(data["format"], "runway-backup")
        self.assertNotIn("auth_sessions", data["tables"])          # sessions don't travel
        for t in ("oauth_clients", "oauth_grants", "oauth_codes", "oauth_tokens", "oauth_consents"):
            self.assertNotIn(t, data["tables"])                    # nor assistants connected with OAuth
        dst = db.connect(self.b)
        dst.execute(insert(Account).values(id="old", name="Old stuff"))   # replaced, not merged
        counts = backup.restore(dst, data)
        dst.commit()
        self.assertEqual(counts["transactions"], 20)
        self.assertEqual([r[0] for r in dst.execute(select(Account.id))], ["chk"])
        self.assertEqual(dst.execute(select(func.sum(Transaction.amount))).fetchone()[0],
                         src.execute(select(func.sum(Transaction.amount))).fetchone()[0])
        self.assertEqual(db.get_setting(dst, "openrouter_api_key"), "sk-secret")
        # auto-numbered ids carry on after the restored ones
        dst.execute(insert(Rule).values(match="grocer", category="Groceries"))
        ids = [r[0] for r in dst.execute(select(Rule.id).order_by(Rule.id))]
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
        self.assertEqual(dst.execute(select(Account.name)).fetchone()[0], "Checking")
        src.close(); dst.close()

    def test_a_column_only_the_database_has_travels(self):
        src = self.fill(self.a)
        src.sa.exec_driver_sql("ALTER TABLE accounts ADD COLUMN legacy_note TEXT")   # not in the schema: SQL on the raw connection
        src.sa.exec_driver_sql("UPDATE accounts SET legacy_note='kept'")
        data = backup.load(backup.dump(src))
        self.assertIn("legacy_note", data["tables"]["accounts"]["columns"])
        dst = db.connect(self.b)
        dst.sa.exec_driver_sql("ALTER TABLE accounts ADD COLUMN legacy_note TEXT")
        backup.restore(dst, data)
        self.assertEqual(dst.sa.exec_driver_sql("SELECT name, legacy_note FROM accounts").fetchall(), [("Checking", "kept")])
        src.close(); dst.close()

    def test_preview_says_what_a_backup_holds(self):
        src = self.fill(self.a)
        src.execute(insert(Budget).values(category="Shopping", amount=200))
        src.commit()
        data = backup.load(backup.dump(src))
        data["tables"]["table_from_the_future"] = {"columns": ["x"], "rows": [[1], [2]]}   # not counted: it isn't restored
        p = backup.preview(data)
        self.assertEqual((p["source"], p["version"]), ("postgres" if db.using_postgres() else "sqlite", backup.VERSION))
        self.assertEqual(p["created"], data["created"])
        self.assertEqual({k: p["counts"][k] for k in backup.SUMMARY}, {"accounts": 1, "transactions": 20, "recurring": 0, "budgets": 1})
        self.assertEqual(p["counts"]["total"], sum(len(t["rows"]) for n, t in data["tables"].items() if n != "table_from_the_future"))
        self.assertEqual(backup.counts(src), p["counts"])                    # the live database, counted the same way
        self.assertEqual(backup.counts(db.connect(self.b))["transactions"], 0)
        src.close()

    def test_safety_copy_keeps_what_was_here(self):
        out = os.path.join(self.tmp.name, "copies")
        os.mkdir(out)
        empty = db.connect(self.b)
        self.assertIsNone(backup.safety_copy(empty, out))                    # nothing to keep
        self.assertEqual(os.listdir(out), [])
        src = self.fill(self.a)
        path = backup.safety_copy(src, out)
        assert path is not None
        self.assertEqual(os.path.dirname(path), out)
        self.assertRegex(os.path.basename(path), r"^runway-before-restore-\d{4}-\d\d-\d\d-\d{6}\.json\.gz$")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)         # it holds bank access: private
        with open(path, "rb") as f:
            kept = backup.load(f.read())
        self.assertEqual(len(kept["tables"]["transactions"]["rows"]), 20)
        backup.restore(empty, kept)                                          # and it restores like any backup
        self.assertEqual(empty.execute(select(func.count()).select_from(Transaction)).fetchone()[0], 20)
        src.close(); empty.close()


class BackupServerTests(unittest.TestCase):
    """POST /api/backup/inspect, and the copy POST /api/restore keeps of what it replaces. A restore replaces the whole
    database, so the test has one of its own: on Postgres, restoring the shared one reset its tables' ids under the
    other modules' tests (test_mcp got a grant id that was already taken)."""
    HEADERS = {"X-Runway": "1", "Content-Type": "application/octet-stream"}

    @classmethod
    def setUpClass(cls):
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close()

    def setUp(self):
        self.data = os.path.dirname(own_database(self))   # RUNWAY_DATA, where the safety copy goes

    def post(self, path, body):
        r = urllib.request.Request(self.base + path, method="POST", data=body, headers=self.HEADERS)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def get(self, path, method="GET"):
        with urllib.request.urlopen(urllib.request.Request(self.base + path, method=method), timeout=10) as resp:
            return resp.status, resp.read()

    def test_downloading_a_backup_records_when(self):
        self.assertIsNone(json.loads(self.get("/api/state")[1])["last_backup"])   # never downloaded
        self.assertEqual(self.get("/api/backup", "HEAD")[0], 200)
        self.assertIsNone(json.loads(self.get("/api/state")[1])["last_backup"])   # a HEAD isn't a download
        before = datetime.now().replace(microsecond=0)
        code, raw = self.get("/api/backup")
        self.assertEqual(code, 200)
        self.assertIn("tables", backup.load(raw))
        stamp = None
        for _ in range(50):   # recorded once the file has been sent, so just after the client has it
            with db.session() as c:
                stamp = db.get_setting(c, sk.LAST_BACKUP)
            if stamp:
                break
            time.sleep(0.05)
        self.assertLessEqual(before, datetime.fromisoformat(stamp))           # the machine's local time, no offset stored
        got = json.loads(self.get("/api/state")[1])["last_backup"]
        self.assertTrue(got.startswith(stamp))                                 # sent with its UTC offset
        self.assertEqual(datetime.fromisoformat(got).replace(tzinfo=None), datetime.fromisoformat(stamp))

    def test_inspect_then_restore_keeps_a_copy(self):
        with db.session() as c:
            c.execute(insert(Account).values(id="test:bk", name="Checking", kind="checking", balance=10))
            c.execute(insert(Transaction).values(id="test:bk|1", account_id="test:bk", posted="2026-09-01", amount=-5,
                                                 description="COFFEE"))
        with db.session() as c:
            raw, here = backup.dump(c), backup.counts(c)
        code, got = self.post("/api/backup/inspect", raw)
        self.assertEqual(code, 200)
        self.assertEqual(got["counts"], here)                                # this backup is of what's here
        self.assertEqual(got["current"], here)
        self.assertIn(got["database"], ("sqlite", "postgres"))
        self.assertEqual(self.post("/api/backup/inspect", b"hello"), (400, {"error": "That file isn't a Runway backup."}))
        self.assertFalse([f for f in os.listdir(self.data) if f.startswith("runway-before-restore-")])   # inspecting changes nothing

        code, got = self.post("/api/restore", raw)
        self.assertEqual(code, 200)
        path = got["safety_copy"]
        self.assertEqual(os.path.dirname(path), self.data)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path, "rb") as f:
            self.assertEqual(backup.preview(backup.load(f.read()))["counts"], here)
