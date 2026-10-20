import contextlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
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
                "RUNWAY_SECRET_KEY": "real", "SIMPLEFIN_TOKEN": "t", "RUNWAY_PUBLIC_URL": "https://r"}
        env = verify.clean_env("/tmp/demo", base)
        self.assertEqual(env, {"PATH": "/bin", "RUNWAY_DATA": "/tmp/demo", "RUNWAY_NO_SYNC": "1", "PYTHONUNBUFFERED": "1"})


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

    def test_the_signed_in_server_is_this_demo_s_own_sign_in_on_loopback(self):
        env = verify.signed_in_env(verify.clean_env("/tmp/demo", {"PATH": "/bin"}), "http://localhost:8124")
        self.assertEqual({k: v for k, v in env.items() if k.startswith(("OIDC_", "RUNWAY_PUBLIC"))},
                         {"OIDC_ISSUER": "http://127.0.0.1:9/verify-sign-in", "OIDC_CLIENT_ID": "runway-verify",
                          "RUNWAY_PUBLIC_URL": "http://localhost:8124", "OIDC_ALLOWED_EMAILS": verify.DEMO_EMAIL})
        self.assertEqual(env["RUNWAY_DATA"], "/tmp/demo")
        with tempfile.TemporaryDirectory() as tmp:   # run.py demo --signed-in: a session for the token it's handed, hashed
            done = subprocess.run([sys.executable, os.path.join(verify.ROOT, "run.py"), "demo", "--signed-in"], cwd=tmp,
                                  env={**verify.clean_env(tmp), verify.SESSION_ENV: "tok"}, capture_output=True, text=True, timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr)
            from sqlalchemy import create_engine, select

            from runway import oidc
            from runway.storage.models import AuthSession
            # The demo's own SQLite file (clean_env drops DATABASE_URL), whatever database these tests run on.
            engine = create_engine(f"sqlite:///{os.path.join(tmp, 'runway.db')}")
            try:
                with engine.connect() as conn:
                    rows = [tuple(r) for r in conn.execute(select(AuthSession.token_hash, AuthSession.email))]
            finally:
                engine.dispose()
            self.assertEqual(rows, [(oidc.session_key("tok"), verify.DEMO_EMAIL)])

    def test_missing_packages_say_what_to_run(self):
        with mock.patch("os.path.isdir", return_value=False), mock.patch("builtins.print") as out:
            self.assertEqual(verify.run([]), 2)
        self.assertIn("npm ci", out.call_args.args[0])

    def test_without_libfaketime_verify_exits_non_zero_with_the_install_line(self):
        with mock.patch("os.path.isdir", return_value=True), mock.patch("os.path.isfile", return_value=True), \
                mock.patch("shutil.which", return_value="/usr/bin/node"), \
                mock.patch.object(verify, "find_libfaketime", return_value=None), \
                mock.patch("tempfile.mkdtemp") as made, mock.patch("builtins.print") as out:
            self.assertEqual(verify.run([]), 2)
        message = out.call_args.args[0]
        self.assertIn("libfaketime", message)
        self.assertIn("sudo apt-get install -y libfaketime", message)
        self.assertIn("Linux", message)
        made.assert_not_called()   # nothing was started


