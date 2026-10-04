"""Daily price history from Yahoo Finance's public chart endpoint (the same source Ghostfolio uses by default).

Yahoo's closes are split-adjusted, so we also keep each ticker's split history to turn them back into the
prices that were actually quoted on the day (needed to value the share counts you really held then)."""
from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, UTC
from typing import TypeGuard

from sqlalchemy import case, func, or_, select, update

from . import db, tls
from .models import Price, PriceMeta, Security

BENCHMARK = "SPY"   # S&P 500
STALE_HOURS = 20


def usable_ticker(t: str | None) -> TypeGuard[str]:
    return t is not None and t != "" and ":" not in t and " " not in t and len(t) <= 12  # skips Plaid cash tickers like "CUR:USD"


def fetch(ticker: str, start: date, end: date) -> tuple[list[tuple[str, float, float]], list[tuple[str, float]], dict]:
    """Returns ([(date, close, adjclose)], [(date, split ratio)], {"type": ETF/MUTUALFUND/..., "name": long name})."""
    base = os.environ.get("RUNWAY_PRICES_URL", "https://query1.finance.yahoo.com/v8/finance/chart")
    p1 = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
    p2 = int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp()) + 86400
    url = f"{base}/{urllib.parse.quote(ticker, safe='')}?period1={p1}&period2={p2}&interval=1d&events=split&includeAdjustedClose=true"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) Runway/0.1", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=tls.ssl_context()) as resp:
        data = json.loads(resp.read().decode())
    result = ((data.get("chart") or {}).get("result") or [None])[0]
    if not result:
        return [], [], {}
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adj = (((result.get("indicators") or {}).get("adjclose") or [{}])[0]).get("adjclose") or []
    closes = quote.get("close") or []
    offset = ((result.get("meta") or {}).get("gmtoffset")) or 0
    rows = []
    for i, ts in enumerate(stamps):
        c = closes[i] if i < len(closes) else None
        if c is None:
            continue
        a = adj[i] if i < len(adj) and adj[i] is not None else c
        d = datetime.fromtimestamp(ts + offset, UTC).date().isoformat()
        rows.append((d, float(c), float(a)))
    splits = []
    for ev in ((result.get("events") or {}).get("splits") or {}).values():
        try:
            ratio = float(ev["numerator"]) / float(ev["denominator"])
            d = datetime.fromtimestamp(int(ev["date"]) + offset, UTC).date().isoformat()
            splits.append((d, ratio))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    meta = result.get("meta") or {}
    return rows, sorted(splits), {"type": meta.get("instrumentType"), "name": meta.get("longName") or meta.get("shortName")}


def refresh(conn, tickers: list[str], start: date, force: bool = False) -> dict:
    """Fetch any tickers whose history is missing or stale. Commits between requests."""
    done: list[str] = []
    failed: list[str] = []
    now = datetime.now()
    for t in sorted(set(x for x in tickers if usable_ticker(x))):
        meta = conn.execute(select(PriceMeta).where(PriceMeta.ticker == t)).fetchone()
        needs_name = meta is not None and meta["ok"] and meta["long_name"] is None   # fetched before names were kept
        if meta and not force and not needs_name and meta["fetched_at"]:
            age = now - datetime.fromisoformat(meta["fetched_at"])
            if age < timedelta(hours=STALE_HOURS if meta["ok"] else 72):
                continue
        conn.commit()
        try:
            rows, splits, info = fetch(t, start, date.today())
            ok = 1 if rows else 0
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:   # the service, not the ticker: try it again next time
                failed.append(t)
                if e.code == 429:   # rate-limited: asking about the rest now only prolongs it
                    break
                continue
            rows, splits, info, ok = [], [], {}, 0
        except (urllib.error.URLError, OSError, http.client.HTTPException):   # no connection: nothing learned about it
            failed.append(t)
            continue
        except ValueError:
            rows, splits, info, ok = [], [], {}, 0
        db.upsert(conn, Price, [{"ticker": t, "date": d, "close": c, "adjclose": a} for d, c, a in rows], key=["ticker", "date"])
        db.upsert(conn, PriceMeta, {"ticker": t, "fetched_at": now.isoformat(timespec="seconds"), "ok": ok,
                                    "splits": json.dumps(splits), "instrument_type": info.get("type"),
                                    "long_name": (info.get("name") or "") if ok else None},
                  key=["ticker"], update=lambda ex: {
                      "fetched_at": ex.fetched_at, "ok": ex.ok,
                      "splits": case((ex.ok == 1, ex.splits), else_=PriceMeta.splits),
                      "instrument_type": func.coalesce(ex.instrument_type, PriceMeta.instrument_type),
                      "long_name": func.coalesce(ex.long_name, PriceMeta.long_name)})
        conn.commit()
        (done if ok else failed).append(t)
        time.sleep(0.25)
    return {"fetched": done, "failed": failed}


