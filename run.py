#!/usr/bin/env python3
"""Start Runway:  poetry run python run.py  [--port 8765]  then open http://localhost:8765
(first time: poetry install --no-root)

Backups:  poetry run python run.py backup [file.json.gz]   save everything to a file
          poetry run python run.py restore file.json.gz    replace everything with a backup (asks first; --yes to skip;
                                                           stop Runway first)
Sample:   poetry run python run.py demo                     fill an empty database with made-up data (for previews)
Verify:   poetry run python run.py verify [page…]           run the app on made-up data in a browser at phone, tablet and
                                                           desktop widths; screenshots and a report go to artifacts/verify/
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from runway.server import serve

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Runway cash-flow forecaster")
    p.add_argument("--port", type=int, default=int(os.environ.get("RUNWAY_PORT", "8765")))
    p.add_argument("--host", default=os.environ.get("RUNWAY_HOST", "127.0.0.1"),
                   help="address to listen on; 0.0.0.0 for other devices (needs OIDC sign-in, see "
                        "https://anthonypluth.github.io/Runway/start/docker/)")
    p.add_argument("--no-sync", action="store_true", default=os.environ.get("RUNWAY_NO_SYNC") == "1",
                   help="don't sync with SimpleFIN in the background")
    p.add_argument("command", nargs="?", choices=["serve", "backup", "restore", "demo", "verify"], default="serve")
    p.add_argument("file", nargs="*", help="backup file (for backup / restore), or the pages to visit (for verify; default all)")
    p.add_argument("--ai-buttons", action="store_true", help="demo: also add what the AI buttons need to show (make verify's)")
    p.add_argument("--investments", action="store_true", help="demo: also add a sample brokerage (make verify's)")
    p.add_argument("--subcategories", action="store_true", help="demo: also add subcategories with transactions in them (make verify's)")
    p.add_argument("--receipt", action="store_true", help="demo: also add a store purchase with its receipt (make verify's)")
    p.add_argument("--signed-in", action="store_true",
                   help="demo: also add a signed-in browser, its token in RUNWAY_VERIFY_SESSION (make verify's)")
    p.add_argument("--yes", action="store_true", help="restore without asking")
    a = p.parse_args()
    if a.command == "verify":
        from runway import verify
        sys.exit(verify.run(a.file))
    if len(a.file) > 1:
        p.error("only one file, please")
    a.file = a.file[0] if a.file else None
    if a.command == "serve":
        serve(host=a.host, port=a.port, auto_sync=not a.no_sync)
    else:
        from datetime import date
        from runway.storage import backup, db
        db.init()
        if a.command == "demo":
            from runway.domain import demo
            with db.session() as conn:
                print(f"Added sample data ({demo.seed(conn)} transactions) to {db.describe()}.")
                if a.ai_buttons:
                    demo.seed_ai_buttons(conn)
                if a.receipt:
                    demo.seed_receipt(conn, date.today())
                if a.subcategories:
                    demo.seed_subcategories(conn)
                if a.investments:
                    demo.seed_investments(conn, date.today())
                if a.signed_in:
                    from runway import verify
                    verify.seed_session(conn, os.environ[verify.SESSION_ENV])
        elif a.command == "backup":
            # Without a file name: this folder, or the data folder when Runway has one set (in Docker, /data: the code
            # folder there is read-only).
            name = f"runway-backup-{date.today().isoformat()}.json.gz"
            out = a.file or (os.path.join(db.data_dir(), name) if os.environ.get("RUNWAY_DATA") else name)
            with db.session() as conn:
                data = backup.dump(conn)
            with open(out, "wb") as f:
                f.write(data)
            os.chmod(out, 0o600)
            print(f"Saved {out} ({len(data) / 1024:.0f} KB) from {db.describe()}. Your API keys and bank access are in it "
                  f"encrypted: restoring it elsewhere needs the same RUNWAY_SECRET_KEY (or secret.key).")
        else:
            if not a.file:
                sys.exit("Which backup file? python3 run.py restore runway-backup.json.gz")
            try:
                with open(a.file, "rb") as f:
                    restored = backup.load(f.read())
            except (OSError, ValueError) as e:
                sys.exit(str(e))
            print(f"Backup from {restored.get('created')} ({restored.get('source')}): "
                  f"{len(restored['tables'].get('transactions', {}).get('rows', []))} transactions.")
            if backup.warning(restored):
                print(f"Note: {backup.warning(restored)}")
            # A running Runway's syncs can't be held off from here (the web restore holds them): one writing during
            # the restore would mix its rows in with the backup's.
            print("Stop Runway first if it's running: a sync it runs during the restore would mix its rows in.")
            if not a.yes and input(f"Replace everything in {db.describe()} with it? Type yes: ").strip().lower() != "yes":
                sys.exit("Nothing changed.")
            try:
                done = backup.restore_all(restored)
            except ValueError as e:
                sys.exit(str(e))
            except OSError as e:
                sys.exit(f"Couldn't save a copy of what's here first ({e.strerror or e}), so nothing was restored.")
            print(f"Restored {sum(done['counts'].values())} rows into {db.describe()}.")
            if done["safety_copy"]:
                print(f"A copy of what was here before is at {done['safety_copy']}.")
            if done["unreadable_secrets"]:
                print(f"These can't be read with this Runway's secret key: {backup.unreadable_summary(done['unreadable_secrets'])}. Set the key "
                      "the backup was made with as RUNWAY_SECRET_KEY_OLD and start Runway, or enter them again in Settings.")
            if done["warning"]:
                print(f"Note: {done['warning']}")