class FrozenClockTests(unittest.TestCase):
    def test_libfaketime_is_found_on_linux_only(self):
        found = verify.find_libfaketime(exists=lambda p: p == "/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1", platform="linux")
        self.assertEqual(found, "/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1")
        self.assertIsNone(verify.find_libfaketime(exists=lambda p: True, platform="darwin"))
        self.assertIsNone(verify.find_libfaketime(exists=lambda p: False, platform="linux"))

    def test_the_server_and_seed_clock_is_stopped_at_the_frozen_day(self):
        env = verify.frozen_env({"PATH": "/bin"}, "/lib/libfaketime.so.1")
        self.assertEqual(env["LD_PRELOAD"], "/lib/libfaketime.so.1")
        self.assertEqual(env["FAKETIME"], f"{verify.FROZEN_DAY} 12:00:00")
        self.assertEqual(env["PATH"], "/bin")

    def test_the_demo_data_is_built_on_the_frozen_day_whatever_day_it_runs(self):
        lib = verify.find_libfaketime()
        if not lib:
            self.skipTest("libfaketime isn't installed")
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run([sys.executable, "-c", "import datetime; print(datetime.date.today())"],
                                  env=verify.frozen_env(verify.clean_env(tmp), lib), capture_output=True, text=True, timeout=60)
        self.assertEqual(done.stdout.strip(), verify.FROZEN_DAY)

    def test_the_browser_is_told_the_same_day(self):
        with open(os.path.join(verify.ROOT, "frontend", "verify", "verify.mjs")) as f:
            self.assertIn("--day", f.read())
        with tempfile.TemporaryDirectory() as tmp, mock.patch("os.path.isdir", return_value=True), \
                mock.patch("os.path.isfile", return_value=True), mock.patch("shutil.which", return_value="/usr/bin/node"), \
                mock.patch.object(verify, "find_libfaketime", return_value="/lib/libfaketime.so.1"), \
                mock.patch.object(verify, "LOGS", tmp), mock.patch.object(verify, "machine_lock", lambda **_: contextlib.nullcontext()), \
                mock.patch.object(verify, "wait_for_quiet", return_value=0.0), mock.patch("builtins.print"), \
                mock.patch("subprocess.run", return_value=mock.Mock(returncode=1, stdout="", stderr="")) as ran:
            verify.run([])
        self.assertEqual(ran.call_args.kwargs["env"]["FAKETIME"], f"{verify.FROZEN_DAY} 12:00:00")   # the seed ran frozen

    def test_the_seed_and_servers_get_the_timed_waits_libfaketime_breaks_back(self):
        env = verify.frozen_env({"PATH": "/bin", "PYTHONPATH": "/x"}, "/lib/libfaketime.so.1")
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep), [verify.SITE_DIR, "/x"])
        self.assertEqual(env[verify.FROZEN_CLOCK_ENV], "1")
        self.assertTrue(os.path.isfile(os.path.join(verify.SITE_DIR, "sitecustomize.py")))

    def test_under_the_frozen_clock_a_timed_wait_waits_and_sleep_works(self):
        """libfaketime's stopped clock made Event.wait(0.5) return at once (the server's hang-up thread spun a CPU, each
        server, all run) and time.sleep fail with EINVAL; verify_site/sitecustomize.py puts both back."""
        lib = verify.find_libfaketime()
        if not lib:
            self.skipTest("libfaketime isn't installed")
        code = ("import threading, time\n"
                "t = time.monotonic(); n = 0\n"
                "while time.monotonic() - t < 0.6: threading.Event().wait(0.2); n += 1\n"
                "s = time.monotonic(); time.sleep(0.2)\n"
                "print(n, round(time.monotonic() - s, 1))")
        with tempfile.TemporaryDirectory() as tmp:
            env = verify.frozen_env(verify.clean_env(tmp), lib)
            done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            waits, slept = done.stdout.split()
            self.assertLessEqual(int(waits), 4)   # about 3 waits of 0.2 s in 0.6 s, not thousands
            self.assertEqual(slept, "0.2")
            frozen = subprocess.run([sys.executable, "-c", "import datetime; print(datetime.date.today())"], env=env,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(frozen.stdout.strip(), verify.FROZEN_DAY)   # and the clock is still stopped


class FrozenSiteTests(unittest.TestCase):
    """verify_site/sitecustomize.py's waits themselves, on the real clock (no libfaketime needed)."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("verify_sitecustomize", os.path.join(verify.SITE_DIR, "sitecustomize.py"))
        assert spec and spec.loader
        self.site = importlib.util.module_from_spec(spec)
        with mock.patch.dict("os.environ", {verify.FROZEN_CLOCK_ENV: ""}):   # load it without installing it here
            spec.loader.exec_module(self.site)

    def test_sleep_waits_and_refuses_a_negative_length(self):
        start = time.monotonic()
        self.site._sleep(0.15)
        self.assertGreaterEqual(time.monotonic() - start, 0.15)
        with self.assertRaises(ValueError):
            self.site._sleep(-1)

    def test_a_timed_acquire_waits_its_timeout_and_returns_when_released(self):
        lock = self.site._PollingLock()
        self.assertTrue(lock.acquire())
        start = time.monotonic()
        self.assertFalse(lock.acquire(True, 0.15))   # held: waits, then gives up
        self.assertGreaterEqual(time.monotonic() - start, 0.15)
        self.assertFalse(lock.acquire(False))
        timer = threading.Timer(0.05, lock.release)
        timer.start()
        self.assertTrue(lock.acquire(True, 5))   # released by another thread: taken well before the timeout
        self.assertTrue(lock.locked())
        lock.release()
        with lock:
            self.assertTrue(lock.locked())
        self.assertFalse(lock.locked())

    def test_installed_it_backs_event_wait_and_time_sleep(self):
        with mock.patch.object(time, "sleep", time.sleep), mock.patch.object(threading, "_allocate_lock", threading._allocate_lock):
            self.site.install()
            self.assertIs(time.sleep, self.site._sleep)
            start = time.monotonic()
            self.assertFalse(threading.Event().wait(0.1))
            self.assertGreaterEqual(time.monotonic() - start, 0.1)
            e = threading.Event()
            threading.Timer(0.05, e.set).start()
            self.assertTrue(e.wait(5))


class MachineLockTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "verify.lock")

    def hold(self):
        """Another run holding the lock: a process of its own, in machine_lock until killed."""
        holder = subprocess.Popen([sys.executable, "-c", "import time; from runway import verify\n"
                                   f"with verify.machine_lock({self.path!r}): print('held', flush=True); time.sleep(60)"],
                                  cwd=verify.ROOT, stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (holder.kill(), holder.wait(10)))
        assert holder.stdout is not None
        self.assertEqual(holder.stdout.readline().strip(), "held")
        return holder

    def test_a_second_run_waits_saying_who_has_it_and_gives_up_after_the_most_it_waits(self):
        said, clock = [], [0.0]

        def sleep(s):
            clock[0] += s
        holder = self.hold()
        with self.assertRaisesRegex(verify.MachineBusy, "gave up waiting"), \
                verify.machine_lock(self.path, max_wait=120, say=said.append, now=lambda: clock[0], sleep=sleep, poll=10):
            self.fail("got a lock another run holds")
        self.assertGreaterEqual(clock[0], 120)
        self.assertIn("another verify is running on this machine", said[0])
        self.assertIn(f"pid {holder.pid} in {verify.ROOT}", said[0])   # who holds it
        self.assertEqual(len(said), 2)   # once at the start, once a minute: not every poll

    def test_it_is_free_again_once_the_holder_is_done(self):
        with verify.machine_lock(self.path):
            pass
        with verify.machine_lock(self.path, max_wait=0):
            pass

    def test_a_holder_that_is_killed_leaves_it_free(self):
        """flock goes with the process: a run killed part way (a timeout, ^C, kill -9) never leaves the lock held."""
        holder = subprocess.Popen([sys.executable, "-c", "import fcntl, os, sys, time\n"
                                   f"fd = os.open({self.path!r}, os.O_RDWR | os.O_CREAT)\n"
                                   "fcntl.flock(fd, fcntl.LOCK_EX); print('held', flush=True); time.sleep(60)"],
                                  stdout=subprocess.PIPE, text=True)
        try:
            assert holder.stdout is not None
            self.assertEqual(holder.stdout.readline().strip(), "held")
            with self.assertRaises(verify.MachineBusy), verify.machine_lock(self.path, max_wait=0, say=lambda _: None):
                pass
            holder.kill()
            holder.wait(10)
            with verify.machine_lock(self.path, max_wait=5, poll=0.05):
                pass
        finally:
            holder.kill()
            holder.wait(10)

    def test_a_run_started_under_flock_on_the_same_file_does_not_wait_for_itself(self):
        """`flock /tmp/runway-verify.lock make verify` holds the lock for the run it starts: that run must not wait on it."""
        if not shutil.which("flock"):
            self.skipTest("no flock command")
        code = ("import sys; from runway import verify\n"
                f"with verify.machine_lock({self.path!r}, max_wait=0, say=print): print('ran')")
        done = subprocess.run(["flock", self.path, sys.executable, "-c", code], cwd=verify.ROOT, capture_output=True,
                              text=True, timeout=60)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "ran"), done.stderr)
        with verify.machine_lock(self.path, max_wait=0):   # and it's free again after
            pass

    def test_the_holders_message_names_a_holder_that_wrote_nothing(self):
        holder = subprocess.Popen(["flock", self.path, "sleep", "30"]) if shutil.which("flock") else None
        if holder is None:
            self.skipTest("no flock command")
        try:
            said: list[str] = []
            for _ in range(100):
                if not os.path.exists(self.path):   # flock hasn't made it yet
                    time.sleep(0.05)
                    continue
                fd = os.open(self.path, os.O_RDONLY)
                try:
                    if verify.lock_holders(fd):
                        break
                finally:
                    os.close(fd)
                time.sleep(0.05)
            with self.assertRaises(verify.MachineBusy) as e, verify.machine_lock(self.path, max_wait=0, say=said.append):
                pass
            self.assertIn(f"pid {holder.pid}: flock", str(e.exception))
        finally:
            holder.kill()
            holder.wait(10)

    def test_a_symlink_at_the_lock_path_is_refused_and_its_target_left_alone(self):
        """In a shared /tmp another user could put a symlink at the lock's name: the holder writes the file it opens."""
        target = os.path.join(os.path.dirname(self.path), "someone-elses-file")
        with open(target, "w") as f:
            f.write("keep me")
        os.symlink(target, self.path)
        with self.assertRaisesRegex(verify.LockUnusable, "must be a regular file, not a symlink"), verify.machine_lock(self.path):
            self.fail("took a lock through a symlink")
        with open(target) as f:
            self.assertEqual(f.read(), "keep me")
        os.remove(target)   # a dangling symlink: not followed to make the file either
        with self.assertRaises(verify.LockUnusable), verify.machine_lock(self.path):
            self.fail("took a lock through a dangling symlink")
        self.assertFalse(os.path.exists(target))

    def test_a_directory_or_a_fifo_at_the_lock_path_is_refused(self):
        os.mkdir(self.path)
        with self.assertRaises(verify.LockUnusable), verify.machine_lock(self.path):
            self.fail("took a lock on a directory")
        os.rmdir(self.path)
        os.mkfifo(self.path)
        with self.assertRaisesRegex(verify.LockUnusable, "isn't a regular file"), verify.machine_lock(self.path):
            self.fail("took a lock on a FIFO")

    def test_the_lock_file_is_made_as_a_regular_file_every_user_can_take(self):
        with verify.machine_lock(self.path):
            pass
        st = os.lstat(self.path)
        self.assertTrue(stat.S_ISREG(st.st_mode))
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o666)

    def test_a_lock_path_it_cannot_use_exits_2_and_says_why(self):
        def unusable(**_):
            raise verify.LockUnusable("verify: can't use the machine lock /tmp/x (Too many levels of symbolic links)")
        with tempfile.TemporaryDirectory() as tmp, mock.patch("os.path.isdir", return_value=True), \
                mock.patch("os.path.isfile", return_value=True), mock.patch("shutil.which", return_value="/usr/bin/node"), \
                mock.patch.object(verify, "find_libfaketime", return_value="/lib/libfaketime.so.1"), \
                mock.patch.object(verify, "LOGS", tmp), mock.patch.object(verify, "machine_lock", side_effect=unusable), \
                mock.patch("builtins.print") as out:
            self.assertEqual(verify.run([]), 2)
        self.assertTrue(any("can't use the machine lock" in str(c.args[0]) for c in out.call_args_list))

    def test_a_run_that_waited_too_long_exits_3_and_says_why(self):
        def busy(**_):
            raise verify.MachineBusy("verify: another verify (pid 1) has held the machine for 60 minutes; gave up waiting")
        with tempfile.TemporaryDirectory() as tmp, mock.patch("os.path.isdir", return_value=True), \
                mock.patch("os.path.isfile", return_value=True), mock.patch("shutil.which", return_value="/usr/bin/node"), \
                mock.patch.object(verify, "find_libfaketime", return_value="/lib/libfaketime.so.1"), \
                mock.patch.object(verify, "LOGS", tmp), mock.patch.object(verify, "machine_lock", side_effect=busy), \
                mock.patch("builtins.print") as out:
            self.assertEqual(verify.run([]), 3)
        self.assertTrue(any("gave up waiting" in str(c.args[0]) for c in out.call_args_list))


