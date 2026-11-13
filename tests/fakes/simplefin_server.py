"""A stand-in SimpleFIN bridge on the loopback address, over real TLS: the protocol Runway's client speaks
(runway/providers/simplefin.py), with made-up accounts and transactions that match the demo's (runway/domain/demo.py).

    python -m tests.fakes.simplefin_server --dir <a run's temporary folder> [--today YYYY-MM-DD] [--slow SECONDS]

It listens on 127.0.0.1, on a port of its own, with a certificate for NAME from a throwaway authority made for this run
(certs.py: only the authority's certificate is left on disk, in --dir, alone and in a bundle with the system's), and
prints one line of JSON once it is ready: {"name", "address", "port", "ca", "bundle"}. runway/verify.py's `serve` starts one beside each demo server and tells that server
alone where NAME is (simplefin.STAND_IN_ENV) and to trust that authority; the tests run it in-process (`start`).

What it serves:
- `POST /claim/<token>`: the access URL for a setup token (SETUP_TOKEN's; any other token is refused with a 403, as a
  bridge refuses one it doesn't know). Unlike a real bridge it gives the same answer each time it is asked, since a
  flow runs once at each width.
- `GET /simplefin/accounts?start-date=&end-date=`: the accounts of the login in the request's basic auth, each with its
  transactions in that window. Two logins: the demo's own (the access URL demo.seed saves: its five accounts, and a few
  purchases since the demo's last) and the one a claimed setup token gives (those, and an account the demo doesn't
  have yet). A request with any other credentials gets a 403.
- `POST /__fake/mode` (the body: one of MODES, or {"mode": ...}): how the bridge answers from then on. `ok`; `401`,
  `403` and `500` answer every bridge request with that status; `slow` answers after --slow seconds; `partial` leaves
  out one institution's accounts and says so in "errors", as a bridge does for a bank that needs attention.
- `GET /__fake/requests`: what it was asked since it started (method, path, the login, the status it answered), for tests.

Dates are relative to --today (the demo's frozen day under make verify; today otherwise)."""
from __future__ import annotations

import argparse
import base64
import contextlib
import json
import signal
import ssl
import sys
import threading
import time
import urllib.parse
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import certs

NAME = "sample-bank.invalid"   # the demo's bank (demo.seed's access URL): a reserved name that never resolves
ADDRESS = "127.0.0.1"
MODES = ("ok", "401", "403", "500", "slow", "partial")

# id, name, institution, balance: demo.ACCOUNTS (tests/test_verify_fakes.py checks they are the same)
ACCOUNTS = [
    ("demo-checking", "Everyday Checking", "Sample Bank", 4820.55),
    ("demo-savings", "High-Yield Savings", "Sample Bank", 18250.00),
    ("demo-card", "Rewards Visa", "Sample Card Co", -1342.18),
    ("demo-mortgage", "Home Mortgage", "Sample Lending", -284900.00),
    ("demo-travel", "Travel Mastercard", "Sample Card Co", -236.40),
]
# What a newly claimed token's login also has: a bank added at the bridge since.
ADDED_ACCOUNTS = [("demo-vacation", "Vacation Savings", "Sample Credit Union", 2310.40)]
# account, id, days before today, amount, description, pending: purchases since the demo's last, at payees it already
# has (so they are categorized as its others are), one of them not posted yet.
TRANSACTIONS = [
    ("demo-card", "fake-coffee", 1, -6.75, "CORNER COFFEE", False),
    ("demo-card", "fake-groceries", 1, -58.12, "GREEN GROCER MARKET", False),
    ("demo-card", "fake-tacos", 0, -31.40, "TACO PLACE", True),
    ("demo-vacation", "fake-transfer", 3, 150.00, "TRANSFER FROM CHECKING", False),
]
PARTIAL_WITHOUT = "Sample Card Co"   # `partial`: the institution whose accounts are left out

# The demo's own login (demo.seed's access URL), and the one a claimed setup token gives.
DEMO_LOGIN = ("demo", "demo")
CLAIMED_LOGIN = ("claimed", "made-up-secret")
SETUP_TOKEN = "demo-setup-token"


