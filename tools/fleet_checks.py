"""Checks that stand in for instructions agents kept getting wrong (AGENTS.md, "Mistakes that keep coming back"): each
one a correction that used to live as prose and now fails `make check` and CI instead. Standard library only.

- Migrations (runway/storage/migrations/versions/): one Alembic head (two branches that each add the next number both point at
  the same parent, and together leave two heads); revision ids unique, four digits, and the start of their file's name;
  and every migration from TESTED_FROM on has its own test in tests/test_migrations.py, a method named
  `test_<revision>_...`.
- Commits (`--commits BASE..HEAD`, a pull request's): an agent's commit (one with a `Claude-Session:` trailer, or any
  `Co-Authored-By:` trailer naming Claude) carries exactly one `Co-Authored-By: Claude <Model> <version>
  <noreply@anthropic.com>` trailer naming the model that wrote it.
- Images (`--commits`): a raster image the branch adds or changes is one `make verify` writes to screenshots/ (a page, a
  page's first screen or a flow image, `<name>-<phone|desktop>.png`, the names read from frontend/verify/verify.mjs and its
  flows) or sits where images belong (the extension's icons, the served assets, the docs site's logo); a hand-made before
  and after image fails.
- Accessibility baseline (frontend/verify/a11y-baseline.txt): the findings `make verify` tolerates, one line each,
  `<page or flow> @ <width> | <finding> | <reason>`; each line has all three parts, names a page or flow that exists at a width
  it is shot at, and is listed once; with `--commits`, no line is new compared with the base. A line whose finding no longer occurs fails `make verify` itself (this check reads no
  browser), so the list only shrinks.
- Workflows (.github/workflows/, .github/actions/): each workflow runs bash by default (so steps get -eo pipefail);
  every action pinned by SHA has its version in a comment; `gh api` with `per_page` paginates; and no `runner`, `steps`, `job` or
  `env` expression in a workflow's or job's `env:` (GitHub rejects the whole file, which then never runs).
- Skills (.claude/skills/*/SKILL.md): valid frontmatter (`name`, its folder's, and a `description`), a body, links that
  lead somewhere; and the verify-runway skill's references/cli.md is exactly what tools/control's help generates
  (`make verify-skill`), so the skill can't drift from the CLI. That one runs Node (frontend/verify/control.mjs, which
  needs no packages for its help).

Exits 1, listing each problem, when any check fails."""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "runway/storage/migrations/versions"
MIGRATION_TESTS = ROOT / "tests/test_migrations.py"
WORKFLOWS = ROOT / ".github/workflows"
ACTIONS = ROOT / ".github/actions"

# Migrations from this one on need a test of their own; the ones before it predate the rule. Never lower it.
TESTED_FROM = "0036"

REVISION = re.compile(r"^\d{4}$")
MODEL_FAMILIES = ("Opus", "Sonnet", "Haiku", "Fable")
CO_AUTHOR = re.compile(r"^co-authored-by:\s*(.*)$", re.IGNORECASE | re.MULTILINE)
SESSION = re.compile(r"^claude-session:\s*\S", re.IGNORECASE | re.MULTILINE)
GOOD_CO_AUTHOR = re.compile(r"^Claude (" + "|".join(MODEL_FAMILIES) + r") \d+(\.\d+)* <noreply@anthropic\.com>$")
CLAUDE = re.compile(r"\bclaude\b|anthropic\.com", re.IGNORECASE)


# --- Migrations ---------------------------------------------------------------------------------------------------

def _assigned(tree: ast.Module, name: str) -> object:
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(getattr(t, "id", None) == name for t in targets) and getattr(node, "value", None) is not None:
            return ast.literal_eval(node.value)  # type: ignore[arg-type]
    return ...  # not assigned at all


def migrations(directory: Path = MIGRATIONS) -> list[dict]:
    """Each migration file's revision and the revisions it follows (a tuple: a merge follows more than one)."""
    found = []
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("__"):
            continue
        tree = ast.parse(path.read_text(), str(path))
        revision, down = _assigned(tree, "revision"), _assigned(tree, "down_revision")
        downs = () if down is None or down is ... else tuple(down) if isinstance(down, (list, tuple)) else (down,)
        found.append({"file": path.name, "revision": revision, "down": downs})
    return found


