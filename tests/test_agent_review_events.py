"""Which pull request events start the agent review (.github/workflows/agent-review.yml), and in which concurrency group.

The caller's job-level `if:` keeps label events out unless fleet's workflow has something to do with them: adding a
trigger label (`security`, `safety-critical`), the owner removing one, and anyone removing `keep-draft`, whose
removal fleet's `keep-draft-removed` job (fleet v4.0.0) answers by starting merge-gate.yml, so a draft held for the
owner is marked ready once the gate passes (AGENTS.md's "Git and PRs"). A filter that dropped that event left such a
draft waiting for the next push. Pushes, trigger-label events and the `keep-draft` removal each have a concurrency
group of their own, so none takes the place of another's waiting run.

The expressions are read from the workflow and evaluated here by a small evaluator of the part of GitHub's expression
syntax they use (string literals, context paths, `!`, `==`, `!=`, `&&`, `||`, parentheses, `contains` and
`fromJSON`), with GitHub's rules: `==` compares strings ignoring case, `contains` matches an array's items the same
way, `&&` and `||` give one of their operands, and false, 0, '' and null are falsy."""
import json
import re
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/agent-review.yml"
OWNER = "the-owner"
PR = 42

TOKEN = re.compile(r"\s*(?:(?P<string>'(?:[^']|'')*')|(?P<op>==|!=|&&|\|\||[!(),])|(?P<name>[A-Za-z_][A-Za-z0-9_.-]*))")


def tokenize(text: str) -> list[tuple[str, str]]:
    tokens, at = [], 0
    while at < len(text):
        if text[at:].strip() == "":
            break
        match = TOKEN.match(text, at)
        if match is None:
            raise ValueError(f"can't read {text[at:]!r}")
        kind = match.lastgroup
        assert kind is not None
        tokens.append((kind, match.group(kind)))
        at = match.end()
    return tokens


def truthy(value: Any) -> bool:
    return not (value is None or value is False or value == "" or (isinstance(value, int | float) and value == 0))


