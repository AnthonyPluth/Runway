import gzip
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime
from unittest import mock

from sqlalchemy import func, insert, select, update

from runway import backup, db, networth, server
from runway import settings_keys as sk
from runway.models import Account, AuthSession, Budget, Category, OAuthClient, OAuthGrant, OAuthToken, Rule, Transaction
from tests.shared import own_database

db_session = db.session


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

    def awkward(self, c):
        """Rows with values a restore must put back exactly as they were."""
        c.execute(insert(Account).values(id="a-card:1", name="Card ’ – − \"quoted\" 'single'", kind="credit", balance=-0.01,
                                         display_name="Ünïcödé 🐶 %s :name \\ back\nslash"))
        c.execute(insert(Account).values(id="z-chk", name="Checking", kind="checking", balance=123456789.123456, available=1e-9))
        c.execute(update(Account).where(Account.id == "a-card:1").values(pay_from="z-chk"))   # paid from one stored after it
        c.execute(insert(Transaction).values(id="a-card:1|1", account_id="a-card:1", posted="2026-02-29", amount=-0.1 - 0.2,
                                             description="Line one\nline two\ttab", payee="", notes="x" * 5000))
        c.execute(insert(Transaction).values(id="z-chk|1", account_id="z-chk", posted="2026-09-01", amount=1e12, description="%_LIKE_%",
                                             payee="O'Brien & Sons", category="Income", pending=1, bank_amount=-0.0))
        c.execute(update(Category).where(Category.name == "Groceries").values(pay_with="a-card:1", icon="🛒", color="#aabbcc"))
        c.execute(insert(Budget).values(category="Groceries", amount=0.005, rollover_from="2026-01"))
        c.execute(insert(Rule).values(match="o'brien", category="Income", account_id="z-chk", split='[{"category": "Income", "percent": 100}]'))
        db.set_setting(c, "openrouter_api_key", "sk-secret ’ 🐶")
        db.set_setting(c, sk.card_apr("a-card:1"), "24.99")

    def rows_of(self, c):
        return {t: sorted(map(tuple, p["rows"]), key=repr) for t, p in backup.export(c)["tables"].items()}

    def test_round_trip_keeps_awkward_values(self):
        src = db.connect(self.a)
        self.awkward(src)
        src.commit()
        data = backup.load(backup.dump(src))
        self.assertEqual(data["revision"], backup.head())   # made at this version's revision: restored as it is
        data["tables"]["accounts"]["rows"].sort(key=lambda r: r[0])   # the card before the account it's paid from
        dst = db.connect(self.b)
        backup.restore(dst, data)
        dst.commit()
        self.assertEqual(self.rows_of(dst), self.rows_of(src))
        src.close(); dst.close()

    def test_an_older_backup_round_trips_through_the_migrations(self):
        from alembic import command
        with db.engine(self.a).begin() as c:
            command.downgrade(db.alembic_config(c), "0038")   # the schema before foreign keys: same columns
        src = db.connect(self.a)
        self.awkward(src)
        src.commit()
        data = backup.load(backup.dump(src))
        self.assertEqual(data["revision"], "0038")
        dst = db.connect(self.b)
        backup.restore(dst, data)
        dst.commit()
        self.assertEqual(self.rows_of(dst), self.rows_of(src))
        if db.using_postgres():   # the database it was brought up to date in is gone, never committed
            self.assertEqual(dst.sa.exec_driver_sql("SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'runway_restore_%%'").scalar(), 0)
        src.close(); dst.close()

    def test_an_older_backup_gets_what_the_migrations_since_change(self):
        # Made at 0031: 0032 shortens the bank's payees, 0036 moves a budget's card to its category, 0039 drops a
        # transaction whose account is gone. Restored here, the backup's data is changed just the same.
        from alembic import command
        with db.engine(self.a).begin() as c:
            command.downgrade(db.alembic_config(c), "0031")
            c.exec_driver_sql("INSERT INTO accounts(id, name, kind) VALUES ('chk', 'Checking', 'checking'), ('cc', 'Visa', 'credit')")
            c.exec_driver_sql("INSERT INTO transactions(id, account_id, posted, amount, description, payee) VALUES "
                              "('chk|1', 'chk', '2026-09-01', -35.91, 'DIRECT DEBIT TARGET DEBIT CACH TRAN (Cash)', 'Target Cach Tran Cash'), "
                              "('gone|1', 'gone', '2026-09-01', -5, 'CAFE', 'Cafe')")
            c.exec_driver_sql("INSERT INTO budgets(category, amount, pay_with) VALUES ('Groceries', 500, 'cc')")
        src = db.connect(self.a)
        data = backup.load(backup.dump(src))
        self.assertEqual((data["revision"], backup.warning(data)), ("0031", None))
        self.assertNotIn("pay_with", data["tables"]["categories"]["columns"])
        dst = db.connect(self.b)
        counts = backup.restore(dst, data)
        dst.commit()
        self.assertEqual(counts["transactions"], 1)
        self.assertEqual(dict(dst.execute(select(Transaction.id, Transaction.payee)).fetchall()), {"chk|1": "Target"})
        self.assertEqual(dst.execute(select(Category.pay_with).where(Category.name == "Groceries")).scalar(), "cc")
        self.assertEqual(dst.execute(select(Budget.amount)).scalar(), 500)
        # The backup is the copy: restoring it doesn't save one of it (0039 does for a database it changes).
        self.assertFalse([f for f in os.listdir(self.tmp.name) if f.startswith("runway-before-migration")])
        src.close(); dst.close()

    def test_a_backup_from_before_backups_recorded_their_revision(self):
        # Restored as it is (the migrations it may predate don't run), then the ones since run, with a warning.
        src = self.fill(self.a)
        data = backup.load(backup.dump(src))
        del data["revision"]
        t = data["tables"]["accounts"]
        t["columns"].append("daily_spend")                       # as it had then (0037 dropped it)
        for r in t["rows"]:
            r.append(1)
        data["tables"]["transactions"]["rows"].append(["gone|1", "gone", "2026-09-01", -5.0] + [None] * (len(data["tables"]["transactions"]["columns"]) - 4))
        self.assertEqual(backup.preview(data)["warning"], backup.OLD_BACKUP)
        dst = db.connect(self.b)
        counts = backup.restore(dst, data)
        dst.commit()
        self.assertEqual(counts["transactions"], 20)              # the one whose account isn't there is left out
        self.assertEqual(dst.execute(select(Account.name)).scalar(), "Checking")
        self.assertEqual(db.get_setting(dst, "openrouter_api_key"), "sk-secret")
        src.close(); dst.close()

    def test_rows_referring_to_what_the_backup_doesnt_have(self):
        # A database changed by hand (SQLite's own shell doesn't enforce foreign keys) can have them: left out, or let go.
        src = self.fill(self.a)
        data = backup.load(backup.dump(src))
        tx = data["tables"]["transactions"]
        tx["rows"].append(["nobody|1", "nobody", "2026-09-01", -5.0] + [None] * (len(tx["columns"]) - 4))
        cats = data["tables"]["categories"]
        i = cats["columns"].index("pay_with")
        cats["rows"][0][i] = "nobody"
        dst = db.connect(self.b)
        self.assertEqual(backup.restore(dst, data)["transactions"], 20)
        dst.commit()
        self.assertIsNone(dst.execute(select(Category.pay_with).where(Category.name == cats["rows"][0][0])).scalar())
        src.close(); dst.close()

    def test_a_backup_from_a_newer_version_is_refused(self):
        src = self.fill(self.a)
        data = backup.load(backup.dump(src))
        for change in ({"revision": "0999"}, {"version": backup.VERSION + 1}):
            with self.subTest(change=change):
                newer = {**data, **change}
                with self.assertRaisesRegex(ValueError, "newer version of Runway"):
                    backup.load(gzip.compress(json.dumps(newer).encode()))
        dst = db.connect(self.b)
        dst.execute(insert(Account).values(id="kept", name="Kept"))
        with self.assertRaisesRegex(ValueError, "newer version of Runway"):
            backup.restore(dst, {**data, "revision": "0999"})
        self.assertEqual([r[0] for r in dst.execute(select(Account.id))], ["kept"])   # nothing touched
        for bad in (5, ["0039"]):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "isn't a Runway backup"):
                backup.load(json.dumps({**data, "revision": bad}).encode())
        src.close(); dst.close()

    def test_rejects_other_files(self):
        for raw in (b"hello", gzip.compress(b'{"format": "something-else"}'), json.dumps({"format": "runway-backup", "version": 99, "tables": {}}).encode()):
            with self.assertRaises(ValueError):
                backup.load(raw)

    def test_backup_from_older_version_with_missing_columns(self):
        src = self.fill(self.a)
        data = backup.load(backup.dump(src))
        t = data["tables"]["accounts"]
        drop = t["columns"].index("logo")                        # pretend an older version didn't have it
        t["columns"].pop(drop)
        t["rows"] = [r[:drop] + r[drop + 1:] for r in t["rows"]]
        t["columns"].append("column_from_the_future"); [r.append(1) for r in t["rows"]]
        dst = db.connect(self.b)
        backup.restore(dst, data)
        self.assertEqual(dst.execute(select(Account.name)).fetchone()[0], "Checking")
        src.close(); dst.close()

    def test_a_backup_from_before_categories_had_cards_keeps_each_budgets_card(self):
        src = self.fill(self.a)
        src.execute(insert(Budget), [{"category": "Groceries", "amount": 500}, {"category": "Shopping", "amount": 100}])
        data = backup.load(backup.dump(src))
        b, c = data["tables"]["budgets"], data["tables"]["categories"]
        b["columns"].append("pay_with")   # as 0034 and earlier had it: on the budget, not the category
        for r in b["rows"]:
            r.append("chk" if r[b["columns"].index("category")] == "Groceries" else None)
        drop = c["columns"].index("pay_with")
        c["columns"].pop(drop)
        c["rows"] = [r[:drop] + r[drop + 1:] for r in c["rows"]]
        del data["revision"]                # and from before backups said which revision they were made at
        self.assertEqual(backup.warning(data), backup.OLD_BACKUP)
        dst = db.connect(self.b)
        backup.restore(dst, data)
        self.assertEqual(dict(dst.execute(select(Category.name, Category.pay_with).where(Category.pay_with.is_not(None))).fetchall()),
                         {"Groceries": "chk"})
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

    def test_restore_all_holds_off_syncs(self):
        lock = threading.Lock()
        with mock.patch.object(db, "session", lambda p=None: db_session(p or self.b)):
            data = backup.load(backup.dump(self.fill(self.a)))
            with lock, self.assertRaises(backup.Busy):
                backup.restore_all(data, locks=(threading.Lock(), lock), directory=self.tmp.name)
            with db.session() as c:
                self.assertEqual(c.execute(select(func.count()).select_from(Transaction)).scalar(), 0)   # nothing restored
            done = backup.restore_all(data, locks=(lock,), directory=self.tmp.name)
            self.assertFalse(lock.locked())
            self.assertEqual((done["counts"]["transactions"], done["safety_copy"], done["unreadable_secrets"], done["warning"]),
                             (20, None, [], None))   # nothing was here to keep a copy of

    def test_every_secret_has_a_label(self):
        self.assertEqual(set(backup.SECRET_LABELS), set(sk.SECRETS))
        self.assertEqual(backup.unreadable_summary([sk.PLAID_SECRET, "plaid:abc"]), "Plaid secret, 1 Plaid connection")
        self.assertEqual(backup.unreadable_summary([]), "")

    def test_preview_says_what_a_backup_holds(self):
        src = self.fill(self.a)
        src.execute(insert(Budget).values(category="Shopping", amount=200))
        src.commit()
        data = backup.load(backup.dump(src))
        data["tables"]["table_from_the_future"] = {"columns": ["x"], "rows": [[1], [2]]}   # not counted: it isn't restored
        p = backup.preview(data)
        self.assertEqual((p["source"], p["version"]), ("postgres" if db.using_postgres() else "sqlite", backup.VERSION))
        self.assertEqual((p["revision"], p["warning"]), (backup.head(), None))
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
        self.assertIsNone(got["warning"])

    def test_restoring_a_backup_from_before_revisions_warns(self):
        with db.session() as c:
            c.execute(insert(Account).values(id="test:old", name="Checking", kind="checking", balance=10))
            data = backup.export(c)
        del data["revision"]
        raw = gzip.compress(json.dumps(data, default=str).encode())
        code, got = self.post("/api/backup/inspect", raw)
        self.assertEqual((code, got["warning"]), (200, backup.OLD_BACKUP))   # said before you restore it
        code, got = self.post("/api/restore", raw)
        self.assertEqual((code, got["warning"], got["accounts"]), (200, backup.OLD_BACKUP, 1))


