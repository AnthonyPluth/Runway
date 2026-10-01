"""Finnhub live prices: the shared WebSocket feed (subscriptions, trades, reconnects, a refused key), how the quote
stream uses it and falls back to Yahoo, and the Settings endpoints. The WebSocket and REST calls are always faked."""
import contextlib
import io
import json
import queue
import time
import unittest
import urllib.error
from unittest import mock

import websocket
from sqlalchemy import select

from runway import db, finnhub, prices
from runway import settings_keys as sk
from runway.models import Setting
from runway.server.api import investments, state
from runway.server.common import ApiError
from tests.shared import DbCase

KEY = "abcd1234efgh5678ijkl"
OTHER_KEY = "zyxw9876vuts5432rqpo"


class FakeWS:
    """A Finnhub connection: `frames` are what it will say, `sent` what Runway said."""

    def __init__(self):
        self.frames: queue.Queue = queue.Queue()
        self.sent: list[dict] = []
        self.closed = False

    def settimeout(self, _t):
        pass

    def send(self, text):
        self.sent.append(json.loads(text))

    def recv(self):
        try:
            frame = self.frames.get(timeout=0.01)
        except queue.Empty:
            raise websocket.WebSocketTimeoutException("timed out") from None
        if isinstance(frame, Exception):
            raise frame
        return frame

    def close(self):
        self.closed = True

    def subscribed(self):
        subs: set = set()
        for m in list(self.sent):
            (subs.add if m["type"] == "subscribe" else subs.discard)(m["symbol"])
        return subs

    def trade(self, sym, price, t_ms):
        self.frames.put(json.dumps({"type": "trade", "data": [{"s": sym, "p": price, "t": t_ms, "v": 10, "c": []}]}))


