"""A stand-in for the price service on the loopback address: the chart reply runway/providers/prices.py reads, for the
made-up symbols the demo holds (demo.HOLDINGS), with the closes demo.seed_investments saves, so a price refresh brings
back what the demo already shows.

    python -m tests.fakes.prices_server [--today YYYY-MM-DD]

It listens on 127.0.0.1 (plain http: RUNWAY_PRICES_URL allows it, for a test server), on a port of its own, and prints
one line of JSON once it is ready: {"address", "port", "url"}, `url` being what RUNWAY_PRICES_URL is set to.
runway/verify.py's `serve` starts one beside each demo server; the tests run it in-process (`start`).

- `GET /chart/<symbol>?period1=&period2=&interval=1d...`: the daily closes in that window (a price refresh).
- `GET /chart/<symbol>?range=1d&interval=1d`: the latest price and the close before it (a live quote).
- Any other symbol (the S&P 500 fund the app also asks about, say) gets the service's 404, "no data found": the app
  then has no quote for it and takes the market to be closed, as the demo shows it.
- `GET /__fake/requests`: the symbols it was asked about since it started, for tests.

Dates are relative to --today (the demo's frozen day under make verify; today otherwise)."""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import signal
import sys
import threading
import urllib.parse
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

ADDRESS = "127.0.0.1"
# symbol: name, the service's type, yesterday's close, today's close: demo.HOLDINGS (tests/test_verify_fakes.py checks
# they are the same, and that the history below is the one demo.seed_investments saves)
HOLDINGS = {
    "DEMOTM": ("Sample Total Market ETF", "ETF", 281.40, 284.95),
    "DEMOIN": ("Sample International ETF", "ETF", 68.20, 67.55),
    "DEMOSI": ("Sample Industries Inc", "EQUITY", 192.10, 192.15),
}
HISTORY_DAYS = 365


def closes(symbol: str, today: date) -> list[tuple[date, float]]:
    """A year of daily closes up to `today`, oldest first: demo.seed_investments's."""
    _name, _kind, before, close = HOLDINGS[symbol]
    out = [(today - timedelta(days=k), round(before * (1 - 0.15 * k / 365) * (1 + 0.012 * math.sin(k / 5 + len(symbol))), 2))
           for k in range(HISTORY_DAYS, 1, -1)]
    return [*out, (today - timedelta(days=1), before), (today, close)]


def _stamp(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, 12, tzinfo=UTC).timestamp())   # midday: the same day in any zone the app reads it in


def chart(symbol: str, today: date, start: int | None = None, end: int | None = None) -> dict[str, Any] | None:
    """The chart reply for `symbol` (None: not one of HOLDINGS): its closes from `start` to before `end` (seconds since
    1970), or just the latest when neither is given (a quote)."""
    if symbol not in HOLDINGS:
        return None
    name, kind, before, close = HOLDINGS[symbol]
    days = closes(symbol, today)
    if start is None and end is None:
        days = days[-1:]
    else:
        days = [(d, c) for d, c in days if (start is None or _stamp(d) >= start) and (end is None or _stamp(d) < end)]
    values = [c for _, c in days]
    return {"chart": {"error": None, "result": [{
        "meta": {"symbol": symbol, "currency": "USD", "instrumentType": kind, "longName": name, "shortName": name, "gmtoffset": 0,
                 "regularMarketPrice": close, "previousClose": before, "chartPreviousClose": before, "regularMarketTime": _stamp(today)},
        "timestamp": [_stamp(d) for d, _ in days],
        "indicators": {"quote": [{"close": values}], "adjclose": [{"adjclose": values}]}}]}}


class Running:
    """A price service that is listening: where (`address`, `port`, `url` for RUNWAY_PRICES_URL), the symbols it was asked
    about (`requests`) and `stop()`."""

    def __init__(self, server: ThreadingHTTPServer, requests: list[str]):
        self._server = server
        self.requests = requests
        self.address, self.port = ADDRESS, server.server_address[1]
        self.url = f"http://{ADDRESS}:{self.port}/chart"

    def ready(self) -> dict[str, Any]:
        return {"address": self.address, "port": self.port, "url": self.url}

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def start(today: date | None = None) -> Running:
    """Starts the price service on a loopback port of its own and serves it from a thread. The caller stops it."""
    as_of = today or date.today()
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: object) -> None:
            pass

        def _send(self, status: int, body: Any) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            parts = urllib.parse.urlsplit(self.path)
            if parts.path == "/__fake/requests":
                self._send(200, requests)
                return
            if not parts.path.startswith("/chart/"):
                self._send(404, {"error": "Not found"})
                return
            symbol = urllib.parse.unquote(parts.path.removeprefix("/chart/"))
            requests.append(symbol)
            query = urllib.parse.parse_qs(parts.query)
            try:
                start_at = int(query["period1"][0]) if "period1" in query else None
                end_at = int(query["period2"][0]) if "period2" in query else None
            except ValueError:
                self._send(400, {"chart": {"result": None, "error": {"code": "Bad Request", "description": "period1 and period2 are seconds since 1970"}}})
                return
            reply = chart(symbol, as_of, start_at, end_at)
            if reply is None:
                self._send(404, {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"}}})
            else:
                self._send(200, reply)

    server = ThreadingHTTPServer((ADDRESS, 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="fake-prices").start()
    return Running(server, requests)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tests.fakes.prices_server", description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--today", type=date.fromisoformat, help="the day its prices are as of (default: today)")
    a = p.parse_args(argv)
    running = start(a.today)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(json.dumps(running.ready()), flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        threading.Event().wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
