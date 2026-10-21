"""Shows which pixels of the committed screenshots a pull request changed (.github/workflows/screenshot-diff.yml posts
the result as a comment). Standard library and Pillow only.

    poetry run python tools/screenshot_diff.py --base origin/main --head HEAD --out artifacts/screenshot-diff [--run-url URL]
    poetry run python tools/screenshot_diff.py --base origin/main --head HEAD --dry-run     # prints the comment, writes nothing

It reads every PNG under screenshots/ that was added, deleted or modified between the merge base of BASE..HEAD and HEAD
straight from git's objects (nothing is checked out). A modified file is compared pixel by pixel, exactly: any difference in
any of the four channels counts, with no tolerance, because the demo data's clock is frozen and `make verify` renders the
same image twice. Per file it counts the changed pixels and finds up to three boxes around them. With --out it writes, into
that directory:

- `summary.md`, the comment's body (it starts with MARKER, which is how the workflow finds its comment again): a link to the
  report, one line of counts and a note, no list of files;
- `summary.json`, the facts for each file for a program;
- `report.html`, one self-contained page (no network, every image a data URI, vanilla JS and CSS inline) that shows each
  changed screenshot with the tabs Reference, Actual and Slider, the way Vitest's browser UI does, and thin boxes around
  what changed over the Actual image and the Slider's right side (a checkbox hides them). Tall images are embedded as the
  bands of rows around the changes (REPORT_CONTEXT rows of context, unchanged rows left out and labelled) and, when the size
  allows, whole. The file is kept under REPORT_BUDGET: past it the context shrinks, then the images are scaled down, and the
  page says so.

`--from-json summary.json --report-url URL` renders the comment again from an earlier run's summary.json, with the link to
the report at its top (the link is only known once the report has been uploaded).

It is informational: it exits 0 whatever it finds, and fails only when it cannot read the history."""
from __future__ import annotations

import argparse
import base64
import html
import io
import itertools
import json
import subprocess
import sys
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = "screenshots/"
MARKER = "<!-- screenshot-diff -->"
RETENTION_DAYS = 14
MAX_BOXES = 3
CELL = 16                  # regions are found on a grid of CELL-pixel squares
JOIN = 2                   # cells this close (in cells) belong to one region
REPORT = "report.html"
REPORT_BUDGET = 25 * 1024 * 1024      # the most bytes of report.html
FULL_BUDGET = 12 * 1024 * 1024        # whole images are embedded only while the file stays under this
REPORT_CONTEXT = 150                  # rows of context above and below a change
BAND_GAP = 40                         # bands this close (rows between them) are one band
MAX_BANDS = 40                        # per image: more are merged, nearest first
WEBP_MAX = 16383                      # WebP's limit on one side
# (context rows, scale) tried in turn until the report fits: first less context, then smaller images.
LEVELS = [(REPORT_CONTEXT, 1.0), (60, 1.0), (20, 1.0), (0, 1.0), (0, 0.75), (0, 0.5), (0, 0.35), (0, 0.25)]
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


def compare(base: str, head: str, status: str, path: str, repo: Path = ROOT) -> FileDiff:
    """One file's FileDiff. A file that can't be read as an image is reported with `error` rather than stopping the run."""
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
    except (OSError, ValueError, Image.DecompressionBombError) as e:
        entry.error = f"{type(e).__name__}: {e}"
    return entry


def diff(base: str, head: str, repo: Path = ROOT) -> dict:
    """Everything changed under screenshots/ from the merge base of base..head to head, as the summary.json dictionary."""
    merge_base, found = changed_files(base, head, repo)
    status = {"A": "added", "D": "deleted", "M": "modified"}
    files = [compare(merge_base, head, status[s], p, repo) for s, p in found]
    return {"base": base, "head": head, "merge_base": merge_base, "changed": bool(files),
            "files": [asdict(f) | {"percent": f.percent} for f in files]}


# --- the comment --------------------------------------------------------------------------------------------------

def _size(s: list[int] | tuple[int, int] | None) -> str:
    return f"{s[0]}×{s[1]}" if s else "?"


def counts(files: list[dict]) -> str:
    """"23 modified, 2 added, 1 deleted": a resized screenshot is a modified one; zero counts are left out."""
    parts = [(sum(f["status"] == k and not f["error"] for f in files), k) for k in ("modified", "added", "deleted")]
    parts.append((sum(bool(f["error"]) for f in files), "could not be compared"))
    return ", ".join(f"{n} {k}" for n, k in parts if n)


def render(report: dict, run_url: str | None = None, report_url: str | None = None) -> str:
    """The comment: MARKER first, then the link to report.html (or, until it is known, where to find it), one line of counts
    and a note. It names no screenshot: the report does."""
    files = report["files"]
    if not files:
        return f"{MARKER}\n### Screenshot changes\n\nNo screenshot changes in this pull request.\n"
    head = [MARKER, "### Screenshot changes", ""]
    if report_url:
        # Only people logged in to GitHub with access to the repository can open the artifact's link.
        head += [f"**View the visual diff: [{REPORT}]({report_url})**", "", f"{counts(files)}.", "",
                 "_Reference, Actual and Slider for each image, with a box around what changed. The link needs you to be "
                 f"logged in to GitHub, and the file is kept {RETENTION_DAYS} days._"]
    else:
        where = f"[this run]({run_url}#artifacts)" if run_url else "this workflow run"
        head += [f"{counts(files)}.", "", f"The visual diff, `{REPORT}`, is in the artifacts of {where} (you need to be logged "
                 f"in to GitHub; kept {RETENTION_DAYS} days)."]
    return "\n".join([*head, "", f"_{REMINDER}_", ""])


