"""tools/app_checks.py: Runway's own checks that `fleet checks` runs (`[tool.fleet] extra-checks`): the accessibility
baseline's form and that it only shrinks, and a flow's inheritance of its page's baseline, each passing on the repository
as it is and failing on the mistake it is for."""
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("app_checks", ROOT / "tools" / "app_checks.py")
assert _spec and _spec.loader
ac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ac)

GIT_ENV = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com", "GIT_COMMITTER_NAME": "Test",
           "GIT_COMMITTER_EMAIL": "test@example.com", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
VERIFY = 'export const PAGES = ["budget", "setup"];\nexport const DEFAULT_VIEWPORTS = ["phone", "desktop"];\n'
LINE = "budget @ phone | tap-target: button A is 32×44 px, under 44 | a small icon button"


@dataclass(frozen=True)
class Commits:
    base: str
    head: str = "HEAD"


@dataclass(frozen=True)
class Context:
    """The shape of fleet's check context (fleet_tools/checks/context.py), as far as these checks read it."""
    root: Path
    commits: Commits | None = None


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout


class Repository(unittest.TestCase):
    def test_the_repository_passes(self):
        for check in ac.CHECKS:
            self.assertEqual(check(Context(ROOT)), [], check.__name__)

    def test_the_real_visits_are_read(self):
        visits = ac.verify_visits((ROOT / ac.VERIFY).read_text(), ROOT / ac.FLOWS)
        self.assertIn("budget @ phone", visits)
        self.assertIn("flow more-sheet-focus @ landscape", visits)
        self.assertFalse([v for v in visits if v.endswith("tablet")])


class A11yBaseline(unittest.TestCase):
    VISITS = {"budget @ phone", "budget @ desktop", "flow lock @ phone", "flow lock @ landscape"}

    def problems(self, text):
        return ac.check_a11y_baseline(text, self.VISITS)

    def test_visits_come_from_the_pages_and_the_flows(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "lock.json").write_text(json.dumps({"name": "lock", "viewports": ["phone", "landscape"]}))
        (Path(tmp.name) / "emoji.json").write_text(json.dumps({"name": "emoji"}))
        self.assertEqual(ac.verify_visits('export const PAGES = ["budget"];\nexport const DEFAULT_VIEWPORTS = ["phone", "desktop"];\n',
                                          Path(tmp.name)),
                         {"budget @ phone", "budget @ desktop", "flow lock @ phone", "flow lock @ landscape",
                          "flow emoji @ phone", "flow emoji @ desktop"})

    def test_an_empty_baseline_and_comments_pass(self):
        self.assertEqual(self.problems(""), [])
        self.assertEqual(self.problems("# header\n\n"), [])

    def test_a_line_with_all_three_parts_passes(self):
        self.assertEqual(self.problems(f"# header\n{LINE}\nflow lock @ landscape | focus: a X shows no focus ring | why\n"), [])

    def test_a_line_without_a_reason_fails(self):
        self.assertEqual(len(self.problems("budget @ phone | tap-target: button A is 32×44 px, under 44")), 1)
        self.assertEqual(len(self.problems("budget @ phone | tap-target: button A is 32×44 px, under 44 |  ")), 1)

    def test_a_page_or_flow_that_is_gone_or_not_shot_at_that_width_fails(self):
        self.assertEqual(len(self.problems("setup @ phone | focus: a X shows no focus ring | why")), 1)
        self.assertEqual(len(self.problems("flow lock @ desktop | focus: a X shows no focus ring | why")), 1)
        self.assertEqual(len(self.problems("budget @ tablet | focus: a X shows no focus ring | why")), 1)

    def test_a_line_listed_twice_fails(self):
        problems = self.problems(f"{LINE}\n{LINE} again")
        self.assertEqual(len(problems), 1)
        self.assertIn("line 2: listed twice", problems[0])

    def test_a_new_baseline_line_fails_and_a_removed_one_passes(self):
        other = "budget @ desktop | focus: a X shows no focus ring | why"
        self.assertEqual(ac.check_a11y_shrinks(f"{LINE}\n{other}\n", f"{LINE}\n"), [])
        self.assertEqual(ac.check_a11y_shrinks("# header\n", ""), [])
        problems = ac.check_a11y_shrinks(f"{LINE}\n", f"# header\n{LINE}\n{other}\n")
        self.assertEqual(len(problems), 1)
        self.assertIn("is new", problems[0])

    def test_a_new_line_for_an_inheriting_flow_still_fails(self):
        # Inheriting the page's baseline adds no way to list a finding: any line the base didn't have is refused.
        line = "flow dialog @ phone | focus: a X shows no focus ring | why"
        problems = ac.check_a11y_shrinks(f"{LINE}\n", f"{LINE}\n{line}\n")
        self.assertEqual(len(problems), 1)
        self.assertIn("is new", problems[0])

    def test_an_unknown_or_malformed_line_for_an_inheriting_flow_still_fails(self):
        self.assertEqual(len(self.problems("flow dialog @ phone | focus: a X shows no focus ring | why")), 1)   # not a visit
        self.assertEqual(len(self.problems("flow lock @ phone | focus: a X shows no focus ring")), 1)           # no reason