def check_migrations(found: list[dict], tests_text: str) -> list[str]:
    problems = []
    revisions = [m["revision"] for m in found]
    for m in found:
        rev = m["revision"]
        if not isinstance(rev, str) or not REVISION.match(rev):
            problems.append(f"{m['file']}: revision should be four digits, like '0041' (it is {rev!r})")
        elif not m["file"].startswith(rev + "_"):
            problems.append(f"{m['file']}: the file name should start with its revision, {rev}_")
        if revisions.count(rev) > 1 and m is next(x for x in found if x["revision"] == rev):
            files = ", ".join(x["file"] for x in found if x["revision"] == rev)
            problems.append(f"revision {rev!r} is used more than once ({files}): renumber yours after main's newest")
        for d in m["down"]:
            if d not in revisions:
                problems.append(f"{m['file']}: down_revision {d!r} is no migration here")
    followed = {d for m in found for d in m["down"]}
    heads = sorted({m["revision"] for m in found if m["revision"] not in followed}, key=str)
    if len(heads) > 1:
        problems.append(f"more than one Alembic head ({', '.join(map(str, heads))}): another branch added a migration "
                        "with the same parent; merge main and make yours follow its newest")
    if len(heads) == 0 and found:
        problems.append("no Alembic head: the migrations go round in a loop")
    tested = set(re.findall(r"^\s*def test_(\d{4})_", tests_text, re.MULTILINE))
    for m in found:
        rev = m["revision"]
        if isinstance(rev, str) and REVISION.match(rev) and rev >= TESTED_FROM and rev not in tested:
            problems.append(f"{m['file']}: no test of its own in tests/test_migrations.py (a method named test_{rev}_...); "
                            "downgrade to the revision before it, write the data it changes, upgrade and check the result")
    return problems


# --- Commits ------------------------------------------------------------------------------------------------------

def check_commit(sha: str, message: str) -> list[str]:
    """An agent's commit names the model that wrote it, in exactly one Co-Authored-By trailer."""
    co_authors = [c.strip() for c in CO_AUTHOR.findall(message)]
    claude = [c for c in co_authors if CLAUDE.search(c)]
    if not SESSION.search(message) and not claude:
        return []   # not an agent's commit
    subject = message.strip().splitlines()[0] if message.strip() else ""
    where = f"commit {sha[:10]} ({subject[:60]})"
    if not claude:
        return [f"{where}: an agent's commit (it has a Claude-Session trailer) needs a "
                "'Co-Authored-By: Claude <Model> <version> <noreply@anthropic.com>' trailer for the model that wrote it"]
    if len(claude) > 1:
        return [f"{where}: more than one Claude Co-Authored-By trailer; keep the one for the model that wrote it"]
    if not GOOD_CO_AUTHOR.match(claude[0]):
        return [f"{where}: 'Co-Authored-By: {claude[0]}' should name the model, as "
                f"'Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>' (one of {', '.join(MODEL_FAMILIES)})"]
    return []


