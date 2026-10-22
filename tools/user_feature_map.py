"""Generates the user feature map: what a person can do in the web app, by page and flow, seen the way `tools/control` and
`make verify` see it. Writes .claude/skills/verify-runway/references/feature-map.md; `--check` writes nothing and fails
when the committed file differs from what this generates. Standard library only.

Everything is read from the code: the pages (`PAGES` in frontend/verify/verify.mjs), their names in the navigation
(frontend/src/lib/nav.svelte.ts, components/MobileNav.svelte), their docs page, and the flows (frontend/verify/flows/*.json). The one hand-written part is
the gotchas, in .claude/skills/verify-runway/references/feature-gotchas.md (a `## <page or flow name>` heading and bullets
under it); a heading that names no page or flow is an error, so a renamed flow can't leave stale advice behind.

Not docs/feature-map.json (tools/feature_map.py): that one is by API route, for finding code."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERIFY = ROOT / "frontend/verify/verify.mjs"
FLOWS = ROOT / "frontend/verify/flows"
NAV = ROOT / "frontend/src/lib/nav.svelte.ts"
DOCS = ROOT / "docs/src/content/docs/using"
REFERENCES = ROOT / ".claude/skills/verify-runway/references"
OUT = REFERENCES / "feature-map.md"
GOTCHAS = REFERENCES / "feature-gotchas.md"

# Pages the navigation doesn't list, and the docs page (under using/) for pages whose name isn't their docs file's.
NOT_IN_NAV = {"setup": ("Settings", "")}
PAGE_DOCS = {"networth": "net-worth", "setup": "settings"}
# From references/ (where the map is written) to the docs pages, so the link works on GitHub and in an editor.
DOCS_LINK = "../../../../docs/src/content/docs/using"
VIEWPORT_ORDER = ["phone", "desktop", "landscape"]


def pages(verify: str) -> list[str]:
    found = re.search(r"export const PAGES = \[(.*?)\];", verify, re.DOTALL)
    return re.findall(r'"([^"]+)"', found.group(1)) if found else []


def nav_labels(nav: str) -> dict[str, tuple[str, str]]:
    """page -> (label, the nav group's name), from the MAIN_NAV and MONEY_NAV items."""
    out = {}
    for group in re.finditer(r'label: "([^"]+)",\s*items: \[(.*?)\n  \],', nav, re.DOTALL):
        for page, label in re.findall(r'page: "([^"]+)", label: "([^"]+)"', group.group(2)):
            out[page] = (label, group.group(1))
    return out


def tab_pages(mobile_nav: str) -> list[str]:
    """The pages in the phone's tab bar (the rest are under its More sheet)."""
    found = re.search(r"const TAB_PAGES = \[(.*?)\]", mobile_nav)
    return re.findall(r'"([^"]+)"', found.group(1)) if found else []


def reach(page: str, nav: dict, tabs: list[str]) -> str:
    desktop = f"desktop: sidebar > {nav[page][1]} > {nav[page][0]}" if page in nav else "desktop: the sidebar's Settings link"
    phone = "phone: the tab bar" if page in tabs else "phone: tab bar > More"
    return f"{desktop}; {phone}"


def docs_page(page: str, docs: Path = DOCS) -> tuple[str, str, str] | None:
    """(file stem, title, description) of the docs page for `page`, or None."""
    path = docs / f"{PAGE_DOCS.get(page, page)}.md"
    if not path.exists():
        return None
    front = dict(re.findall(r"^(title|description): (.*)$", path.read_text().split("---")[1], re.MULTILINE))
    return path.stem, front.get("title", ""), front.get("description", "")


def flows(directory: Path = FLOWS) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))]


def gotchas(text: str, names: set[str]) -> tuple[dict[str, list[str]], list[str]]:
    """name -> bullets, and the problems (a heading that names no page or flow, text outside a heading, a repeat)."""
    out: dict[str, list[str]] = {}
    problems = []
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            if current not in names:
                problems.append(f"feature-gotchas.md: `## {current}` is not a page or flow name")
            elif current in out:
                problems.append(f"feature-gotchas.md: `## {current}` appears twice")
            out.setdefault(current, [])
        elif line.startswith("- ") and current:
            out[current].append(line[2:].strip())
        elif current is None:
            continue   # the file's title and intro
        elif line.strip() and not line.startswith(("- ", "  ")):
            problems.append(f"feature-gotchas.md: only `- ` bullets go under a heading: {line[:50]!r}")
    return out, problems


def quoted(selector: str) -> list[str]:
    """The visible names a selector points at: aria-label prefixes, has-text and text-is strings, text= selectors."""
    found = re.findall(r"""aria-label[\^*]?=(?:'([^']+)'|"([^"]+)")|(?:has-text|text-is)\((?:'([^']+)'|"([^"]+)")\)|text=(.+)$""", selector)
    return [next(x for x in f if x) for f in found]