REPORT_CSS = r"""
:root{color-scheme:dark light;--bg:#0d1117;--panel:#161b22;--raised:#1f2630;--border:#30363d;--text:#e6edf3;--muted:#9da7b3;
--accent:#58a6ff;--accent-ink:#0d1117;--s-modified:#d29922;--s-resized:#db6d28;--s-added:#3fb950;--s-deleted:#f85149;--s-error:#f85149;
--hatch:#2a313a;--box:#ff2d87}
@media (prefers-color-scheme:light){:root{--bg:#ffffff;--panel:#f6f8fa;--raised:#eaeef2;--border:#d0d7de;--text:#1f2328;
--muted:#59636e;--accent:#0969da;--accent-ink:#ffffff;--s-modified:#9a6700;--s-resized:#bc4c00;--s-added:#1a7f37;
--s-deleted:#cf222e;--s-error:#cf222e;--hatch:#dde3ea;--box:#e0006b}}
*{box-sizing:border-box}
html{background:var(--bg)}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1500px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.5rem;margin:0 0 4px}
h2{font-size:1rem;margin:0;word-break:break-all}
a{color:var(--accent)}
.lead{margin:0 0 12px;color:var(--muted)}
.notices{margin:0 0 16px;padding:10px 12px 10px 28px;background:var(--panel);border:1px solid var(--s-modified);border-radius:6px}
.controls{display:flex;flex-wrap:wrap;gap:12px;margin:0 0 12px}
.controls label{display:flex;align-items:center;gap:8px;color:var(--muted)}
input,select,button{font:inherit;color:var(--text);background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:6px 10px}
input{min-width:min(100%,260px)}
button{cursor:pointer}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
#index{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:4px 16px;margin:0 0 24px;padding:12px 12px 12px 36px;
background:var(--panel);border:1px solid var(--border);border-radius:8px;max-height:260px;overflow:auto}
#index li{word-break:break-all}
.n{color:var(--muted);font-size:.85em;white-space:nowrap}
.badge{display:inline-block;padding:0 8px;border:1px solid currentColor;border-radius:999px;font-size:.75rem;line-height:1.5;font-weight:600;white-space:nowrap}
.s-modified{color:var(--s-modified)}.s-resized{color:var(--s-resized)}.s-added{color:var(--s-added)}.s-deleted{color:var(--s-deleted)}.s-error{color:var(--s-error)}
.card{margin:0 0 32px;padding:16px;background:var(--panel);border:1px solid var(--border);border-radius:10px;scroll-margin-top:12px}
.card[hidden],#index li[hidden]{display:none}
.card>header{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px}
.verdict{margin:8px 0 4px;font-weight:600}
.meta{display:flex;flex-wrap:wrap;gap:4px 16px;margin:0 0 12px;color:var(--muted);font-size:.9rem}
.note{margin:8px 0;color:var(--muted);font-size:.9rem}
.tools{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px;margin:0 0 8px}
.boxtoggle{display:inline-flex;align-items:center;gap:8px;cursor:pointer}
.boxtoggle input{min-width:0;margin:0;padding:0;width:18px;height:18px;accent-color:var(--accent)}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--border);overflow-x:auto}
.tabs [role=tab]{border:0;border-bottom:2px solid transparent;border-radius:6px 6px 0 0;background:none;color:var(--muted);padding:8px 14px}
.tabs [role=tab][aria-selected=true]{color:var(--text);border-bottom-color:var(--accent);background:var(--raised)}
.pane{padding:12px 0 0}
.js .pane[hidden]{display:none}
.seg{margin:0 0 4px}
figcaption{margin:8px 0 4px;color:var(--muted);font-size:.85rem}
.gap{margin:8px 0;padding:6px;text-align:center;color:var(--muted);font-size:.85rem;border:1px dashed var(--border);border-radius:6px}
.win{position:relative;width:100%;max-width:var(--w);overflow:hidden;background:repeating-linear-gradient(135deg,var(--hatch) 0 6px,transparent 6px 12px);
outline:1px solid var(--border)}
.win img{position:absolute;left:0;display:block;max-width:none}
.win.none{display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:.85rem;padding:8px}
.box{position:absolute;pointer-events:none;outline:2px solid var(--box);outline-offset:3px}
.box.added,.box.removed{outline:0;border:2px dashed var(--box-x);background:color-mix(in srgb,var(--box-x) 14%,transparent)}
.box.added{--box-x:#22c55e}.box.removed{--box-x:#f59e0b}
.box b{position:absolute;right:0;top:0;padding:0 6px;background:var(--box-x);color:#000;font:600 .75rem/1.5 system-ui,sans-serif;white-space:nowrap}
.hide-boxes .box{display:none}
.allview{display:none}
.card.all .allview{display:block}
.card.all .bands{display:none}
.cmp{position:relative;width:100%;max-width:var(--w);touch-action:pan-y;user-select:none;-webkit-user-select:none;cursor:ew-resize}
.cmp .win{max-width:none}
.cmp .win.top{position:absolute;inset:0;width:auto;aspect-ratio:auto;clip-path:inset(0 calc(100% - var(--p,50%)) 0 0)}
.cmp .tag{position:absolute;top:6px;padding:1px 8px;border-radius:999px;font-size:.75rem;background:rgb(0 0 0/.65);color:#fff;pointer-events:none}
.cmp .tag.l{left:6px}.cmp .tag.r{right:6px}
.cmp .handle{position:absolute;top:0;bottom:0;left:var(--p,50%);width:44px;margin-left:-22px;cursor:ew-resize}
.cmp .handle:focus-visible{outline:none}
.cmp .handle:focus-visible::after{outline:3px solid var(--text);outline-offset:2px}
.cmp .handle::before{content:"";position:absolute;top:0;bottom:0;left:50%;width:2px;margin-left:-1px;background:var(--accent);box-shadow:0 0 0 1px rgb(0 0 0/.4)}
.cmp .handle::after{content:"↔";position:absolute;top:50%;left:50%;width:30px;height:30px;margin:-15px 0 0 -15px;border-radius:50%;
background:var(--accent);color:var(--accent-ink);display:grid;place-items:center;font-weight:700;box-shadow:0 1px 4px rgb(0 0 0/.5)}
footer{margin-top:32px;color:var(--muted);font-size:.8rem}
@media (max-width:600px){.card{padding:12px}#index{grid-template-columns:1fr}}
"""

