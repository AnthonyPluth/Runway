"""Shows which pixels of the committed screenshots a pull request changed (.github/workflows/screenshot-diff.yml posts
the result as a comment). Standard library and Pillow only.

    poetry run python tools/screenshot_diff.py --base origin/main --head HEAD --out artifacts/screenshot-diff [--run-url URL]
    poetry run python tools/screenshot_diff.py --base origin/main --head HEAD --dry-run     # prints the comment, writes nothing

It reads every PNG under screenshots/ that was added, deleted or modified between the merge base of BASE..HEAD and HEAD
straight from git's objects (nothing is checked out). A modified file is compared pixel by pixel, exactly: any difference in
any of the four channels counts, with no tolerance, because the demo data's clock is frozen and `make verify` renders the
same image twice. Per file it counts the changed pixels and finds up to three boxes around them. With --out it writes, into
that directory:

- `<name>.diff.png` for each modified screenshot: the old image faded to 30% and gray, every changed pixel in magenta (grown
  by two pixels so one pixel can be seen) and the boxes outlined in blue. If the size changed, the canvas is the larger of
  the two, and light blue is where the old image does not reach;
- `summary.md`, the comment's body (it starts with MARKER, which is how the workflow finds its comment again);
- `summary.json`, the same facts for a program.

It is informational: it exits 0 whatever it finds, and fails only when it cannot read the history."""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = "screenshots/"
MARKER = "<!-- screenshot-diff -->"
ARTIFACT = "screenshot-diff"
RETENTION_DAYS = 14
MAX_BOXES = 3
CELL = 16                  # regions are found on a grid of CELL-pixel squares
JOIN = 2                   # cells this close (in cells) belong to one region
GROW = 2                   # pixels a changed pixel is grown by in the highlight
FADE = 0.3                 # how much of the old image shows through in the highlight
CHANGED = (255, 0, 102)    # changed pixels in the highlight
OUTLINE = (0, 102, 255)    # the boxes
BEYOND = (214, 228, 255)   # where the old image doesn't reach, when the size changed
MAX_COMMENT_CHARS = 30_000   # GitHub's limit is 65,536
REMINDER = ("An unexpected diff on a page this change doesn't touch is a leak or a nondeterminism bug, "
            "not noise: find out why (AGENTS.md, \"Git and PRs\").")

Box = tuple[int, int, int, int]   # left, top, right, bottom (right and bottom are not part of the box)


@dataclass
class FileDiff:
    path: str
    status: str                                   # "added", "deleted" or "modified"
    old_size: tuple[int, int] | None = None
    new_size: tuple[int, int] | None = None
    changed_pixels: int = 0
    compared_pixels: int = 0                      # the area both images have
    boxes: list[Box] = field(default_factory=list)
    highlight: str | None = None                  # the file name in the output directory
    error: str | None = None

    @property
    def resized(self) -> bool:
        return self.old_size is not None and self.new_size is not None and self.old_size != self.new_size

    @property
    def percent(self) -> float:
        return 100 * self.changed_pixels / self.compared_pixels if self.compared_pixels else 0.0


# --- git ----------------------------------------------------------------------------------------------------------