def history(conn, ticker: str, start: date) -> tuple[dict[str, float], dict[str, float], list[tuple[str, float]]]:
    """(real close by date, adjusted close by date, splits). Real = split-adjusted close x splits that happened later."""
    rows = conn.execute(select(Price.date, Price.close, Price.adjclose)
                        .where(Price.ticker == ticker, Price.date >= (start - timedelta(days=10)).isoformat())
                        .order_by(Price.date)).fetchall()
    meta = conn.execute(select(PriceMeta.splits).where(PriceMeta.ticker == ticker)).fetchone()
    splits = [tuple(s) for s in json.loads(meta["splits"] or "[]")] if meta else []
    real, adj = {}, {}
    for r in rows:
        factor = 1.0
        for d, ratio in splits:
            if d > r["date"]:
                factor *= ratio
        real[r["date"]] = r["close"] * factor
        adj[r["date"]] = r["adjclose"]
    return real, adj, splits


YAHOO_TYPES = {"EQUITY": "equity", "ETF": "etf", "MUTUALFUND": "mutual fund", "MONEYMARKET": "cash",
               "CRYPTOCURRENCY": "cryptocurrency", "OPTION": "derivative", "BOND": "fixed income"}


def fill_security_types(conn) -> None:
    """SimpleFIN doesn't say what a security is; borrow the price service's classification."""
    for r in conn.execute(select(PriceMeta.ticker, PriceMeta.long_name)
                          .where(PriceMeta.long_name.is_not(None), PriceMeta.long_name != "")).fetchall():
        conn.execute(update(Security).where(
            Security.ticker == r["ticker"],
            or_(Security.name.is_(None), Security.name == "", func.upper(Security.name) == func.upper(Security.ticker)),
        ).values(name=r["long_name"]))
    for r in conn.execute(select(PriceMeta.ticker, PriceMeta.instrument_type).where(PriceMeta.instrument_type.is_not(None))).fetchall():
        t = YAHOO_TYPES.get((r["instrument_type"] or "").upper())
        if t:
            conn.execute(update(Security).where(Security.ticker == r["ticker"], Security.type.is_(None)).values(type=t))
            if t == "cash":
                conn.execute(update(Security).where(Security.ticker == r["ticker"], Security.id.like("sf:%")).values(is_cash=1))


# ------------------------------------------------------------------------------------------------ live quotes

_quote_cache: dict[str, tuple[float, dict]] = {}
QUOTE_TTL = 20  # seconds; the page polls every 30s, so every poll sees fresh numbers without hammering Yahoo


def _quote(ticker: str) -> dict | None:
    base = os.environ.get("RUNWAY_PRICES_URL", "https://query1.finance.yahoo.com/v8/finance/chart")
    url = f"{base}/{urllib.parse.quote(ticker, safe='')}?range=1d&interval=1d"   # safe='': a "/" in a name stays in the name
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) Runway/0.1", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10, context=tls.ssl_context()) as resp:
        data = json.loads(resp.read().decode())
    result = ((data.get("chart") or {}).get("result") or [None])[0]
    if not result:
        return None
    m = result.get("meta") or {}
    price = m.get("regularMarketPrice")
    prev = m.get("previousClose") or m.get("chartPreviousClose")
    if price is None:
        return None
    period = (m.get("currentTradingPeriod") or {}).get("regular") or {}
    return {"price": float(price), "prev_close": float(prev) if prev is not None else None,
            "time": m.get("regularMarketTime"), "open_start": period.get("start"), "open_end": period.get("end"),
            "type": m.get("instrumentType")}


def quotes(tickers: list[str], ttl: float = QUOTE_TTL) -> dict[str, dict]:
    """Latest price and previous close per ticker (Yahoo's quotes are real-time for most US stocks and ETFs;
    mutual funds only change once a day, after the close)."""
    now = time.time()
    want = sorted(set(t for t in tickers if usable_ticker(t)))
    out, todo = {}, []
    for t in want:
        hit = _quote_cache.get(t)
        if hit and now - hit[0] < ttl:
            out[t] = hit[1]
        else:
            todo.append(t)

    def one(t):
        try:
            return t, _quote(t)
        except (urllib.error.URLError, ValueError, OSError):
            return t, None

    if todo:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for t, q in pool.map(one, todo):
                if q:
                    _quote_cache[t] = (now, q)
                    out[t] = q
                elif t in _quote_cache:      # keep showing the last good quote
                    out[t] = _quote_cache[t][1]
    return out


