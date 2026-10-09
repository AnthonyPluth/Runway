"""The independent review's decisions, for .github/workflows/agent-review.yml (run from main's copy, never a pull
request's). Standard library only.

  agent_review.py detect          Is this an agent's pull request? Reads its commit messages, one JSON string per line,
                                  on stdin, and its description in $BODY; prints true or false.
  agent_review.py previous COMMENTS.jsonl OUT.md
                                  The last review: reads the bot's comments on the pull request (one JSON string per
                                  line, oldest first), writes the review comment's text to OUT.md and prints the commit
                                  it reviewed and its verdict ("<sha> <pass|blocking>"), or nothing when there is none.
  agent_review.py plan            Gathers the change into $OUT_DIR from the pull request's checkout in $REPO_DIR (git
                                  only reads it), and writes mode (review, or carry to copy the last passing verdict),
                                  description and incremental (true when the reviewer gets the last review's findings
                                  and the diff since it) to $GITHUB_OUTPUT. See plan().
  agent_review.py report OUT.json Turns the reviewer's output (claude --output-format json, with --json-schema) into a
                                  verdict: writes verdict (pass, blocking or error), description and comment (the PR
                                  comment, base64) to $GITHUB_OUTPUT. A blocking finding, or output it can't read, fails
                                  the "Agent review" status, and with it the merge gate.

The reviewer read the pull request's code, so what it says is untrusted: the comment is built here, @mentions are
defused, and it is never interpolated into a command. A reply that contains $ANTHROPIC_API_KEY or
$CLAUDE_CODE_OAUTH_TOKEN is not posted. The last review's comment is untrusted too (it quotes that reviewer): only its
state line, which nothing the reviewer writes can forge (defuse() escapes "<!--"), is parsed, and the rest is handed to
the next reviewer as data."""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

MARKER = "<!-- agent-review -->"
# The comment's second line: the commit the review read and its verdict, for the next push's review.
STATE = re.compile(r"^<!-- agent-review-state sha=([0-9a-f]{40}) verdict=(pass|blocking) -->$", re.MULTILINE)
SHA = re.compile(r"^[0-9a-f]{40}$")
SESSION = re.compile(r"^claude-session:\s*\S", re.IGNORECASE | re.MULTILINE)
CO_AUTHOR = re.compile(r"^co-authored-by:.*(\bclaude\b|anthropic\.com)", re.IGNORECASE | re.MULTILINE)
BODY_SIGNS = ("Generated with [Claude Code]", "claude.ai/code/session_")
SEVERITIES = ("blocking", "advisory")
MAX_COMMENT = 60000
SECRETS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")   # what the reviewer authenticates with (agent-review-run.sh)


def is_agent(messages: list[str], body: str) -> bool:
    if any(sign in body for sign in BODY_SIGNS):
        return True
    return any(SESSION.search(m) or CO_AUTHOR.search(m) for m in messages)


def previous(bodies: list[str]) -> tuple[str, str, str]:
    """The last review, from the bot's comments: (commit, verdict, its text without the hidden lines). The comment is
    the first that starts with MARKER, as the report job finds it to update it; ("", "", "") when there is none or it
    has no state line (written before reviews recorded one)."""
    for body in bodies:
        if not body.startswith(MARKER):
            continue
        lines = body.splitlines()
        m = STATE.match(lines[1]) if len(lines) > 1 else None
        if not m:
            return "", "", ""
        return m.group(1), m.group(2), "\n".join(lines[2:]).strip() + "\n"
    return "", "", ""


def git(repo: str, *args: str) -> str:
    """git over the pull request's checkout. Only commands that read: nothing in it is run (no hooks, no external diff
    or textconv drivers)."""
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True).stdout


def diff(repo: str, *revs: str, paths: list[str] | None = None) -> str:
    spec = ["--", *(f":(literal){p}" for p in paths)] if paths is not None else []
    return git(repo, "diff", "--no-ext-diff", "--no-textconv", *revs, *spec)


def own_files(repo: str, main: str, rev: str) -> list[str]:
    """The files a commit's change touches: its diff against where it meets main (a file only main changed isn't in
    it)."""
    out = git(repo, "diff", "--no-renames", "--name-only", "-z", f"{main}...{rev}")
    return [p for p in out.split("\0") if p]


def is_ancestor(repo: str, old: str, new: str) -> bool:
    if not (SHA.match(old) and SHA.match(new)) or old == new:
        return False
    return subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", old, new], capture_output=True).returncode == 0


