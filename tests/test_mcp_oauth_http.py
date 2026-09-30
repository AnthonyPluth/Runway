"""OAuth for the MCP endpoint over HTTP: the metadata, registration, the consent page, tokens, revocation and /mcp
itself, against a real Runway (runway/server/handler.py). The functions underneath are tested in tests/test_mcp_oauth.py."""
import base64
import hashlib
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from http.server import HTTPServer
from unittest import mock

from runway import db, mcp_access, mcp_oauth, mcp_server, oidc, server
from runway.server import common
from tests.shared import forget_oauth, hold_mcp_switch, tag
from tests.test_server import NoRedirect, Provider

VERIFIER = "correct-horse-battery-staple-" + "x" * 30
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
CALLBACK = "http://127.0.0.1:43210/callback"


class Reply:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    @property
    def json(self):
        return json.loads(self.body or b"null")

    @property
    def location(self):
        return self.headers.get("Location")

    def query(self):
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.location).query))

    def cookie(self, name):
        for h in self.headers.get_all("Set-Cookie") or []:
            c = SimpleCookie()
            c.load(h)
            if name in c:
                return c[name]
        return None


class OAuthServer(unittest.TestCase):
    """A real Runway on a temporary database, with no sign-in (so you're "signed in" on this computer). On Postgres the
    database is shared with test modules running alongside (tests/shared.py): each test removes only what it made, and
    holds the churning switch."""
    env: dict = {}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.saved = {k: os.environ.get(k) for k in ("RUNWAY_PUBLIC_URL", "OIDC_ISSUER", "RUNWAY_DATA", *cls.env)}
        for k in ("RUNWAY_PUBLIC_URL", "OIDC_ISSUER"):
            os.environ.pop(k, None)
        os.environ["RUNWAY_DATA"] = cls.tmp.name
        os.environ.update(cls.env)
        db.init()
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"
        cls.iss = cls.base
        cls.resource = cls.base + "/mcp"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()
        for k, v in cls.saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def setUp(self):
        hold_mcp_switch(self)
        self.tag = tag()
        self.owner = "Alex " + self.tag                                           # the churning cards this test makes
        self.card = {"owner": self.owner, "issuer": "chase", "product": "Sapphire", "opened_on": "2025-01-15"}
        self.clients: list[str] = []
        self.addCleanup(self.forget)

    def forget(self):
        with db.session() as conn:
            forget_oauth(conn, self.clients)
            conn.execute("DELETE FROM churn_cards WHERE owner=?", (self.owner,))
            mcp_access.set_allow_writes(conn, False)

    def mine(self, sql, *params):
        """The first column of this SQL's first row (put {mine} in it for "this test's clients")."""
        marks = ", ".join("?" * len(self.clients)) or "NULL"
        with db.session() as conn:
            row = conn.execute(sql.format(mine=f"({marks})"), (*self.clients, *params)).fetchone()
        return row[0] if row else None

    def http(self, method, path, body=None, headers=None, cookies=None):
        h = dict(headers or {})
        if cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        r = urllib.request.Request(self.base + path if path.startswith("/") else path, method=method, headers=h, data=body)
        try:
            resp = urllib.request.build_opener(NoRedirect).open(r, timeout=20)
        except urllib.error.HTTPError as e:
            resp = e
        with resp:
            return Reply(resp.status if hasattr(resp, "status") else resp.code, resp.headers, resp.read())

    def form(self, path, fields, headers=None, cookies=None):
        return self.http("POST", path, urllib.parse.urlencode(fields).encode(),
                         {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}, cookies)

    def register(self, **meta):
        body = {"client_name": "Claude Code", "redirect_uris": [CALLBACK], **meta}
        r = self.http("POST", "/oauth/register", json.dumps(body).encode(), {"Content-Type": "application/json"})
        if r.status == 201:
            self.clients.append(r.json["client_id"])
        return r

    def client(self, **meta):
        r = self.register(**meta)
        self.assertEqual(r.status, 201, r.body)
        return r.json

    def authorize(self, client, cookies=None, **over):
        q = {"response_type": "code", "client_id": client["client_id"], "redirect_uri": client["redirect_uris"][0],
             "code_challenge": CHALLENGE, "code_challenge_method": "S256", "state": "xyz/+&=", "resource": self.resource, **over}
        return self.http("GET", "/oauth/authorize?" + urllib.parse.urlencode({k: v for k, v in q.items() if v is not None}),
                         cookies=cookies)

    def consent_token(self, page):
        body = page.body.decode()
        start = body.index('name="consent" value="') + len('name="consent" value="')
        return body[start:body.index('"', start)]

    def answer(self, page, decision="allow", churning=False, cookies=None, headers=None, token=None):
        fields = {"consent": token if token is not None else self.consent_token(page), "decision": decision,
                  **({"churning": "1"} if churning else {})}
        ck = {"runway_consent": page.cookie("runway_consent").value} if cookies is None else cookies
        return self.form("/oauth/authorize", fields, headers, {**ck, **getattr(self, "session", {})})

    def code(self, client, scope="read", churning=False):
        page = self.authorize(client, scope=scope, cookies=getattr(self, "session", None))
        self.assertEqual(page.status, 200, page.body)
        back = self.answer(page, churning=churning)
        self.assertEqual(back.status, 302, back.body)
        return back.query()["code"]

    def exchange(self, client, code, **over):
        fields = {"grant_type": "authorization_code", "code": code, "redirect_uri": client["redirect_uris"][0],
                  "code_verifier": VERIFIER, "client_id": client["client_id"], **over}
        return self.form("/oauth/token", {k: v for k, v in fields.items() if v is not None})

    def tokens(self, client=None, scope="read", churning=False):
        client = client or self.client()
        r = self.exchange(client, self.code(client, scope, churning))
        self.assertEqual(r.status, 200, r.body)
        return r.json

    def rpc(self, token, method, params=None, mid=1):
        msg = {"jsonrpc": "2.0", "id": mid, "method": method, **({"params": params} if params is not None else {})}
        return self.http("POST", "/mcp", json.dumps(msg).encode(),
                         {"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})

    def tool_names(self, token):
        r = self.rpc(token, "tools/list")
        self.assertEqual(r.status, 200, r.body)
        return {t["name"] for t in r.json["result"]["tools"]}

    def call(self, token, name, args=None):
        r = self.rpc(token, "tools/call", {"name": name, "arguments": args or {}})
        self.assertEqual(r.status, 200, r.body)
        return r.json["result"]

    def switch(self, on):
        with db.session() as conn:
            mcp_access.set_allow_writes(conn, on)


class MetadataTests(OAuthServer):
    def test_the_metadata(self):
        for path in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"):
            with self.subTest(path=path):
                r = self.http("GET", path)
                self.assertEqual(r.status, 200)
                self.assertEqual(r.json, {"resource": self.resource, "authorization_servers": [self.iss],
                                          "scopes_supported": ["read", "churning:write"], "bearer_methods_supported": ["header"]})
                self.assertIsNone(r.headers.get("Access-Control-Allow-Origin"))   # no CORS
        m = self.http("GET", "/.well-known/oauth-authorization-server").json
        self.assertEqual(m, mcp_oauth.authorization_server_metadata(self.iss))
        self.assertEqual(m["token_endpoint"], self.iss + "/oauth/token")
        for path in ("/.well-known/openid-configuration", "/.well-known/whatever", "/oauth/nothing"):
            with self.subTest(path=path):
                self.assertEqual(self.http("GET", path).status, 404)
        self.assertEqual(self.http("POST", "/.well-known/oauth-authorization-server", b"").status, 405)

    def test_the_issuer_is_runway_public_url_never_the_host(self):
        with mock.patch.dict(os.environ, {"RUNWAY_PUBLIC_URL": "https://runway.example.com"}):
            m = self.http("GET", "/.well-known/oauth-authorization-server").json
            self.assertEqual(m["issuer"], "https://runway.example.com")
            r = self.http("GET", "/.well-known/oauth-protected-resource/mcp", headers={"X-Forwarded-Host": "evil.example"})
            self.assertEqual(r.json["resource"], "https://runway.example.com/mcp")

    def test_without_it_an_internet_address_gets_no_oauth(self):
        common.EXTRA_HOSTS.add("runway.example.com")
        try:
            host = {"Host": "runway.example.com"}
            for path in ("/.well-known/oauth-authorization-server", "/.well-known/oauth-protected-resource/mcp"):
                r = self.http("GET", path, headers=host)
                self.assertEqual(r.status, 404)
                self.assertIn("RUNWAY_PUBLIC_URL", r.json["error"])
            self.assertEqual(self.register().status, 201)                                     # on the home address, fine
            r = self.http("POST", "/oauth/register", b"{}", {"Content-Type": "application/json", **host})
            self.assertEqual(r.status, 404)
            page = self.http("GET", "/oauth/authorize?client_id=x", headers=host)
            self.assertEqual(page.status, 404)
            self.assertIn(b"RUNWAY_PUBLIC_URL", page.body)
            r = self.rpc(None, "ping")
            self.assertIn("resource_metadata", r.headers["WWW-Authenticate"])
            r = self.http("POST", "/mcp", b"{}", {"Content-Type": "application/json", **host})
            self.assertEqual((r.status, r.headers["WWW-Authenticate"]), (401, 'Bearer realm="Runway"'))
        finally:
            common.EXTRA_HOSTS.discard("runway.example.com")

    def test_an_unknown_host_is_refused_first(self):
        self.assertEqual(self.http("GET", "/.well-known/oauth-authorization-server", headers={"Host": "evil.example"}).status, 403)
        self.assertEqual(self.http("POST", "/oauth/token", b"", headers={"Host": "evil.example"}).status, 403)


class RegistrationTests(OAuthServer):
    def test_register(self):
        r = self.register()
        self.assertEqual(r.status, 201)
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertTrue(r.json["client_id"].startswith("rwc_"))
        self.assertEqual(r.json["redirect_uris"], [CALLBACK])
        secret = self.client(token_endpoint_auth_method="client_secret_basic")
        self.assertTrue(secret["client_secret"])

    def test_refusals(self):
        name = "Refused " + self.tag
        r = self.register(client_name=name, redirect_uris=["myapp://callback"])
        self.assertEqual((r.status, r.json["error"]), (400, "invalid_redirect_uri"))
        r = self.register(client_name="x" * 101)
        self.assertEqual((r.status, r.json["error"]), (400, "invalid_client_metadata"))
        r = self.http("POST", "/oauth/register", json.dumps({"client_name": name, "redirect_uris": [CALLBACK]}).encode(),
                      {"Content-Type": "application/x-www-form-urlencoded"})   # what a form on another site could send
        self.assertEqual((r.status, r.json["error"]), (400, "invalid_client_metadata"))
        r = self.http("POST", "/oauth/register", b"{nope", {"Content-Type": "application/json"})
        self.assertEqual(r.status, 400)
        r = self.http("POST", "/oauth/register", json.dumps({"client_name": name, "redirect_uris": [CALLBACK], "x": "y" * 9000}).encode(),
                      {"Content-Type": "application/json"})
        self.assertEqual(r.status, 413)                                           # over 8 KB
        self.assertEqual(self.http("GET", "/oauth/register").status, 405)
        with db.session() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM oauth_clients WHERE name=?", (name,)).fetchone()[0], 0)
        self.assertEqual(self.clients, [])


class ConsentTests(OAuthServer):
    def test_a_bad_app_or_redirect_is_shown_here_never_redirected(self):
        c = self.client()
        for over in ({"client_id": "rwc_nope"}, {"redirect_uri": "https://evil.example/cb"}, {"redirect_uri": None},
                     {"redirect_uri": "http://127.0.0.1:43210/other"}):
            with self.subTest(over=over):
                r = self.authorize(c, **over)
                self.assertEqual(r.status, 400)
                self.assertIsNone(r.location)
                self.assertIn(b"Can&#x27;t connect this app", r.body)
        self.assertIn(b"Remove Runway from the assistant and add it again", self.authorize(c, client_id="rwc_nope").body)

    def test_other_mistakes_are_sent_back_with_the_state(self):
        c = self.client()
        for over, error in (({"code_challenge_method": None}, "invalid_request"), ({"code_challenge_method": "plain"}, "invalid_request"),
                            ({"code_challenge": None}, "invalid_request"), ({"response_type": "token"}, "unsupported_response_type"),
                            ({"resource": "https://other.example/mcp"}, "invalid_target"), ({"scope": "admin"}, "invalid_scope")):
            with self.subTest(over=over):
                r = self.authorize(c, **over)
                self.assertEqual(r.status, 302)
                self.assertTrue(r.location.startswith(CALLBACK + "?"))
                q = r.query()
                self.assertEqual((q["error"], q["state"], q["iss"]), (error, "xyz/+&=", self.iss))
                self.assertNotIn("code", q)

    def test_the_page_escapes_the_app_name_and_says_where_it_goes(self):
        c = self.client(client_name='<script>alert("x")</script>')
        r = self.authorize(c)
        self.assertEqual(r.status, 200)
        page = r.body.decode()
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; wants to connect to Runway", page)
        self.assertIn("127.0.0.1:43210", page)
        self.assertIn("Read your finances", page)
        self.assertNotIn("Change churning", page)                                 # it didn't ask
        csp = r.headers["Content-Security-Policy"]
        self.assertIn("form-action 'self' http://127.0.0.1:*", csp)
        self.assertIn("default-src 'none'", csp)
        ck = r.cookie("runway_consent")
        self.assertEqual((ck["path"], ck["httponly"], ck["samesite"]), ("/oauth", True, "Lax"))
        self.assertEqual(ck.value, self.consent_token(r))

    def test_the_churning_box_follows_the_switch(self):
        c = self.client()
        page = self.authorize(c, scope="read churning:write").body.decode()      # asked, switch off: shown, off, and why
        self.assertIn("Change churning", page)
        self.assertIn('type="checkbox" disabled><span><b>Change churning', page)
        self.assertIn("Turn on Let assistants change churning in Settings → Advanced first", page)
        self.switch(True)
        page = self.authorize(c, scope="read churning:write").body.decode()      # asked, switch on: ticked
        self.assertIn('name="churning" value="1" checked', page)
        page = self.authorize(c, scope="read").body.decode()                     # not asked: not there
        self.assertNotIn("Change churning", page)

    def test_allow_and_deny(self):
        c = self.client()
        page = self.authorize(c)
        r = self.answer(page)
        self.assertEqual(r.status, 302)
        q = r.query()
        self.assertTrue(q["code"].startswith("rwo_"))
        self.assertEqual((q["state"], q["iss"]), ("xyz/+&=", self.iss))
        self.assertEqual(r.cookie("runway_consent").value, "")                    # the cookie is cleared
        r = self.answer(self.authorize(c), "deny")
        self.assertEqual((r.query()["error"], r.query()["state"]), ("access_denied", "xyz/+&="))

    def test_the_answer_must_come_from_this_browser_and_runway(self):
        c = self.client()
        page = self.authorize(c)
        token = self.consent_token(page)
        for kwargs in ({"token": ""}, {"cookies": {}}, {"cookies": {"runway_consent": "other"}}, {"token": "other"},
                       {"headers": {"Sec-Fetch-Site": "cross-site"}}, {"headers": {"Origin": "https://evil.example"}}):
            with self.subTest(kwargs=kwargs):
                r = self.answer(page, **kwargs)
                self.assertEqual(r.status, 403)
                self.assertIsNone(r.location)
        r = self.answer(page)                                                     # the real one still works, once
        self.assertEqual(r.status, 302)
        again = self.answer(page, token=token)
        self.assertEqual((again.status, again.location), (403, None))

    def test_the_switch_is_read_again_when_you_answer(self):
        c = self.client()
        self.switch(True)
        page = self.authorize(c, scope="read churning:write")
        self.switch(False)
        tokens = self.exchange(c, self.answer(page, churning=True).query()["code"]).json
        self.assertEqual(tokens["scope"], "read")
        self.switch(True)
        page = self.authorize(c, scope="read churning:write")
        tokens = self.exchange(c, self.answer(page, churning=False).query()["code"]).json   # you unticked it
        self.assertEqual(tokens["scope"], "read")


class TokenTests(OAuthServer):
    def test_tokens_for_a_code(self):
        c = self.client()
        r = self.exchange(c, self.code(c), resource=self.resource)
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual((r.headers["Cache-Control"], r.headers["Pragma"]), ("no-store", "no-cache"))
        self.assertEqual((r.json["token_type"], r.json["expires_in"], r.json["scope"]), ("Bearer", 3600, "read"))

    def test_refusals(self):
        c = self.client()
        for over, error in (({"code_verifier": "wrong-" + "y" * 50}, "invalid_grant"), ({"redirect_uri": "http://127.0.0.1:43210/x"}, "invalid_grant"),
                            ({"client_id": self.client()["client_id"]}, "invalid_grant"), ({"resource": "https://other.example/mcp"}, "invalid_target"),
                            ({"client_id": "rwc_nope"}, "invalid_client"), ({"grant_type": "client_credentials"}, "unsupported_grant_type"),
                            ({"code_verifier": None}, "invalid_request")):
            with self.subTest(over=over):
                r = self.exchange(c, self.code(c), **over)
                self.assertIn(r.status, (400, 401))
                self.assertEqual(r.json["error"], error)
                self.assertNotIn("access_token", r.json)
        r = self.http("POST", "/oauth/token", json.dumps({"grant_type": "authorization_code"}).encode(), {"Content-Type": "application/json"})
        self.assertEqual((r.status, r.json["error"]), (400, "invalid_request"))
        r = self.http("POST", "/oauth/token", b"grant_type=a&grant_type=b", {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual((r.status, r.json["error"]), (400, "invalid_request"))

    def test_a_code_works_once_and_a_second_try_revokes_it(self):
        c = self.client()
        code = self.code(c)
        first = self.exchange(c, code)
        self.assertEqual(first.status, 200)
        again = self.exchange(c, code)
        self.assertEqual((again.status, again.json["error"]), (400, "invalid_grant"))
        self.assertEqual(self.rpc(first.json["access_token"], "ping").status, 401)   # the tokens from it are gone too

    def test_an_expired_code(self):
        c = self.client()
        code = self.code(c)
        with db.session() as conn:
            conn.execute("UPDATE oauth_codes SET created = created - 601 WHERE client_id=?", (c["client_id"],))
        self.assertEqual(self.exchange(c, code).json["error"], "invalid_grant")

    def test_a_confidential_client(self):
        c = self.client(token_endpoint_auth_method="client_secret_basic")
        basic = "Basic " + base64.b64encode(f"{c['client_id']}:{c['client_secret']}".encode()).decode()
        wrong = "Basic " + base64.b64encode(f"{c['client_id']}:nope".encode()).decode()
        code = self.code(c)
        fields = {"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK, "code_verifier": VERIFIER}
        r = self.form("/oauth/token", fields, {"Authorization": wrong})
        self.assertEqual((r.status, r.json["error"], r.headers["WWW-Authenticate"]), (401, "invalid_client", 'Basic realm="Runway"'))
        r = self.form("/oauth/token", {**fields, "client_id": c["client_id"]})       # no secret at all
        self.assertEqual(r.status, 401)
        r = self.form("/oauth/token", fields, {"Authorization": basic})
        self.assertEqual(r.status, 200, r.body)

    def test_revoke(self):
        c = self.client()
        t = self.tokens(c)
        self.assertEqual(self.rpc(t["access_token"], "ping").status, 200)
        r = self.form("/oauth/revoke", {"token": "rwr_unknown", "client_id": c["client_id"]})
        self.assertEqual(r.status, 200)                                           # unknown tokens are fine too
        r = self.form("/oauth/revoke", {"token": t["refresh_token"], "client_id": c["client_id"], "token_type_hint": "refresh_token"})
        self.assertEqual(r.status, 200)
        self.assertEqual(self.rpc(t["access_token"], "ping").status, 401)
        r = self.form("/oauth/token", {"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": c["client_id"]})
        self.assertEqual(r.json["error"], "invalid_grant")
        self.assertEqual(self.mine("SELECT revoked_reason FROM oauth_grants WHERE client_id IN {mine}"), "revoked_by_client")


class McpTests(OAuthServer):
    def test_no_token_is_401_pointing_at_the_metadata(self):
        r = self.rpc(None, "ping")
        self.assertEqual(r.status, 401)
        self.assertEqual(r.headers["WWW-Authenticate"],
                         f'Bearer realm="Runway", resource_metadata="{self.iss}/.well-known/oauth-protected-resource/mcp"')
        for token in ("rwa_nope", "garbage"):
            r = self.rpc(token, "ping")
            self.assertEqual(r.status, 401)
            self.assertTrue(r.headers["WWW-Authenticate"].endswith(', error="invalid_token"'))

    def test_an_expired_token_or_one_for_another_resource(self):
        t = self.tokens()
        self.assertEqual(self.rpc(t["access_token"], "ping").status, 200)
        with db.session() as conn:
            conn.execute("UPDATE oauth_tokens SET expires = 1 WHERE kind = 'access' AND grant_id IN "
                         "(SELECT id FROM oauth_grants WHERE client_id=?)", (self.clients[-1],))
        self.assertEqual(self.rpc(t["access_token"], "ping").status, 401)
        with db.session() as conn:   # a token Runway issued at another address (another RUNWAY_PUBLIC_URL)
            c = mcp_oauth.register(conn, {"redirect_uris": [CALLBACK]})
            self.clients.append(c["client_id"])
            params = {"client_id": c["client_id"], "redirect_uri": CALLBACK, "code_challenge": CHALLENGE,
                      "resource": "https://elsewhere.example/mcp"}
            code = mcp_oauth.approve(conn, params, frozenset({"read"}), None, None)
            other = mcp_oauth.token(conn, mcp_oauth.get_client(conn, c["client_id"]),
                                    {"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK, "code_verifier": VERIFIER},
                                    "https://elsewhere.example")
        self.assertEqual(self.rpc(other["access_token"], "ping").status, 401)

    def test_a_read_only_connection_is_told_to_reconnect(self):
        t = self.tokens()
        self.switch(True)
        self.assertEqual(self.tool_names(t["access_token"]), {x["name"] for x in mcp_server.TOOLS})
        result = self.call(t["access_token"], "add_card", {"fields": self.card})
        self.assertTrue(result["isError"])
        self.assertIn("reconnect", result["content"][0]["text"])
        self.assertNotIn("isError", self.call(t["access_token"], "list_accounts"))

    def test_changes_need_the_scope_and_the_switch(self):
        self.switch(True)
        t = self.tokens(scope="read churning:write", churning=True)
        self.assertEqual(t["scope"], "read churning:write")
        token = t["access_token"]
        self.assertEqual(self.tool_names(token), {x["name"] for x in mcp_server.ALL_TOOLS})
        self.assertNotIn("isError", self.call(token, "add_card", {"fields": self.card}))
        self.switch(False)                                                        # off: at once, without revoking
        self.assertEqual(self.tool_names(token), {x["name"] for x in mcp_server.TOOLS})
        result = self.call(token, "add_card", {"fields": self.card})
        self.assertTrue(result["isError"])
        self.assertIn("switched off", result["content"][0]["text"])
        self.assertEqual(self.rpc(token, "ping").status, 200)
        with db.session() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM churn_cards WHERE owner=?", (self.owner,)).fetchone()[0], 1)


class EndToEndTests(OAuthServer):
    def test_connect_use_refresh_replay(self):
        # What an assistant does: find the server's metadata from the 401, register, send you to approve, get tokens.
        challenge = self.rpc(None, "initialize").headers["WWW-Authenticate"]
        prm_url = challenge.split('resource_metadata="')[1].split('"')[0]
        prm = self.http("GET", prm_url).json
        asm = self.http("GET", prm["authorization_servers"][0] + "/.well-known/oauth-authorization-server").json
        reg = self.http("POST", asm["registration_endpoint"], json.dumps({"client_name": "Claude", "redirect_uris": [CALLBACK]}).encode(),
                        {"Content-Type": "application/json"}).json
        self.clients.append(reg["client_id"])
        self.switch(True)
        q = {"response_type": "code", "client_id": reg["client_id"], "redirect_uri": CALLBACK, "scope": "read churning:write",
             "state": "s1", "code_challenge": CHALLENGE, "code_challenge_method": "S256", "resource": prm["resource"]}
        page = self.http("GET", asm["authorization_endpoint"] + "?" + urllib.parse.urlencode(q))
        self.assertIn(b"Claude wants to connect to Runway", page.body)
        back = self.answer(page, churning=True)
        got = back.query()
        self.assertEqual((got["state"], got["iss"]), ("s1", asm["issuer"]))
        t = self.form(asm["token_endpoint"], {"grant_type": "authorization_code", "code": got["code"], "redirect_uri": CALLBACK,
                                              "code_verifier": VERIFIER, "client_id": reg["client_id"], "resource": prm["resource"]}).json
        self.assertEqual(self.rpc(t["access_token"], "initialize", {"protocolVersion": "2025-06-18"}).json["result"]["serverInfo"]["name"], "runway")
        self.assertIn("add_card", self.tool_names(t["access_token"]))
        self.assertNotIn("isError", self.call(t["access_token"], "add_card", {"fields": self.card}))
        self.switch(False)
        self.assertTrue(self.call(t["access_token"], "add_card", {"fields": self.card})["isError"])
        self.switch(True)
        # Refresh: a new pair; the old refresh token is spent.
        r = self.form(asm["token_endpoint"], {"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": reg["client_id"]})
        self.assertEqual(r.status, 200, r.body)
        t2 = r.json
        self.assertEqual(t2["scope"], "read churning:write")
        self.assertEqual(self.rpc(t2["access_token"], "ping").status, 200)
        # Someone replays the old one: the whole connection is revoked, the new tokens included.
        r = self.form(asm["token_endpoint"], {"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": reg["client_id"]})
        self.assertEqual((r.status, r.json["error"]), (400, "invalid_grant"))
        self.assertEqual(self.rpc(t2["access_token"], "ping").status, 401)
        self.assertEqual(self.rpc(t["access_token"], "ping").status, 401)
        r = self.form(asm["token_endpoint"], {"grant_type": "refresh_token", "refresh_token": t2["refresh_token"], "client_id": reg["client_id"]})
        self.assertEqual(r.json["error"], "invalid_grant")
        self.assertEqual(self.mine("SELECT revoked_reason FROM oauth_grants WHERE client_id IN {mine}"), "refresh_reuse")


class SignInTests(OAuthServer):
    """With sign-in (OIDC): someone signed out is sent to sign in and comes back to the same consent page."""

    @classmethod
    def setUpClass(cls):
        cls.idp = HTTPServer(("127.0.0.1", 0), Provider)
        threading.Thread(target=cls.idp.serve_forever, daemon=True).start()
        cls.env = {"OIDC_CLIENT_ID": "runway", "OIDC_CLIENT_SECRET": "s3cret", "OIDC_ALLOWED_EMAILS": "me@example.com"}
        super().setUpClass()
        os.environ["OIDC_ISSUER"] = f"http://127.0.0.1:{cls.idp.server_port}"
        os.environ["RUNWAY_PUBLIC_URL"] = cls.base
        oidc._discovery.clear()
        oidc._jwks.clear()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.idp.shutdown()
        cls.idp.server_close()
        oidc._discovery.clear()
        oidc._jwks.clear()

    def test_signed_out_goes_to_sign_in_and_back(self):
        c = self.client()
        first = self.authorize(c, scope="read churning:write")
        self.assertEqual(first.status, 302)
        path = first.location
        self.assertTrue(path.startswith("/auth/login?next="))
        back_to = urllib.parse.unquote(path[len("/auth/login?next="):])
        self.assertTrue(back_to.startswith("/oauth/authorize?"))
        login = self.http("GET", path)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(login.location).query))
        Provider.issued["c-oauth"] = {"nonce": q["nonce"], "challenge": q["code_challenge"], "email": "me@example.com"}
        done = self.http("GET", f"/auth/callback?code=c-oauth&state={q['state']}", cookies={"runway_login": login.cookie("runway_login").value})
        self.assertEqual(done.location, back_to)                                  # the whole request survived
        self.assertEqual(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(back_to).query))["state"], "xyz/+&=")
        self.session = {"runway_session": done.cookie("runway_session").value}
        page = self.http("GET", done.location, cookies=self.session)
        self.assertEqual(page.status, 200)
        self.assertIn(b"signed in as <b>me@example.com</b>", page.body)
        r = self.answer(page)
        self.assertEqual(r.status, 302)
        t = self.exchange(c, r.query()["code"]).json
        with db.session() as conn:
            self.assertEqual(conn.execute("SELECT sub, email FROM oauth_grants WHERE client_id=?", (c["client_id"],)).fetchone()[:],
                             ("user-1", "me@example.com"))
        self.assertEqual(self.rpc(t["access_token"], "ping").status, 200)       # /mcp needs no session
        # The answer must come from the person who was shown the page
        page = self.http("GET", done.location, cookies=self.session)
        self.session = {}
        signed_out = self.answer(page)
        self.assertEqual(signed_out.status, 302)
        self.assertTrue(signed_out.location.startswith("/auth/login"))

    def sign_in_as(self, email):
        """A Runway session for `email`, signed in through the test provider."""
        login = self.http("GET", "/auth/login?next=/")
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(login.location).query))
        code = "c-" + tag()
        Provider.issued[code] = {"nonce": q["nonce"], "challenge": q["code_challenge"], "email": email}
        done = self.http("GET", f"/auth/callback?code={code}&state={q['state']}", cookies={"runway_login": login.cookie("runway_login").value})
        return {"runway_session": done.cookie("runway_session").value}

    def test_taken_off_the_sign_in_list_ends_their_assistants(self):
        self.session = self.sign_in_as("me@example.com")
        c = self.client()
        t = self.tokens(c)
        self.assertEqual(self.rpc(t["access_token"], "ping").status, 200)
        with mock.patch.dict(os.environ, {"OIDC_ALLOWED_EMAILS": "someone-else@example.com"}):
            r = self.rpc(t["access_token"], "ping")                               # the very next request
            self.assertEqual(r.status, 401)
            self.assertEqual(r.headers["WWW-Authenticate"], f'Bearer realm="Runway", resource_metadata="{self.iss}'
                                                              '/.well-known/oauth-protected-resource/mcp", error="invalid_token"')
            self.assertEqual(self.mine("SELECT revoked_reason FROM oauth_grants WHERE client_id IN {mine}"), "user_removed")
            r = self.form("/oauth/token", {"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": c["client_id"]})
            self.assertEqual((r.status, r.json["error"]), (400, "invalid_grant"))
            self.assertNotIn("access_token", r.json)
            self.assertEqual(self.mine("SELECT COUNT(*) FROM oauth_tokens WHERE grant_id IN "
                                       "(SELECT id FROM oauth_grants WHERE client_id IN {mine})"), 0)
        self.assertEqual(self.rpc(t["access_token"], "ping").status, 401)       # back on the list: still ended (reconnect)

    def test_a_person_still_allowed_keeps_their_assistant(self):
        self.session = self.sign_in_as("me@example.com")
        t = self.tokens()
        with mock.patch.dict(os.environ, {"OIDC_ALLOWED_EMAILS": "me@example.com,someone-else@example.com"}):
            self.assertEqual(self.rpc(t["access_token"], "ping").status, 200)
        self.assertIsNone(self.mine("SELECT revoked_reason FROM oauth_grants WHERE client_id IN {mine}"))


if __name__ == "__main__":
    unittest.main()
