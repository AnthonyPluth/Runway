#!/usr/bin/env python3
"""Start Runway:  python3 run.py  [--port 8765]  then open http://localhost:8765"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from runway.server import serve  # noqa: E402

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Runway cash-flow forecaster")
    p.add_argument("--port", type=int, default=int(os.environ.get("RUNWAY_PORT", "8765")))
    p.add_argument("--host", default=os.environ.get("RUNWAY_HOST", "127.0.0.1"),
                   help="address to listen on; 0.0.0.0 for other devices (needs RUNWAY_PASSWORD)")
    p.add_argument("--no-sync", action="store_true", default=os.environ.get("RUNWAY_NO_SYNC") == "1",
                   help="don't sync with SimpleFIN in the background")
    a = p.parse_args()
    serve(host=a.host, port=a.port, auto_sync=not a.no_sync)
