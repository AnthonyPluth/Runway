"""`python run.py verify [page or flow…]`: run the real app on made-up data and capture proof that it works.

Makes a temporary database, fills it with demo.seed (as `run.py demo` does), starts the server on a free port, and runs
frontend/verify/verify.mjs (Playwright) against it. Pages are visited, and screenshotted, at phone (an iPhone 17 Pro Max,
440×956) and desktop widths. Never touches your own data: the server gets its own folder and SQLite database, and none of
your Postgres, sign-in, bank or error-report settings.

Built to give the same answer on a busy machine as on an idle one:
- One run at a time per machine (machine_lock): a second waits for the first rather than both racing for the CPU.
- It waits (a while) for the machine's load to drop before it starts, and scales its waits with the load it finds.
- A page or flow that fails is run again, on a fresh copy of the demo data, up to ATTEMPTS times in all.
- All or nothing: artifacts/verify/ gets the images only when every page and flow passed (report.json's "ok"); after a
  failure it holds the report, the failed steps' images and, under unverified/, what was captured, never as a result.
- Its output also goes to a log file of its own (artifacts/verify-logs/), named for the run.
Returns the exit code: 0 when every page and flow passed, 1 when one failed, 2 when it couldn't start, 3 when another
run held the machine for too long.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any, TextIO

from . import tls

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "artifacts", "verify")
LOGS = os.path.join(ROOT, "artifacts", "verify-logs")
KEEP_LOGS = 20


# The frozen clock. Demo data is built relative to today (transactions, due dates, the budget month, chart axes), so an
# image would change every day it's taken. The server and the demo seed run under libfaketime as if it were this moment
# (stopped there: the wall clock does not advance, the monotonic clocks do, so timeouts and waits still work), and
# verify.mjs sets the browser's clock to the same day with Playwright's page.clock (the run passes it FROZEN_DAY as --day).
# libfaketime also breaks Python's timed lock waits and time.sleep: verify_site/sitecustomize.py rebuilds them for these
# processes. Linux only: macOS has no LD_PRELOAD.
FROZEN_DAY = "2026-06-17"
FROZEN_TIME = f"{FROZEN_DAY} 12:00:00"
LIBFAKETIME_INSTALL = "sudo apt-get install -y libfaketime"
_LIBFAKETIME_DIRS = ("/usr/lib/x86_64-linux-gnu/faketime", "/usr/lib/aarch64-linux-gnu/faketime", "/usr/lib/faketime",
                     "/usr/local/lib/faketime", "/usr/lib64/faketime")
SITE_DIR = os.path.join(ROOT, "runway", "verify_site")
FROZEN_CLOCK_ENV = "RUNWAY_VERIFY_FROZEN_CLOCK"   # verify_site/sitecustomize.py's FROZEN_ENV

# The machine-wide lock: one file every checkout on the machine shares (worktrees and clones alike), so concurrent runs
# queue. flock is let go of when the process holding it ends, however it ends, so a killed run never leaves it held.
LOCK_FILE = os.environ.get("RUNWAY_VERIFY_LOCK", "/tmp/runway-verify.lock")
LOCK_WAIT = float(os.environ.get("RUNWAY_VERIFY_LOCK_WAIT", 60 * 60))   # seconds; then it gives up (exit 3)
# The machine's load: above BUSY runnable tasks per CPU it waits, up to LOAD_WAIT seconds, before it starts.
BUSY = 1.5
LOAD_WAIT = float(os.environ.get("RUNWAY_VERIFY_LOAD_WAIT", 10 * 60))
MAX_SCALE = 8.0   # the most the load stretches a wait by (times the attempt)
ATTEMPTS = 3      # a page or flow that fails is run this many times in all before the run fails


def find_libfaketime(exists=os.path.isfile, platform: str = sys.platform) -> str | None:
    """The path of libfaketime.so.1, or None when it isn't installed (or this isn't Linux, where LD_PRELOAD works)."""
    if not platform.startswith("linux"):
        return None
    for d in _LIBFAKETIME_DIRS:
        if exists(p := os.path.join(d, "libfaketime.so.1")):
            return p
    return None


def libfaketime_missing_message() -> str:
    return ("verify: libfaketime isn't installed, and make verify needs it to freeze the clock so its images only change when "
            f"the UI or its data does. On Debian/Ubuntu: {LIBFAKETIME_INSTALL}. verify runs on Linux (the dev container and CI); "
            "macOS doesn't support LD_PRELOAD.")


def frozen_env(env: dict[str, str], lib: str) -> dict[str, str]:
    """env with the process's wall clock stopped at FROZEN_TIME (a time of day the server prints, like "as of 12:00:18 PM",
    would otherwise change every run). Monotonic clocks keep running, so timeouts and waits still work; file times stay real.
    verify_site/ comes first on PYTHONPATH, for the timed waits libfaketime breaks."""
    path = os.pathsep.join(p for p in (SITE_DIR, env.get("PYTHONPATH", "")) if p)
    return {**env, "LD_PRELOAD": lib, "FAKETIME": FROZEN_TIME, "NO_FAKE_STAT": "1", "DONT_FAKE_MONOTONIC": "1",
            "FAKETIME_DONT_FAKE_MONOTONIC": "1", "PYTHONPATH": path, FROZEN_CLOCK_ENV: "1"}


def clean_env(data: str, base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for the demo server: its own folder and SQLite database, nothing from the caller's setup that
    could point it at real data (Postgres, sign-in, bank providers)."""
    env = {k: v for k, v in (os.environ if base is None else base).items()
           if not k.startswith(("OIDC_", "SIMPLEFIN", "PLAID", "RUNWAY_SECRET_KEY"))
           and k not in ("DATABASE_URL", "RUNWAY_PUBLIC_URL", "RUNWAY_ALLOW_NO_AUTH")}
    env.update(RUNWAY_DATA=data, RUNWAY_NO_SYNC="1", PYTHONUNBUFFERED="1")
    return env


