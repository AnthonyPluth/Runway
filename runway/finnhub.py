"""Real-time stock and ETF prices from Finnhub's WebSocket trade feed, for the Investments page's live stream.

Yahoo stays the source for everything else (history, splits, funds, previous close and market hours) and is the live
source too when there's no Finnhub key. With a key, Runway keeps ONE connection to Finnhub for the whole server, however
many pages are open: a background thread that starts when someone streams prices and closes a few minutes after the
last stream ends. It subscribes to the union of what the open streams want (Finnhub's free plan allows 50 symbols at
once; the rest keep using Yahoo), remembers the latest trade per symbol, and wakes the streams when one arrives.

If Finnhub refuses the key, drops the connection or can't be reached, the streams simply carry on with Yahoo (they ask
`connected`, which is false then) while the thread retries with a growing delay. The last problem is kept for Settings.
The key stays on the server: it goes to Finnhub in the connection URL (the only place its WebSocket takes it), never
into the browser, a response or a log line."""
from __future__ import annotations

import contextlib
import json
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import prices

WS_URL = "wss://ws.finnhub.io"
REST_URL = "https://finnhub.io/api/v1"
MAX_SYMBOLS = 50        # the free plan's limit on symbols streamed at once
LINGER = 120            # a symbol nobody watches any more stays subscribed this long (a page reload shouldn't churn)
IDLE_CLOSE = 300        # with no stream open for this long the connection is closed
BACKOFF_MIN, BACKOFF_MAX = 2.0, 120.0
RATE_LIMIT_WAIT = 300   # after a 429: Finnhub asked us to slow down
STALE = 120             # no frame at all (Finnhub pings regularly) for this long: the connection is dead
POLL = 1.0              # how often the socket thread looks at the subscription list (also its read timeout)
BASELINE_TTL = 1800     # a REST quote used for a previous close is kept this long

KEY_FORMAT = re.compile(r"[A-Za-z0-9_-]{10,80}")


class FinnhubError(Exception):
    """A message that's safe to show: it never contains the key."""


class KeyRejected(FinnhubError):
    pass


def rest_quote(key: str, symbol: str, opener=urllib.request.urlopen) -> dict:
    """One quote over REST: {c: price, pc: previous close, t: time, ...}. Used to check a key, and for a previous
    close when Yahoo has none. Raises KeyRejected or FinnhubError."""
    # the key goes in a header, so it's not in any URL that gets logged
    req = urllib.request.Request(f"{REST_URL}/quote?symbol={urllib.parse.quote(symbol)}",
                                 headers={"X-Finnhub-Token": key, "Accept": "application/json"})
    try:
        with opener(req, timeout=10, context=prices._ctx()) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise KeyRejected("Finnhub doesn't accept that key.") from None
        if e.code == 429:
            raise FinnhubError("Finnhub says too many requests. Try again in a minute.") from None
        raise FinnhubError(f"Finnhub answered with an error ({e.code}).") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise FinnhubError("Couldn't reach Finnhub. Check the connection and try again.") from None
    if not isinstance(data, dict):
        raise FinnhubError("Finnhub sent something Runway couldn't read.")
    return data


def check_key(key: str, opener=urllib.request.urlopen) -> None:
    """Raises FinnhubError if the key isn't usable: the format, then one quote."""
    if not KEY_FORMAT.fullmatch(key):
        raise FinnhubError("That doesn't look like a Finnhub API key (letters and digits, from finnhub.io/dashboard).")
    rest_quote(key, "AAPL", opener)


def _connect(key: str):
    import websocket   # imported here: only installs with a Finnhub key need it
    return websocket.create_connection(f"{WS_URL}?token={key}", timeout=10, sslopt={"context": prices._ctx()})


