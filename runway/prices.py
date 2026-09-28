"""Daily price history from Yahoo Finance's public chart endpoint (the same source Ghostfolio uses by default).

Yahoo's closes are split-adjusted, so we also keep each ticker's split history to turn them back into the
prices that were actually quoted on the day (needed to value the share counts you really held then)."""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone


BENCHMARK = "SPY"   # S&P 500
STALE_HOURS = 20


def _ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        import certifi  # type: ignore

        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


def usable_ticker(t: str | None) -> bool:
    return bool(t) and ":" not in t and " " not in t and len(t) <= 12  # skips Plaid cash tickers like "CUR:USD"


def fetch(ticker: str, start: date, end: date) -> tuple[list[tuple[str, float, float]], list[tuple[str, float]], dict]:
    """Returns ([(date, close, adjclose)], [(date, split ratio)], {"type": ETF/MUTUALFUND/..., "name": long name})."""
    base = os.environ.get("RUNWAY_PRICES_URL", "https://query1.finance.yahoo.com/v8/finance/chart")
    p1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    p2 = int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp()) + 86400
    url = f"{base}/{urllib.parse.quote(ticker)}?period1={p1}&period2={p2}&interval=1d&events=split&includeAdjustedClose=true"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) Runway/0.1", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=_ctx()) as resp:
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
        d = datetime.fromtimestamp(ts + offset, timezone.utc).date().isoformat()
        rows.append((d, float(c), float(a)))
    splits = []
    for ev in ((result.get("events") or {}).get("splits") or {}).values():
        try:
            ratio = float(ev["numerator"]) / float(ev["denominator"])
            d = datetime.fromtimestamp(int(ev["date"]) + offset, timezone.utc).date().isoformat()
            splits.append((d, ratio))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    meta = result.get("meta") or {}
    return rows, sorted(splits), {"type": meta.get("instrumentType"), "name": meta.get("longName") or meta.get("shortName")}


def refresh(conn, tickers: list[str], start: date, force: bool = False) -> dict:
    """Fetch any tickers whose history is missing or stale. Commits between requests."""
    done, failed = [], []
    now = datetime.now()
    for t in sorted(set(x for x in tickers if usable_ticker(x))):
        meta = conn.execute("SELECT * FROM price_meta WHERE ticker=?", (t,)).fetchone()
        needs_name = meta is not None and meta["ok"] and meta["long_name"] is None   # fetched before names were kept
        if meta and not force and not needs_name and meta["fetched_at"]:
            age = now - datetime.fromisoformat(meta["fetched_at"])
            if age < timedelta(hours=STALE_HOURS if meta["ok"] else 72):
                continue
        conn.commit()
        try:
            rows, splits, info = fetch(t, start, date.today())
            ok = 1 if rows else 0
        except (urllib.error.URLError, ValueError, OSError):
            rows, splits, info, ok = [], [], {}, 0
        conn.executemany("INSERT INTO prices(ticker, date, close, adjclose) VALUES (?,?,?,?) "
                         "ON CONFLICT(ticker, date) DO UPDATE SET close=excluded.close, adjclose=excluded.adjclose", [(t, *r) for r in rows])
        conn.execute(
            "INSERT INTO price_meta(ticker, fetched_at, ok, splits, instrument_type, long_name) VALUES (?,?,?,?,?,?) ON CONFLICT(ticker) DO UPDATE SET "
            "fetched_at=excluded.fetched_at, ok=excluded.ok, splits=CASE WHEN excluded.ok=1 THEN excluded.splits ELSE price_meta.splits END, "
            "instrument_type=COALESCE(excluded.instrument_type, price_meta.instrument_type), long_name=COALESCE(excluded.long_name, price_meta.long_name)",
            (t, now.isoformat(timespec="seconds"), ok, json.dumps(splits), info.get("type"), (info.get("name") or "") if ok else None),
        )
        conn.commit()
        (done if ok else failed).append(t)
        time.sleep(0.25)
    return {"fetched": done, "failed": failed}


def history(conn, ticker: str, start: date) -> tuple[dict[str, float], dict[str, float], list[tuple[str, float]]]:
    """(real close by date, adjusted close by date, splits). Real = split-adjusted close x splits that happened later."""
    rows = conn.execute("SELECT date, close, adjclose FROM prices WHERE ticker=? AND date>=? ORDER BY date",
                        (ticker, (start - timedelta(days=10)).isoformat())).fetchall()
    meta = conn.execute("SELECT splits FROM price_meta WHERE ticker=?", (ticker,)).fetchone()
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
    for r in conn.execute("SELECT ticker, long_name FROM price_meta WHERE long_name IS NOT NULL AND long_name<>''").fetchall():
        conn.execute("UPDATE securities SET name=? WHERE ticker=? AND (name IS NULL OR name='' OR upper(name)=upper(ticker))",
                     (r["long_name"], r["ticker"]))
    for r in conn.execute("SELECT ticker, instrument_type FROM price_meta WHERE instrument_type IS NOT NULL").fetchall():
        t = YAHOO_TYPES.get((r["instrument_type"] or "").upper())
        if t:
            conn.execute("UPDATE securities SET type=? WHERE ticker=? AND type IS NULL", (t, r["ticker"]))
            if t == "cash":
                conn.execute("UPDATE securities SET is_cash=1 WHERE ticker=? AND id LIKE 'sf:%'", (r["ticker"],))


# ------------------------------------------------------------------------------------------------ live quotes

_quote_cache: dict[str, tuple[float, dict]] = {}
QUOTE_TTL = 20  # seconds; the page polls every 30s, so every poll sees fresh numbers without hammering Yahoo


def _quote(ticker: str) -> dict | None:
    base = os.environ.get("RUNWAY_PRICES_URL", "https://query1.finance.yahoo.com/v8/finance/chart")
    url = f"{base}/{urllib.parse.quote(ticker)}?range=1d&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) Runway/0.1", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10, context=_ctx()) as resp:
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
    from concurrent.futures import ThreadPoolExecutor
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


def quote_stream(tickers: list[str], lifetime: float = STREAM_LIFETIME, clock=time.monotonic, sleep=time.sleep):
    """Prices as they move: first every quote, then only the tickers whose price or quote time changed. Yields a dict
    ({"quotes", "market", "as_of"}) for news, or None when nothing moved (the caller sends a keep-alive). Ends after one
    update when the market is closed, or when `lifetime` seconds have passed."""
    want = sorted(set(tickers) | {BENCHMARK})
    interval = stream_interval(len(want))
    sent: dict[str, tuple] = {}
    started = clock()
    while True:
        q = quotes(want, ttl=interval - 1)   # the shared cache means two open tabs don't double Yahoo's traffic
        market = market_state(q.get(BENCHMARK))
        changed = {t: v for t, v in q.items() if sent.get(t) != (v["price"], v.get("time"))}
        if changed or not sent:
            sent.update({t: (v["price"], v.get("time")) for t, v in changed.items()})
            yield {"quotes": changed, "market": market, "as_of": datetime.now().isoformat(timespec="seconds")}
        else:
            yield None
        if market != "open" or clock() - started >= lifetime:
            return
        sleep(interval)
