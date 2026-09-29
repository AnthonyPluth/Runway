import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sentry_sdk

from runway import monitoring, server

DSN = "https://publickey@o123.ingest.us.sentry.io/456"
SIMPLEFIN = "https://user:secretpass@beta-bridge.simplefin.org/simplefin"


class Capture(sentry_sdk.transport.Transport):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.events = []

    def capture_envelope(self, envelope):
        self.events += [i.payload.json for i in envelope.items if i.type == "event"]


class MonitoringTests(unittest.TestCase):
    def tearDown(self):
        sentry_sdk.get_client().close()
        sentry_sdk.init(dsn=None)
        monitoring._enabled = False

    def test_off_without_a_dsn(self):
        with mock.patch.dict(os.environ, {"SENTRY_DSN": ""}):
            self.assertFalse(monitoring.init())
            self.assertIsNone(monitoring.browser_config())
            self.assertNotIn("sentry.io", server.content_security_policy("n"))
            try:
                raise RuntimeError("boom")
            except RuntimeError:
                with mock.patch("traceback.print_exc"):
                    monitoring.report()   # just logged

    def test_secrets_are_blanked(self):
        text = f"Couldn't reach {SIMPLEFIN}/accounts?start-date=1 with access-production-1234abcd-9f00-4c1e"
        out = monitoring.scrub(text)
        for secret in ("secretpass", "user:", "start-date", "1234abcd"):
            self.assertNotIn(secret, out)
        self.assertIn("beta-bridge.simplefin.org/simplefin/accounts", out)

    def test_a_report_carries_the_error_and_nothing_private(self):
        with mock.patch.dict(os.environ, {"SENTRY_DSN": DSN, "RUNWAY_VERSION": "v9.9.9"}):
            self.assertTrue(monitoring.init())
        transport = Capture()
        sentry_sdk.get_client().transport = transport
        balance = sum([1000, 234.56])   # a local variable: its value must not be sent  # noqa: F841
        try:
            raise ValueError(f"SimpleFIN said no: {SIMPLEFIN}?token=abc")
        except ValueError:
            with mock.patch("traceback.print_exc"):
                monitoring.report(ref="abcd1234")
        sentry_sdk.flush()
        self.assertEqual(len(transport.events), 1)
        ev = transport.events[0]
        self.assertEqual((ev["release"], ev["tags"]["ref"]), ("v9.9.9", "abcd1234"))
        exc = ev["exception"]["values"][0]
        self.assertEqual(exc["type"], "ValueError")
        self.assertNotIn("secretpass", exc["value"])
        self.assertNotIn("token=abc", exc["value"])
        self.assertTrue(all("vars" not in f for f in exc["stacktrace"]["frames"]))
        self.assertNotIn("1234.56", str(ev))

    def test_request_details_are_trimmed(self):
        ev = monitoring._before_send({"request": {"method": "POST", "url": "https://runway.example/api/sync?x=1",
                                                  "data": {"amount": 5}, "cookies": {"runway_session": "s"},
                                                  "headers": {"Authorization": "Bearer t"}},
                                      "user": {"email": "a@b.c"}, "extra": {"body": "x"}}, {})
        self.assertEqual(ev, {"request": {"method": "POST", "url": "https://runway.example/api/sync"}, "message": None})
        crumb = monitoring._before_breadcrumb({"category": "httplib", "data": {"url": SIMPLEFIN + "/accounts?a=1",
                                               "http.query": "a=1"}}, {})
        self.assertEqual(crumb["data"], {"url": "https://beta-bridge.simplefin.org/simplefin/accounts"})
        crumb = monitoring._before_breadcrumb({"category": "query", "message": "SELECT 1 WHERE x=?", "data": {"params": [5]}}, {})
        self.assertNotIn("data", crumb)

    def test_the_web_app_may_report_to_sentry_only(self):
        with mock.patch.dict(os.environ, {"SENTRY_DSN": DSN, "SENTRY_BROWSER_DSN": ""}):
            self.assertEqual(monitoring.browser_config()["dsn"], DSN)
            self.assertIn("connect-src 'self' https://production.plaid.com https://o123.ingest.us.sentry.io;",
                          server.content_security_policy("n").replace(server.PLAID_API, "https://production.plaid.com"))
        with mock.patch.dict(os.environ, {"SENTRY_DSN": DSN, "RUNWAY_SENTRY_BROWSER": "0"}):
            self.assertIsNone(monitoring.browser_config())
        with mock.patch.dict(os.environ, {"SENTRY_DSN": "http://k@evil.example/1"}):   # not https: never allowed
            self.assertIsNone(monitoring.browser_config())
            self.assertIsNone(monitoring.browser_origin())


if __name__ == "__main__":
    unittest.main()