REPORT_JS = r"""
(function(){
"use strict";
var doc=document,root=doc.documentElement;
root.classList.add("js");
function all(sel,ctx){return Array.prototype.slice.call((ctx||doc).querySelectorAll(sel));}
function el(tag,cls,text){var e=doc.createElement(tag);if(cls)e.className=cls;if(text)e.textContent=text;return e;}

// The before/after slider: reference on the left of the handle, actual on the right.
function slider(refWin,actWin){
  var box=el("div","cmp"),base=actWin.cloneNode(true),top=refWin.cloneNode(true),handle=el("div","handle"),pos=50;
  top.classList.add("top");
  handle.setAttribute("role","slider");handle.tabIndex=0;
  handle.setAttribute("aria-label","Comparison position: reference on the left, actual on the right");
  handle.setAttribute("aria-valuemin","0");handle.setAttribute("aria-valuemax","100");handle.setAttribute("aria-orientation","horizontal");
  box.append(base,top,el("span","tag l","Reference"),el("span","tag r","Actual"),handle);
  function set(p){
    pos=Math.max(0,Math.min(100,p));
    box.style.setProperty("--p",pos+"%");
    handle.setAttribute("aria-valuenow",String(Math.round(pos)));
    handle.setAttribute("aria-valuetext",Math.round(pos)+"% reference, "+Math.round(100-pos)+"% actual");
  }
  function fromEvent(e){var r=box.getBoundingClientRect();set((e.clientX-r.left)/r.width*100);}
  box.addEventListener("pointerdown",function(e){
    if(e.button>0)return;
    box.setPointerCapture(e.pointerId);box.dataset.drag="1";fromEvent(e);handle.focus({preventScroll:true});
  });
  box.addEventListener("pointermove",function(e){if(box.dataset.drag)fromEvent(e);});
  function end(e){delete box.dataset.drag;if(box.hasPointerCapture(e.pointerId))box.releasePointerCapture(e.pointerId);}
  box.addEventListener("pointerup",end);box.addEventListener("pointercancel",end);
  handle.addEventListener("keydown",function(e){
    var step=e.shiftKey?10:1,k=e.key;
    if(k==="ArrowLeft"||k==="ArrowDown")set(pos-step);
    else if(k==="ArrowRight"||k==="ArrowUp")set(pos+step);
    else if(k==="PageDown")set(pos-10);else if(k==="PageUp")set(pos+10);
    else if(k==="Home")set(0);else if(k==="End")set(100);
    else return;
    e.preventDefault();
  });
  set(50);
  return box;
}

// One window onto a whole image (the "all rows" view): the image of the first band, shown from its top.
function wholeWindow(card,pane){
  var img=pane.querySelector(".win img");
  if(!img)return null;
  var w=+card.dataset.w,h=+card.dataset.h,win=el("div","win"),c=img.cloneNode();
  win.style.aspectRatio=w+"/"+h;
  c.style.top="0";c.style.height=(+img.dataset.ih/h*100)+"%";c.style.width=(+img.dataset.iw/w*100)+"%";c.setAttribute("loading","lazy");
  win.append(c);
  if(pane.classList.contains("pane-act")){
    var t=pane.querySelector("template");
    if(t)win.append(t.content.cloneNode(true));
  }
  return win;
}
function fill(view,win,title){var f=el("figure","seg");f.append(el("figcaption","",title),win);view.append(f);}

all("img[data-reuse]").forEach(function(img){img.src=img.closest(".pane").querySelector("img[src]").src;});

all(".card").forEach(function(card){
  var tabs=all('[role=tab]',card);
  var panes=tabs.map(function(t){return doc.getElementById(t.getAttribute("aria-controls"));});
  function select(i,focus){
    tabs.forEach(function(t,k){
      var on=k===i;
      t.setAttribute("aria-selected",on?"true":"false");t.tabIndex=on?0:-1;panes[k].hidden=!on;
    });
    if(focus)tabs[i].focus();
  }
  if(tabs.length){
    select(Math.max(0,tabs.findIndex(function(t){return t.getAttribute("aria-selected")==="true";})),false);
    tabs.forEach(function(t,i){
      t.addEventListener("click",function(){select(i,false);});
      t.addEventListener("keydown",function(e){
        var n=tabs.length,k=e.key,j=-1;
        if(k==="ArrowRight")j=(i+1)%n;else if(k==="ArrowLeft")j=(i+n-1)%n;else if(k==="Home")j=0;else if(k==="End")j=n-1;
        if(j<0)return;
        e.preventDefault();select(j,true);
      });
    });
  }
  var refPane=card.querySelector(".pane-ref"),actPane=card.querySelector(".pane-act"),sl=card.querySelector(".pane-slider");
  if(sl&&refPane&&actPane){
    var refWins=all(".bands .win",refPane),actWins=all(".bands .win",actPane);
    all(".host",sl).forEach(function(h,i){h.replaceWith(slider(refWins[i],actWins[i]));});
  }
  all(".boxtoggle input",card).forEach(function(c){
    c.addEventListener("change",function(){
      root.classList.toggle("hide-boxes",!c.checked);
      all(".boxtoggle input").forEach(function(o){o.checked=c.checked;});
    });
  });
  var btn=card.querySelector(".allbtn");
  if(btn){
    var built=false;
    btn.addEventListener("click",function(){
      var on=!card.classList.contains("all");
      if(on&&!built){
        built=true;
        var title="All "+Number(card.dataset.h).toLocaleString("en-US")+" rows",wins={};
        all(".pane",card).forEach(function(p){
          var kind=p.className.match(/pane-(\w+)/)[1];
          if(kind==="slider")return;
          var w=wholeWindow(card,p);
          if(w){wins[kind]=w;fill(p.querySelector(".allview"),w,title);}
        });
        if(sl&&wins.ref&&wins.act)fill(sl.querySelector(".allview"),slider(wins.ref,wins.act),title);
      }
      card.classList.toggle("all",on);
      btn.setAttribute("aria-pressed",on?"true":"false");
      btn.textContent=on?"Show only the changed rows":"Show all "+Number(card.dataset.h).toLocaleString("en-US")+" rows";
    });
  }
});

// Filter: the index and the cards follow the text and the status.
var q=doc.getElementById("filter"),st=doc.getElementById("status"),count=doc.getElementById("count");
function apply(){
  var text=q.value.trim().toLowerCase(),status=st.value,shown=0,total=+count.dataset.total;
  all("#index li, .card").forEach(function(n){
    var ok=(!text||n.dataset.name.indexOf(text)>=0)&&(!status||n.dataset.status===status);
    n.hidden=!ok;
    if(ok&&n.tagName==="LI")shown++;
  });
  doc.getElementById("none").hidden=shown>0;
  var base=count.dataset.base||(count.dataset.base=count.textContent);
  count.textContent=shown===total?base:base+" Showing "+shown+" of "+total+".";
}
q.addEventListener("input",apply);st.addEventListener("change",apply);
})();
"""


