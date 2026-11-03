"""Runway's own checks, run by `fleet checks` through `[tool.fleet] extra-checks` in pyproject.toml (`make fleet-checks`
and CI). Each takes fleet's check context (`.root`, the repository; `.commits`, the pull request's range when
`--commits BASE..HEAD` is given, with `.base` where the branch left main) and returns a list of problems. Standard library
only, and nothing here runs a browser: these read what `make verify` would.

- Accessibility baseline (frontend/verify/a11y-baseline.txt): the findings `make verify` tolerates, one line each,
  `<page or flow> @ <width> | <finding> | <reason>`; each line has all three parts, names a page or flow that exists at a
  width it is shot at, and is listed once; with `--commits`, no line is new compared with the base. A line whose finding
  no longer occurs fails `make verify` itself, so the list only shrinks.
- Flow inheritance: a flow that inherits its page's baseline (`"inherits_page_baseline": true`, frontend/verify/a11y.mjs)
  names the page it starts on, written out, and runs only at widths pages are visited at: the same rules verify applies
  when it starts, so a mistake fails here before CI's verify run.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

VERIFY = "frontend/verify/verify.mjs"
FLOWS = "frontend/verify/flows"
A11Y_BASELINE = "frontend/verify/a11y-baseline.txt"


def _verify_list(text: str, name: str) -> list[str]:
    found = re.search(r"export const " + name + r" = \[([^\]]*)\]", text)
    return re.findall(r"""["']([\w-]+)["']""", found.group(1)) if found else []


def _flows(flows: Path) -> list[dict[str, Any]]:
    return [json.loads(path.read_text()) for path in sorted(flows.glob("*.json"))]


def verify_visits(text: str, flows: Path) -> set[str]:
    """Every `<page or flow> @ <width>` visit `make verify` makes: a page at the default widths, a flow (named
    `flow <name>`) at the widths it lists, else the defaults."""
    pages, widths = _verify_list(text, "PAGES"), _verify_list(text, "DEFAULT_VIEWPORTS")
    visits = {f"{page} @ {w}" for page in pages for w in widths}
    for flow in _flows(flows):
        visits |= {f"flow {flow['name']} @ {w}" for w in flow.get("viewports") or widths}
    return visits


def check_a11y_baseline(text: str, visits: set[str]) -> list[str]:
    problems = []
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        where = f"{A11Y_BASELINE} line {number}"
        parts = [p.strip() for p in line.split(" | ")]
        if len(parts) < 3 or not all(parts):
            problems.append(f"{where}: expected `<page or flow> @ <width> | <finding> | <reason>`, all three parts")
            continue
        if parts[0] not in visits:
            problems.append(f"{where}: `{parts[0]}` is not a page or flow make verify visits at that width: delete the line")
        key = " | ".join(parts[:-1])
        if key in seen:
            problems.append(f"{where}: listed twice")
        seen.add(key)
    return problems


def baseline_lines(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")}


def check_a11y_shrinks(base_text: str, text: str) -> list[str]:
    """The baseline only shrinks: every line is one the base (main) already had, word for word."""
    added = sorted(baseline_lines(text) - baseline_lines(base_text))
    return [f"{A11Y_BASELINE}: `{line}` is new: the list only shrinks, so fix the finding instead of listing it" for line in added]


def check_flow_inheritance(text: str, flows: Path) -> list[str]:
    pages, widths = _verify_list(text, "PAGES"), _verify_list(text, "DEFAULT_VIEWPORTS")
    problems = []
    for path in sorted(flows.glob("*.json")):
        flow = json.loads(path.read_text())
        want = flow.get("inherits_page_baseline")
        if want is None or want is False:
            continue
        where = f"{FLOWS}/{path.name}"
        if want is not True:
            problems.append(f"{where}: inherits_page_baseline is true or false")
            continue
        if not isinstance(flow.get("page"), str):
            problems.append(f"{where}: inherits_page_baseline needs `page`, written out: the page whose baseline it inherits")
        elif flow["page"] not in pages:
            problems.append(f"{where}: inherits_page_baseline: `{flow['page']}` is not a page make verify visits")
        problems += [f"{where}: inherits_page_baseline: no page is visited at {w}, so there is no baseline to inherit"
                     for w in flow.get("viewports") or [] if w not in widths]
    return problems


def _read(root: Path, path: str) -> str:
    file = root / path
    return file.read_text() if file.exists() else ""


def _at(root: Path, rev: str, path: str) -> str | None:
    """The file at `rev`, or None when that revision has none (the pull request that introduces it)."""
    out = subprocess.run(["git", "show", f"{rev}:./{path}"], cwd=root, capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else None


def a11y_baseline(context: Any) -> list[str]:
    root: Path = context.root
    verify = _read(root, VERIFY)
    problems = check_a11y_baseline(_read(root, A11Y_BASELINE), verify_visits(verify, root / FLOWS))
    if context.commits is not None:
        base = _at(root, context.commits.base, A11Y_BASELINE)
        if base is not None:
            problems += check_a11y_shrinks(base, _read(root, A11Y_BASELINE))
    return problems


def flow_inheritance(context: Any) -> list[str]:
    root: Path = context.root
    return check_flow_inheritance(_read(root, VERIFY), root / FLOWS)


CHECKS = [a11y_baseline, flow_inheritance]