def commits(revision_range: str) -> list[tuple[str, str]]:
    out = subprocess.run(["git", "log", "--format=%H%x00%B%x1e", revision_range, "--"], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout
    return [tuple(rec.strip("\n").split("\0", 1)) for rec in out.split("\x1e") if rec.strip()]  # type: ignore[misc]


# --- Images -------------------------------------------------------------------------------------------------------

IMAGE = re.compile(r"\.(png|jpe?g|gif|webp|avif|ico|heic|bmp|tiff?)$", re.IGNORECASE)
SCREENSHOTS = "screenshots/"
# Where an image belongs besides screenshots/: the extension's icons, the served assets (icons, logo) and the docs site's logo.
# The docs site and the README take their screenshots from screenshots/, so they never need a picture of their own.
IMAGE_HOMES = ("extension/icons/", "runway/static/")   # folders: anything under them
IMAGE_FILES = ("docs/src/assets/logo.png",)            # exactly these files
VERIFY = ROOT / "frontend/verify/verify.mjs"
FLOWS = ROOT / "frontend/verify/flows"


def _verify_list(text: str, name: str) -> list[str]:
    found = re.search(r"export const " + name + r" = \[([^\]]*)\]", text)
    return re.findall(r"""["']([\w-]+)["']""", found.group(1)) if found else []


def verify_images(text: str | None = None, flows: Path = FLOWS) -> set[str]:
    """The file names `make verify` can write: `<page>-<width>.png` and its first screen `<page>-top-<width>.png` for each
    page in verify.mjs's PAGES at its DEFAULT_VIEWPORTS, and for each flow `flow-<name>-<width>.png` plus
    `flow-<name>-<step>-<width>.png` for each of its `screenshot` steps, at the widths it lists (else the defaults)."""
    text = VERIFY.read_text() if text is None else text
    pages, widths = _verify_list(text, "PAGES"), _verify_list(text, "DEFAULT_VIEWPORTS")
    top = re.search(r"""export const TOP = ["'](\w+)["']""", text)
    names = {f"{page}{suffix}-{w}.png" for page in pages for w in widths for suffix in ("", f"-{top.group(1) if top else 'top'}")}
    for path in sorted(flows.glob("*.json")):
        flow = json.loads(path.read_text())
        steps = [s["screenshot"] for s in flow.get("steps", []) if isinstance(s.get("screenshot"), str)]
        for w in flow.get("viewports") or widths:
            names |= {f"flow-{flow['name']}-{w}.png", *(f"flow-{flow['name']}-{n}-{w}.png" for n in steps)}
    return names


def added_images(base: str) -> list[str]:
    """The images this branch adds, changes or renames since `base` (a deleted one is always fine)."""
    out = subprocess.run(["git", "diff", "--name-only", "-z", "--diff-filter=AMR", base, "HEAD", "--"], cwd=ROOT,
                         check=True, capture_output=True, text=True).stdout
    return [path for path in out.split("\0") if IMAGE.search(path)]   # -z: git doesn't quote a non-ASCII name


def check_images(paths: list[str], made: set[str]) -> list[str]:
    return [f"{path}: the only images a change commits are the ones make verify writes to {SCREENSHOTS} (a page or flow "
            "image named <name>-phone.png or <name>-desktop.png; they are the visual regression baselines), plus the icons and "
            "logos in extension/icons/, runway/static/ and docs/src/assets/logo.png; show a before and after through those images' "
            "diff, not a hand-made file"
            for path in paths if not (path.startswith(IMAGE_HOMES) or path in IMAGE_FILES
                                      or (path.startswith(SCREENSHOTS) and path[len(SCREENSHOTS):] in made))]


# --- Workflows ----------------------------------------------------------------------------------------------------

USES = re.compile(r"^[ \t]*(?:-[ \t]*)?uses:[ \t]*([^\s#]+)(.*)$", re.MULTILINE)
PINNED = re.compile(r"@[0-9a-f]{40}$")
BASH_DEFAULT = re.compile(r"^defaults:[ \t]*\n[ \t]+run:[ \t]*\n(?:[ \t]{4,}\S.*\n)*?[ \t]{4,}shell:[ \t]*bash[ \t]*(?:#.*)?$",
                          re.MULTILINE)
# A workflow-level or job-level `env:` block (a step's is indented deeper). GitHub's context table lets these see `github`, `vars`,
# `secrets`, `inputs`, `needs`, `strategy` and `matrix` only: a `runner`, `steps`, `job` or `env` expression there makes the
# whole file invalid, so it never runs and shows as a failed run with no jobs. Put it in a step's `env:`.
EARLY_ENV = re.compile(r"^( {0}| {4})env:[ \t]*\n((?:\1[ ]{2,}\S.*\n)+)", re.MULTILINE)
# (not after a word character or a dot: `matrix.runner.os` and `needs.job.outputs.x` are fine; `.*` not `[^}]*`, which a `{0}` in a format() would stop)
STEP_ONLY_CONTEXT = re.compile(r"\$\{\{.*(?<![\w.])(runner|steps|job|env)\.")
GH_API_LIST = re.compile(r"\bgh api\b[^\n]*per_page=")


# --- The accessibility baseline -------------------------------------------------------------------------------------

A11Y_BASELINE = ROOT / "frontend/verify/a11y-baseline.txt"
A11Y_BASELINE_NAME = "frontend/verify/a11y-baseline.txt"


def verify_visits(text: str | None = None, flows: Path = FLOWS) -> set[str]:
    """Every `<page or flow> @ <width>` visit `make verify` makes: a page at the default widths, a flow (named
    `flow <name>`) at the widths it lists, else the defaults."""
    text = VERIFY.read_text() if text is None else text
    pages, widths = _verify_list(text, "PAGES"), _verify_list(text, "DEFAULT_VIEWPORTS")
    visits = {f"{page} @ {w}" for page in pages for w in widths}
    for path in sorted(flows.glob("*.json")):
        flow = json.loads(path.read_text())
        visits |= {f"flow {flow['name']} @ {w}" for w in flow.get("viewports") or widths}
    return visits


def check_a11y_baseline(text: str, visits: set[str]) -> list[str]:
    problems = []
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        where = f"{A11Y_BASELINE_NAME} line {number}"
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
    return [f"{A11Y_BASELINE_NAME}: `{line}` is new: the list only shrinks, so fix the finding instead of listing it" for line in added]


def base_a11y_baseline(base: str) -> str | None:
    """The base's baseline, or None when the base has none yet (the pull request that introduces the list)."""
    out = subprocess.run(["git", "show", f"{base}:{A11Y_BASELINE_NAME}"], cwd=ROOT, capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else None


def check_workflow(name: str, text: str, workflow: bool = True) -> list[str]:
    problems = []
    if workflow and not BASH_DEFAULT.search(text):
        problems.append(f"{name}: no top-level `defaults: run: shell: bash` (steps then run without -o pipefail)")
    for m in USES.finditer(text):
        target, rest = m.group(1), m.group(2)
        if target.startswith("./") or target.startswith("docker://"):
            continue
        if not PINNED.search(target):
            problems.append(f"{name}: {target} isn't pinned by its commit SHA")
        elif not re.search(r"#\s*v?\d", rest):
            problems.append(f"{name}: {target} has no version in a comment (`# v1.2.3`)")
    for block in EARLY_ENV.finditer(text if workflow else ""):
        for line in block.group(2).splitlines():
            m = STEP_ONLY_CONTEXT.search(line)
            if m:
                problems.append(f"{name}: `{m.group(1)}` isn't available in a workflow's or job's `env:` (the file would be invalid "
                                f"and never run); set it in a step's `env:`: {line.strip()[:80]}")
    for line in text.splitlines():
        if GH_API_LIST.search(line) and "--paginate" not in line:
            problems.append(f"{name}: `gh api ... per_page=` without --paginate reads only the first page: {line.strip()[:80]}")
    return problems


def check_workflows() -> list[str]:
    problems = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        problems += check_workflow(str(path.relative_to(ROOT)), path.read_text())
    for path in sorted(ACTIONS.glob("*/action.y*ml")):
        problems += check_workflow(str(path.relative_to(ROOT)), path.read_text(), workflow=False)
    return problems


# --- Skills -------------------------------------------------------------------------------------------------------

SKILLS = ROOT / ".claude/skills"
CONTROL = ROOT / "frontend/verify/control.mjs"
CLI_REFERENCE = SKILLS / "verify-runway/references/cli.md"
SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
MD_LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def check_skill(folder: str, text: str, exists=lambda rel: True) -> list[str]:
    """A skill's SKILL.md: frontmatter of `key: value` lines with a `name` (its folder's) and a `description` (what makes
    an agent load it: one line, at most 1024 characters), a body, and relative links that lead to files that exist."""
    where = f".claude/skills/{folder}/SKILL.md"
    found = FRONTMATTER.match(text)
    if not found:
        return [f"{where}: no frontmatter (a --- line, `name:` and `description:`, then a --- line)"]
    fields: dict[str, str] = {}
    problems = []
    for line in found.group(1).splitlines():
        key, sep, value = line.partition(":")
        if not sep or not key.strip() or key != key.strip():
            problems.append(f"{where}: frontmatter line isn't `key: value`: {line[:60]!r}")
            continue
        fields[key.strip()] = value.strip()
    if fields.get("name") != folder or not SKILL_NAME.match(folder):
        problems.append(f"{where}: `name` should be {folder!r}, its folder's name (lowercase letters, digits and -), not {fields.get('name')!r}")
    description = fields.get("description", "")
    if not description:
        problems.append(f"{where}: no `description` (it is what makes an agent use the skill)")
    elif len(description) > 1024 or "<" in description or ">" in description:
        problems.append(f"{where}: `description` is at most 1024 characters, without < or > ({len(description)} now)")
    if not found.group(2).strip():
        problems.append(f"{where}: no body after the frontmatter")
    for target in MD_LINK.findall(found.group(2)):
        if "://" not in target and not target.startswith("/") and not exists(target):
            problems.append(f"{where}: links to {target}, which isn't there")
    return problems


def check_skills(directory: Path = SKILLS) -> list[str]:
    problems = []
    for skill in sorted(p for p in directory.glob("*/SKILL.md")):
        problems += check_skill(skill.parent.name, skill.read_text(), lambda rel, base=skill.parent: (base / rel).exists())
    return problems


def check_cli_reference(committed: str | None, generated: str | None, why: str = "") -> list[str]:
    """references/cli.md is tools/control's help, generated (`make verify-skill`), so the skill can't drift from the CLI."""
    name = str(CLI_REFERENCE.relative_to(ROOT))
    if generated is None:
        return [f"{name}: couldn't generate it to compare ({why}); it needs Node"]
    if committed is None:
        return [f"{name}: missing; run make verify-skill and commit it"]
    if committed != generated:
        return [f"{name} isn't what tools/control's help generates: run make verify-skill and commit the result "
                "(edit the help in frontend/verify/control.mjs, never the file)"]
    return []


def generated_cli_reference() -> tuple[str | None, str]:
    try:
        done = subprocess.run(["node", str(CONTROL), "help", "--markdown"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    return (done.stdout, "") if done.returncode == 0 else (None, (done.stderr or done.stdout).strip()[-200:])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--commits", metavar="BASE..HEAD", help="also check these commits' trailers (a pull request's)")
    args = ap.parse_args(argv)
    problems = check_migrations(migrations(), MIGRATION_TESTS.read_text())
    problems += check_workflows()
    problems += check_a11y_baseline(A11Y_BASELINE.read_text() if A11Y_BASELINE.exists() else "", verify_visits())
    problems += check_skills()
    generated, why = generated_cli_reference()
    problems += check_cli_reference(CLI_REFERENCE.read_text() if CLI_REFERENCE.exists() else None, generated, why)
    if args.commits:
        for sha, message in commits(args.commits):
            problems += check_commit(sha, message)
        base = subprocess.run(["git", "merge-base", *args.commits.split("..", 1)], cwd=ROOT, check=True, capture_output=True,
                              text=True).stdout.strip() if ".." in args.commits else args.commits
        problems += check_images(added_images(base), verify_images())
        base_baseline = base_a11y_baseline(base)
        if base_baseline is not None:
            problems += check_a11y_shrinks(base_baseline, A11Y_BASELINE.read_text() if A11Y_BASELINE.exists() else "")
    for p in problems:
        print(f"::error::{p}" if "GITHUB_ACTIONS" in os.environ else p, file=sys.stderr)
    if not problems:
        print("Fleet checks passed: one migration head, migrations tested" + (", commit trailers, images only where make verify puts them" if args.commits else "")
              + ", a11y baseline, workflow conventions, skills and the generated tools/control reference.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