# --- the report ---------------------------------------------------------------------------------------------------

class OverBudget(Exception):
    """The images of a level need more bytes than the report may have."""


class Meter:
    """Counts the bytes of the images embedded so far (as base64, which is what the file holds)."""

    def __init__(self, room: int) -> None:
        self.room, self.used = room, 0

    def add(self, nbytes: int) -> None:
        self.used += nbytes * 4 // 3 + 32
        if self.used > self.room:
            raise OverBudget


@dataclass
class Piece:
    """A run of rows of one image, encoded: rows y0 to y1 (the canvas's rows) of an image `width` pixels wide."""
    y0: int
    y1: int
    width: int
    src: str


@dataclass
class Plan:
    """What the report shows for one file: the bands (y0, y1) of the canvas to display, and the pieces of the images."""
    f: dict
    canvas: tuple[int, int] = (0, 0)
    bands: list[tuple[int, int]] = field(default_factory=list)
    pieces: dict[str, list[Piece]] = field(default_factory=dict)    # "ref", "act"
    whole: bool = False        # the pieces are whole images (so every row can be shown)
    omitted: bool = False      # left without images: the size budget was used up

    @property
    def nbytes(self) -> int:
        return sum(len(p.src) for ps in self.pieces.values() for p in ps)


def _runs(flags) -> list[tuple[int, int]]:
    runs, y = [], 0
    for key, group in itertools.groupby(flags):
        n = len(list(group))
        if key:
            runs.append((y, y + n))
        y += n
    return runs


def changed_runs(old: Image.Image, new: Image.Image, mask: Image.Image) -> list[tuple[int, int]]:
    """The runs of rows (of the larger canvas) that differ: rows with a changed pixel, and rows only one image has. A
    different width changes every row, since the columns past the narrower image differ."""
    height = max(old.height, new.height)
    if old.width != new.width:
        return [(0, height)]
    runs = _runs(mask.getprojection()[1])
    if old.height != new.height:
        runs.append((min(old.height, new.height), height))
    joined: list[tuple[int, int]] = []
    for a, b in sorted(runs):
        if joined and a <= joined[-1][1]:
            joined[-1] = (joined[-1][0], max(b, joined[-1][1]))
        else:
            joined.append((a, b))
    return joined


def make_bands(runs: list[tuple[int, int]], height: int, context: int) -> list[tuple[int, int]]:
    """Each run with `context` rows around it, bands that touch or nearly touch joined, and at most MAX_BANDS of them."""
    bands: list[tuple[int, int]] = []
    for a, b in runs:
        a, b = max(a - context, 0), min(b + context, height)
        if bands and a <= bands[-1][1] + BAND_GAP:
            bands[-1] = (bands[-1][0], max(b, bands[-1][1]))
        else:
            bands.append((a, b))
    while len(bands) > MAX_BANDS:
        i = min(range(len(bands) - 1), key=lambda k: bands[k + 1][0] - bands[k][1])
        bands[i:i + 2] = [(bands[i][0], bands[i + 1][1])]
    return bands