def access_url(login: tuple[str, str] = DEMO_LOGIN) -> str:
    return f"https://{login[0]}:{login[1]}@{NAME}/simplefin"


def setup_token(token: str = SETUP_TOKEN) -> str:
    """What you paste into Settings: the claim address, base64, as a bridge's setup token is."""
    return base64.b64encode(f"https://{NAME}/claim/{token}".encode()).decode()


def _stamp(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


class Bridge:
    """The bridge's state: how it answers (mode), what it was asked (requests), and its data as of `today`."""

    def __init__(self, today: date | None = None, slow: float = 2.0):
        self.today = today or date.today()
        self.slow = slow
        self.mode = "ok"
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"the mode is one of {', '.join(MODES)}, not {mode!r}")
        self.mode = mode

    def note(self, method: str, path: str, login: str | None, status: int) -> None:
        with self._lock:
            self.requests.append({"method": method, "path": path, "login": login, "status": status})

    def accounts(self, login: str, start: int, end: int | None, partial: bool = False) -> dict[str, Any]:
        """The /accounts reply for `login`: its accounts, with the transactions dated from `start` to before `end`
        (seconds since 1970; no end: up to now)."""
        mine = ACCOUNTS + (ADDED_ACCOUNTS if login == CLAIMED_LOGIN[0] else [])
        out, errors = [], []
        for acct_id, name, org, balance in mine:
            if partial and org == PARTIAL_WITHOUT:
                continue
            txs = []
            for account, tx_id, ago, amount, description, pending in TRANSACTIONS:
                when = _stamp(self.today - timedelta(days=ago))
                if account != acct_id or when < start or (end is not None and when >= end):
                    continue
                txs.append({"id": tx_id, "posted": 0 if pending else when, "transacted_at": when, "amount": f"{amount:.2f}",
                            "description": description, **({"pending": True} if pending else {})})
            out.append({"org": {"name": org, "domain": NAME}, "id": acct_id, "name": name, "currency": "USD",
                        "balance": f"{balance:.2f}", "balance-date": _stamp(self.today), "transactions": txs})
        if partial:
            errors.append(f"Connection to {PARTIAL_WITHOUT} may need attention")
        return {"errors": errors, "accounts": out}