def uniq(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def summary(flow: dict, start: str = "") -> dict[str, list[str]]:
    routes, clicks, texts = [], [], []
    for step in flow["steps"]:
        if "goto" in step:
            routes.append(step["goto"])
        for key in ("click", "fill", "press"):
            if key in step:
                target = step[key]["selector"] if isinstance(step[key], dict) else step[key]
                clicks += quoted(target)
        if "expect_text" in step:
            texts.append(step["expect_text"]["text"])
    return {"goes to": [r for r in uniq(routes) if r != start], "touches": uniq(clicks), "expects to see": uniq(texts)}


def widths(flow: dict) -> list[str]:
    chosen = flow.get("viewports") or ["phone", "desktop"]
    return sorted(chosen, key=lambda v: VIEWPORT_ORDER.index(v) if v in VIEWPORT_ORDER else 99)


def code(items: list[str]) -> str:
    return ", ".join(f"`{i}`" for i in items)


def render(page_names: list[str], nav: dict, tabs: list[str], all_flows: list[dict], notes: dict[str, list[str]], docs: Path = DOCS) -> str:
    lines = [
        "# User feature map",
        "",
        "Generated by `tools/user_feature_map.py` (`make user-feature-map`); do not edit. The gotchas come from "
        "[feature-gotchas.md](feature-gotchas.md), which is hand-edited. `make fleet-checks` fails when this file is out of date.",
        "",
        "What a person can do in the web app, by page: what is there, how to reach it, how to drive it with `tools/control` "
        "(or `make verify`), and what usually breaks. It is not `docs/feature-map.json` (and its docs page), which goes by API "
        "route to find the code behind a feature; this goes by what a user does, from the pages and flows `make verify` runs. "
        "All of it is demo data; `tools/control start` first, and `flow <name>` runs a flow's steps (after `reset` for the data "
        "`make verify` starts it from).",
        "",
    ]
    for page in page_names:
        label, _group = nav.get(page, NOT_IN_NAV.get(page, (page.title(), "")))
        doc = docs_page(page, docs)
        lines += [f"## {label} (`{page}`)", ""]
        if doc:
            lines.append(f"- **What exists:** {doc[2]} Docs: [{doc[1]}]({DOCS_LINK}/{doc[0]}.md).")
        else:
            lines.append("- **What exists:** no docs page of its own.")
        lines += [
            f"- **Reach it:** {reach(page, nav, tabs)}; URL `#{page}`; control `tools/control goto {page}`.",
            f"- **Drive it:** `tools/control goto {page}`, `tools/control snapshot --text`, then `click`/`type`/`press`; "
            f"`tools/control viewport phone|desktop`; batch check `make verify PAGES=\"{page}\"`.",
        ]
        if notes.get(page):
            lines += ["- **What usually breaks:**", *[f"  - {n}" for n in notes[page]]]
        mine = [f for f in all_flows if f.get("page", "overview") == page]
        if mine:
            lines += ["", f"### Flows on {label}", ""]
        for flow in mine:
            name = flow["name"]
            s = summary(flow, f"#{flow.get('page', 'overview')}")
            run = ("`tools/control start --signed-in`, then " if flow.get("signed_in") else "") + f"`tools/control flow {name}`"
            lines += [f"#### `{name}`", "",
                      f"- **Starts at:** `#{flow.get('page', 'overview')}`" + "".join(f"; {k}: {code(v)}" for k, v in s.items() if k == "goes to" and v) + ".",
                      f"- **Widths:** {', '.join(widths(flow))}" + ("; signed in (needs `start --signed-in`)" if flow.get("signed_in") else "") + ".",
                      ]
            if s["touches"]:
                lines.append(f"- **Touches:** {code(s['touches'])}.")
            if s["expects to see"]:
                lines.append(f"- **Expects to see:** {code(s['expects to see'])}.")
            lines.append(f"- **Drive it:** {run}; or `make verify PAGES=\"{name}\"`.")
            if notes.get(name):
                lines += ["- **What usually breaks:**", *[f"  - {n}" for n in notes[name]]]
            lines.append("")
        if not mine:
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generate(root: Path = ROOT) -> tuple[str, list[str]]:
    """The file's text and the problems with the inputs (a gotcha for a name that no longer exists)."""
    page_names = pages((root / "frontend/verify/verify.mjs").read_text())
    all_flows = flows(root / "frontend/verify/flows")
    names = set(page_names) | {f["name"] for f in all_flows}
    gotcha_file = root / ".claude/skills/verify-runway/references/feature-gotchas.md"
    notes, problems = gotchas(gotcha_file.read_text() if gotcha_file.exists() else "", names)
    if not page_names:
        problems.append("no PAGES found in frontend/verify/verify.mjs")
    return render(page_names, nav_labels((root / "frontend/src/lib/nav.svelte.ts").read_text()),
                  tab_pages((root / "frontend/src/lib/components/MobileNav.svelte").read_text()), all_flows, notes, root / "docs/src/content/docs/using"), problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="write nothing; fail when the committed file is out of date")
    args = ap.parse_args(argv)
    text, problems = generate()
    if not args.check and not problems:
        OUT.write_text(text)
    if args.check:
        problems += check(OUT.read_text() if OUT.exists() else None, text)
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


def check(committed: str | None, generated: str) -> list[str]:
    name = str(OUT.relative_to(ROOT))
    if committed is None:
        return [f"{name}: missing; run make user-feature-map and commit it"]
    if committed != generated:
        return [f"{name} isn't what tools/user_feature_map.py generates: run make user-feature-map and commit the result "
                "(hand-written advice goes in references/feature-gotchas.md, never in the generated file)"]
    return []


if __name__ == "__main__":
    sys.exit(main())