def encode(img: Image.Image, scale: float) -> tuple[str, bytes]:
    """Lossless WebP (every browser reads it, and flat screenshots shrink a lot), PNG past WebP's size limit."""
    if scale < 1:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.Resampling.LANCZOS)
    if img.mode == "RGBA" and img.getchannel("A").getextrema() == (255, 255):
        img = img.convert("RGB")
    buf = io.BytesIO()
    if max(img.size) <= WEBP_MAX:
        img.save(buf, "WEBP", lossless=True, quality=75, method=4)
        return "image/webp", buf.getvalue()
    img.save(buf, "PNG", optimize=True)
    return "image/png", buf.getvalue()


def _piece(img: Image.Image, y0: int, y1: int, scale: float, meter: Meter) -> Piece:
    mime, data = encode(img.crop((0, y0, img.width, y1)), scale)
    meter.add(len(data))
    return Piece(y0, y1, img.width, f"data:{mime};base64,{base64.b64encode(data).decode()}")


def build_plan(f: dict, merge_base: str, head: str, repo: Path, context: int, scale: float, meter: Meter,
               whole: bool = False) -> Plan:
    """The bands and encoded pieces for one file. `whole` embeds the whole images instead of the bands' rows only."""
    plan = Plan(f)
    if f["error"]:
        return plan
    try:
        old = blob(merge_base, f["path"], repo) if f["status"] != "added" else None
        new = blob(head, f["path"], repo) if f["status"] != "deleted" else None
    except (OSError, ValueError, Image.DecompressionBombError):
        return plan
    sizes = [i.size for i in (old, new) if i is not None]
    plan.canvas = (max(s[0] for s in sizes), max(s[1] for s in sizes))
    height = plan.canvas[1]
    images: dict[str, Image.Image] = {}
    if old is not None and new is not None:
        mask = changed_mask(old, new)
        runs = changed_runs(old, new, mask)
        if not runs:
            return plan
        plan.bands = make_bands(runs, height, context)
        images = {"ref": old, "act": new}
    else:
        plan.bands = [(0, height)]
        images = {"ref": old} if old is not None else {"act": new}   # type: ignore[dict-item]
    plan.whole = whole or plan.bands == [(0, height)]
    for kind, img in images.items():
        ranges = [(0, img.height)] if whole else [(a, min(b, img.height)) for a, b in plan.bands if a < img.height]
        plan.pieces[kind] = [_piece(img, a, b, scale, meter) for a, b in ranges]
    return plan


def _n(x: int) -> str:
    return f"{x:,}"


def _ratio(r: float) -> str:
    """The share of changed pixels as a fraction, the way Vitest prints it (0.02 is 2%)."""
    if r <= 0:
        return "0"
    return "<0.0001" if r < 0.0001 else f"{r:.4f}".rstrip("0").rstrip(".")


def _status(f: dict) -> str:
    if f["error"]:
        return "error"
    return "resized" if f["status"] == "modified" and f["old_size"] != f["new_size"] else f["status"]


def _share(f: dict) -> float:
    return f["changed_pixels"] / f["compared_pixels"] if f["compared_pixels"] else 0.0


def _verdict(f: dict) -> str:
    status, px = _status(f), f["changed_pixels"]
    differ = f"{_n(px)} pixels (ratio {_ratio(_share(f))}) differ"
    if status == "error":
        return f"Could not be compared: {f['error']}"
    if status == "added":
        return "New screenshot: there is no stored reference."
    if status == "deleted":
        return "Screenshot removed: there is no actual image any more."
    if status == "resized":
        return (f"Screenshot size changed from {_size(f['old_size'])} to {_size(f['new_size'])}. "
                + (f"{differ} in the area both images have." if px else "No pixel differs in the area both images have."))
    if not px:
        return "Same pixels as the stored reference: only the file's bytes changed (it was re-encoded)."
    return f"Screenshot does not match the stored reference. {differ}."


def _find(pieces: list[Piece], y: int) -> Piece | None:
    return next((p for p in pieces if p.y0 <= y < p.y1), None)


def overlay_boxes(f: dict) -> list[tuple[str, Box, str]]:
    """What the Actual image is boxed with, as (kind, box, label) in the canvas's pixels: the regions that changed ("change",
    from the comparison) and, when the size differs, the rows and columns only one image has, labelled ("added": only the
    actual image reaches them; "removed": only the reference does, so they are empty in the actual one)."""
    out: list[tuple[str, Box, str]] = [("change", (b[0], b[1], b[2], b[3]), "") for b in f["boxes"]]
    if f["error"] or not (f["old_size"] and f["new_size"]) or f["old_size"] == f["new_size"]:
        return out
    (ow, oh), (nw, nh) = f["old_size"], f["new_size"]
    if nh > oh:
        out.append(("added", (0, oh, nw, nh), f"{_n(nh - oh)} rows only in the actual image"))
    elif oh > nh:
        out.append(("removed", (0, nh, ow, oh), f"{_n(oh - nh)} rows only in the reference"))
    if nw > ow:
        out.append(("added", (ow, 0, nw, min(nh, oh)), f"{_n(nw - ow)} columns only in the actual image"))
    elif ow > nw:
        out.append(("removed", (nw, 0, ow, min(nh, oh)), f"{_n(ow - nw)} columns only in the reference"))
    return out