def _handler(bridge: Bridge) -> type[BaseHTTPRequestHandler]:
    logins = {base64.b64encode(f"{u}:{p}".encode()).decode(): u for u, p in (DEMO_LOGIN, CLAIMED_LOGIN)}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: object) -> None:   # quiet: requests are kept in bridge.requests
            pass

        def _send(self, status: int, body: bytes | str | dict[str, Any] | list[Any], kind: str = "text/plain") -> int:
            if isinstance(body, (dict, list)):
                body, kind = json.dumps(body), "application/json"
            data = body.encode() if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return status

        def _login(self) -> str | None:
            scheme, _, given = self.headers.get("Authorization", "").partition(" ")
            return logins.get(given.strip()) if scheme == "Basic" else None

        def _body(self) -> bytes:
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))

        def _failing(self) -> int | None:
            """The bridge's mode, for a request to the bridge itself: the status it fails with, or None to go on (after
            the wait, when it is slow)."""
            failures = {"401": (401, "Unauthorized"), "403": (403, "Forbidden"), "500": (500, "Internal Server Error")}
            if bridge.mode in failures:
                return self._send(*failures[bridge.mode])
            if bridge.mode == "slow":
                time.sleep(bridge.slow)
            return None

        def do_POST(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            body = self._body()
            if path == "/__fake/mode":
                text = body.decode("utf-8", errors="replace").strip()
                try:
                    mode = json.loads(text)["mode"] if text.startswith("{") else text
                    bridge.set_mode(str(mode))
                except (ValueError, KeyError, TypeError) as e:
                    self._send(400, {"error": str(e) or "the body is a mode, or {\"mode\": ...}", "modes": list(MODES)})
                    return
                self._send(200, {"mode": bridge.mode})
            elif path.startswith("/claim/"):
                status = self._failing()
                if status is None:
                    if path == f"/claim/{SETUP_TOKEN}":
                        status = self._send(200, access_url(CLAIMED_LOGIN))
                    else:
                        status = self._send(403, "Forbidden: this setup token isn't known, or was already claimed")
                bridge.note("POST", self.path, None, status)
            else:
                bridge.note("POST", self.path, self._login(), self._send(404, "Not found"))

        def do_GET(self) -> None:
            parts = urllib.parse.urlsplit(self.path)
            if parts.path == "/__fake/mode":
                self._send(200, {"mode": bridge.mode, "modes": list(MODES)})
            elif parts.path == "/__fake/requests":
                self._send(200, bridge.requests)
            elif parts.path == "/simplefin/accounts":
                login = self._login()
                if login is None:   # whatever the mode: the bridge doesn't know who is asking
                    status = self._send(403, "Forbidden: bad credentials")
                else:
                    status = self._failing()
                if status is None:
                    query = urllib.parse.parse_qs(parts.query)
                    try:
                        start = int(query.get("start-date", ["0"])[0])
                        end = int(query["end-date"][0]) if "end-date" in query else None
                    except ValueError:
                        status = self._send(400, "start-date and end-date are seconds since 1970")
                    else:
                        status = self._send(200, bridge.accounts(login, start, end, partial=bridge.mode == "partial"))
                bridge.note("GET", self.path, login, status)
            else:
                bridge.note("GET", self.path, self._login(), self._send(404, "Not found"))

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        """Quiet about a connection that never became a request: a client that doesn't trust the certificate hangs up
        during the handshake, which is its answer, not a fault here. Anything else is printed as usual."""
        if not isinstance(sys.exception(), (ssl.SSLError, ConnectionError)):
            super().handle_error(request, client_address)


class Running:
    """A bridge that is listening: where (`address`, `port`, under `name`), the certificate to trust (`ca`: the throwaway
    authority's; `bundle`: the system's certificates and it, for SSL_CERT_FILE), its state (`bridge`) and `stop()`."""

    def __init__(self, server: ThreadingHTTPServer, bridge: Bridge, ca: str, bundle: str):
        self._server = server
        self.bridge = bridge
        self.name, self.address, self.port, self.ca, self.bundle = NAME, ADDRESS, server.server_address[1], ca, bundle

    @property
    def stand_in(self) -> str:
        """simplefin.STAND_IN_ENV's value for this bridge."""
        return f"{self.name}={self.address}:{self.port}"

    def ready(self) -> dict[str, Any]:
        return {"name": self.name, "address": self.address, "port": self.port, "ca": self.ca, "bundle": self.bundle}

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def start(folder: str, today: date | None = None, slow: float = 2.0) -> Running:
    """Starts a bridge on a loopback port of its own, with a certificate for NAME from a throwaway authority whose
    certificate is left in `folder`, alone and in a bundle (certs.make, certs.bundle), and serves it from a thread. The
    caller stops it (`stop()`)."""
    bridge = Bridge(today, slow)
    made = certs.make(folder, NAME, (bridge.today,))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_cert_chain(made.cert, made.key)
    finally:
        made.forget()   # loaded (or not): the server's key isn't left on disk
    server = _Server((ADDRESS, 0), _handler(bridge))
    # The handshake happens in each request's own thread (on its first read), not in accept: a client that connects and
    # says nothing holds up nobody else.
    server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    threading.Thread(target=server.serve_forever, daemon=True, name="fake-simplefin").start()
    return Running(server, bridge, made.ca, certs.bundle(folder, made.ca))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tests.fakes.simplefin_server", description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--dir", required=True, help="where the throwaway authority's certificate goes (a run's temporary folder)")
    p.add_argument("--today", type=date.fromisoformat, help="the day its data is as of (default: today)")
    p.add_argument("--slow", type=float, default=2.0, help="how long the `slow` mode waits before each answer, in seconds")
    a = p.parse_args(argv)
    running = start(a.dir, a.today, a.slow)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(json.dumps(running.ready()), flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        threading.Event().wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
