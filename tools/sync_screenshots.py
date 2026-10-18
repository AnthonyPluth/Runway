"""Copies `make verify`'s full-page screenshots into screenshots/, the one committed directory of them all.

    python tools/sync_screenshots.py

Every page and flow screenshot, at phone and desktop widths, lives there as `<name>-<width>.png`. A pull request that
changes the UI re-runs `make screenshots` and commits the result, so the screenshots that changed show as image diffs on the
pull request. Run it after a full `make verify`: files in screenshots/ with no counterpart in artifacts/verify/ are removed.
Tablet screenshots are never kept: any `*-tablet.png` is removed. Only `make verify`'s made-up demo data is ever copied (the repository is public)."""
from __future__ import annotations

import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERIFY_DIR = ROOT / "artifacts" / "verify"
DEST = ROOT / "screenshots"


def is_tablet(name: str) -> bool:
    """A tablet screenshot (`<name>-tablet.png`): never copied, always removed. Only the width at the end counts: a flow
    named `…-tablet` still has phone and desktop screenshots."""
    return name.endswith("-tablet.png")


def sync(src: Path = VERIFY_DIR, dest: Path = DEST) -> tuple[list[str], list[str]]:
    """Makes dest hold exactly src's screenshots, leaving a file alone when its bytes are the same (so git sees no change).
    Returns the names written and the names removed. A screenshot of a failed step (`…-FAILED-…`) is never kept."""
    shots = {p.name: p for p in sorted(src.glob("*.png")) if "-FAILED-" not in p.name and not is_tablet(p.name)}
    if not shots:
        raise SystemExit(f"no screenshots in {src}: run `make verify` first")
    dest.mkdir(exist_ok=True)
    written, removed = [], []
    for name, path in shots.items():
        target = dest / name
        if not target.is_file() or target.read_bytes() != path.read_bytes():
            shutil.copyfile(path, target)
            written.append(name)
    for old in sorted(dest.glob("*.png")):
        if old.name not in shots:
            old.unlink()
            removed.append(old.name)
    return written, removed


def main(argv: Sequence[str]) -> int:
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    written, removed = sync()
    print(f"screenshots: {len(written)} written, {len(removed)} removed, in {DEST.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