def _overlay(plan: Plan, band: tuple[int, int]) -> str:
    """The boxes that reach into the band, placed over its window by percentages of the band (the window clips them)."""
    width, (y0, y1) = plan.canvas[0], band
    out = []
    for kind, (left, top, right, bottom), label in overlay_boxes(plan.f):
        if bottom <= y0 or top >= y1:
            continue
        style = (f"left:{left / width * 100:.4f}%;top:{(top - y0) / (y1 - y0) * 100:.4f}%;"
                 f"width:{(right - left) / width * 100:.4f}%;height:{(bottom - top) / (y1 - y0) * 100:.4f}%")
        title = html.escape(label or f"Changed region at ({left}, {top}), {right - left}×{bottom - top}")
        out.append(f'<div class="box {kind}" style="{style}" title="{title}" aria-hidden="true">'
                   + (f"<b>{html.escape(label)}</b>" if label else "") + "</div>")
    return "".join(out)


def _window(plan: Plan, kind: str, label: str, band: tuple[int, int], eager: bool, seen: set[int]) -> str:
    """The band's rows of one image, in a box with the band's proportions: the image is placed in it by percentages, so
    one embedded image serves every band that shows a part of it. Its bytes are written once: the later windows onto the
    same piece (`seen`) carry no src, and the page's script copies the first one's."""
    width = plan.canvas[0]
    y0, y1 = band
    piece = _find(plan.pieces.get(kind, []), y0)
    style = f"aspect-ratio:{width}/{y1 - y0}"
    if piece is None:
        return (f'<div class="win none" style="{style}"><span>No rows here: the {label} image is shorter.</span>'
                f'{_overlay(plan, band) if kind == "act" else ""}</div>')
    top, tall = -(y0 - piece.y0) / (y1 - y0) * 100, (piece.y1 - piece.y0) / (y1 - y0) * 100
    alt = {"ref": f"Reference screenshot {plan.f['path']}", "act": f"Actual screenshot {plan.f['path']}"}[kind]
    if (y0, y1) != (0, plan.canvas[1]):
        alt += f", rows {y0} to {y1}"
    source = 'data-reuse="" ' if id(piece) in seen else f'src="{piece.src}" '
    seen.add(id(piece))
    return (f'<div class="win" style="{style}"><img {source}alt="{html.escape(alt)}" '
            f'loading="{"eager" if eager else "lazy"}" decoding="async" data-iw="{piece.width}" data-ih="{piece.y1 - piece.y0}" '
            f'style="top:{top:.4f}%;height:{tall:.4f}%;width:{piece.width / width * 100:.4f}%">'
            f'{_overlay(plan, band) if kind == "act" else ""}</div>')


def _gap(plan: Plan, y0: int, y1: int) -> str:
    what = "hidden: use “Show all rows”" if plan.whole else "omitted from this report"
    return f'<div class="gap">⋯ {_n(y1 - y0)} unchanged rows (y {_n(y0)}–{_n(y1)}) {what} ⋯</div>'


def _pane(plan: Plan, kind: str, label: str, eager: bool) -> str:
    """The bands for one tab, each with its y-range and a line for the rows left out between them. `kind` "slider" makes
    empty hosts that the page's script fills with the reference and actual windows."""
    height = plan.canvas[1]
    out = ['<div class="bands">']
    seen: set[int] = set()
    end = 0
    for k, (y0, y1) in enumerate(plan.bands):
        if y0 > end:
            out.append(_gap(plan, end, y0))
        title = (f"All {_n(height)} rows" if (y0, y1) == (0, height)
                 else f"Rows y {_n(y0)}–{_n(y1)} of {_n(height)}" + ("" if plan.whole else " (unchanged rows omitted)"))
        body = (f'<div class="host" data-i="{k}"></div>' if kind == "slider"
                else _window(plan, kind, label, (y0, y1), eager and k == 0, seen))
        out.append(f'<figure class="seg"><figcaption>{title}</figcaption>{body}</figure>')
        end = y1
    if end < height:
        out.append(_gap(plan, end, height))
    out.append('</div><div class="allview"></div>')
    whole_boxes = _overlay(plan, (0, height)) if kind == "act" and plan.whole and plan.bands != [(0, height)] else ""
    if whole_boxes:          # the "all rows" view's window is built by the script: it takes its boxes from here
        out.append(f"<template>{whole_boxes}</template>")
    return "".join(out)


def _resized_note(f: dict) -> str:
    (ow, oh), (nw, nh) = f["old_size"], f["new_size"]
    extra = (f" The reference has {_n(oh - nh)} more rows than the actual image." if oh > nh
             else f" The actual image has {_n(nh - oh)} more rows than the reference." if nh > oh else "")
    return (f'<p class="note">Dimensions differ: reference {_size(f["old_size"])}, actual {_size(f["new_size"])}. The changed '
            f"pixels are counted in the {min(ow, nw)}×{min(oh, nh)} area both images have (top-aligned).{extra} The Actual image "
            "marks the rows and columns only one of them has with a labelled box (green: only in actual, amber: only in the "
            "reference).</p>")


