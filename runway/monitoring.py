"""Reports to Sentry, only when you ask for them (SENTRY_DSN). Off, nothing leaves the machine.

Runway holds bank access and your transactions, so what's sent carries only what it takes to find a bug or a slow
spot: errors and their stack traces (without the values of variables), request methods and route names, timings, and
the release. Never request bodies, cookies, headers or query strings, nor the values bound to database queries (what's
sent to the AI only on its own spans, with SENTRY_AI_CONTENT below); credentials in addresses (a SimpleFIN access URL has them) and Plaid tokens are blanked wherever
they turn up.

Once SENTRY_DSN is set, everything below is on; turn any of it off with a rate of 0, or =0:

  SENTRY_DSN                          the server's reports (from your Sentry project's Client Keys)
  SENTRY_BROWSER_DSN                  the web app's reports; defaults to SENTRY_DSN (RUNWAY_SENTRY_BROWSER=0 turns them off)
  SENTRY_ENVIRONMENT                  e.g. production (the default) or staging
  SENTRY_TRACES_SAMPLE_RATE           share of requests, syncs and page views to trace for performance, 0 to 1 (the default)
  SENTRY_PROFILE_SESSION_SAMPLE_RATE  share of server runs and browser visits to profile while tracing, 0 to 1 (the default)
  SENTRY_REPLAY_SAMPLE_RATE           share of browser visits to record as a replay (text, inputs and images masked), 0 to 1
                                      (the default)
  SENTRY_REPLAY_ON_ERROR_SAMPLE_RATE  share of visits with an error whose last minute is sent as a replay, 0 to 1 (the default)
  SENTRY_LOGS                         Runway's log lines (and the web app's console warnings and errors) as Sentry Logs
  SENTRY_METRICS                      a few counters and timings: sync durations, new transactions, AI tokens
  SENTRY_AI_CONTENT                   the prompts sent to the AI and its replies, on its spans (Agent Tracing's
                                      Conversations): the merchants, amounts and dates of the transactions it's asked
                                      about, which the AI provider sees anyway. Off, only models, timings and tokens.
  SENTRY_CRONS                        a Cron Monitor for the daily bank sync, so a missed or failed one alerts you
  SENTRY_FEEDBACK                     a "Send feedback" link in Settings
  SENTRY_USER                         who's signed in, as a stable code that doesn't say who (see user_id), so Sentry counts
                                      the people an error or a slow page affects instead of calling everyone anonymous
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import hmac
import json
import os
import re
import sys
import time
import traceback
import urllib.parse
from collections.abc import Iterator
from typing import Any, overload

_enabled = False
_opts: dict[str, Any] = {}   # which of the optional features are on (see init)
_agent: contextvars.ContextVar[str | None] = contextvars.ContextVar("runway_ai_agent", default=None)
_in_request: contextvars.ContextVar[bool] = contextvars.ContextVar("runway_sentry_request", default=False)

# Secrets that can appear in an error's text: user:password@ in an address, and Plaid's tokens.
_USERINFO = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s@]+@", re.I)
_QUERY = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s?#]*)\?[^\s#]*", re.I)
_PLAID_TOKEN = re.compile(r"\b(access|public|link|processor)-(sandbox|development|production)-[0-9a-f-]{8,}", re.I)

# Nothing collected automatically beyond the error and its code: the SDK's own switches, all off (bound query values
# included, which it would otherwise send once this option is given).
_DATA_COLLECTION: Any = {
    "user_info": False, "cookies": {"mode": "off"}, "http_headers": {"request": {"mode": "off"}}, "http_bodies": [],
    "url_query_params": {"mode": "off"}, "graphql": {"document": False, "variables": False},
    "gen_ai": {"inputs": False, "outputs": False}, "database_query_data": False, "queues": False,   # (see ai_content)
    "stack_frame_variables": False, "frame_context_lines": 5,
}


def scrub(text):
    if not isinstance(text, str):
        return text
    text = _USERINFO.sub(r"\1[Filtered]@", text)
    text = _QUERY.sub(r"\1?[Filtered]", text)
    text = _database_values(text)
    return _PLAID_TOKEN.sub("[Filtered]", text)


_DIGITS = re.compile(r"\d{5,}")


@overload
def public_text(text: str) -> str: ...
@overload
def public_text(text: None) -> None: ...
def public_text(text: str | None) -> str | None:
    """What another service said (a bank's message through SimpleFIN or Plaid, an API's error), made safe to keep and
    show: scrub(), and any run of five or more digits blanked, in case a message names an account or card number."""
    if not isinstance(text, str):
        return text
    return _DIGITS.sub("[number]", scrub(text))


def _database_values(text: str) -> str:
    """A database error's text names the row it was writing: SQLAlchemy's "[parameters: ...]", and Postgres's "Failing row
    contains (...)" and "Key (...)=(...) already exists". Blank the values and keep the rest (the SQL helps). Plain string
    scans, a line at a time: a regex here could take quadratic time on text that repeats one of the markers."""
    lines = text.split("\n")
    for n, line in enumerate(lines):
        if (i := line.find("[parameters: ")) != -1:
            lines[n] = line[:i] + "[parameters: [Filtered]]"
        elif (i := line.find("Failing row contains (")) != -1:
            lines[n] = line[:i] + "Failing row contains ([Filtered])."
        elif (i := line.find("Key (")) != -1 and (j := line.find(")=(", i)) != -1:
            end = line.rfind(") ")   # before "already exists", "is not present in table ..." and the like
            lines[n] = line[:j] + ")=([Filtered])" + (line[end + 1:] if end > j else "")
    return "\n".join(lines)


def _path_only(url: str | None) -> str | None:
    """scheme://host/path, without who's signing in or what's asked."""
    if not url:
        return url
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))