# The signed-in demo server: the same demo data with sign-in on, for the flows that need a sign-in (the app lock). Its
# issuer is never contacted: nobody signs in, the browser is handed the session seed_session made (its token passed in
# SESSION_ENV). Only ever this loopback server and its throwaway database.
SESSION_ENV = "RUNWAY_VERIFY_SESSION"
DEMO_EMAIL = "demo@example.com"


def signed_in_env(env: dict[str, str], public_url: str) -> dict[str, str]:
    return {**env, "OIDC_ISSUER": "http://127.0.0.1:9/verify-sign-in", "OIDC_CLIENT_ID": "runway-verify",
            "RUNWAY_PUBLIC_URL": public_url, "OIDC_ALLOWED_EMAILS": DEMO_EMAIL}


def seed_session(conn, token: str) -> None:
    """`run.py demo --signed-in`: a signed-in browser for the signed-in demo server, with the token in SESSION_ENV."""
    from sqlalchemy import insert

    from . import oidc
    from .storage.models import AuthSession
    now = time.time()
    conn.execute(insert(AuthSession).values(token_hash=oidc.session_key(token), sub="demo", email=DEMO_EMAIL, name="Demo",
                                            created=now, expires=now + 86400))
    oidc.remember_user(conn, "demo", DEMO_EMAIL, "Demo", "Demo", now)


