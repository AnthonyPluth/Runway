import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from runway import verify


class ServerCommandTests(unittest.TestCase):
    def test_the_demo_server_listens_on_loopback_whatever_runway_host_says(self):
        with mock.patch.dict("os.environ", {"RUNWAY_HOST": "0.0.0.0"}):
            command = verify.server_command(8123)
        self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
        self.assertEqual(command[command.index("--port") + 1], "8123")


class CleanEnvTests(unittest.TestCase):
    def test_the_demo_server_never_sees_real_data_settings(self):
        base = {"PATH": "/bin", "DATABASE_URL": "postgresql://real", "RUNWAY_DATA": "/real", "OIDC_ISSUER": "https://idp",
                "SENTRY_DSN": "https://k@sentry", "RUNWAY_SECRET_KEY": "real", "SIMPLEFIN_TOKEN": "t", "RUNWAY_PUBLIC_URL": "https://r"}
        env = verify.clean_env("/tmp/demo", base)
        self.assertEqual(env, {"PATH": "/bin", "RUNWAY_DATA": "/tmp/demo", "RUNWAY_NO_SYNC": "1", "PYTHONUNBUFFERED": "1"})


class TopScreenshotNameTests(unittest.TestCase):
    """verify.mjs saves `<page>-<width>-top.png` beside each full-page file; tools/pr_screenshots.py picks those up by name."""

    def test_the_top_screenshot_name_is_the_one_pr_screenshots_looks_for(self):
        node = shutil.which("node")
        mjs = os.path.join(verify.ROOT, "frontend", "verify", "verify.mjs")
        if not node or not os.path.isdir(os.path.join(verify.ROOT, "frontend", "node_modules", "@playwright")):
            self.skipTest("needs node and the web app's packages (npm ci in frontend/)")
        script = f"import('{mjs}').then((m) => console.log(JSON.stringify(m.screenshotFiles('budget', 'phone'))))"
        done = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"full": "budget-phone.png", "top": "budget-phone-top.png"})
        self.assertTrue(fnmatch.fnmatch("budget-phone-top.png", "*-top.png"))


class RunPyFileArgumentTests(unittest.TestCase):
    """run.py's file argument takes several words now (verify's pages); backup and restore still take one file."""

    def run_py(self, tmp, *args):
        return subprocess.run([sys.executable, os.path.join(verify.ROOT, "run.py"), *args], cwd=tmp,
                              env=verify.clean_env(tmp), capture_output=True, text=True, timeout=120)

    def test_backup_still_saves_to_the_one_file_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = self.run_py(tmp, "backup", "out.json.gz")
            self.assertEqual(done.returncode, 0, done.stderr)
            with open(os.path.join(tmp, "out.json.gz"), "rb") as f:
                self.assertEqual(f.read(2), b"\x1f\x8b")

    def test_backup_and_restore_refuse_a_second_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            for command in ("backup", "restore"):
                done = self.run_py(tmp, command, "a.json.gz", "b.json.gz")
                self.assertEqual(done.returncode, 2, command)
                self.assertIn("only one file, please", done.stderr)
            self.assertEqual(os.listdir(tmp), [])

    def test_restore_without_a_file_still_asks_which(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = self.run_py(tmp, "restore")
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("Which backup file?", done.stderr)


class RunTests(unittest.TestCase):
    def test_a_server_that_dies_is_reported_not_waited_for(self):
        server = mock.Mock(spec=subprocess.Popen)
        server.poll.return_value = 3
        server.returncode = 3
        with self.assertRaisesRegex(RuntimeError, "exited with code 3"):
            verify.wait_ready("http://127.0.0.1:1", server, timeout=5)

    def test_the_readiness_check_skips_the_system_proxy(self):
        server = mock.Mock(spec=subprocess.Popen)
        server.poll.return_value = None
        with mock.patch.dict("os.environ", {"HTTP_PROXY": "http://proxy:3128", "http_proxy": "http://proxy:3128"}), \
                mock.patch("runway.tls.urlopen") as opened:
            verify.wait_ready("http://127.0.0.1:8123", server, timeout=5)
        (handler,) = opened.call_args.kwargs["handlers"]
        self.assertEqual(handler.proxies, {})

    def test_a_server_that_never_answers_times_out_with_the_address(self):
        server = mock.Mock(spec=subprocess.Popen)
        server.poll.return_value = None
        with self.assertRaisesRegex(RuntimeError, r"http://127\.0\.0\.1:1 after 0 seconds"):
            verify.wait_ready("http://127.0.0.1:1", server, timeout=0)

    def test_missing_packages_say_what_to_run(self):
        with mock.patch("os.path.isdir", return_value=False), mock.patch("builtins.print") as out:
            self.assertEqual(verify.run([]), 2)
        self.assertIn("npm ci", out.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