class Feed:
    """The one shared connection. Streams call `watch(owner, symbols, key)` on every round (cheap; it starts the thread
    and keeps their symbols wanted), read `connected` / `latest(symbol)`, and sleep in `wait(seen, timeout)`, which
    returns early when a trade arrives or the connection changes."""

    def __init__(self, connect=_connect, clock=time.monotonic):
        self._connect = connect
        self._clock = clock
        self._cond = threading.Condition()
        self._watchers: dict[object, list[str]] = {}
        self._wanted_at: dict[str, float] = {}      # symbol -> the last time any stream wanted it
        self._subscribed: set[str] = set()
        self._latest: dict[str, tuple[float, int]] = {}   # symbol -> (price, trade time in ms)
        self._baselines: dict[str, tuple[float, dict | None]] = {}
        self._key: str | None = None
        self._rejected: str | None = None           # the key Finnhub refused (not retried until the key changes)
        self._connected = False
        self._thread: threading.Thread | None = None
        self._last_watch = clock()
        self._version = 0
        self._stop = False
        self.error: str | None = None

    # ---- for the streams

    def watch(self, owner: object, symbols: list[str], key: str) -> None:
        with self._cond:
            now = self._clock()
            self._last_watch = now
            self._watchers[owner] = list(dict.fromkeys(symbols))
            for s in symbols:
                self._wanted_at[s] = now
            if key != self._key:
                self._key = key
                if self._rejected != key:
                    self._rejected = None
                self._bump()   # a running connection notices the new key and reconnects
            if self._thread is None and self._rejected != key:
                self._stop = False
                self._thread = threading.Thread(target=self._run, name="finnhub", daemon=True)
                self._thread.start()

    def unwatch(self, owner: object) -> None:
        with self._cond:
            self._watchers.pop(owner, None)
            self._last_watch = self._clock()

    @property
    def connected(self) -> bool:
        return self._connected

    def latest(self, symbol: str) -> tuple[float, int] | None:
        """(price, trade time in ms) of the last trade seen for a symbol that's being streamed."""
        with self._cond:
            return self._latest.get(symbol) if symbol in self._subscribed else None

    def version(self) -> int:
        return self._version

    def wait(self, seen: int, timeout: float) -> int:
        with self._cond:
            self._cond.wait_for(lambda: self._version != seen, timeout)
            return self._version

    def baseline(self, symbol: str) -> dict | None:
        """Finnhub's REST quote, for the previous close when Yahoo has no quote for a symbol. Kept for a while, misses too."""
        with self._cond:
            hit = self._baselines.get(symbol)
            key = self._key
            if hit and self._clock() - hit[0] < BASELINE_TTL:
                return hit[1]
        q = None
        if key:
            try:
                got = rest_quote(key, symbol)
                q = got if got.get("pc") else None   # an unknown symbol comes back as all zeros
            except FinnhubError:
                q = None
        with self._cond:
            self._baselines[symbol] = (self._clock(), q)
        return q

    # ---- for Settings

    def status(self) -> dict:
        with self._cond:
            return {"active": self._thread is not None, "connected": self._connected, "symbols": len(self._subscribed),
                    "limit": MAX_SYMBOLS, "error": self.error}

    def reset(self) -> None:
        """The key was changed or removed: drop the connection (the next stream opens a fresh one) and forget the error."""
        with self._cond:
            self._key = None
            self._rejected = None
            self._latest.clear()
            self._baselines.clear()
            self.error = None
            self._bump()

    # ---- the connection thread

    def _bump(self) -> None:
        self._version += 1
        self._cond.notify_all()

    def _fail(self, message: str, key: str | None = None) -> None:
        with self._cond:
            self.error = message.replace(key, "…") if key else message

    def _desired(self, now: float) -> set[str]:
        """What to subscribe to: everything wanted right now, then what was wanted recently, up to the plan's limit.
        Symbols already streaming come first so a busy moment doesn't swap them around."""
        wanted = list(dict.fromkeys(s for syms in self._watchers.values() for s in syms))
        wanted.sort(key=lambda s: s not in self._subscribed)
        recent = sorted((s for s, t in self._wanted_at.items() if s not in wanted and now - t < LINGER),
                        key=lambda s: -self._wanted_at[s])
        return set((wanted + recent)[:MAX_SYMBOLS])

    def _idle_expired(self) -> bool:
        return not self._watchers and self._clock() - self._last_watch > IDLE_CLOSE

    def _run(self) -> None:
        backoff = BACKOFF_MIN
        try:
            while True:
                with self._cond:
                    key = self._key
                    if self._stop or self._idle_expired() or key is None:
                        return
                started = self._clock()
                try:
                    ws = self._connect(key)
                except Exception as e:   # whatever the reason, Yahoo carries on; the reason goes to Settings
                    wait = self._connect_failed(e, key, backoff)
                    if wait < 0:
                        return
                    if wait < RATE_LIMIT_WAIT:
                        backoff = min(backoff * 2, BACKOFF_MAX)
                else:
                    try:
                        self._serve(ws, key)
                    except Exception as e:
                        self._fail(f"Finnhub's connection dropped ({type(e).__name__}); using Yahoo meanwhile.", key)
                    finally:
                        with contextlib.suppress(Exception):
                            ws.close()
                        with self._cond:
                            self._connected = False
                            self._subscribed = set()
                            self._bump()
                    if self._clock() - started > 30:   # it was a good connection: start the delays over
                        backoff = BACKOFF_MIN
                    wait = backoff * random.uniform(0.75, 1.25)
                    backoff = min(backoff * 2, BACKOFF_MAX)
                def done(k: str = key) -> bool:   # wake for a new key, or when there's nothing left to do
                    return self._stop or self._key != k or self._idle_expired()
                with self._cond:
                    self._cond.wait_for(done, wait)
        finally:
            with self._cond:
                self._thread = None
                self._connected = False
                self._subscribed = set()
                self._bump()

    def _connect_failed(self, e: Exception, key: str, backoff: float) -> float:
        """Record why the connection failed; returns how long to wait, or -1 to stop trying until the key changes."""
        code = getattr(e, "status_code", None)
        if code in (401, 403):
            with self._cond:
                self._rejected = key
            self._fail("Finnhub rejected the API key. Runway is using Yahoo instead; check the key in Settings.", key)
            return -1
        if code == 429:
            self._fail("Finnhub says too many connections. Runway is using Yahoo and will try again in a few minutes.", key)
            return RATE_LIMIT_WAIT
        self._fail(f"Couldn't connect to Finnhub ({type(e).__name__}); using Yahoo instead.", key)
        return backoff * random.uniform(0.75, 1.25)

    def _serve(self, ws, key: str) -> None:
        import websocket
        ws.settimeout(POLL)
        mine: set[str] = set()
        last_frame = self._clock()
        with self._cond:
            self._connected = True
            self.error = None
            self._bump()
        while True:
            with self._cond:
                now = self._clock()
                if self._stop or self._key != key or self._idle_expired():
                    return
                want = self._desired(now)
                for s in [s for s, t in self._wanted_at.items() if s not in want and now - t > LINGER]:
                    del self._wanted_at[s]
            for s in sorted(want - mine):
                ws.send(json.dumps({"type": "subscribe", "symbol": s}))
            for s in sorted(mine - want):
                ws.send(json.dumps({"type": "unsubscribe", "symbol": s}))
            with self._cond:
                self._subscribed = mine = want
                for s in [s for s in self._latest if s not in want]:
                    del self._latest[s]
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                if self._clock() - last_frame > STALE:
                    raise ConnectionError("no data from Finnhub") from None
                continue
            last_frame = self._clock()
            if not raw:
                raise ConnectionError("closed by Finnhub")
            self._handle(raw, mine)

    def _handle(self, raw: str | bytes, mine: set[str]) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "error":
            self._fail(f"Finnhub: {str(msg.get('msg') or 'error')[:120]}")
            return
        if msg.get("type") != "trade":
            return   # pings and anything new
        moved = False
        with self._cond:
            for d in msg.get("data") or []:
                try:
                    sym, price, t = d["s"], float(d["p"]), int(d["t"])
                except (KeyError, TypeError, ValueError):
                    continue
                if sym not in mine or price <= 0:
                    continue
                if sym in self._latest and t < self._latest[sym][1]:   # trades can arrive out of order
                    continue
                self._latest[sym] = (price, t)
                moved = True
            if moved:
                self._bump()


feed = Feed()   # the server's one connection