def server_command(port: int) -> list[str]:
    """run.py on the loopback address only: without --host it takes RUNWAY_HOST, and a demo server shouldn't listen on
    every address (nor refuse to start for lack of the sign-in that clean_env removes)."""
    return [sys.executable, os.path.join(ROOT, "run.py"), "--host", "127.0.0.1", "--port", str(port), "--no-sync"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(url: str, server: subprocess.Popen, timeout: float = 60) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if server.poll() is not None:
            raise RuntimeError(f"the server exited with code {server.returncode} before it was ready")
        try:
            # No proxy: the server is on this machine, and a system HTTP proxy that doesn't exempt 127.0.0.1 would never reach it.
            with tls.urlopen(url, 2, allow_http=True, handlers=(urllib.request.ProxyHandler({}),)):
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    raise RuntimeError(f"the server wasn't answering at {url} after {timeout:.0f} seconds")


# --- Sharing the machine -------------------------------------------------------------------------------------------

def _span(seconds: float) -> str:
    return f"{seconds / 60:.0f} minutes" if seconds >= 120 else f"{seconds:.0f} seconds"


def lock_holders(fd: int, locks: str = "/proc/locks") -> set[int]:
    """The processes holding a lock on fd's file, from /proc/locks (empty when it can't be read)."""
    st = os.fstat(fd)
    me = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}"
    out = set()
    try:
        with open(locks) as f:
            for line in f:
                parts = line.split()
                if "->" in parts:   # a waiter, listed under the holder
                    continue
                if len(parts) > 5 and parts[5] == me and parts[4].isdigit():
                    out.add(int(parts[4]))
    except OSError:
        pass
    return out


def ancestors(pid: int | None = None) -> set[int]:
    """pid (this process by default) and the processes that started it, up to init."""
    out, pid = set(), os.getpid() if pid is None else pid
    while pid > 1 and pid not in out:
        out.add(pid)
        try:
            with open(f"/proc/{pid}/stat") as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return out


def _cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode(errors="replace").strip()[:120] or "?"
    except OSError:
        return "?"


class MachineBusy(Exception):
    """Another run held the machine-wide lock for longer than this one would wait."""