class A11yBaselineOnABranch(unittest.TestCase):
    """The check as fleet runs it: the baseline in a repository, compared with where the branch left main."""

    def repository(self, baseline: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "frontend/verify/flows").mkdir(parents=True)
        (root / ac.VERIFY).write_text(VERIFY)
        (root / ac.A11Y_BASELINE).write_text(baseline)
        git(root, "init", "-q", "-b", "main")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "main")
        return root

    def test_a_pull_request_adding_a_baseline_line_fails_and_one_removing_a_line_passes(self):
        root = self.repository(f"# header\n{LINE}\nsetup @ phone | focus: a X shows no focus ring | why\n")
        base = git(root, "rev-parse", "HEAD").strip()
        (root / ac.A11Y_BASELINE).write_text(f"# header\n{LINE}\nsetup @ desktop | focus: a X shows no focus ring | why\n")
        problems = ac.a11y_baseline(Context(root, Commits(base)))
        self.assertEqual(len(problems), 1)
        self.assertIn("`setup @ desktop | focus: a X shows no focus ring | why` is new", problems[0])
        (root / ac.A11Y_BASELINE).write_text(f"# header\n{LINE}\n")
        self.assertEqual(ac.a11y_baseline(Context(root, Commits(base))), [])

    def test_without_a_range_only_the_form_is_checked(self):
        root = self.repository("# header\n")
        (root / ac.A11Y_BASELINE).write_text(f"{LINE}\nbudget @ tablet | focus: a X shows no focus ring | why\n")
        problems = ac.a11y_baseline(Context(root))
        self.assertEqual(len(problems), 1)
        self.assertIn("budget @ tablet", problems[0])

    def test_the_pull_request_that_introduces_the_baseline_passes(self):
        root = self.repository("")
        base = git(root, "rev-parse", "HEAD").strip()
        git(root, "rm", "-q", ac.A11Y_BASELINE)
        git(root, "commit", "-q", "-m", "without a baseline")
        base = git(root, "rev-parse", "HEAD").strip()
        (root / ac.A11Y_BASELINE).write_text(f"{LINE}\n")
        self.assertEqual(ac.a11y_baseline(Context(root, Commits(base))), [])


class FlowInheritance(unittest.TestCase):
    def problems(self, **flow):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "f.json").write_text(json.dumps({"name": "f", "steps": [], **flow}))
        return ac.check_flow_inheritance(VERIFY, Path(tmp.name))

    def test_a_flow_that_does_not_inherit_passes(self):
        self.assertEqual(self.problems(), [])
        self.assertEqual(self.problems(inherits_page_baseline=False), [])

    def test_a_flow_that_names_its_page_passes(self):
        self.assertEqual(self.problems(inherits_page_baseline=True, page="setup"), [])
        self.assertEqual(self.problems(inherits_page_baseline=True, page="setup", viewports=["phone"]), [])

    def test_a_page_left_to_the_default_is_refused(self):
        self.assertEqual(len(self.problems(inherits_page_baseline=True)), 1)

    def test_an_unknown_page_a_width_without_pages_or_a_non_boolean_is_refused(self):
        self.assertEqual(len(self.problems(inherits_page_baseline=True, page="nope")), 1)
        self.assertEqual(len(self.problems(inherits_page_baseline=True, page="setup", viewports=["phone", "landscape"])), 1)
        self.assertEqual(len(self.problems(inherits_page_baseline="yes", page="setup")), 1)


if __name__ == "__main__":
    unittest.main()