class LoadTests(unittest.TestCase):
    def test_load_per_cpu(self):
        self.assertEqual(verify.load_per_cpu(lambda: (12.0, 0.0, 0.0), lambda: 4), 3.0)
        self.assertEqual(verify.load_per_cpu(lambda: (1.0, 0.0, 0.0), lambda: 0), 1.0)

    def test_a_busy_machine_is_waited_for_until_it_settles(self):
        loads, said, clock = iter([9.0, 6.0, 1.2]), [], [0.0]

        def sleep(s):
            clock[0] += s
        ratio = verify.wait_for_quiet(600, load=lambda: next(loads), say=said.append, now=lambda: clock[0], sleep=sleep)
        self.assertEqual(ratio, 1.2)
        self.assertEqual(clock[0], 10)   # two polls of 5 s
        self.assertIn("the machine is busy (load 9.0 per CPU", said[0])

    def test_the_wait_is_bounded_and_the_run_goes_ahead_with_longer_waits(self):
        said, clock = [], [0.0]

        def sleep(s):
            clock[0] += s
        ratio = verify.wait_for_quiet(60, load=lambda: 12.0, say=said.append, now=lambda: clock[0], sleep=sleep)
        self.assertEqual((ratio, clock[0]), (12.0, 60))
        self.assertIn("going ahead with longer waits", said[-1])
        self.assertEqual(verify.wait_for_quiet(60, load=lambda: 0.5, say=self.fail), 0.5)   # a quiet machine: no wait, nothing said

    def test_waits_stretch_with_the_load_and_the_attempt_within_bounds(self):
        self.assertEqual(verify.time_scale(0.2), 1.0)   # an idle machine: the waits as written
        self.assertEqual(verify.time_scale(3.0), 3.0)
        self.assertEqual(verify.time_scale(50.0), verify.MAX_SCALE)
        self.assertEqual(verify.time_scale(3.0, attempt=2), 6.0)   # a retry is more patient