def git(*args: str, repo: Path = ROOT) -> bytes:
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True)
    if done.returncode:
        raise SystemExit(f"git {' '.join(args[:2])} failed: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout


def changed_files(base: str, head: str, repo: Path = ROOT) -> tuple[str, list[tuple[str, str]]]:
    """The merge base of base..head, and the PNGs under screenshots/ added (A), deleted (D) or modified (M) since it."""
    merge_base = git("merge-base", base, head, repo=repo).decode().strip()
    raw = git("diff", "--name-status", "-z", "--no-renames", "--diff-filter=ADM", merge_base, head, "--", DIRECTORY,
              repo=repo).decode()
    parts = raw.split("\0")
    found = [(parts[i], parts[i + 1]) for i in range(0, len(parts) - 1, 2)
             if parts[i + 1].startswith(DIRECTORY) and parts[i + 1].lower().endswith(".png")]
    return merge_base, sorted(found, key=lambda f: f[1])


def blob(rev: str, path: str, repo: Path = ROOT) -> Image.Image:
    img = Image.open(io.BytesIO(git("cat-file", "blob", f"{rev}:{path}", repo=repo)))
    img.load()
    return img.convert("RGBA")


# --- comparing ----------------------------------------------------------------------------------------------------

def changed_mask(old: Image.Image, new: Image.Image) -> Image.Image:
    """An "L" image the size of the area both have: 255 where any channel of the pixel differs, else 0."""
    w, h = min(old.width, new.width), min(old.height, new.height)
    diff = ImageChops.difference(old.crop((0, 0, w, h)), new.crop((0, 0, w, h)))
    r, g, b, a = diff.split()
    return ImageChops.lighter(ImageChops.lighter(r, g), ImageChops.lighter(b, a)).point(lambda v: 255 if v else 0)


def _union(a: Box, b: Box) -> Box:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _near(a: Box, b: Box, gap: int) -> bool:
    return a[0] - gap < b[2] and b[0] - gap < a[2] and a[1] - gap < b[3] and b[1] - gap < a[3]


def regions(mask: Image.Image, limit: int = MAX_BOXES) -> list[Box]:
    """At most `limit` boxes that together hold every changed pixel, in reading order. Changed pixels are put in CELL-pixel
    cells, cells close to each other form a region, and regions are merged (nearest first, with a gap that doubles) until
    few enough are left, so a change scattered over the page gives a few big boxes, not hundreds of small ones."""
    if mask.getbbox() is None:
        return []
    small = mask.reduce(CELL)      # a cell is nonzero when any of its pixels is (one pixel of 255 rounds to 1)
    sw = small.width
    cells: dict[tuple[int, int], Box] = {}
    for i, v in enumerate(small.tobytes()):
        if v:
            cx, cy = i % sw, i // sw
            x, y = cx * CELL, cy * CELL
            tight = mask.crop((x, y, min(x + CELL, mask.width), min(y + CELL, mask.height))).getbbox()
            assert tight is not None
            cells[cx, cy] = (x + tight[0], y + tight[1], x + tight[2], y + tight[3])
    boxes: list[Box] = []
    seen: set[tuple[int, int]] = set()
    for start in cells:
        if start in seen:
            continue
        seen.add(start)
        queue, box = deque([start]), cells[start]
        while queue:
            cx, cy = queue.popleft()
            box = _union(box, cells[cx, cy])
            for nx in range(cx - JOIN, cx + JOIN + 1):
                for ny in range(cy - JOIN, cy + JOIN + 1):
                    if (nx, ny) in cells and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        queue.append((nx, ny))
        boxes.append(box)
    size = CELL * 4                # a very scattered change: bucket the boxes first, so the pairwise merge below stays small
    while len(boxes) > 64:
        buckets: dict[tuple[int, int], Box] = {}
        for b in boxes:
            key = ((b[0] + b[2]) // 2 // size, (b[1] + b[3]) // 2 // size)
            buckets[key] = _union(buckets[key], b) if key in buckets else b
        boxes, size = list(buckets.values()), size * 2
    gap = 0
    while True:
        merged = True
        while merged:
            merged = False
            for i in range(len(boxes)):
                for j in range(i + 1, len(boxes)):
                    if _near(boxes[i], boxes[j], gap):
                        boxes[i] = _union(boxes[i], boxes[j])
                        del boxes[j]
                        merged = True
                        break
                if merged:
                    break
        if len(boxes) <= limit:
            return sorted(boxes, key=lambda b: (b[1], b[0]))
        gap = max(gap * 2, CELL)


def highlight(old: Image.Image, mask: Image.Image, boxes: list[Box], new_size: tuple[int, int]) -> Image.Image:
    """The old image faded and gray, the changed pixels grown by GROW and painted, the boxes outlined."""
    size = (max(old.width, new_size[0]), max(old.height, new_size[1]))
    canvas = Image.new("RGB", size, BEYOND if size != old.size else (255, 255, 255))
    flat = Image.alpha_composite(Image.new("RGBA", old.size, (255, 255, 255, 255)), old).convert("L").convert("RGB")
    canvas.paste(Image.blend(Image.new("RGB", old.size, (255, 255, 255)), flat, FADE), (0, 0))
    bbox = mask.getbbox()
    if bbox is not None:
        pad = GROW + 1
        area = (max(bbox[0] - pad, 0), max(bbox[1] - pad, 0), min(bbox[2] + pad, mask.width), min(bbox[3] + pad, mask.height))
        grown = Image.new("L", mask.size, 0)
        grown.paste(mask.crop(area).filter(ImageFilter.MaxFilter(2 * GROW + 1)), area[:2])
        canvas.paste(Image.new("RGB", mask.size, CHANGED), (0, 0), grown)
    draw = ImageDraw.Draw(canvas)
    for left, top, right, bottom in boxes:
        draw.rectangle((left - 4, top - 4, right + 3, bottom + 3), outline=OUTLINE, width=2)
    return canvas


def compare(base: str, head: str, status: str, path: str, out: Path | None, repo: Path = ROOT) -> FileDiff:
    """One file's FileDiff; with `out`, a modified file's highlight image is written there. A file that can't be read as an
    image is reported with `error` rather than stopping the run."""
    entry = FileDiff(path=path, status=status)
    try:
        old = blob(base, path, repo) if status != "added" else None
        new = blob(head, path, repo) if status != "deleted" else None
        entry.old_size = old.size if old else None
        entry.new_size = new.size if new else None
        if old is not None and new is not None:
            mask = changed_mask(old, new)
            entry.compared_pixels = mask.width * mask.height
            entry.changed_pixels = mask.histogram()[255]
            entry.boxes = regions(mask)
            if entry.changed_pixels and out is not None:
                name = Path(path).relative_to(DIRECTORY).with_suffix("").as_posix().replace("/", "__") + ".diff.png"
                highlight(old, mask, entry.boxes, new.size).save(out / name)
                entry.highlight = name
    except (OSError, ValueError, Image.DecompressionBombError) as e:
        entry.error = f"{type(e).__name__}: {e}"
    return entry


def diff(base: str, head: str, out: Path | None = None, repo: Path = ROOT) -> dict:
    """Everything changed under screenshots/ from the merge base of base..head to head, as the summary.json dictionary."""
    merge_base, found = changed_files(base, head, repo)
    status = {"A": "added", "D": "deleted", "M": "modified"}
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
    files = [compare(merge_base, head, status[s], p, out, repo) for s, p in found]
    return {"base": base, "head": head, "merge_base": merge_base, "changed": bool(files),
            "files": [asdict(f) | {"percent": f.percent} for f in files]}


# --- the comment --------------------------------------------------------------------------------------------------

def _size(s: list[int] | tuple[int, int] | None) -> str:
    return f"{s[0]}×{s[1]}" if s else "?"


def _percent(p: float) -> str:
    return "<0.01%" if 0 < p < 0.01 else f"{p:.2f}%"


def _boxes(boxes: list) -> str:
    return "<br>".join(f"`({b[0]}, {b[1]})` {b[2] - b[0]}×{b[3] - b[1]}" for b in boxes) or "–"


def _row(f: dict) -> str:
    if f["error"]:
        status, count, percent, where = f"could not be compared (`{f['error'][:80]}`)", "–", "–", "–"
    else:
        status = f"resized {_size(f['old_size'])} → {_size(f['new_size'])}" if f["old_size"] != f["new_size"] else "modified"
        if not f["changed_pixels"]:
            status += ", same pixels (re-encoded)"
        count, percent, where = f"{f['changed_pixels']:,}", _percent(f["percent"]), _boxes(f["boxes"])
    return f"| `{Path(f['path']).name}` | {status} | {count} | {percent} | {where} |"


def _take(lines: list[str], room: int) -> tuple[list[str], int]:
    """The lines that fit in `room` characters, and how many were left out."""
    kept, used = [], 0
    for line in lines:
        if used + len(line) + 1 > room:
            break
        kept.append(line)
        used += len(line) + 1
    return kept, len(lines) - len(kept)


def render(report: dict, run_url: str | None = None, max_chars: int = MAX_COMMENT_CHARS) -> str:
    """The comment: MARKER first, then a table of the modified screenshots (biggest change first) and the added and deleted
    ones. Lines that would take it past max_chars are left out, and it says how many."""
    files = report["files"]
    if not files:
        return f"{MARKER}\n### Screenshot changes\n\nNo screenshot changes in this pull request.\n"
    modified = sorted((f for f in files if f["status"] == "modified"), key=lambda f: (-f["changed_pixels"], f["path"]))
    added = [f for f in files if f["status"] == "added"]
    deleted = [f for f in files if f["status"] == "deleted"]
    counts = [f"{len(modified)} modified", f"{len(added)} added", f"{len(deleted)} deleted"]
    head = [MARKER, "### Screenshot changes", "",
            f"{', '.join(c for c in counts if not c.startswith('0 '))} (compared pixel by pixel against the merge base)."]
    if any(f["highlight"] for f in modified):
        head += ["", f"The changed pixels are highlighted in magenta, with the boxes in blue, in the `{ARTIFACT}` artifact"
                    + (f" of [this run]({run_url}#artifacts)" if run_url else "")
                    + f" (kept {RETENTION_DAYS} days): one `<name>.diff.png` per modified screenshot."]
    tail = ["", f"_{REMINDER}_", ""]
    room = max_chars - sum(len(x) + 1 for x in head + tail) - 600   # 600: the headers and the "left out" notes below
    table_head = ["", "| Screenshot | Change | Changed pixels | Of the image | Where: `(x, y)` width×height |",
                  "|---|---|--:|--:|---|"]
    rows, left = _take([_row(f) for f in modified], room // 2 if added or deleted else room)
    body = (table_head + rows) if modified else []
    if left:
        body += ["", f"…and {left} more modified screenshots with smaller changes (all of them are in `summary.json` in the "
                     "artifact)."]
    used = sum(len(x) + 1 for x in body)
    for title, group in (("Added", added), ("Deleted", deleted)):
        if group:
            items, more = _take([f"- `{Path(f['path']).name}` ({_size(f['new_size'] or f['old_size'])})" for f in group],
                                max(room - used, 0) // (2 if title == "Added" and deleted else 1))
            body += ["", f"**{title}**", ""] + items + ([f"- …and {more} more"] if more else [])
            used = sum(len(x) + 1 for x in body)
    return "\n".join(head + body + tail)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", required=True, help="the pull request's base (a ref or sha)")
    ap.add_argument("--head", default="HEAD", help="the pull request's head (a ref or sha)")
    ap.add_argument("--out", type=Path, help="where to write the highlight images, summary.md and summary.json")
    ap.add_argument("--run-url", help="the workflow run's URL, for the comment's link to the artifact")
    ap.add_argument("--max-chars", type=int, default=MAX_COMMENT_CHARS, help="the most characters in the comment")
    ap.add_argument("--repo", type=Path, default=ROOT, help="the repository to read (default: this one)")
    ap.add_argument("--dry-run", action="store_true", help="print the comment instead of writing files")
    args = ap.parse_args(argv)
    report = diff(args.base, args.head, None if args.dry_run else args.out, args.repo)
    comment = render(report, args.run_url, args.max_chars)
    if args.dry_run or args.out is None:
        print(comment)
    else:
        (args.out / "summary.md").write_text(comment)
        (args.out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"{len(report['files'])} screenshots changed; wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