def _card(i: int, plan: Plan) -> str:
    f, status = plan.f, _status(plan.f)
    name = html.escape(f["path"])
    width, height = plan.canvas
    if f["old_size"] and f["new_size"]:
        sizes = _size(f["old_size"]) if f["old_size"] == f["new_size"] else f"{_size(f['old_size'])} → {_size(f['new_size'])}"
    else:
        sizes = _size(f["new_size"] or f["old_size"])
    meta = [f"<span>{sizes}</span>"]
    if status in ("modified", "resized"):
        meta += [f"<span>{_n(f['changed_pixels'])} changed pixels</span>", f"<span>ratio {_ratio(_share(f))}</span>"]
        if f["boxes"]:
            meta.append("<span>" + "; ".join(f"({b[0]}, {b[1]}) {b[2] - b[0]}×{b[3] - b[1]}" for b in f["boxes"]) + "</span>")
    tabs = [("ref", "Reference"), ("act", "Actual"), ("slider", "Slider")]
    shown = "slider"           # what opens first: the two images together, the change boxed on the right
    if status == "added":
        tabs, shown = [("act", "Actual")], "act"
    elif status == "deleted":
        tabs, shown = [("ref", "Reference")], "ref"
    elif not plan.pieces:
        tabs = []
    out = [f'<article class="card" id="img-{i}" data-status="{status}" data-name="{name.lower()}" data-w="{width}" '
           f'data-h="{height}" style="--w:{width}px"><header><h2><a href="#img-{i}">{name}</a></h2>'
           f'<span class="badge s-{status}">{status}</span></header><p class="verdict">{html.escape(_verdict(f))}</p>'
           f'<p class="meta">{"".join(meta)}</p>']
    if plan.omitted:
        out.append('<p class="note">The images of this file are left out: the report would have gone past its size budget.</p>')
    if not tabs:
        return "".join(out) + "</article>"
    if status == "resized":
        out.append(_resized_note(f))
    tools = []
    if any(kind == "act" for kind, _ in tabs) and overlay_boxes(f):
        tools.append('<label class="boxtoggle"><input type="checkbox" checked> Show change boxes</label>')
    if plan.whole and plan.bands != [(0, height)]:
        tools.append(f'<button type="button" class="allbtn" aria-pressed="false">Show all {_n(height)} rows</button>')
    if tools:
        out.append(f'<p class="tools">{"".join(tools)}</p>')
    out.append(f'<div class="tabs" role="tablist" aria-label="Views of {name}">')
    for kind, label in tabs:
        on = kind == shown
        out.append(f'<button type="button" role="tab" id="t-{i}-{kind}" aria-controls="p-{i}-{kind}" '
                   f'aria-selected="{"true" if on else "false"}" tabindex="{0 if on else -1}">{label}</button>')
    out.append("</div>")
    for kind, _label in tabs:
        extra = ""
        if kind == "slider":
            extra = '<p class="note">Drag the handle, or focus it and use the arrow keys: reference on the left, actual on the right.</p>'
        out.append(f'<div role="tabpanel" class="pane pane-{kind}" id="p-{i}-{kind}" aria-labelledby="t-{i}-{kind}" tabindex="0">'
                   f'{extra}{_pane(plan, kind, "actual" if kind == "act" else "reference", i < 2)}</div>')
    out.append("</article>")
    return "".join(out)


def render_report(plans: list[Plan], notes: list[str], run_url: str | None = None) -> str:
    """The page: heading, summary, filter, index, then one card per file."""
    counts: dict[str, int] = {}
    for p in plans:
        counts[_status(p.f)] = counts.get(_status(p.f), 0) + 1
    word = "image" if len(plans) == 1 else "images"
    parts = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
    index = "".join(
        f'<li data-status="{_status(p.f)}" data-name="{html.escape(p.f["path"].lower())}"><a href="#img-{i}">'
        f'{html.escape(Path(p.f["path"]).name)}</a> <span class="badge s-{_status(p.f)}">{_status(p.f)}</span>'
        + (f' <span class="n">{_n(p.f["changed_pixels"])} px</span>' if p.f["changed_pixels"] else "") + "</li>"
        for i, p in enumerate(plans))
    options = "".join(f'<option value="{s}">{s} ({n})</option>' for s, n in sorted(counts.items()))
    notices = "".join(f"<li>{html.escape(n)}</li>" for n in notes)
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="dark light"><title>Screenshot diff report</title>'
        f"<style>{REPORT_CSS}</style></head><body><main>"
        f'<h1>Screenshot diff</h1><p id="count" class="lead" aria-live="polite" data-total="{len(plans)}">'
        f"{len(plans)} {word} changed ({parts}).</p>"
        + (f'<ul class="notices" aria-label="Notes on this report">{notices}</ul>' if notices else "")
        + '<div class="controls"><label>Filter <input type="search" id="filter" placeholder="path contains…"></label>'
        f'<label>Status <select id="status"><option value="">all</option>{options}</select></label></div>'
        f'<nav aria-label="Changed images"><ol id="index">{index}</ol></nav>'
        '<p id="none" class="note" hidden>No image matches the filter.</p>'
        + "".join(_card(i, p) for i, p in enumerate(plans))
        + f'<footer>Made by tools/screenshot_diff.py{" for " + html.escape(run_url) if run_url else ""}. '
          "This page is one file: every image, script and style is inline, and it makes no network requests.</footer>"
        + f"</main><script>{REPORT_JS}</script></body></html>\n")