def own_change(repo: str, main: str, rev: str) -> str:
    """A commit's own change: its diff against where it meets main, so only the files it touches, with each file's
    blob ids (--full-index), so a file main changed too never compares equal."""
    return git(repo, "diff", "--no-ext-diff", "--no-textconv", "--full-index", "--binary", f"{main}...{rev}")


def only_main_merged(repo: str, main: str, prev: str, head: str) -> bool:
    """Whether everything pushed since `prev` was merges of main that leave the pull request's own change exactly as it
    was: every commit after `prev` that isn't main's is a merge, it touches the same files, and its diff against main
    (blob ids included) is byte for byte the one that was reviewed. Anything it can't prove is False."""
    if not is_ancestor(repo, prev, head):
        return False
    added = git(repo, "rev-list", "--parents", f"{prev}..{head}", f"^{main}").splitlines()
    if not added or any(len(line.split()) < 3 for line in added):   # none, or a commit that isn't a merge
        return False
    if sorted(own_files(repo, main, prev)) != sorted(own_files(repo, main, head)):
        return False
    return own_change(repo, main, prev) == own_change(repo, main, head)


def plan(env: dict[str, str]) -> dict[str, str]:
    """Decides what the review does (mode) and writes the change into OUT_DIR for the reviewer.

    mode is carry when the last review passed (PREV_VERDICT pass and its "Agent review" status, PREV_STATE, success)
    and only_main_merged(): its verdict is copied to the new head instead of reviewing again. Otherwise it is review,
    with OUT_DIR holding:

      diff.patch   the pull request's change against main (git diff main...head)
      files.txt    the files it changes (--name-status)
      commits.txt  its commits' messages

    and, when the last review read a commit this head descends from (PREV_SHA, from `previous`), a re-review:

      previous-review.md      the last review's comment (PREV_BODY)
      since-last-review.patch what changed since, in the files the pull request touches (main's own changes, merged
                              in, left out)

    with TRUSTED/incremental.md (main's copy) added to OUT_DIR/prompt.md. Anything that goes wrong with the re-review
    gives a full review instead."""
    repo, out, main, head = env["REPO_DIR"], Path(env["OUT_DIR"]), env["MAIN"], env["HEAD"]
    (out / "diff.patch").write_text(diff(repo, f"{main}...{head}"))
    (out / "files.txt").write_text(git(repo, "diff", "--name-status", f"{main}...{head}"))
    (out / "commits.txt").write_text(git(repo, "log", "--format=commit %H%n%B", f"{main}..{head}"))
    result = {"mode": "review", "description": "", "incremental": "false"}
    prev = env.get("PREV_SHA", "")
    try:
        if env.get("PREV_VERDICT") == "pass" and env.get("PREV_STATE") == "success" \
                and only_main_merged(repo, main, prev, head):
            return {**result, "mode": "carry",
                    "description": f"Carried forward from the review of {prev[:7]}: only main merged in since"}
    except Exception as e:   # can't prove it: review
        print(f"::warning::Reviewing again: couldn't compare with the last review ({type(e).__name__}).")
    try:
        body = Path(env["PREV_BODY"]).read_text() if env.get("PREV_BODY") else ""
        if body and is_ancestor(repo, prev, head):
            files = sorted(set(own_files(repo, main, head)) | set(own_files(repo, main, prev)))
            since = diff(repo, prev, head, paths=files) if files else ""
            (out / "since-last-review.patch").write_text(since)
            (out / "previous-review.md").write_text(body)
            with open(out / "prompt.md", "a", encoding="utf-8") as f:
                f.write("\n" + Path(env["TRUSTED"], "incremental.md").read_text())
            result["incremental"] = "true"
    except Exception as e:   # any failure: fall back to the full review, never to less
        print(f"::warning::Reviewing the whole change: the re-review couldn't be prepared ({type(e).__name__}).")
        for name in ("since-last-review.patch", "previous-review.md"):
            (out / name).unlink(missing_ok=True)
        result["incremental"] = "false"
    return result


