"""`python run.py verify [page…]`: run the real app on made-up data and capture proof that it works.

Makes a temporary database, fills it with demo.seed (as `run.py demo` does), starts the server on a free port, and runs
frontend/verify/verify.mjs (Playwright) against it. Screenshots at phone, tablet and desktop widths, console errors and
failed requests go to artifacts/verify/. Returns the exit code: non-zero on a console error or a 5xx response. Never
touches your own data: the server gets its own folder and SQLite database, and none of your Postgres, sign-in, bank or
error-report settings.
"""
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

from . import tls

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "artifacts", "verify")


def clean_env(data: str, base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for the demo server: its own folder and SQLite database, nothing from the caller's setup that
    could point it at real data (Postgres, sign-in, bank providers, error reports)."""
    env = {k: v for k, v in (os.environ if base is None else base).items()
           if not k.startswith(("OIDC_", "SIMPLEFIN", "PLAID", "SENTRY", "RUNWAY_SECRET_KEY"))
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


def run(pages: list[str]) -> int:
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
    data = tempfile.mkdtemp(prefix="runway-verify-")
    env = clean_env(data)
    token = secrets.token_urlsafe(32)
    servers: list[subprocess.Popen] = []
    try:
        seeded = subprocess.run([sys.executable, os.path.join(ROOT, "run.py"), "demo", "--ai-buttons", "--investments", "--signed-in"],
                                env={**env, SESSION_ENV: token}, cwd=ROOT, capture_output=True, text=True)
        if seeded.returncode:
            print(f"verify: couldn't fill the demo database:\n{seeded.stdout}{seeded.stderr}", file=sys.stderr)
            return 2
        port, signed_in_port = free_port(), free_port()
        url, signed_in_url = f"http://127.0.0.1:{port}", f"http://localhost:{signed_in_port}"
        for name, p, server_env in (("server", port, env), ("signed-in server", signed_in_port, signed_in_env(env, signed_in_url))):
            log_path = os.path.join(data, f"{name.replace(' ', '-')}.log")
            with open(log_path, "w") as log:
                servers.append(subprocess.Popen(server_command(p), env=server_env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT))
            try:
                wait_ready(f"http://127.0.0.1:{p}/healthz", servers[-1])
            except RuntimeError as e:
                with open(log_path) as f:
                    print(f"verify: the {name}: {e}\n{f.read()[-2000:]}", file=sys.stderr)
                return 2
        print(f"verify: demo data served at {url} (and signed in, for the flows that need it, at {signed_in_url})")
        return subprocess.run([node, os.path.join(frontend, "verify", "verify.mjs"), "--url", url, "--out", OUT,
                               "--signed-in-url", signed_in_url, *pages],
                              cwd=frontend, env={**env, SESSION_ENV: token}).returncode
    finally:
        for server in servers:
            server.terminate()
            try:
                server.wait(10)
            except subprocess.TimeoutExpired:
                server.kill()
        shutil.rmtree(data, ignore_errors=True)