class RetryTests(unittest.TestCase):
    """retrying: a page or flow that fails is run again (alone, on fresh data) before the run fails."""

    def rounds(self, script):
        """A run_round that answers from `script`: for each attempt, {id: ok}; records what each round was asked for."""
        asked = []

        def run_round(attempt, only):
            asked.append(only)
            got = script[attempt - 1]
            if got is None:
                return None
            return {"out": f"/round-{attempt}", "items": [{"id": i, "ok": ok, "files": [f"{i}.png"] if ok else [],
                                                            "problems": [] if ok else [f"{i} timed out"]} for i, ok in got.items()]}
        return run_round, asked

    def test_a_flaky_item_passes_on_a_retry_and_is_reported_as_flaky(self):
        run_round, asked = self.rounds([{"page:budget": True, "flow:x": False}, {"flow:x": True}])
        results, flaky, planned = verify.retrying(run_round, lambda _: None)
        self.assertEqual(asked, [None, ["flow:x"]])   # the retry runs only what failed
        self.assertEqual(planned, ["page:budget", "flow:x"])
        self.assertTrue(all(item["ok"] for _, item in results.values()))
        self.assertEqual(results["flow:x"][0], "/round-2")   # its images come from the round that passed
        self.assertEqual(flaky, {"flow:x": ["flow:x timed out"]})

    def test_an_item_that_keeps_failing_fails_after_every_attempt(self):
        run_round, asked = self.rounds([{"page:budget": True, "flow:x": False}] + [{"flow:x": False}] * (verify.ATTEMPTS - 1))
        said = []
        results, flaky, _ = verify.retrying(run_round, said.append)
        self.assertEqual(len(asked), verify.ATTEMPTS)
        self.assertFalse(results["flow:x"][1]["ok"])
        self.assertTrue(results["page:budget"][1]["ok"])
        self.assertEqual(flaky, {})
        self.assertEqual(len(said), verify.ATTEMPTS - 1)
        self.assertIn("running it again on a fresh copy of the demo data", said[0])

    def test_a_round_that_died_before_its_report_is_run_again_whole(self):
        crashed = verify._crashed(None, "the browser run ended with code 139 before it wrote its report")
        rounds = iter([crashed, {"out": "/r2", "items": [{"id": "page:budget", "ok": True, "files": [], "problems": []}]}])
        asked = []

        def run_round(attempt, only):
            asked.append(only)
            return next(rounds)
        results, _, planned = verify.retrying(run_round, lambda _: None)
        self.assertEqual(asked, [None, None])
        self.assertEqual((planned, list(results)), (["page:budget"], ["page:budget"]))

    def test_a_run_that_cannot_start_is_not_retried(self):
        run_round, asked = self.rounds([None])
        self.assertIsNone(verify.retrying(run_round, lambda _: None))
        self.assertEqual(len(asked), 1)


