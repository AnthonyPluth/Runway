"""Copies `make verify`'s full-page screenshots into screenshots/, the one committed directory of them all.

    python tools/sync_screenshots.py

Every page and flow screenshot, at phone and desktop widths, lives there as `<name>-<width>.png`. A pull request that
changes the UI re-runs `make screenshots` and commits the result, so the screenshots that changed show as image diffs on the
pull request. It copies only from a run that passed whole (artifacts/verify/report.json's "ok"), and only the images that
report lists; after a failed run it copies nothing and says why. After a full run, files in screenshots/ the run didn't
write are removed; after a limited one (`PAGES=...`) only that run's images are updated and every other file is left as it
is. The new files are written beside screenshots/ first and moved in once all are ready, so a sync that fails part way
leaves no half-copied file. Only `make verify`'s made-up demo data is ever copied (the repository is public)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERIFY_DIR = ROOT / "artifacts" / "verify"
DEST = ROOT / "screenshots"
PNG = b"\x89PNG\r\n\x1a\n"
PNG_END = b"IEND\xaeB`\x82"


def _refuse(why: str) -> SystemExit:
    return SystemExit(f"screenshots: {why}. screenshots/ is unchanged.")


def verified(src: Path) -> tuple[dict[str, Path], bool]:
    """The images of the last `make verify` in src that may be copied, and whether that run covered every page and flow.
    Refuses (SystemExit) a run that didn't pass, an image that isn't a whole PNG, and a run with nothing to copy."""
    try:
        report = json.loads((src / "report.json").read_text())
    except (OSError, ValueError):
        raise _refuse(f"no report from make verify in {src}: run `make screenshots` (or `make verify`) first") from None
    if not report.get("ok"):
        failed = ", ".join(sorted(report.get("failed") or {})) or "see its report"
        raise _refuse(f"the last make verify failed ({failed}), so none of its images are copied: fix it and run "
                      f"`make screenshots` again (artifacts/verify/report.json and the log it names say why)")
    shots: dict[str, Path] = {}
    for name in report.get("files") or []:
        path = src / name
        if "-FAILED-" in name or not name.endswith(".png") or "/" in name:
            raise _refuse(f"the report lists {name}, which is no screenshot")
        data = path.read_bytes() if path.is_file() else b""
        if not (data.startswith(PNG) and data.endswith(PNG_END)):
            raise _refuse(f"{path} is missing or isn't a whole PNG")
        shots[name] = path
    if not shots:
        raise _refuse(f"no screenshots in {src}: run `make screenshots` first")
    return shots, not report.get("limited")


def sync(src: Path = VERIFY_DIR, dest: Path = DEST) -> tuple[list[str], list[str]]:
    """Copies the last passing `make verify`'s screenshots into dest, leaving a file alone when its bytes are the same (so
    git sees no change); after a full run, also removes the files no page or flow wrote. Returns the names written and
    the names removed."""
    shots, full = verified(src)
    dest.mkdir(exist_ok=True)
    changed = [name for name, path in shots.items()
               if not (dest / name).is_file() or (dest / name).read_bytes() != path.read_bytes()]
    removed = sorted(p.name for p in dest.glob("*.png") if p.name not in shots) if full else []
    # Every new file is written into a folder beside dest first; only then is each moved in (a rename, which doesn't
    # fail part way through a file), and the stale ones removed.
    with tempfile.TemporaryDirectory(prefix=".screenshots-sync-", dir=dest.parent) as stage:
        for name in changed:
            shutil.copyfile(shots[name], Path(stage) / name)
        for name in changed:
            os.replace(Path(stage) / name, dest / name)
    for name in removed:
        (dest / name).unlink()
    return sorted(changed), removed


def main(argv: Sequence[str]) -> int:
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        written, removed = sync(VERIFY_DIR, DEST)
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"screenshots: {len(written)} written, {len(removed)} removed, in {DEST.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
