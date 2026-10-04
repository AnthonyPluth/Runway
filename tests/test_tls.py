"""runway/tls.py: every outbound https request checks certificates the same way, with one shared context."""
import io
import ssl
import sys
import unittest
from unittest import mock

from runway import carta, finnhub, oidc, tls


class TlsTests(unittest.TestCase):
    def test_one_context_that_checks_certificates(self):
        ctx = tls.ssl_context()
        self.assertIs(tls.ssl_context(), ctx)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_requests_use_it(self):
        seen = []

        def urlopen(_req, timeout=None, context=None):
            seen.append(context)
            return io.BytesIO(b"{}")
        with mock.patch("urllib.request.urlopen", urlopen):
            carta._post_form("https://carta.invalid/token", {}, "id", "secret")
            oidc._get_json("https://idp.invalid/.well-known/openid-configuration")
        self.assertEqual(seen, [tls.ssl_context()] * 2)

    def test_finnhubs_stream_uses_it(self):
        ws = mock.Mock()
        with mock.patch.dict(sys.modules, {"websocket": ws}):
            finnhub._connect("k")
        self.assertIs(ws.create_connection.call_args.kwargs["sslopt"]["context"], tls.ssl_context())
