"""Copies `make verify`'s full-page screenshots into screenshots/, the one committed directory of them all.

    python tools/sync_screenshots.py
    python tools/sync_screenshots.py --check [--out DIR]

Every page and flow screenshot, at phone and desktop widths, lives there as `<name>-<width>.png`, and each page's first screen
as `<page>-top-<width>.png` (what the README and the docs site show). A pull request that
changes the UI re-runs `make screenshots` and commits the result, so the screenshots that changed show as image diffs on the
pull request. It copies only from a run that passed whole (artifacts/verify/report.json's "ok"), and only the images that
report lists; after a failed run it copies nothing and says why. After a full run, files in screenshots/ the run didn't
write are removed; after a limited one (`PAGES=...`) only that run's images are updated, an image of a page or flow it ran
that it no longer writes (a screenshot step renamed or removed) is removed, and every other page's and flow's are left as
they are. `--check` (the Verify flows workflow runs it after a full `make verify`) writes nothing to screenshots/: it
exits 1, listing each image that differs, is missing from screenshots/ or is there without a page or flow to write it,
when a sync would change anything, and with `--out DIR` it saves that list and the run's images there for an artifact. The new files are written beside screenshots/ first and moved in once all are ready, so a sync that fails part way
leaves no half-copied file. Only `make verify`'s made-up demo data is ever copied (the repository is public)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERIFY_DIR = ROOT / "artifacts" / "verify"
DEST = ROOT / "screenshots"
PNG = b"\x89PNG\r\n\x1a\n"
PNG_END = b"IEND\xaeB`\x82"


def _refuse(why: str) -> SystemExit:
    return SystemExit(f"screenshots: {why}. screenshots/ is unchanged.")


def owner(name: str, items: Sequence[str]) -> str | None:
    """The item (`page:<page>` or `flow:<flow>`, from verify.mjs's all_items) whose image `name` is, or None. A page's
    images are `<page>-<width>.png` and its first screen `<page>-top-<width>.png` (verify.mjs's TOP); a flow's start
    `flow-<flow>-`, and since one flow's name can start another's (transactions-delete, transactions-delete-details), the
    longest flow name that fits is the one."""
    stem = name.removesuffix(".png")
    if not stem.startswith("flow-"):
        page = f"page:{stem.rsplit('-', 1)[0].removesuffix('-top')}"
        return page if page in items else None
    flows = [i for i in items if i.startswith("flow:") and stem.startswith(f"flow-{i[5:]}-")]
    return max(flows, key=len) if flows else None


def verified(src: Path) -> tuple[dict[str, Path], Callable[[str], bool]]:
    """The images of the last `make verify` in src that may be copied, and which files in screenshots/ that run answers
    for (it removes those it didn't write): all of them after a full run; after a limited one, the images of the pages
    and flows it ran, and only when its report says which item an image belongs to (otherwise none).
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
    if not report.get("limited"):
        return shots, lambda _name: True
    known, ran = report.get("all_items") or [], set(report.get("items") or [])
    return shots, lambda name: bool(known) and owner(name, known) in ran


def plan(src: Path, dest: Path) -> tuple[dict[str, Path], list[str], list[str]]:
    """What a sync would do: the run's images, the names that differ from (or are missing in) dest, and the names it
    would remove. Refuses (SystemExit) as `verified` does."""
    shots, answers_for = verified(src)
    changed = [name for name, path in shots.items()
               if not (dest / name).is_file() or (dest / name).read_bytes() != path.read_bytes()]
    removed = sorted(p.name for p in dest.glob("*.png") if p.name not in shots and answers_for(p.name))
    return shots, changed, removed


def check(src: Path = VERIFY_DIR, dest: Path = DEST, out: Path | None = None) -> list[str]:
    """The lines naming each way dest differs from what a sync would make (none: dest is what the run writes, byte for
    byte), changing nothing in dest. With out, saves them there as mismatches.txt, and the run's images under
    regenerated/, for an artifact."""
    shots, changed, removed = plan(src, dest)
    lines = [f"differs from the committed image: {n}" if (dest / n).is_file() else f"not committed: {n}"
             for n in sorted(changed)]
    lines += [f"committed, but no page or flow writes it: {n}" for n in removed]
    if out is not None and lines:
        (out / "regenerated").mkdir(parents=True, exist_ok=True)
        for name, path in shots.items():
            shutil.copyfile(path, out / "regenerated" / name)
        (out / "mismatches.txt").write_text("\n".join(lines) + "\n")
    return lines


def sync(src: Path = VERIFY_DIR, dest: Path = DEST) -> tuple[list[str], list[str]]:
    """Copies the last passing `make verify`'s screenshots into dest, leaving a file alone when its bytes are the same (so
    git sees no change), and removes the files the run answers for (verified) but didn't write. Returns the names
    written and the names removed."""
    shots, changed, removed = plan(src, dest)
    dest.mkdir(exist_ok=True)
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
    args = list(argv)
    if args[:1] == ["--check"] and (len(args) == 1 or (len(args) == 3 and args[1] == "--out")):
        try:
            lines = check(VERIFY_DIR, DEST, Path(args[2]) if len(args) == 3 else None)
        except SystemExit as e:
            print(str(e), file=sys.stderr)
            return 1
        if lines:
            print(f"screenshots/ doesn't match what this tree's make verify writes ({len(lines)} images):", file=sys.stderr)
            print("\n".join(f"  {line}" for line in lines), file=sys.stderr)
            print("Run `make screenshots` and commit what changed in screenshots/ (a diff on a page you didn't touch is a "
                  "leak or nondeterminism: report it).", file=sys.stderr)
            return 1
        print("screenshots/ matches what make verify writes, byte for byte.")
        return 0
    if args:
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