class RestoreCommandTests(unittest.TestCase):
    """python run.py restore: the same restore as the web's (a copy first, what can't be read, the warning)."""

    def run_py(self, data_dir, *args, stdin=None):
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}   # a SQLite database of its own
        env.update(RUNWAY_DATA=data_dir, RUNWAY_SECRET_KEY="k" * 40)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return subprocess.run([sys.executable, os.path.join(root, "run.py"), *args], env=env, input=stdin, capture_output=True,
                              text=True, timeout=120, cwd=root)

    def test_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = os.path.join(tmp, "src"), os.path.join(tmp, "dst")
            os.mkdir(src); os.mkdir(dst)
            made = self.run_py(src, "demo")
            self.assertEqual(made.returncode, 0, made.stderr)
            out = os.path.join(tmp, "b.json.gz")
            self.assertEqual(self.run_py(src, "backup", out).returncode, 0)
            with open(out, "rb") as f:
                data = backup.load(f.read())
            del data["revision"]                                          # from before backups recorded it
            old = os.path.join(tmp, "old.json.gz")
            with open(old, "wb") as f:
                f.write(gzip.compress(json.dumps(data, default=str).encode()))

            r = self.run_py(dst, "restore", old, stdin="no\n")
            self.assertEqual(r.returncode, 1)
            self.assertIn("Nothing changed.", r.stderr)
            self.assertIn("Stop Runway first", r.stdout)
            self.assertIn(backup.OLD_BACKUP, r.stdout)                    # warned before asking

            r = self.run_py(dst, "restore", old, "--yes")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Restored", r.stdout)
            self.assertNotIn("A copy of what was here", r.stdout)        # an empty database: nothing to keep
            self.assertIn(f"Note: {backup.OLD_BACKUP}", r.stdout)

            r = self.run_py(dst, "restore", out, "--yes")                 # again, over what's there now
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("A copy of what was here before is at " + dst, r.stdout)
            self.assertNotIn("Note:", r.stdout)
            self.assertTrue([f for f in os.listdir(dst) if f.startswith("runway-before-restore-")])
            self.assertNotIn("can't be read", r.stdout)

            # Secrets this key can't read (made under another): named as Settings has them, Plaid's as a count, never
            # a connection's id.
            data["revision"] = backup.head()
            data["tables"]["settings"]["rows"] += [[sk.OPENROUTER_API_KEY, "enc:v1:not-this-key"],
                                                   [sk.CARTA_ACCESS_TOKEN, "enc:v1:x"], [sk.CARTA_REFRESH_TOKEN, "enc:v1:y"]]
            items = data["tables"]["plaid_items"]
            for item_id in ("item-sekrit-1", "item-sekrit-2"):
                items["rows"].append([{"item_id": item_id, "access_token": "enc:v1:not-this-key"}.get(c) for c in items["columns"]])
            locked = os.path.join(tmp, "locked.json.gz")
            with open(locked, "wb") as f:
                f.write(gzip.compress(json.dumps(data, default=str).encode()))
            r = self.run_py(dst, "restore", locked, "--yes")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("These can't be read with this Runway's secret key: OpenRouter API key, Carta sign-in, "
                          "2 Plaid connections.", r.stdout)
            self.assertNotIn("sekrit", r.stdout + r.stderr)
            self.assertNotIn(sk.OPENROUTER_API_KEY, r.stdout)

            data["revision"] = "0999"
            with open(old, "wb") as f:
                f.write(gzip.compress(json.dumps(data, default=str).encode()))
            r = self.run_py(dst, "restore", old, "--yes")
            self.assertEqual(r.returncode, 1)
            self.assertIn("newer version of Runway", r.stderr)