def _trim_request(event) -> None:
    req = event.get("request")
    if req:
        event["request"] = {"method": req.get("method"), "url": _path_only(req.get("url"))}
    user = event.pop("user", None)
    if isinstance(user, dict) and user.get("id"):   # only the code set_user gives: never a name, email or address
        event["user"] = {"id": str(user["id"])}
    event.pop("extra", None)


def _before_send(event, _hint):
    _trim_request(event)
    event["message"] = scrub(event.get("message"))
    if isinstance(event.get("logentry"), dict):
        event["logentry"] = {k: scrub(v) if k in ("message", "formatted") else v for k, v in event["logentry"].items()
                             if k != "params"}
    for exc in (event.get("exception") or {}).get("values") or []:
        exc["value"] = scrub(exc.get("value"))
        for frame in (exc.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
    return event


def _clean_span_data(data) -> None:
    if not isinstance(data, dict):
        return
    for k in ("http.query", "http.fragment", "url.query", "url.fragment", "db.params", "db.sql.bindings"):
        data.pop(k, None)
    for k in ("url", "http.url", "url.full"):
        if isinstance(data.get(k), str):
            data[k] = _path_only(data[k])


def _before_send_transaction(event, _hint):
    """A trace: the same trims as an error, and every span's address without its query string or credentials."""
    _trim_request(event)
    _clean_span_data(((event.get("contexts") or {}).get("trace") or {}).get("data"))
    for span in event.get("spans") or []:
        span["description"] = scrub(span.get("description"))
        _clean_span_data(span.get("data"))
    return event


def _before_breadcrumb(crumb, _hint):
    if crumb.get("category") == "query":   # a database query: its SQL helps, the values bound to it are your data
        crumb.pop("data", None)
    data = crumb.get("data")
    if isinstance(data, dict):
        if "url" in data:
            data["url"] = _path_only(data["url"])
        for k in ("http.query", "http.fragment"):
            data.pop(k, None)
    crumb["message"] = scrub(crumb.get("message"))
    return crumb


def _before_send_log(entry, _hint):
    entry["body"] = scrub(entry.get("body"))
    attrs = entry.get("attributes")
    if isinstance(attrs, dict):
        for k, v in attrs.items():
            if isinstance(v, str):
                attrs[k] = scrub(v)
    return entry


def _rate(name: str) -> float:
    """A share from 0 to 1: all (1) when it's not set, none when it's not a number."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return 1.0
    try:
        return min(1.0, max(0.0, float(raw)))
    except ValueError:
        return 0.0


def _on(name: str) -> bool:
    """On unless it's set to something else than 1, true, yes or on (see the top of this file)."""
    raw = (os.environ.get(name) or "").strip().lower()
    return not raw or raw in ("1", "true", "yes", "on")


def _profiles(traces: float) -> float:
    return _rate("SENTRY_PROFILE_SESSION_SAMPLE_RATE") if traces else 0.0


def init() -> bool:
    """Start reporting if SENTRY_DSN is set. Returns whether it's on."""
    global _enabled, _opts
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        return False
    import logging

    import sentry_sdk   # loaded only when reporting is on, so it costs nothing otherwise
    from sentry_sdk.integrations.logging import LoggingIntegration
    traces = _rate("SENTRY_TRACES_SAMPLE_RATE")
    _opts = {"traces": traces, "profiles": _profiles(traces),
             "logs": _on("SENTRY_LOGS"), "metrics": _on("SENTRY_METRICS"), "crons": _on("SENTRY_CRONS"),
             "ai_content": _on("SENTRY_AI_CONTENT")}
    sentry_sdk.init(
        dsn=dsn, release=os.environ.get("RUNWAY_VERSION") or "dev",
        environment=os.environ.get("SENTRY_ENVIRONMENT") or "production",
        server_name="runway", data_collection=_DATA_COLLECTION, max_request_body_size="never",
        traces_sample_rate=traces,
        trace_propagation_targets=[],   # never add trace headers to calls to banks, Plaid or the AI
        # Profiles hold function names and timings, no values. "trace": the profiler runs while a sampled trace does.
        profile_session_sample_rate=_opts["profiles"], profile_lifecycle="trace",
        enable_logs=_opts["logs"], enable_metrics=_opts["metrics"],
        # Libraries' warnings become Sentry Logs too (with SENTRY_LOGS); their errors are reported as before.
        integrations=[LoggingIntegration(sentry_logs_level=logging.WARNING)],
        before_send=_before_send, before_send_transaction=_before_send_transaction,
        before_breadcrumb=_before_breadcrumb, before_send_log=_before_send_log,
    )
    _enabled = True
    on = [name for name, v in (("tracing", traces), ("profiling", _opts["profiles"]), ("logs", _opts["logs"]),
                               ("metrics", _opts["metrics"]), ("crons", _opts["crons"]),
                               ("AI prompts and replies", _opts["ai_content"])) if v]
    log(f"Error reports go to Sentry (SENTRY_DSN is set){'; also ' + ', '.join(on) if on else ''}.")
    return True


def enabled() -> bool:
    return _enabled


def tracing() -> bool:
    return _enabled and bool(_opts.get("traces"))


def report(e: BaseException | None = None, **tags) -> None:
    """Log an error that was caught (the current one, or `e`), and send it to Sentry when that's on."""
    # Scrubbed like a Sentry report: a database error's text names the row it was writing, so the local log gets the
    # same treatment as the remote one.
    text = "".join(traceback.format_exception(*sys.exc_info()) if e is None else traceback.format_exception(type(e), e, e.__traceback__))
    print(scrub(text), file=sys.stderr, end="", flush=True)
    if not _enabled:
        return
    import sentry_sdk   # loaded only when reporting is on (see init)
    with sentry_sdk.new_scope() as scope:
        for k, v in tags.items():
            scope.set_tag(k, v)
        sentry_sdk.capture_exception(e)


# ------------------------------------------------------------------------------------------------ logs and metrics

def log(message: str, level: str = "info", *, remote: str | None = None, stderr: bool = False, **attrs) -> None:
    """Print a line to Runway's log, and send it to Sentry Logs when those are on: `remote` instead of the line when the
    line itself holds more than Sentry should see."""
    print(message, file=sys.stderr if stderr else sys.stdout, flush=True)
    send_log(remote if remote is not None else message, level, **attrs)


def send_log(message: str, level: str = "info", **attrs) -> None:
    """A Sentry Log only (with SENTRY_LOGS), for a line that's printed differently or not at all."""
    if not (_enabled and _opts.get("logs")):
        return
    from sentry_sdk import logger
    send = {"debug": logger.debug, "info": logger.info, "warning": logger.warning, "error": logger.error}.get(level, logger.info)
    send(scrub(message), attributes=attrs)   # no parameters: the message is sent as it is, never formatted


def metric(kind: str, name: str, value: float, unit: str | None = None, /, **attrs) -> None:
    """A counter ("count"), "gauge" or "distribution" (with SENTRY_METRICS). Names start runway.; attributes are labels,
    never amounts or names."""
    if not (_enabled and _opts.get("metrics")):
        return
    from sentry_sdk import metrics
    getattr(metrics, kind)(name, value, unit=unit, attributes=attrs or None)


# ------------------------------------------------------------------------------------------------ tracing

@contextlib.contextmanager
def request(method: str, name: str, headers) -> Iterator[Any]:
    """One HTTP request, kept apart from others (its breadcrumbs, its tags) and, when tracing, traced as `method name`
    (the route, like GET /api/transactions/{id}/category), continuing the web app's trace if it sent one. Yields the
    transaction (set its status with set_http_status), or None."""
    if not _enabled:
        yield None
        return
    import sentry_sdk
    from sentry_sdk.sessions import track_session
    with sentry_sdk.isolation_scope() as scope, track_session(scope, session_mode="request"):
        scope.clear_breadcrumbs()
        in_request = _in_request.set(True)   # set_user may name who's asking, on this request's scope only
        try:
            if not _opts.get("traces"):
                yield None
                return
            incoming = {k: headers.get(k) for k in ("sentry-trace", "baggage") if headers.get(k)}
            tx = sentry_sdk.continue_trace(incoming, op="http.server", name=f"{method} {name}", source="route",
                                           origin="manual")
            tx.set_data("http.request.method", method)
            with sentry_sdk.start_transaction(tx):
                yield tx
        finally:
            _in_request.reset(in_request)


def user_id(user: dict | None) -> str | None:
    """Who's signed in, as Sentry sees them (with SENTRY_USER): a code made from their sign-in id and Runway's key, the
    same each time but useless for finding out who it is. Without OIDC (everyone is "local"), "local"."""
    if not user or not _on("SENTRY_USER"):
        return None
    if user.get("local"):
        return "local"
    if not user.get("sub"):
        return None
    from . import secretbox   # (it imports this module)
    return hmac.new(secretbox.derived_key("sentry-user"), str(user["sub"]).encode(), hashlib.sha256).hexdigest()[:16]


def set_user(user: dict | None) -> None:
    """Put who's asking (user_id) on this request's reports, traces and profiles. Only inside request(): anywhere else
    the scope is shared, and the next report would carry someone else."""
    if not (_enabled and _in_request.get()) or not (uid := user_id(user)):
        return
    import sentry_sdk
    sentry_sdk.get_isolation_scope().set_user({"id": uid})


@contextlib.contextmanager
def task(name: str, op: str = "task") -> Iterator[None]:
    """Work in the background (a sync): its own trace when tracing, and its duration and outcome as a metric."""
    started, outcome = time.monotonic(), "ok"
    try:
        if not tracing():
            yield
        else:
            import sentry_sdk
            with sentry_sdk.isolation_scope(), sentry_sdk.start_transaction(op=op, name=name, source="task"):
                yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        metric("distribution", "runway.task.duration", time.monotonic() - started, "second", task=name, outcome=outcome)


@contextlib.contextmanager
def span(op: str, name: str, **data) -> Iterator[Any]:
    """A step inside the current trace (None when tracing is off)."""
    if not tracing():
        yield None
        return
    import sentry_sdk
    with sentry_sdk.start_span(op=op, name=name) as s:
        for k, v in data.items():
            s.set_data(k, v)
        yield s


def trace_meta() -> str:
    """<meta> tags that let the web app's page-load trace continue this request's (see browserTracingIntegration)."""
    if not tracing():
        return ""
    import html

    import sentry_sdk
    parent, baggage = sentry_sdk.get_traceparent(), sentry_sdk.get_baggage()
    if not parent:
        return ""
    return (f'<meta name="sentry-trace" content="{html.escape(parent)}">'
            + (f'<meta name="baggage" content="{html.escape(baggage)}">' if baggage else ""))


# AI calls (Sentry's Agent Tracing): each of Runway's AI tasks is an agent, each request to the model a chat inside it.
# The model, timings and token counts are always there; the prompt and reply only with SENTRY_AI_CONTENT.

@contextlib.contextmanager
def ai_agent(name: str, pipeline: str | None = None) -> Iterator[None]:
    """One run of an AI task (like categorizing a sync's new transactions): an invoke_agent span around its requests to
    the model, which are grouped as one Conversation in Sentry. Works as a decorator too."""
    token = _agent.set(name)
    try:
        if not tracing():
            yield
            return
        import uuid

        import sentry_sdk
        with sentry_sdk.new_scope() as scope:
            # The run is the conversation: its batches are its turns. A fresh id each time (never the trace's).
            scope.set_conversation_id(f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:12]}")
            data = {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": name}
            if pipeline:
                data["gen_ai.pipeline.name"] = pipeline
            with span("gen_ai.invoke_agent", f"invoke_agent {name}", **data):
                yield
    finally:
        _agent.reset(token)


def ai_content() -> bool:
    return _enabled and bool(_opts.get("ai_content"))


@contextlib.contextmanager
def ai_call(model: str, prompt: str | None = None, provider: str = "openrouter", **request) -> Iterator[Any]:
    """One request to a model (a chat span): `request` holds its settings (max_tokens, temperature). The prompt is only
    recorded with SENTRY_AI_CONTENT."""
    data: dict[str, Any] = {"gen_ai.operation.name": "chat", "gen_ai.request.model": model, "gen_ai.provider.name": provider,
                            **{f"gen_ai.request.{k}": v for k, v in request.items()}}
    if _agent.get():
        data["gen_ai.agent.name"] = _agent.get()
    if prompt is not None and ai_content():
        data["gen_ai.input.messages"] = json.dumps([{"role": "user", "parts": [{"type": "text", "content": prompt}]}])
    with span("gen_ai.chat", f"chat {model}", **data) as s:
        try:
            yield s
        except BaseException as e:
            if s is not None:
                s.set_data("error.type", type(e).__name__)
            raise


def ai_result(s, reply: dict, model: str, text: str | None = None) -> None:
    """Record what an OpenAI-style chat reply says about itself: the model that answered, why it stopped and the tokens
    it took (and its text, with SENTRY_AI_CONTENT)."""
    reply = reply if isinstance(reply, dict) else {}
    raw = reply.get("usage")
    usage: dict[str, Any] = raw if isinstance(raw, dict) else {}
    details = usage.get("prompt_tokens_details")
    tokens = {"input": usage.get("prompt_tokens"), "output": usage.get("completion_tokens")}
    if s is not None:
        if isinstance(reply.get("model"), str):
            s.set_data("gen_ai.response.model", reply["model"])
        if isinstance(reply.get("id"), str):
            s.set_data("gen_ai.response.id", reply["id"])
        reasons = [c.get("finish_reason") for c in reply.get("choices") or [] if isinstance(c, dict) and c.get("finish_reason")]
        if reasons:
            s.set_data("gen_ai.response.finish_reasons", json.dumps(reasons))
        for kind, n in tokens.items():
            if isinstance(n, int):
                s.set_data(f"gen_ai.usage.{kind}_tokens", n)
        if isinstance(usage.get("total_tokens"), int):
            s.set_data("gen_ai.usage.total_tokens", usage["total_tokens"])
        cached = details.get("cached_tokens") if isinstance(details, dict) else None
        if isinstance(cached, int):
            s.set_data("gen_ai.usage.cache_read.input_tokens", cached)
        if text is not None and ai_content():
            s.set_data("gen_ai.output.messages", json.dumps([{"role": "assistant", "parts": [{"type": "text", "content": text}]}]))
    for kind, n in tokens.items():
        if isinstance(n, int):
            metric("count", "runway.ai.tokens", n, None, model=model, kind=kind, agent=_agent.get() or "")


# MCP calls (Sentry's MCP view): which method and tool, and whether it failed; never the arguments or the answer.

@contextlib.contextmanager
def mcp_call(msg) -> Iterator[Any]:
    msg = msg if isinstance(msg, dict) else {}
    method, params = msg.get("method"), msg.get("params")
    params = params if isinstance(params, dict) else {}
    tool = params.get("name") if method == "tools/call" and isinstance(params.get("name"), str) else None
    data = {"mcp.method.name": str(method), "mcp.transport": "http", "network.transport": "tcp"}
    if tool:
        data["mcp.tool.name"] = tool
    if msg.get("id") is not None:
        data["mcp.request.id"] = str(msg["id"])
    with span("mcp.server", f"{method} {tool}" if tool else str(method), **data) as s:
        yield s


def mcp_result(s, reply) -> None:
    if s is not None and isinstance(reply, dict):
        result = reply.get("result")
        s.set_data("mcp.tool.result.is_error", bool(reply.get("error") or (isinstance(result, dict) and result.get("isError"))))


# ------------------------------------------------------------------------------------------------ crons

_IANA_ZONE = re.compile(r"(UTC|[A-Za-z]+(?:/[A-Za-z0-9_+-]+)+)")


def local_timezone() -> str | None:
    """The IANA name of the zone Runway's clock runs in (the daily sync's hour is local time): TZ if it's a name like
    America/Chicago, else the system's (/etc/localtime, /etc/timezone). None when it can't be told, as with a POSIX
    rule in TZ (EST5EDT, CST6CDT,M3.2.0,M11.1.0)."""
    tz = (os.environ.get("TZ") or "").strip().lstrip(":")
    if tz:
        return tz if _IANA_ZONE.fullmatch(tz) else None
    target = os.path.realpath("/etc/localtime")
    if "/zoneinfo/" in target:
        name = target.split("/zoneinfo/", 1)[1]
        return name if _IANA_ZONE.fullmatch(name) else None
    with contextlib.suppress(OSError):
        with open("/etc/timezone") as f:
            name = f.read().strip()
        return name if _IANA_ZONE.fullmatch(name) else None
    return None


def cron_start(slug: str, schedule: str | None, margin_minutes: int = 30, max_runtime_minutes: int = 60) -> dict | None:
    """A Cron Monitor check-in (with SENTRY_CRONS) that a job has started; finish it with cron_finish. With a schedule,
    Sentry creates the monitor on the first one, in Runway's time zone, and alerts when one is missed or fails. Without
    one (the job isn't on a schedule here), or when the zone can't be told (the schedule would be off by hours), the
    check-in carries no monitor_config: it goes to a monitor set up in Sentry with the same slug, and makes none."""
    if not (_enabled and _opts.get("crons")):
        return None
    from sentry_sdk.crons import capture_checkin
    zone = local_timezone() if schedule else None
    config: Any = {"schedule": {"type": "crontab", "value": schedule}, "timezone": zone,
                   "checkin_margin": margin_minutes, "max_runtime": max_runtime_minutes} if zone else None
    check = {"slug": slug, "config": config, "started": time.monotonic()}
    check["id"] = capture_checkin(slug, status="in_progress", monitor_config=config)
    return check


def cron_finish(check: dict | None, ok: bool) -> None:
    if check is None:
        return
    from sentry_sdk.crons import capture_checkin
    capture_checkin(check["slug"], check["id"], "ok" if ok else "error", time.monotonic() - check["started"], check["config"])


# ------------------------------------------------------------------------------------------------ the web app

def browser_dsn() -> str | None:
    if os.environ.get("RUNWAY_SENTRY_BROWSER") == "0":
        return None
    return (os.environ.get("SENTRY_BROWSER_DSN") or os.environ.get("SENTRY_DSN") or "").strip() or None


def browser_config(user: dict | None = None) -> dict | None:
    """What the web app needs to send its own reports (a DSN is meant to be public), or None when that's off. `user`:
    who's signed in, sent as user_id's code."""
    dsn = browser_dsn()
    if not dsn or not browser_origin():   # the page may only send to an https address it's been told about
        return None
    traces = _rate("SENTRY_TRACES_SAMPLE_RATE")
    return {"dsn": dsn, "environment": os.environ.get("SENTRY_ENVIRONMENT") or "production",
            "release": os.environ.get("RUNWAY_VERSION") or "dev",
            "traces": traces, "profiles": _profiles(traces),
            "replays": _rate("SENTRY_REPLAY_SAMPLE_RATE"), "replays_on_error": _rate("SENTRY_REPLAY_ON_ERROR_SAMPLE_RATE"),
            "logs": _on("SENTRY_LOGS"), "feedback": _on("SENTRY_FEEDBACK"), "user_id": user_id(user)}


def browser_profiling() -> bool:
    """Whether the page needs Document-Policy: js-profiling (the browser's profiler is only there with it)."""
    cfg = browser_config()
    return bool(cfg and cfg["profiles"])


def browser_origin() -> str | None:
    """Where the web app's reports go, for the Content-Security-Policy's connect-src."""
    dsn = browser_dsn()
    if not dsn:
        return None
    parts = urllib.parse.urlsplit(dsn)
    if parts.scheme != "https" or not parts.hostname or not re.fullmatch(r"[a-z0-9.-]+", parts.hostname, re.I):
        return None
    return f"https://{parts.hostname}" + (f":{parts.port}" if parts.port else "")