class PublishTests(unittest.TestCase):
    """All or nothing: artifacts/verify/ holds a passing run's images, or, after a failure, none at its top."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.out = os.path.join(tmp.name, "artifacts", "verify")
        self.round = os.path.join(tmp.name, "round")
        os.makedirs(self.round)
        for f in ("budget-phone.png", "setup-phone.png", "flow-x-FAILED-phone.png"):
            with open(os.path.join(self.round, f), "wb") as fh:
                fh.write(f.encode())
        os.makedirs(self.out)
        with open(os.path.join(self.out, "old-phone.png"), "w") as fh:
            fh.write("from the last run")

    def results(self, setup_ok):
        return {"page:budget": (self.round, {"id": "page:budget", "ok": True, "files": ["budget-phone.png"]}),
                "page:setup": (self.round, {"id": "page:setup", "ok": setup_ok, "files": ["setup-phone.png"],
                                            "failed_files": [] if setup_ok else ["flow-x-FAILED-phone.png"]})}

    def report(self):
        with open(os.path.join(self.out, "report.json")) as f:
            return json.load(f)

    def test_a_passing_run_s_images_replace_the_last_run_s(self):
        verify.publish(self.out, self.results(True), {"ok": True}, "run1")
        self.assertEqual(sorted(os.listdir(self.out)), ["budget-phone.png", "report.json", "setup-phone.png"])
        self.assertEqual(self.report()["files"], ["budget-phone.png", "setup-phone.png"])
        self.assertEqual([f for f in os.listdir(os.path.dirname(self.out)) if f != "verify"], [])   # no staging left

    def test_after_a_failure_no_image_is_left_where_a_result_would_be(self):
        verify.publish(self.out, self.results(False), {"ok": False, "failed": {"page:setup": ["timed out"]}}, "run1")
        self.assertEqual(sorted(os.listdir(self.out)), ["flow-x-FAILED-phone.png", "report.json", "unverified"])
        self.assertEqual(sorted(os.listdir(os.path.join(self.out, "unverified"))), ["budget-phone.png", "setup-phone.png"])
        self.assertEqual((self.report()["ok"], self.report()["files"]), (False, []))


class RunLogTests(unittest.TestCase):
    def test_each_run_logs_to_a_file_of_its_own_and_old_ones_are_cleared(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("builtins.print"):
            logs = [verify.RunLog(tmp, keep=3) for _ in range(5)]
            for i, log in enumerate(logs):
                log.say(f"line {i}")
                log.close()
            self.assertEqual(len({log.path for log in logs}), 5)   # never one name two runs share
            self.assertEqual(sorted(os.listdir(tmp)), sorted(os.path.basename(log.path) for log in logs[-3:]))
            with open(logs[-1].path) as f:
                self.assertEqual(f.read(), "line 4\n")

    def test_the_summary_names_each_failure_and_what_passed_only_on_a_retry(self):
        lines = verify.summary({"flow:budget-edit": ["budget-edit @ phone: never settled"]}, {"page:setup": ["timed out"]}, 9)
        self.assertIn("flaky: page:setup", lines[0])
        self.assertIn("FAIL: flow:budget-edit: budget-edit @ phone: never settled", lines[1])
        self.assertIn("FAILED: 1 of 9 pages and flows (budget-edit)", lines[-1])
        self.assertIn("screenshots/ is untouched", lines[-1])
        self.assertEqual(verify.summary({}, {}, 9), ["verify: OK, 9 pages and flows"])

    def test_differing_names_the_images_that_changed(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            for folder, data in ((a, b"x"), (b, b"x")):
                with open(os.path.join(folder, "same.png"), "wb") as f:
                    f.write(data)
            with open(os.path.join(a, "moved.png"), "wb") as f:
                f.write(b"1")
            with open(os.path.join(b, "moved.png"), "wb") as f:
                f.write(b"2")
            self.assertEqual(verify.differing(a, b, ["same.png", "moved.png"]), ["moved.png"])


class MakeTargetTests(unittest.TestCase):
    def make(self, *args):
        return subprocess.run(["make", "-n", *args], cwd=verify.ROOT, capture_output=True, text=True, timeout=60).stdout

    def test_pages_limits_make_screenshots_too_not_only_make_verify(self):
        for target in ("verify", "screenshots", "verify-twice"):
            line = next(ln for ln in self.make(target, "PAGES=networth budget-edit").splitlines() if "run.py verify" in ln)
            self.assertTrue(line.rstrip().endswith("networth budget-edit"), line)
        self.assertIn("sync_screenshots.py", self.make("screenshots"))


if __name__ == "__main__":
    unittest.main()