@contextlib.contextmanager
def machine_lock(path: str | None = None, max_wait: float | None = None, *, say: Callable[[str], None] = print,
                 poll: float = 1.0, now: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> Iterator[None]:
    """Holds the machine-wide verify lock for the block: waits (saying so, and who holds it) while another run has it, up
    to max_wait seconds, then raises MachineBusy. The holder writes who it is into the file for the next one to read."""
    path = path or LOCK_FILE
    max_wait = LOCK_WAIT if max_wait is None else max_wait
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
        with contextlib.suppress(OSError):
            os.fchmod(fd, 0o666)   # so another user's run on the machine can take it too
    except PermissionError:   # another user's file, made before that: flock works on a file opened to read
        fd = os.open(path, os.O_RDONLY)
    try:
        start, told = now(), None
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                holders = lock_holders(fd)
                if holders & ancestors():   # `flock <file> make verify`: the run it started is the one holding it
                    yield
                    return
                waited = now() - start
                holder = os.pread(fd, 300, 0).decode(errors="replace").strip() or ", ".join(
                    f"pid {p}: {_cmdline(p)}" for p in sorted(holders)) or "another process"
                if waited >= max_wait:
                    raise MachineBusy(f"verify: another verify ({holder}) has held the machine for {_span(waited)}; "
                                      f"gave up waiting (RUNWAY_VERIFY_LOCK_WAIT sets how long, in seconds)") from None
                if told is None or now() - told >= 60:
                    say(f"verify: another verify is running on this machine ({holder}), waiting... "
                        f"({_span(waited)} so far, {_span(max_wait)} at most)")
                    told = now()
                sleep(poll)
        with contextlib.suppress(OSError):
            os.ftruncate(fd, 0)
            os.pwrite(fd, f"pid {os.getpid()} in {ROOT}, since {time.strftime('%H:%M:%S')}".encode(), 0)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                os.ftruncate(fd, 0)
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def cpu_count() -> int:
    try:
        return len(os.sched_getaffinity(0)) or 1
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def load_per_cpu(load: Callable[[], tuple[float, float, float]] = os.getloadavg, cpus: Callable[[], int] = cpu_count) -> float:
    """The last minute's load average per CPU: 1.0 is a machine that's just busy, 10 one where each CPU has ten takers."""
    try:
        return load()[0] / max(1, cpus())
    except OSError:
        return 0.0


def wait_for_quiet(max_wait: float | None = None, *, busy: float = BUSY, say: Callable[[str], None] = print,
                   load: Callable[[], float] = load_per_cpu, poll: float = 5.0, now: Callable[[], float] = time.monotonic,
                   sleep: Callable[[float], None] = time.sleep) -> float:
    """Waits while the machine is busier than `busy` per CPU, up to max_wait seconds, and returns the load it starts at
    (a run that starts busy anyway scales its waits by it)."""
    max_wait = LOAD_WAIT if max_wait is None else max_wait
    start, told = now(), None
    while (ratio := load()) > busy and now() - start < max_wait:
        if told is None or now() - told >= 60:
            say(f"verify: the machine is busy (load {ratio:.1f} per CPU, more than {busy}), waiting for it to settle... "
                f"({_span(now() - start)} so far, {_span(max_wait)} at most)")
            told = now()
        sleep(poll)
    if ratio > busy:
        say(f"verify: still busy (load {ratio:.1f} per CPU) after {_span(max_wait)}; going ahead with longer waits")
    return ratio


def time_scale(ratio: float, attempt: int = 1) -> float:
    """How much longer than on an idle machine each wait may take: the load per CPU (at least 1, at most MAX_SCALE),
    times the attempt, so a retry is also more patient."""
    return round(min(MAX_SCALE, max(1.0, ratio)) * attempt, 2)


# --- A run -----------------------------------------------------------------------------------------------------------

class RunLog:
    """What the run prints, also written to a log file of its own (never a fixed name another run could share)."""

    def __init__(self, folder: str | None = None, keep: int = KEEP_LOGS, out: TextIO | None = None):
        folder = folder or LOGS
        os.makedirs(folder, exist_ok=True)
        old = sorted(f for f in os.listdir(folder) if f.startswith("verify-") and f.endswith(".log"))
        for f in old[:max(0, len(old) - keep + 1)]:
            with contextlib.suppress(OSError):
                os.remove(os.path.join(folder, f))
        now = time.time()   # the name sorts by when the run started: the oldest are the ones cleared
        stamp = f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}.{int(now * 1e6) % 1_000_000:06d}"
        self.path = os.path.join(folder, f"verify-{stamp}-{os.getpid()}.log")
        self._file = open(self.path, "x", encoding="utf-8")   # noqa: SIM115 -- open for the run, closed by close()
        self._out = out or sys.stdout

    def say(self, line: str, err: bool = False) -> None:
        print(line, file=sys.stderr if err else self._out, flush=True)
        self._file.write(line + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def _plain(item: str) -> str:
    return item.split(":", 1)[-1]


def summary(failed: dict[str, list[str]], flaky: dict[str, list[str]], total: int) -> list[str]:
    """The lines that end a run: what failed and why (the first problem of each), and what passed only on a retry."""
    lines = []
    for item, problems in sorted(flaky.items()):
        lines.append(f"  flaky: {item} failed, then passed when run again: {problems[0] if problems else ''}")
    for item, problems in sorted(failed.items()):
        lines.append(f"  FAIL: {item}: {problems[0] if problems else 'failed'}")
        lines.extend(f"        {p}" for p in problems[1:4])
    if failed:
        lines.append(f"verify: FAILED: {len(failed)} of {total} pages and flows ({', '.join(_plain(i) for i in sorted(failed))}), "
                     f"after up to {ATTEMPTS} attempts each. screenshots/ is untouched; artifacts/verify/ has the report, each failed "
                     "step's -FAILED- image and, under unverified/, what was captured")
    else:
        lines.append(f"verify: OK, {total} pages and flows")
    return lines


# An item's latest result: (the folder its round wrote to, verify.mjs's entry for it: id, ok, files, failed_files, problems).
Result = tuple[str, dict[str, Any]]


def publish(out: str, results: dict[str, Result], report: dict[str, Any], stage_id: str) -> None:
    """Swaps out's contents for this run's at once (built in a new folder beside it, then renamed into place). The images
    go at the top only when every item passed (report["ok"], and then report["files"] lists them); after a failure they go
    under unverified/, beside the report and the failed steps' -FAILED- images, so nothing from a failed run can pass
    for a result (tools/sync_screenshots.py reads the report, and copies nothing from a failed run)."""
    parent = os.path.dirname(out)
    os.makedirs(parent, exist_ok=True)
    stage = os.path.join(parent, f".verify-new-{stage_id}")
    shutil.rmtree(stage, ignore_errors=True)
    images = stage if report["ok"] else os.path.join(stage, "unverified")
    os.makedirs(images)
    files = []
    for folder, item in results.values():
        for f in item.get("files", []):
            if os.path.isfile(src := os.path.join(folder, f)):
                shutil.copyfile(src, os.path.join(images, f))
                files.append(f)
        for f in item.get("failed_files", []):
            if os.path.isfile(src := os.path.join(folder, f)):
                shutil.copyfile(src, os.path.join(stage, f))
        if "first_run" in item:   # --twice: the first run's version of each image that came out different
            first_folder, differ = item.pop("first_run")
            os.makedirs(first := os.path.join(stage, "unverified", "first-run"), exist_ok=True)
            for f in differ:
                shutil.copyfile(os.path.join(first_folder, f), os.path.join(first, f))
    report["files"] = sorted(files) if report["ok"] else []
    with open(os.path.join(stage, "report.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    old = os.path.join(parent, f".verify-old-{stage_id}")
    if os.path.exists(out):
        os.rename(out, old)
    os.rename(stage, out)
    shutil.rmtree(old, ignore_errors=True)


def run(pages: list[str], twice: bool = False) -> int:
    frontend = os.path.join(ROOT, "frontend")
    if not os.path.isdir(os.path.join(frontend, "node_modules", "@playwright")):
        print("verify: the web app's packages aren't installed. Run: cd frontend && npm ci", file=sys.stderr)
        return 2
    if not os.path.isfile(os.path.join(ROOT, "runway", "static", "app", "index.html")):
        print("verify: the web app isn't built. Run: cd frontend && npm run build (make verify does it)", file=sys.stderr)
        return 2
    node = shutil.which("node")
    if not node:
        print("verify: Node isn't installed (the web app needs it too).", file=sys.stderr)
        return 2
    lib = find_libfaketime()
    if not lib:
        print(libfaketime_missing_message(), file=sys.stderr)
        return 2
    log = RunLog()
    log.say(f"verify: this run's log is {log.path}")
    try:
        with machine_lock(say=log.say):
            ratio = wait_for_quiet(say=log.say)
            return _run(pages, twice, node, lib, ratio, log)
    except MachineBusy as e:
        log.say(str(e), err=True)
        return 3
    finally:
        log.say(f"verify: log in {log.path}")
        log.close()


class _Servers:
    """The demo server and the signed-in one, on a copy of the seeded data, for one round."""

    def __init__(self, data: str, frozen: dict[str, str], scale: float):
        self.data, self.frozen, self.scale = data, frozen, scale
        self.procs: list[subprocess.Popen] = []
        port, signed_in_port = free_port(), free_port()
        self.url, self.signed_in_url = f"http://127.0.0.1:{port}", f"http://localhost:{signed_in_port}"
        self.ports = (("server", port, frozen), ("signed-in server", signed_in_port, signed_in_env(frozen, self.signed_in_url)))

    def start(self) -> str | None:
        """Starts both, and returns what went wrong (with the server's log), or None."""
        for name, p, server_env in self.ports:
            log_path = os.path.join(self.data, f"{name.replace(' ', '-')}.log")
            with open(log_path, "w") as log:
                self.procs.append(subprocess.Popen(server_command(p), env=server_env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT))
            try:
                wait_ready(f"http://127.0.0.1:{p}/healthz", self.procs[-1], timeout=60 * self.scale)
            except RuntimeError as e:
                with open(log_path) as f:
                    return f"the {name}: {e}\n{f.read()[-2000:]}"
        return None

    def stop(self) -> None:
        for server in self.procs:
            server.terminate()
            try:
                server.wait(10)
            except subprocess.TimeoutExpired:
                server.kill()


def retrying(run_round: Callable[[int, list[str] | None], dict[str, Any] | None], say: Callable[[str], None] = print,
             attempts: int = ATTEMPTS) -> tuple[dict[str, Result], dict[str, list[str]], list[str]] | None:
    """Runs a round (`run_round(attempt, only)`: verify.mjs on fresh servers and data, only those items when given), then
    the items that failed again, up to `attempts` rounds in all. Returns each item's last result, the items that passed
    only on a retry (with the problems they had), and the items the run covers; None when a round can't run at all."""
    results: dict[str, Result] = {}
    flaky: dict[str, list[str]] = {}
    planned: list[str] = []
    pending: list[str] | None = None   # None: everything the pages select
    for attempt in range(1, attempts + 1):
        # A round that died before it said what it covers ("run") is run again whole.
        report = run_round(attempt, None if pending is None or "run" in pending else pending)
        if report is None:
            return None
        if not planned or "run" in planned:
            planned = [i["id"] for i in report["items"]]
            results.pop("run", None)
        for item in report["items"]:
            before = results.get(item["id"])
            if item["ok"] and before and not before[1]["ok"]:
                flaky[item["id"]] = before[1]["problems"]
            results[item["id"]] = (report["out"], item)
        pending = sorted(i for i, (_, item) in results.items() if not item["ok"])
        if not pending:
            break
        if attempt < attempts:
            say(f"verify: {len(pending)} failed ({', '.join(_plain(i) for i in pending)}): running "
                f"{'it' if len(pending) == 1 else 'them'} again on a fresh copy of the demo data (attempt {attempt + 1} of {attempts})")
    return results, flaky, planned


def _run(pages: list[str], twice: bool, node: str, lib: str, ratio: float, log: RunLog) -> int:
    work = tempfile.mkdtemp(prefix="runway-verify-")
    seed = os.path.join(work, "seed")
    os.makedirs(seed)
    token = secrets.token_urlsafe(32)
    try:
        seeded = subprocess.run([sys.executable, os.path.join(ROOT, "run.py"), "demo", "--ai-buttons", "--investments", "--receipt",
                                 "--subcategories", "--signed-in"], env={**frozen_env(clean_env(seed), lib), SESSION_ENV: token},
                                cwd=ROOT, capture_output=True, text=True)
        if seeded.returncode:
            log.say(f"verify: couldn't fill the demo database:\n{seeded.stdout}{seeded.stderr}", err=True)
            return 2

        def run_round(attempt: int, only: list[str] | None, name: str = "") -> dict[str, Any] | None:
            scale = time_scale(max(ratio, load_per_cpu()), attempt)
            return _round(os.path.join(work, name or f"round-{attempt}"), seed, pages, only, scale, attempt, node, lib, token, log)

        done = retrying(run_round, log.say)
        if done is None:
            return 2
        results, flaky, planned = done
        if twice and all(item["ok"] for _, item in results.values()):
            log.say("verify: --twice: everything again, on fresh data, to check each image comes out byte for byte the same")
            again = run_round(2, None, "again")
            if again is None:
                return 2
            for item in again["items"]:
                first = results.get(item["id"])
                differ = differing(first[0], again["out"], first[1]["files"]) if first else []
                if not item["ok"] or differ:
                    problems = [f"the same tree gave different images the second time: {', '.join(differ)}"] if differ else item["problems"]
                    # Both versions are kept, for comparing: the second run's under unverified/, the first's under unverified/first-run/.
                    first_run = {"first_run": (first[0], differ)} if first and differ else {}
                    results[item["id"]] = (again["out"], {**item, "ok": False, "problems": problems, **first_run})
        failed = {i: item["problems"] for i, (_, item) in results.items() if not item["ok"]}
        ok = not failed and bool(results)
        report = {"ok": ok, "limited": bool(pages), "pages": pages, "items": planned, "failed": failed, "flaky": flaky,
                  "attempts": ATTEMPTS, "load_per_cpu": round(ratio, 2), "log": log.path}
        publish(OUT, results, report, os.path.basename(log.path).removesuffix(".log"))
        for line in summary(failed, flaky, len(planned)):
            log.say(line, err=line.lstrip().startswith(("FAIL", "verify: FAILED")))
        return 0 if ok else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


def differing(first: str, second: str, files: list[str]) -> list[str]:
    """The images that aren't byte for byte the same in the two folders (or missing from the second)."""
    out = []
    for f in files:
        b = os.path.join(second, f)
        with open(os.path.join(first, f), "rb") as x:
            if not os.path.isfile(b):
                out.append(f)
                continue
            with open(b, "rb") as y:
                if x.read() != y.read():
                    out.append(f)
    return out


def _round(folder: str, seed: str, pages: list[str], only: list[str] | None, scale: float, attempt: int, node: str, lib: str,
           token: str, log: RunLog) -> dict[str, Any] | None:
    """One pass of the browser over the pages and flows (`only` those items, when given) on a fresh copy of the seeded
    data and fresh servers. Returns verify.mjs's report, or None for a run that can't work (a malformed flow, an unknown
    page); a run that crashed reports its unfinished items as failed, to be tried again."""
    data = os.path.join(folder, "data")
    out = os.path.join(folder, "out")
    shutil.copytree(seed, data)
    servers = _Servers(data, frozen_env(clean_env(data), lib), scale)
    try:
        problem = servers.start()
        if problem:
            log.say(f"verify: {problem}", err=True)
            return _crashed(only, f"the demo server didn't start: {problem.splitlines()[0]}")
        log.say(f"verify: attempt {attempt}: demo data served at {servers.url} (and signed in, for the flows that need it, "
                f"at {servers.signed_in_url}); waits up to {scale}x as long as on an idle machine")
        args = [node, os.path.join(ROOT, "frontend", "verify", "verify.mjs"), "--url", servers.url, "--out", out,
                "--signed-in-url", servers.signed_in_url, "--day", FROZEN_DAY, "--time-scale", str(scale),
                "--attempt", str(attempt), *(["--only", ",".join(only)] if only else []), *pages]
        env = {**clean_env(data), SESSION_ENV: token}
        with subprocess.Popen(args, cwd=os.path.join(ROOT, "frontend"), env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, bufsize=1) as browser:
            assert browser.stdout is not None
            for line in browser.stdout:
                log.say(line.rstrip("\n"))
        code = browser.returncode
    finally:
        servers.stop()
    try:
        with open(os.path.join(out, "report.json")) as f:
            report: dict[str, Any] = json.load(f)
    except (OSError, ValueError):
        if code == 2:
            return None   # verify.mjs refused the run (it said why)
        return _crashed(only, f"the browser run ended with code {code} before it wrote its report")
    if code == 2 and not report.get("items"):
        return None
    if code not in (0, 1):   # it died part way: what it didn't finish is in the report as failed ("not run")
        log.say(f"verify: the browser run ended with code {code}", err=True)
    return {**report, "out": out}


def _crashed(only: list[str] | None, why: str) -> dict[str, Any]:
    return {"out": "", "items": [{"id": i, "ok": False, "files": [], "problems": [why]} for i in (only or ["run"])]}