def until(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.sockets: list[FakeWS] = []
        self.outcomes: list = []   # exceptions to raise for the next connects, before one works
        self.keys: list[str] = []

        def connect(key):
            self.keys.append(key)
            if self.outcomes:
                raise self.outcomes.pop(0)
            ws = FakeWS()
            self.sockets.append(ws)
            return ws

        self.feed = finnhub.Feed(connect=connect)
        for name, value in (("BACKOFF_MIN", 0.01), ("BACKOFF_MAX", 0.05), ("RATE_LIMIT_WAIT", 0.2), ("POLL", 0.01)):
            p = mock.patch.object(finnhub, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.feed.reset)

    def test_subscribes_to_the_union_of_what_streams_want(self):
        a, b = object(), object()
        self.feed.watch(a, ["AAPL", "MSFT"], KEY)
        self.feed.watch(b, ["MSFT", "VTI"], KEY)
        self.assertTrue(until(lambda: self.sockets and self.sockets[0].subscribed() == {"AAPL", "MSFT", "VTI"}))
        self.assertEqual([m for m in self.sockets[0].sent if m["symbol"] == "MSFT"], [{"type": "subscribe", "symbol": "MSFT"}])
        self.assertEqual(self.keys, [KEY])   # one connection for everyone

    def test_never_more_than_the_plans_limit(self):
        self.feed.watch(object(), [f"S{i:02d}" for i in range(60)], KEY)
        self.assertTrue(until(lambda: self.sockets and len(self.sockets[0].subscribed()) == finnhub.MAX_SYMBOLS))
        time.sleep(0.05)
        self.assertEqual(len(self.sockets[0].subscribed()), finnhub.MAX_SYMBOLS)

    def test_unwatched_symbols_are_unsubscribed_after_a_while(self):
        a, b = object(), object()
        with mock.patch.object(finnhub, "LINGER", 0.15):
            self.feed.watch(a, ["AAPL"], KEY)
            self.feed.watch(b, ["MSFT"], KEY)
            self.assertTrue(until(lambda: self.sockets and self.sockets[0].subscribed() == {"AAPL", "MSFT"}))
            self.feed.unwatch(b)
            self.feed.watch(a, ["AAPL"], KEY)
            self.assertEqual(self.sockets[0].subscribed(), {"AAPL", "MSFT"})   # not straight away: a reload shouldn't churn
            self.assertTrue(until(lambda: self.sockets[0].subscribed() == {"AAPL"}))

    def test_keeps_the_latest_trade_and_ignores_the_rest(self):
        self.feed.watch(object(), ["AAPL"], KEY)
        self.assertTrue(until(lambda: self.sockets and self.sockets[0].subscribed() == {"AAPL"}))
        ws = self.sockets[0]
        seen = self.feed.version()
        ws.trade("AAPL", 190.5, 2_000)
        self.assertNotEqual(self.feed.wait(seen, 2), seen)   # a trade wakes whoever is waiting
        self.assertEqual(self.feed.latest("AAPL"), (190.5, 2_000))
        ws.trade("AAPL", 1.0, 1_000)      # out of order
        ws.trade("AAPL", 0, 3_000)        # not a price
        ws.trade("TSLA", 250, 3_000)      # not subscribed
        ws.frames.put("{not json")
        ws.frames.put(json.dumps({"type": "ping"}))
        ws.trade("AAPL", 191, 4_000)
        self.assertTrue(until(lambda: self.feed.latest("AAPL") == (191.0, 4_000)))
        self.assertIsNone(self.feed.latest("TSLA"))
        self.assertTrue(self.feed.connected)

    def test_reconnects_with_a_delay_and_resubscribes(self):
        self.outcomes = [OSError("down"), OSError("still down")]
        self.feed.watch(object(), ["AAPL"], KEY)
        self.assertTrue(until(lambda: self.sockets and self.sockets[0].subscribed() == {"AAPL"}))
        self.assertEqual(len(self.keys), 3)
        self.assertTrue(self.feed.connected)
        self.assertIsNone(self.feed.status()["error"])                 # a good connection clears the last problem
        self.sockets[0].frames.put(ConnectionResetError("dropped"))
        self.assertTrue(until(lambda: len(self.sockets) == 2 and self.sockets[1].subscribed() == {"AAPL"}))
        self.assertTrue(self.sockets[0].closed)

    def test_records_why_it_failed_and_keeps_trying(self):
        self.outcomes = [OSError(f"cannot reach wss://ws.finnhub.io?token={KEY}")] * 50
        self.feed.watch(object(), ["AAPL"], KEY)
        self.assertTrue(until(lambda: self.feed.status()["error"]))
        st = self.feed.status()
        self.assertFalse(st["connected"])
        self.assertNotIn(KEY, json.dumps(st))
        self.assertIn("Yahoo", st["error"])
        self.assertTrue(until(lambda: len(self.keys) >= 3))

    def test_a_refused_key_stops_retrying_until_the_key_changes(self):
        class Refused(Exception):
            status_code = 401
        self.outcomes = [Refused(f"Handshake status 401 for {KEY}")]
        owner = object()
        self.feed.watch(owner, ["AAPL"], KEY)
        self.assertTrue(until(lambda: "rejected" in (self.feed.status()["error"] or "") and not self.feed.status()["active"]))
        self.assertNotIn(KEY, self.feed.status()["error"])
        self.feed.watch(owner, ["AAPL"], KEY)                 # same key on the next stream round: not tried again
        time.sleep(0.05)
        self.assertEqual(len(self.keys), 1)
        self.feed.watch(owner, ["AAPL"], OTHER_KEY)
        self.assertTrue(until(lambda: self.sockets and self.sockets[0].subscribed() == {"AAPL"}))
        self.assertEqual(self.keys, [KEY, OTHER_KEY])

    def test_a_new_key_reconnects(self):
        owner = object()
        self.feed.watch(owner, ["AAPL"], KEY)
        self.assertTrue(until(lambda: self.sockets))
        self.feed.watch(owner, ["AAPL"], OTHER_KEY)
        self.assertTrue(until(lambda: len(self.sockets) == 2 and self.sockets[0].closed))

    def test_closes_when_nobody_is_streaming(self):
        owner = object()
        with mock.patch.object(finnhub, "IDLE_CLOSE", 0.1):
            self.feed.watch(owner, ["AAPL"], KEY)
            self.assertTrue(until(lambda: self.sockets))
            self.feed.unwatch(owner)
            self.assertTrue(until(lambda: self.sockets[0].closed and not self.feed.status()["active"]))
        self.assertFalse(self.feed.connected)
        self.feed.watch(owner, ["AAPL"], KEY)                 # the next stream opens it again
        self.assertTrue(until(lambda: len(self.sockets) == 2))

    def test_the_servers_error_messages_are_kept(self):
        self.feed.watch(object(), ["AAPL"], KEY)
        self.assertTrue(until(lambda: self.sockets))
        self.sockets[0].frames.put(json.dumps({"type": "error", "msg": "Subscribing to too many symbols"}))
        self.assertTrue(until(lambda: "too many symbols" in (self.feed.status()["error"] or "")))

    def test_baseline_quote_is_asked_once(self):
        self.feed.watch(object(), ["AAPL"], KEY)
        with mock.patch.object(finnhub, "rest_quote", return_value={"c": 190, "pc": 188.0}) as rq:
            self.assertEqual(self.feed.baseline("AAPL")["pc"], 188.0)
            self.feed.baseline("AAPL")
        rq.assert_called_once_with(KEY, "AAPL")


class RestTests(unittest.TestCase):
    def opener(self, body=None, code=None):
        seen = []

        def opener(req, timeout, context):
            seen.append(req)
            if code:
                raise urllib.error.HTTPError(req.full_url, code, "no", {}, None)
            return contextlib.closing(mock.Mock(read=lambda: json.dumps(body).encode()))
        opener.seen = seen
        return opener

    def test_the_key_travels_in_a_header_not_the_url(self):
        op = self.opener({"c": 190, "pc": 188})
        self.assertEqual(finnhub.rest_quote(KEY, "AAPL", op)["pc"], 188)
        req = op.seen[0]
        self.assertNotIn(KEY, req.full_url)
        self.assertEqual(req.get_header("X-finnhub-token"), KEY)

    def test_check_key_explains_each_failure_without_the_key(self):
        for code, text in ((401, "doesn't accept"), (403, "doesn't accept"), (429, "too many"), (500, "error")):
            with self.assertRaises(finnhub.FinnhubError) as cm:
                finnhub.check_key(KEY, self.opener(code=code))
            self.assertIn(text, str(cm.exception))
            self.assertNotIn(KEY, str(cm.exception))
        with self.assertRaises(finnhub.FinnhubError) as cm:
            finnhub.check_key("short", self.opener({}))
        self.assertIn("doesn't look like", str(cm.exception))

    def test_unreachable(self):
        def op(*_a, **_k):
            raise urllib.error.URLError("no route")
        with self.assertRaises(finnhub.FinnhubError) as cm:
            finnhub.check_key(KEY, op)
        self.assertIn("Couldn't reach", str(cm.exception))


def yq(price, prev=100.0, t=1000, type_="EQUITY", open_=True):
    return {"price": price, "prev_close": prev, "time": t, "type": type_,
            "open_start": 1 if open_ else None, "open_end": 2 ** 40 if open_ else None}


class FakeLive:
    """Stands in for finnhub.Feed. `trades` is {symbol: (price, ms)}; `on_wait` runs when the stream waits for news."""

    def __init__(self, connected=True, trades=None, baselines=None):
        self.connected = connected
        self.trades = trades or {}
        self.baselines = baselines or {}
        self.watched: list = []
        self.unwatched = 0
        self.v = 0
        self.on_wait = lambda: None

    def watch(self, _owner, symbols, key):
        self.watched.append((symbols, key))

    def unwatch(self, _owner):
        self.unwatched += 1

    def latest(self, s):
        return self.trades.get(s) if self.connected else None

    def baseline(self, s):
        return self.baselines.get(s)

    def version(self):
        return self.v

    def wait(self, _seen, _timeout):
        self.on_wait()
        self.v += 1
        return self.v


class QuoteStreamTests(unittest.TestCase):
    def run_stream(self, rounds, live, lifetime=1, tickers=("VTI", "AAPL", "SPAXX"), wait_takes=0.2):
        """quote_stream on a fake clock; each wait on the feed takes `wait_takes` seconds of it, as a trade arriving would."""
        clock = [0.0]
        slept: list[float] = []
        ttls: list[float] = []
        answers = iter(rounds)
        step = live.on_wait

        def on_wait():
            clock[0] += wait_takes
            step()
        live.on_wait = on_wait

        def quotes(_t, ttl=0):
            ttls.append(ttl)
            return next(answers)

        def sleep(s):
            slept.append(s)
            clock[0] += s

        with mock.patch.object(prices, "quotes", side_effect=quotes):
            out = list(prices.quote_stream(list(tickers), lifetime=lifetime, clock=lambda: clock[0], sleep=sleep,
                                           live=live, live_key=KEY))
        return out, slept, ttls

    def test_trades_replace_the_price_and_keep_yahoos_previous_close(self):
        live = FakeLive(trades={"VTI": (251.25, 1_500_000)})
        base = {"SPY": yq(500), "VTI": yq(250), "AAPL": yq(200), "SPAXX": yq(1, type_="MUTUALFUND")}
        out, _, ttls = self.run_stream([base] * 3, live)
        first = out[0]
        self.assertEqual(set(first), {"quotes", "market", "as_of"})   # the same payload as ever
        self.assertEqual(first["market"], "open")
        self.assertEqual(first["quotes"]["VTI"], {**yq(250), "price": 251.25, "time": 1500})
        self.assertEqual(first["quotes"]["VTI"]["prev_close"], 100.0)   # so day change stays right
        self.assertEqual(first["quotes"]["AAPL"], yq(200))              # no trade yet: Yahoo's
        self.assertEqual(ttls[0], prices.LIVE_QUOTE_TTL)
        self.assertEqual(live.watched[0], (["AAPL", "SPY", "VTI"], KEY))   # the fund isn't streamed
        self.assertEqual(live.unwatched, 1)

    def test_a_trade_older_than_yahoos_quote_is_ignored(self):
        live = FakeLive(trades={"VTI": (240.0, 900_000)})   # 900s; Yahoo's is at 1000s
        out, _, _ = self.run_stream([{"SPY": yq(500), "VTI": yq(250)}] * 3, live)
        self.assertEqual(out[0]["quotes"]["VTI"]["price"], 250)

    def test_an_update_goes_out_when_a_trade_arrives_but_no_more_than_once_a_second(self):
        live = FakeLive(trades={"VTI": (251.0, 1_100_000)})
        live.on_wait = lambda: live.trades.__setitem__("VTI", (252.0, 1_200_000))   # a trade lands 0.2s after the update
        out, slept, _ = self.run_stream([{"SPY": yq(500), "VTI": yq(250)}] * 4, live, lifetime=1.5, tickers=("VTI",))
        self.assertEqual([u["quotes"]["VTI"]["price"] for u in out[:2]], [251.0, 252.0])
        self.assertAlmostEqual(slept[0], prices.LIVE_THROTTLE - 0.2)   # held back to one a second, not every trade
        self.assertNotIn(prices.STREAM_SECONDS, slept)                 # and no polling sleep

    def test_polls_yahoo_as_before_while_the_feed_is_down(self):
        live = FakeLive(connected=False, trades={"VTI": (999.0, 9_000_000)})
        rounds = [{"SPY": yq(500), "VTI": yq(250)}] + [{"SPY": yq(500), "VTI": yq(251, t=1005)}] * 5
        out, _, ttls = self.run_stream(rounds, live, lifetime=2)
        self.assertEqual(out[0]["quotes"]["VTI"]["price"], 250)          # never the feed's numbers
        self.assertEqual(out[1]["quotes"]["VTI"]["price"], 251)
        self.assertEqual(ttls[0], prices.stream_interval(3) - 1)         # Yahoo's normal cadence

    def test_a_symbol_yahoo_has_no_quote_for_uses_finnhubs_previous_close(self):
        live = FakeLive(trades={"AAPL": (191.0, 1_500_000)}, baselines={"AAPL": {"c": 190, "pc": 188.0}})
        out, _, _ = self.run_stream([{"SPY": yq(500)}] * 3, live)
        self.assertEqual(out[0]["quotes"]["AAPL"]["prev_close"], 188.0)
        self.assertEqual(out[0]["quotes"]["AAPL"]["price"], 191.0)

    def test_closed_market_ends_the_stream_and_streams_nothing(self):
        live = FakeLive()
        out, _, _ = self.run_stream([{"SPY": yq(500, open_=False), "VTI": yq(250, open_=False)}], live)
        self.assertEqual(len(out), 1)
        self.assertEqual(live.watched, [])
        self.assertEqual(live.unwatched, 1)

    def test_without_a_key_nothing_touches_the_feed(self):
        live = FakeLive()
        clock = [0.0]
        with mock.patch.object(prices, "quotes", return_value={"SPY": yq(500)}):
            list(prices.quote_stream(["VTI"], lifetime=1, clock=lambda: clock[0],
                                     sleep=lambda s: clock.__setitem__(0, clock[0] + s), live=None))
        self.assertEqual((live.watched, live.unwatched), ([], 0))


class SettingsApiTests(DbCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(finnhub.feed.reset)

    def test_save_checks_the_key_first(self):
        with mock.patch.object(finnhub, "rest_quote", return_value={"c": 1, "pc": 1}) as rq:
            out = investments.api_finnhub_settings(self.c, {}, {"api_key": f" {KEY} "})
        self.assertEqual(out, {"ok": True, "configured": True})
        rq.assert_called_once_with(KEY, "AAPL", mock.ANY)
        self.assertEqual(db.get_setting(self.c, sk.FINNHUB_API_KEY), KEY)

    def test_a_key_finnhub_refuses_is_not_saved(self):
        with mock.patch.object(finnhub, "rest_quote", side_effect=finnhub.KeyRejected("Finnhub doesn't accept that key.")):
            with self.assertRaises(ApiError) as cm:
                investments.api_finnhub_settings(self.c, {}, {"api_key": KEY})
        self.assertEqual(str(cm.exception), "Finnhub doesn't accept that key.")
        self.assertIsNone(db.get_setting(self.c, sk.FINNHUB_API_KEY))
        with self.assertRaises(ApiError):
            investments.api_finnhub_settings(self.c, {}, {"api_key": "nope!"})   # not even asked
        with self.assertRaises(ApiError):
            investments.api_finnhub_settings(self.c, {}, {})

    def test_remove_and_status_and_the_key_stays_private(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(finnhub, "rest_quote", return_value={"c": 1, "pc": 1}):
            saved = investments.api_finnhub_settings(self.c, {}, {"api_key": KEY})
            self.c.commit()
            status = investments.api_finnhub_status(self.c, {}, {})
            everything = state.api_state(self.c, {}, {})
            stored = self.c.execute(select(Setting.value).where(Setting.key == sk.FINNHUB_API_KEY)).scalar()
            gone = investments.api_finnhub_settings(self.c, {}, {"clear": True})
        self.assertTrue(status["configured"])
        self.assertEqual(set(status), {"configured", "active", "connected", "symbols", "limit", "error"})
        self.assertTrue(everything["finnhub_configured"])
        for text in (json.dumps(saved), json.dumps(status), json.dumps(everything, default=str), out.getvalue()):
            self.assertNotIn(KEY, text)
        self.assertNotIn(KEY, stored)                     # encrypted at rest
        self.assertIn(sk.FINNHUB_API_KEY, sk.SECRETS)     # and kept out of backups
        self.assertEqual(gone, {"ok": True, "configured": False})
        self.assertFalse(investments.api_finnhub_status(self.c, {}, {})["configured"])


if __name__ == "__main__":
    unittest.main()
