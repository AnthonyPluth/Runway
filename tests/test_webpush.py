import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runway import webpush as w  # noqa: E402


class CryptoVectors(unittest.TestCase):
    def test_aes_fips197(self):
        rk = w._expand_key(bytes(range(16)))
        self.assertEqual(w._encrypt_block(rk, bytes.fromhex("00112233445566778899aabbccddeeff")).hex(),
                         "69c4e0d86a7b0430d8cdb78070b4c55a")

    def test_gcm_vectors(self):
        self.assertEqual(w.aes128gcm_encrypt(bytes(16), bytes(12), bytes(16)).hex(),
                         "0388dace60b6a392f328c2b971b2fe78" "ab6e47d42cec13bdf53a67b21257bddf")
        key, iv = bytes.fromhex("feffe9928665731c6d6a8f9467308308"), bytes.fromhex("cafebabefacedbaddecaf888")
        pt = bytes.fromhex("d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a721c3c0c95956809532fcf0e2449a6b525"
                           "b16aedf5aa0de657ba637b39")
        aad = bytes.fromhex("feedfacedeadbeeffeedfacedeadbeefabaddad2")
        out = w.aes128gcm_encrypt(key, iv, pt, aad)
        self.assertEqual(out[-16:].hex(), "5bc94fbc3221a5db94fae95ae7121a47")
        self.assertEqual(w.aes128gcm_decrypt(key, iv, out, aad), pt)
        with self.assertRaises(ValueError):
            w.aes128gcm_decrypt(key, iv, out[:-1] + bytes([out[-1] ^ 1]), aad)

    def test_rfc8291_example(self):
        """The worked example in RFC 8291, appendix A, byte for byte."""
        auth = w.unb64u("BTBZMqHH6r4Tts7J_aSIgg")
        ua_priv = int.from_bytes(w.unb64u("q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"), "big")
        ua_pub = w.unb64u("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4")
        as_priv = int.from_bytes(w.unb64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big")
        self.assertEqual(w.public_key(ua_priv), ua_pub)
        out = w.encrypt(b"When I grow up, I want to be a watermelon", ua_pub, auth, as_private=as_priv,
                        salt=w.unb64u("DGv6ra1nlYgDCS1FRnbzlw"))
        self.assertEqual(w.b64u(out),
                         "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_"
                         "yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")
        self.assertEqual(w.decrypt(out, ua_priv, auth), b"When I grow up, I want to be a watermelon")

    def test_ecdsa(self):
        priv = w.new_private_key()
        pub = w.public_key(priv)
        sig = w.ecdsa_sign(priv, b"message")
        self.assertEqual(sig, w.ecdsa_sign(priv, b"message"))        # deterministic (RFC 6979)
        self.assertTrue(w.ecdsa_verify(pub, b"message", sig))
        self.assertFalse(w.ecdsa_verify(pub, b"massage", sig))
        with self.assertRaises(ValueError):
            w.decode_point(b"\x04" + bytes(64))                     # not on the curve

    def test_vapid_header(self):
        priv = w.new_private_key()
        pub = w.b64u(w.public_key(priv))
        h = w.vapid_header(priv, pub, "https://web.push.apple.com/QGuQyavXutnMH", "mailto:me@example.com", now=1_800_000_000)
        token = h.split("t=")[1].split(",")[0]
        head, body, sig = token.split(".")
        self.assertEqual(json.loads(w.unb64u(body)), {"aud": "https://web.push.apple.com", "exp": 1_800_043_200,
                                                      "sub": "mailto:me@example.com"})
        self.assertTrue(w.ecdsa_verify(w.unb64u(pub), f"{head}.{body}".encode(), w.unb64u(sig)))
        self.assertTrue(h.endswith(f"k={pub}"))


class PushService(BaseHTTPRequestHandler):
    received = []
    status = 201

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        PushService.received.append((self.path, self.headers, body))
        self.send_response(PushService.status)
        self.send_header("Content-Length", "0")
        self.end_headers()


class SendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), PushService)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_send_and_gone(self):
        ua_priv, auth = w.new_private_key(), os.urandom(16)
        sub = {"endpoint": f"http://127.0.0.1:{self.srv.server_port}/push/abc", "p256dh": w.b64u(w.public_key(ua_priv)),
               "auth": w.b64u(auth)}
        vpriv = w.new_private_key()
        PushService.received.clear()
        self.assertEqual(w.send(sub, {"title": "Hi", "body": "$1,234.56"}, vpriv, w.b64u(w.public_key(vpriv)), "mailto:a@b.c"), 201)
        path, headers, body = PushService.received[-1]
        self.assertEqual((path, headers["Content-Encoding"], headers["TTL"]), ("/push/abc", "aes128gcm", "86400"))
        self.assertTrue(headers["Authorization"].startswith("vapid t="))
        self.assertEqual(json.loads(w.decrypt(body, ua_priv, auth)), {"title": "Hi", "body": "$1,234.56"})
        PushService.status = 410
        try:
            with self.assertRaises(w.Gone):
                w.send(sub, {"title": "x"}, vpriv, w.b64u(w.public_key(vpriv)), "mailto:a@b.c")
        finally:
            PushService.status = 201


if __name__ == "__main__":
    unittest.main()