def structured(output: dict) -> dict | None:
    """The reviewer's answer: --json-schema's structured_output, else the reply itself as JSON (or its last ```json
    block)."""
    if isinstance(output.get("structured_output"), dict):
        return output["structured_output"]
    text = output.get("result")
    if not isinstance(text, str):
        return None
    blocks = re.findall(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    for candidate in [text, *reversed(blocks)]:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def findings_of(answer: dict) -> list[dict] | None:
    found = answer.get("findings")
    if not isinstance(found, list):
        return None
    out = []
    for f in found:
        if not isinstance(f, dict) or f.get("severity") not in SEVERITIES or not isinstance(f.get("title"), str):
            return None
        out.append(f)
    return out


def defuse(text: object, limit: int = 4000) -> str:
    """Untrusted text, safe to put in a comment: no @mentions, no HTML comments (our marker), no runaway length."""
    s = str(text or "").replace("@", "@​").replace("<!--", "&lt;!--")
    return s if len(s) <= limit else s[:limit] + " …"


def render(findings: list[dict], summary: str, run_url: str, sha: str = "") -> str:
    blocking = [f for f in findings if f["severity"] == "blocking"]
    advisory = [f for f in findings if f["severity"] == "advisory"]
    lines = [MARKER]
    if SHA.match(sha):
        lines.append(f"<!-- agent-review-state sha={sha} verdict={'blocking' if blocking else 'pass'} -->")
    lines += ["## Independent review", ""]
    if blocking:
        lines.append(f"**{len(blocking)} blocking finding(s)**: the merge gate stays red until a new push clears them.")
    else:
        lines.append("No blocking findings.")
    if summary:
        lines += ["", defuse(summary, 2000)]
    for title, group in (("Blocking", blocking), ("Advisory", advisory)):
        if not group:
            continue
        lines += ["", f"### {title}", ""]
        for f in group:
            where = defuse(f.get("file") or "", 300)
            if where and isinstance(f.get("line"), int):
                where += f":{f['line']}"
            lines.append(f"- **{defuse(f['title'], 300)}**" + (f" (`{where.replace('`', '')}`)" if where else ""))
            if f.get("detail"):
                lines.append("  " + defuse(f["detail"]).replace("\n", "\n  "))
    lines += ["", f"<sub>From a fresh session with read-only access to this pull request's code and main's AGENTS.md, "
                  f"run by .github/workflows/agent-review.yml ([run]({run_url})).</sub>"]
    text = "\n".join(lines)
    return text if len(text) <= MAX_COMMENT else text[:MAX_COMMENT] + "\n\n(cut short)"


def report(path: str) -> dict:
    run_url = os.environ.get("RUN_URL", "")
    error = {"verdict": "error", "comment": ""}
    try:
        with open(path, encoding="utf-8") as f:
            output = json.load(f)
    except (OSError, ValueError):
        return {**error, "description": "The reviewer gave no readable output; see the run"}
    if not isinstance(output, dict) or output.get("is_error"):
        return {**error, "description": "The reviewer stopped with an error; see the run"}
    answer = structured(output)
    findings = findings_of(answer) if answer else None
    if findings is None:
        return {**error, "description": "The reviewer's answer wasn't in the expected form; see the run"}
    comment = render(findings, str(answer.get("summary") or ""), run_url,  # type: ignore[union-attr]
                     os.environ.get("REVIEWED_SHA", ""))
    raw = json.dumps(output)
    if any(key and key in raw for key in (os.environ.get(name, "") for name in SECRETS)):
        return {**error, "description": "The reviewer's answer contained a secret; not posted"}
    blocking = sum(f["severity"] == "blocking" for f in findings)
    if blocking:
        return {"verdict": "blocking", "description": f"{blocking} blocking finding(s); see the comment", "comment": comment}
    return {"verdict": "pass", "description": f"No blocking findings ({len(findings)} advisory)", "comment": comment}


def append_outputs(lines: list[str]) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def write_outputs(values: dict) -> None:
    append_outputs([f"verdict={values['verdict']}", f"description={values['description'][:140]}",
                    "comment=" + base64.b64encode(values["comment"].encode()).decode()])
    print(f"{values['verdict']}: {values['description']}")


def main(argv: list[str]) -> int:
    if argv[:1] == ["detect"]:
        messages = [json.loads(line) for line in sys.stdin if line.strip()]
        print("true" if is_agent(messages, os.environ.get("BODY", "")) else "false")
        return 0
    if len(argv) == 3 and argv[0] == "previous":
        with open(argv[1], encoding="utf-8") as f:
            bodies = [b for b in (json.loads(line) for line in f if line.strip()) if isinstance(b, str)]
        sha, verdict, text = previous(bodies)
        if sha:
            Path(argv[2]).write_text(text, encoding="utf-8")
            print(sha, verdict)
        return 0
    if argv == ["plan"]:
        values = plan(dict(os.environ))
        append_outputs([f"{k}={v}" for k, v in values.items()])
        print(", ".join(f"{k}: {v}" for k, v in values.items()))
        return 0
    if len(argv) == 2 and argv[0] == "report":
        write_outputs(report(argv[1]))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
