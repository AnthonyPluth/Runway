#!/usr/bin/env python3
"""Start Runway:  poetry run python run.py  [--port 8765]  then open http://localhost:8765
(first time: poetry install --no-root)

Backups:  poetry run python run.py backup [file.json.gz]   save everything to a file
          poetry run python run.py restore file.json.gz    replace everything with a backup (asks first; --yes to skip)
Sample:   poetry run python run.py demo                     fill an empty database with made-up data (for previews)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from runway.server import serve  # noqa: E402

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Runway cash-flow forecaster")
    p.add_argument("--port", type=int, default=int(os.environ.get("RUNWAY_PORT", "8765")))
    p.add_argument("--host", default=os.environ.get("RUNWAY_HOST", "127.0.0.1"),
                   help="address to listen on; 0.0.0.0 for other devices (needs OIDC sign-in, see DOCKER.md)")
    p.add_argument("--no-sync", action="store_true", default=os.environ.get("RUNWAY_NO_SYNC") == "1",
                   help="don't sync with SimpleFIN in the background")
    p.add_argument("command", nargs="?", choices=["serve", "backup", "restore", "demo"], default="serve")
    p.add_argument("file", nargs="?", help="backup file (for backup / restore)")
    p.add_argument("--yes", action="store_true", help="restore without asking")
    a = p.parse_args()
    if a.command == "serve":
        serve(host=a.host, port=a.port, auto_sync=not a.no_sync)
    else:
        from datetime import date
        from runway import backup, db
        db.init()
        if a.command == "demo":
            from runway import demo
            with db.session() as conn:
                print(f"Added sample data ({demo.seed(conn)} transactions) to {db.describe()}.")
        elif a.command == "backup":
            out = a.file or f"runway-backup-{date.today().isoformat()}.json.gz"
            with db.session() as conn:
                data = backup.dump(conn)
            with open(out, "wb") as f:
                f.write(data)
            os.chmod(out, 0o600)
            print(f"Saved {out} ({len(data) / 1024:.0f} KB) from {db.describe()}. It includes your API keys and bank access: keep it private.")
        else:
            if not a.file:
                sys.exit("Which backup file? python3 run.py restore runway-backup.json.gz")
            with open(a.file, "rb") as f:
                data = backup.load(f.read())
            print(f"Backup from {data.get('created')} ({data.get('source')}): "
                  f"{len(data['tables'].get('transactions', {}).get('rows', []))} transactions.")
            if not a.yes and input(f"Replace everything in {db.describe()} with it? Type yes: ").strip().lower() != "yes":
                sys.exit("Nothing changed.")
            with db.session() as conn:
                counts = backup.restore(conn, data)
            print(f"Restored {sum(counts.values())} rows into {db.describe()}.")
