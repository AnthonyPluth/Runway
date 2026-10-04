"""The TLS settings for every https request Runway makes (Plaid, SimpleFIN, prices, Finnhub, Logo.dev, Carta, Realie,
the AI provider, sign-in): one place, so none of them checks certificates differently from the others."""
from __future__ import annotations

import functools
import ssl


@functools.cache
def ssl_context() -> ssl.SSLContext:
    """Certificates checked against the system's, plus certifi's when it's installed. Made once: loading the
    certificates takes a while, and a context can be shared by many connections at once."""
    ctx = ssl.create_default_context()
    try:  # python.org builds on macOS ship without system certs; use certifi when present
        import certifi

        ctx.load_verify_locations(certifi.where())
    except (ImportError, OSError):   # certifi is optional; without it (or its bundle) the system certs still apply
        pass
    return ctx