def equal(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left.casefold() == right.casefold()
    return bool(left == right)


class Evaluator:
    def __init__(self, text: str, context: dict[str, Any]):
        self.tokens = tokenize(text)
        self.at = 0
        self.context = context

    def peek(self) -> str | None:
        return self.tokens[self.at][1] if self.at < len(self.tokens) else None

    def take(self, expected: str | None = None) -> tuple[str, str]:
        token = self.tokens[self.at]
        if expected is not None and token[1] != expected:
            raise ValueError(f"expected {expected!r}, got {token[1]!r}")
        self.at += 1
        return token

    def evaluate(self) -> Any:
        value = self.either()
        if self.at != len(self.tokens):
            raise ValueError(f"unread tokens from {self.tokens[self.at:]!r}")
        return value

    def either(self) -> Any:
        value = self.both()
        while self.peek() == "||":
            self.take()
            right = self.both()
            value = value if truthy(value) else right
        return value

    def both(self) -> Any:
        value = self.comparison()
        while self.peek() == "&&":
            self.take()
            right = self.comparison()
            value = right if truthy(value) else value
        return value

    def comparison(self) -> Any:
        value = self.unary()
        while self.peek() in ("==", "!="):
            op = self.take()[1]
            right = self.unary()
            value = equal(value, right) if op == "==" else not equal(value, right)
        return value

    def unary(self) -> Any:
        if self.peek() == "!":
            self.take()
            return not truthy(self.unary())
        return self.primary()

    def primary(self) -> Any:
        kind, text = self.take()
        if text == "(" and kind == "op":
            value = self.either()
            self.take(")")
            return value
        if kind == "string":
            return text[1:-1].replace("''", "'")
        if kind != "name":
            raise ValueError(f"unexpected {text!r}")
        if text in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[text]
        if self.peek() == "(":
            self.take("(")
            args = [self.either()]
            while self.peek() == ",":
                self.take()
                args.append(self.either())
            self.take(")")
            return self.call(text, args)
        value: Any = self.context
        for part in text.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        return value

    @staticmethod
    def call(name: str, args: list[Any]) -> Any:
        if name == "fromJSON" and len(args) == 1:
            return json.loads(args[0])
        if name == "contains" and len(args) == 2:
            search, item = args
            if isinstance(search, list):
                return any(equal(each, item) for each in search)
            return isinstance(search, str) and isinstance(item, str) and item.casefold() in search.casefold()
        raise ValueError(f"no function {name} with {len(args)} arguments")


def render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def template(text: str, context: dict[str, Any]) -> str:
    return re.sub(r"\$\{\{(.*?)\}\}", lambda m: render(Evaluator(m.group(1), context).evaluate()), text)


def review_job() -> tuple[str, str, str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    job = re.search(r"^  review:\n((?:(?: {4}.*)?\n)+)", text, re.MULTILINE)
    assert job, "agent-review.yml has no `review` job"
    body = job.group(1)
    condition = re.search(r"^    if: >-\n((?: {6,}\S.*\n)+)", body, re.MULTILINE)
    group = re.search(r"^      group: (.+)$", body, re.MULTILINE)
    cancel = re.search(r"^      cancel-in-progress: (.+)$", body, re.MULTILINE)
    assert condition and group and cancel, "the review job's `if:` (folded, >-) or its concurrency group moved"
    return " ".join(line.strip() for line in condition.group(1).splitlines()), group.group(1), cancel.group(1)


def event(action: str, label: str | None = None, sender: str = "someone-else") -> dict[str, Any]:
    payload: dict[str, Any] = {"action": action, "sender": {"login": sender}, "pull_request": {"number": PR}}
    if label is not None:
        payload["label"] = {"name": label}
    return {"github": {"event": payload, "repository_owner": OWNER}}


class Evaluating(unittest.TestCase):
    def test_github_rules_the_caller_relies_on(self) -> None:
        context = {"github": {"event": {"label": {"name": "Keep-Draft"}}}}
        self.assertIs(Evaluator("github.event.label.name == 'keep-draft'", context).evaluate(), True)
        self.assertIs(Evaluator("contains(fromJSON('[\"a\", \"B\"]'), 'b')", context).evaluate(), True)
        self.assertEqual(Evaluator("false && 'x' || 'y'", context).evaluate(), "y")
        self.assertEqual(Evaluator("true && 'x' || 'y'", context).evaluate(), "x")
        self.assertIs(Evaluator("!github.event.action", context).evaluate(), True)
        self.assertIs(Evaluator("!(true && false) && 'it''s' == 'IT''S'", context).evaluate(), True)
        with self.assertRaises(ValueError):
            Evaluator("github.event.action ~ 'x'", context).evaluate()


class Events(unittest.TestCase):
    def setUp(self) -> None:
        self.condition, self.group, self.cancel = review_job()

    def runs(self, context: dict[str, Any]) -> bool:
        return truthy(Evaluator(self.condition, context).evaluate())

    def assert_runs(self, context: dict[str, Any], group: str, cancel: str) -> None:
        self.assertTrue(self.runs(context), context)
        self.assertEqual(template(self.group, context), f"agent-review-pr-{PR}-{group}")
        self.assertEqual(template(self.cancel, context), cancel)

    def test_pushes_and_opening_run_in_the_push_group_and_cancel_older_runs(self) -> None:
        for action in ("opened", "synchronize", "reopened", "ready_for_review"):
            with self.subTest(action):
                self.assert_runs(event(action), "push", "true")

    def test_adding_a_trigger_label_runs_in_the_label_group_and_waits(self) -> None:
        for label in ("security", "safety-critical", "Security"):
            with self.subTest(label):
                self.assert_runs(event("labeled", label), "label", "false")

    def test_removing_a_trigger_label_runs_only_for_the_owner(self) -> None:
        self.assert_runs(event("unlabeled", "security", sender=OWNER), "label", "false")
        self.assertFalse(self.runs(event("unlabeled", "security")))
        self.assertFalse(self.runs(event("unlabeled", "safety-critical")))

    def test_removing_keep_draft_runs_for_anyone_in_a_group_of_its_own(self) -> None:
        for label, sender in (("keep-draft", "someone-else"), ("keep-draft", OWNER), ("Keep-Draft", "someone-else")):
            with self.subTest(label=label, sender=sender):
                self.assert_runs(event("unlabeled", label, sender=sender), "keep-draft", "false")

    def test_other_label_events_start_nothing(self) -> None:
        for action, label, sender in (("labeled", "keep-draft", OWNER), ("labeled", "needs_preview", OWNER),
                                      ("unlabeled", "needs_preview", OWNER), ("unlabeled", "keep-draft-later", OWNER),
                                      ("unlabeled", "keep", OWNER)):
            with self.subTest(action=action, label=label):
                self.assertFalse(self.runs(event(action, label, sender=sender)))


if __name__ == "__main__":
    unittest.main()