def build_report(report: dict, repo: Path = ROOT, budget: int = REPORT_BUDGET, run_url: str | None = None,
                 full_budget: int = FULL_BUDGET) -> tuple[str, list[str]]:
    """report.html for a `diff()` result, and the notes on what it had to cut to stay under `budget` bytes (the UTF-8 bytes
    of the returned text, notes included, which is what gets written). It never fails for size: it uses less context
    around the changes, then smaller images, and as a last resort leaves out the images of the largest files (and says so)."""
    order = {"modified": 0, "added": 1, "deleted": 2}
    files = sorted(report["files"], key=lambda f: (order[f["status"]], -f["changed_pixels"], f["path"]))
    merge_base, head = report["merge_base"], report["head"]
    # The images' share: the rest is the script, style and markup, about 6 KB a file (two views of each image, not three). It is an
    # estimate: the exact size is checked below, and a page that is over gets less context or smaller images.
    room = max(budget - (60_000 + 6_500 * len(files)), 0)

    def notes_for(context: int, scale: float, plans: list[Plan]) -> list[str]:
        notes: list[str] = []
        if (context, scale) != (REPORT_CONTEXT, 1.0):
            notes.append(f"To keep this file under {budget / 2**20:.3g} MB, the context around each change was cut from "
                         f"{REPORT_CONTEXT} to {context} rows" + (f" and the images were scaled down to {round(scale * 100)}%"
                                                                  if scale < 1 else "") + ".")
        left_out = sum(p.omitted for p in plans)
        if left_out:
            notes.append(f"{left_out} of {len(plans)} files are listed without their images: even at {round(scale * 100)}% "
                         "the report would have gone past its size budget.")
        return notes

    def page_of(plans: list[Plan], context: int, scale: float) -> tuple[str, list[str]]:
        notes = notes_for(context, scale, plans)
        return render_report(plans, notes, run_url), notes

    plans: list[Plan] = []
    chosen = None
    for context, scale in LEVELS:
        meter = Meter(room)
        try:
            plans = [build_plan(f, merge_base, head, repo, context, scale, meter) for f in files]
        except OverBudget:
            continue
        if len(page_of(plans, context, scale)[0].encode()) <= budget:
            chosen = (context, scale)
            break
    if chosen is None:          # not even the smallest level fits: keep the files that do, the biggest changes first
        context, scale = LEVELS[-1]
        meter, plans = Meter(room), []
        for f in files:
            before = meter.used
            try:
                plans.append(build_plan(f, merge_base, head, repo, context, scale, meter))
            except OverBudget:
                meter.used = before
                plans.append(Plan(f, omitted=True))
        # the notes and markup are in the file too: drop the biggest images until the exact text fits
        while len(page_of(plans, context, scale)[0].encode()) > budget and any(p.pieces for p in plans):
            biggest = max(plans, key=lambda p: p.nbytes)
            biggest.pieces, biggest.omitted = {}, True
    else:
        context, scale = chosen
    # Whole images, smallest first, while the file stays small: each replaces its file's bands, so one click shows every row.
    bands_only = {id(p): p.pieces for p in plans}
    used = sum(p.nbytes for p in plans)
    for p in sorted((p for p in plans if p.pieces and not p.whole), key=lambda p: p.canvas[0] * p.canvas[1]):
        try:
            whole = build_plan(p.f, merge_base, head, repo, context, scale,
                               Meter(min(full_budget, room) - used + p.nbytes), whole=True)
        except OverBudget:
            break
        used += whole.nbytes - p.nbytes
        p.pieces, p.whole = whole.pieces, True
    page, notes = page_of(plans, context, scale)
    if len(page.encode()) > budget:      # the estimate for the markup was too low: fall back to the bands, which fitted
        for p in plans:
            if id(p) in bands_only and p.whole and p.bands != [(0, p.canvas[1])]:
                p.pieces, p.whole = bands_only[id(p)], False
        page, notes = page_of(plans, context, scale)
    return page, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", help="the pull request's base (a ref or sha)")
    ap.add_argument("--head", default="HEAD", help="the pull request's head (a ref or sha)")
    ap.add_argument("--out", type=Path, help="where to write summary.md, summary.json and report.html")
    ap.add_argument("--run-url", help="the workflow run's URL, for the comment's link to the artifact")
    ap.add_argument("--report-url", help="where report.html was uploaded: the comment starts with a link to it")
    ap.add_argument("--from-json", type=Path, help="render the comment from this summary.json instead of comparing (with --out, "
                    "rewrites summary.md there)")
    ap.add_argument("--report-budget", type=int, default=REPORT_BUDGET, help="the most bytes of report.html")
    ap.add_argument("--repo", type=Path, default=ROOT, help="the repository to read (default: this one)")
    ap.add_argument("--dry-run", action="store_true", help="print the comment instead of writing files")
    args = ap.parse_args(argv)
    if args.from_json:
        comment = render(json.loads(args.from_json.read_text()), args.run_url, args.report_url)
        if args.out is None or args.dry_run:
            print(comment)
        else:
            (args.out / "summary.md").write_text(comment)
        return 0
    if not args.base:
        ap.error("--base is required (unless --from-json is given)")
    report = diff(args.base, args.head, args.repo)
    comment = render(report, args.run_url, args.report_url)
    if args.dry_run or args.out is None:
        print(comment)
    else:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "summary.md").write_text(comment)
        (args.out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
        wrote = f"wrote {args.out}"
        if report["files"]:
            page, notes = build_report(report, args.repo, args.report_budget, args.run_url)
            (args.out / REPORT).write_text(page, encoding="utf-8")
            wrote += f" (report.html: {len(page) / 2**20:.1f} MB" + ("; " + " ".join(notes) if notes else "") + ")"
        print(f"{len(report['files'])} screenshots changed; {wrote}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