def market_state(q: dict | None, now: float | None = None) -> str:
    """'open' or 'closed', from a quote's trading-period bounds (use a stock or ETF, not a mutual fund)."""
    now = now or time.time()
    if not q or not q.get("open_start") or not q.get("open_end"):
        return "closed"
    return "open" if q["open_start"] <= now < q["open_end"] else "closed"


# ------------------------------------------------------------------------------------------------ streaming

STREAM_SECONDS = 5        # fastest re-quote while the market is open (slower with many tickers, to go easy on Yahoo)
STREAM_LIFETIME = 600     # one stream lasts this long, then the browser reconnects (so no connection is held forever)
CLOSED_RETRY = 300        # with the market closed the stream ends and the browser comes back after this many seconds


def stream_interval(n_tickers: int) -> float:
    return max(STREAM_SECONDS, n_tickers / 4)


LIVE_QUOTE_TTL = 60       # with trades streaming in, Yahoo is only needed for previous close and market hours
LIVE_THROTTLE = 1.0       # trades can come many times a second; the browser gets at most one update a second


def _with_trades(q: dict[str, dict], want: list[str], live) -> dict[str, dict]:
    """Yahoo's quotes with the price and time of each symbol's latest Finnhub trade on top (a trade from before Yahoo's own
    quote, say yesterday's, is ignored). The previous close stays Yahoo's, or Finnhub's own when Yahoo has no quote."""
    out = dict(q)
    for t in want:
        trade = live.latest(t)
        if not trade:
            continue
        price, ms = trade
        base = q.get(t)
        if base is None:
            fq = live.baseline(t)
            if not fq:
                continue
            base = {"prev_close": float(fq["pc"]), "time": None, "open_start": None, "open_end": None, "type": None}
        elif base.get("time") and ms / 1000 < base["time"]:
            continue
        out[t] = {**base, "price": price, "time": int(ms / 1000)}
    return out


def quote_stream(tickers: list[str], lifetime: float = STREAM_LIFETIME, clock=time.monotonic, sleep=time.sleep,
                 live=None, live_key: str | None = None):
    """Prices as they move: first every quote, then only the tickers whose price or quote time changed. Yields a dict
    ({"quotes", "market", "as_of"}) for news, or None when nothing moved (the caller sends a keep-alive). Ends after one
    update when the market is closed, or when `lifetime` seconds have passed.

    With `live` (a finnhub.Feed) and its key, trades from Finnhub replace Yahoo's price for stocks and ETFs as they
    happen, and an update goes out as soon as one arrives (at most one a second) instead of every few seconds. Whenever
    the feed isn't connected the polling below carries on unchanged, so a failure there is never a gap in prices."""
    want = sorted(set(tickers) | {BENCHMARK})
    interval = stream_interval(len(want))
    sent: dict[str, tuple] = {}
    started = clock()
    owner = object()
    seen = live.version() if live else 0
    try:
        while True:
            # the shared cache means two open tabs don't double Yahoo's traffic
            q = quotes(want, ttl=LIVE_QUOTE_TTL if live and live.connected else interval - 1)
            market = market_state(q.get(BENCHMARK))
            if live and live_key and market == "open":
                # mutual funds only price once a day, so they don't need one of the plan's few streamed symbols
                live.watch(owner, [t for t in want if (q.get(t) or {}).get("type") != "MUTUALFUND"], live_key)
                q = _with_trades(q, want, live)
            changed = {t: v for t, v in q.items() if sent.get(t) != (v["price"], v.get("time"))}
            if changed or not sent:
                sent.update({t: (v["price"], v.get("time")) for t, v in changed.items()})
                yield {"quotes": changed, "market": market, "as_of": datetime.now().isoformat(timespec="seconds")}
            else:
                yield None
            last_sent = clock()
            if market != "open" or clock() - started >= lifetime:
                return
            if live and live_key:
                seen = live.wait(seen, interval)   # a trade (or the connection changing) ends the wait early
                gap = LIVE_THROTTLE - (clock() - last_sent)
                if gap > 0:
                    sleep(gap)
            else:
                sleep(interval)
    finally:
        if live:
            live.unwatch(owner)
