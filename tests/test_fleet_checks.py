"""tools/fleet_checks.py: the checks that replaced instructions (one migration head, migrations tested, agents' commit
trailers, workflow conventions), each passing on the repository as it is and failing on the mistake it is for."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("fleet_checks", ROOT / "tools" / "fleet_checks.py")
assert _spec and _spec.loader
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)

MIGRATION = "revision = {rev!r}\ndown_revision = {down!r}\n\ndef upgrade():\n    pass\n"


class Migrations(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.write("0035_a.py", "0035", None)
        self.write("0036_b.py", "0036", "0035")

    def write(self, name, rev, down):
        (self.dir / name).write_text(MIGRATION.format(rev=rev, down=down))

    def problems(self, tests="def test_0036_b(self): ...\ndef test_0037_c(self): ...\ndef test_0038_d(self): ...\n"):
        return fc.check_migrations(fc.migrations(self.dir), tests)

    def test_the_repository_passes(self):
        self.assertEqual(fc.check_migrations(fc.migrations(), fc.MIGRATION_TESTS.read_text()), [])

    def test_a_chain_with_tests_passes(self):
        self.write("0037_c.py", "0037", "0036")
        self.assertEqual(self.problems(), [])

    def test_two_branches_each_adding_the_next_number_leave_two_heads(self):
        # What nearly merged: two pull requests both added a migration after the same parent.
        self.write("0037_c.py", "0037", "0036")
        self.write("0038_d.py", "0038", "0036")
        problems = self.problems()
        self.assertEqual(len(problems), 1)
        self.assertIn("more than one Alembic head (0037, 0038)", problems[0])

    def test_the_same_number_twice_is_caught(self):
        self.write("0037_c.py", "0037", "0036")
        self.write("0037_d.py", "0037", "0036")
        self.assertTrue(any("'0037' is used more than once (0037_c.py, 0037_d.py)" in p for p in self.problems()))

    def test_a_new_migration_without_its_own_test_fails(self):
        self.write("0037_c.py", "0037", "0036")
        problems = self.problems(tests="def test_0036_b(self): ...\n")
        self.assertEqual(len(problems), 1)
        self.assertIn("0037_c.py: no test of its own", problems[0])

    def test_migrations_before_the_rule_need_no_test(self):
        self.assertEqual(self.problems(tests="def test_0036_b(self): ...\n"), [])

    def test_a_parent_that_isnt_there_and_a_misnamed_file_fail(self):
        self.write("0037_c.py", "0037", "0099")
        self.write("c_0038.py", "0038", "0037")
        problems = self.problems()
        self.assertTrue(any("down_revision '0099' is no migration here" in p for p in problems))
        self.assertTrue(any("c_0038.py: the file name should start with its revision" in p for p in problems))

    def test_a_merge_migration_follows_both_heads(self):
        self.write("0037_c.py", "0037", "0036")
        self.write("0038_d.py", "0038", "0036")
        (self.dir / "0039_merge.py").write_text("revision = '0039'\ndown_revision = ('0037', '0038')\n")
        self.assertEqual(self.problems(tests="def test_0036_b(self): ...\ndef test_0037_c(self): ...\n"
                                             "def test_0038_d(self): ...\ndef test_0039_m(self): ...\n"), [])


class Commits(unittest.TestCase):
    SESSION = "Claude-Session: https://claude.ai/code/session_x"

    def test_a_person_s_commit_needs_nothing(self):
        self.assertEqual(fc.check_commit("a" * 40, "fix: something\n\nWhy it changed."), [])

    def test_an_agent_s_commit_naming_its_model_passes(self):
        for model in ("Opus 4.5", "Sonnet 4", "Haiku 4.5", "Fable 5.1"):
            msg = f"fix: x\n\nCo-Authored-By: Claude {model} <noreply@anthropic.com>\n{self.SESSION}\n"
            self.assertEqual(fc.check_commit("a" * 40, msg), [], model)

    def test_an_agent_s_commit_without_the_trailer_fails(self):
        problems = fc.check_commit("a" * 40, f"fix: x\n\n{self.SESSION}\n")
        self.assertEqual(len(problems), 1)
        self.assertIn("needs a 'Co-Authored-By: Claude", problems[0])

    def test_a_trailer_that_doesnt_name_the_model_fails(self):
        for trailer in ("Claude <noreply@anthropic.com>", "Claude Opus <noreply@anthropic.com>",
                        "Claude Opus 4.5 <claude@example.com>", "claude-opus-4-5 <noreply@anthropic.com>"):
            problems = fc.check_commit("a" * 40, f"fix: x\n\nCo-authored-by: {trailer}\n")
            self.assertEqual(len(problems), 1, trailer)
            self.assertIn("should name the model", problems[0])

    def test_two_claude_trailers_fail(self):
        msg = ("fix: x\n\nCo-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>\n"
               "Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>\n")
        self.assertIn("more than one", fc.check_commit("a" * 40, msg)[0])

    def test_another_person_as_co_author_is_left_alone(self):
        self.assertEqual(fc.check_commit("a" * 40, "fix: x\n\nCo-Authored-By: Pat Doe <pat@example.com>\n"), [])


class Workflows(unittest.TestCase):
    GOOD = ("name: X\non: push\ndefaults:\n  run:\n    shell: bash\njobs:\n  a:\n    steps:\n"
            "      - uses: actions/checkout@" + "a" * 40 + "   # v7.0.1\n"
            "      - uses: ./.github/actions/local\n"
            "      - run: gh api --paginate \"repos/$R/pulls?per_page=100\"\n")

    def test_the_repository_passes(self):
        self.assertEqual(fc.check_workflows(), [])

    def test_a_workflow_following_the_rules_passes(self):
        self.assertEqual(fc.check_workflow("x.yml", self.GOOD), [])

    def test_no_bash_default_fails(self):
        text = self.GOOD.replace("defaults:\n  run:\n    shell: bash\n", "")
        self.assertIn("no top-level `defaults: run: shell: bash`", fc.check_workflow("x.yml", text)[0])

    def test_an_unpinned_action_or_a_pin_without_its_version_fails(self):
        self.assertIn("isn't pinned", fc.check_workflow("x.yml", self.GOOD.replace("@" + "a" * 40, "@v7"))[0])
        self.assertIn("no version in a comment", fc.check_workflow("x.yml", self.GOOD.replace("   # v7.0.1", ""))[0])

    def test_a_context_a_job_env_cannot_use_fails_but_a_steps_env_may(self):
        job = self.GOOD.replace("  a:\n", "  a:\n    env:\n      OUT: ${{ runner.temp }}/x\n")
        self.assertIn("`runner` isn't available in a workflow's or job's `env:`", fc.check_workflow("x.yml", job)[0])
        top = self.GOOD.replace("jobs:\n", "env:\n  A: ${{ github.sha }}\n  B: ${{ steps.s.outputs.o }}\njobs:\n")
        self.assertIn("`steps` isn't available", fc.check_workflow("x.yml", top)[0])
        ok_job = self.GOOD.replace("  a:\n", "  a:\n    env:\n      OUT: ${{ github.workspace }}/x\n      T: ${{ secrets.T }}\n")
        self.assertEqual(fc.check_workflow("x.yml", ok_job), [])
        step = self.GOOD.replace("      - run:", "      - env:\n          OUT: ${{ runner.temp }}/x\n        run:")
        self.assertEqual(fc.check_workflow("x.yml", step), [])

    def test_only_a_context_of_its_own_counts_not_a_property_with_the_same_name(self):
        def job_env(expression):
            return self.GOOD.replace("  a:\n", "  a:\n    env:\n      X: " + expression + "\n")
        for fine in ("${{ matrix.runner.os }}", "${{ needs.job.outputs.x }}", "${{ matrix.env.name }}", "${{ vars.steps.n }}"):
            with self.subTest(fine=fine):
                self.assertEqual(fc.check_workflow("x.yml", job_env(fine)), [])
        for bad, context in (("${{ runner.temp }}/x", "runner"), ("${{ steps.x.outputs.y }}", "steps"), ("${{ job.status }}", "job"),
                             ("${{ env.X }}", "env"), ("${{ format('{0}/x', runner.temp) }}", "runner"),
                             ("${{ matrix.a && steps.s.outputs.o }}", "steps")):
            with self.subTest(bad=bad):
                self.assertIn(f"`{context}` isn't available", fc.check_workflow("x.yml", job_env(bad))[0])

    def test_a_list_read_without_paginate_fails(self):
        self.assertIn("without --paginate", fc.check_workflow("x.yml", self.GOOD.replace("--paginate ", ""))[0])


class Images(unittest.TestCase):
    VERIFY = 'export const PAGES = ["budget", "setup"];\nexport const DEFAULT_VIEWPORTS = ["phone", "desktop"];\n'

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.flows = Path(tmp.name)
        self.flow("emoji", steps=[{"goto": "#setup"}, {"screenshot": "open"}, {"click": "a"}])
        self.flow("lock", viewports=["phone", "landscape"], steps=[{"screenshot": "locked"}])
        self.made = fc.verify_images(self.VERIFY, self.flows)

    def flow(self, name, **fields):
        (self.flows / f"{name}.json").write_text(json.dumps({"name": name, **fields}))

    def problems(self, *paths):
        return fc.check_images(list(paths), self.made)

    def test_the_names_come_from_the_pages_and_the_flows(self):
        self.assertEqual(self.made, {
            "budget-phone.png", "budget-desktop.png", "setup-phone.png", "setup-desktop.png",
            "budget-top-phone.png", "budget-top-desktop.png", "setup-top-phone.png", "setup-top-desktop.png",
            "flow-emoji-phone.png", "flow-emoji-desktop.png", "flow-emoji-open-phone.png", "flow-emoji-open-desktop.png",
            "flow-lock-phone.png", "flow-lock-landscape.png", "flow-lock-locked-phone.png", "flow-lock-locked-landscape.png"})

    def test_the_real_pages_and_flows_are_read(self):
        made = fc.verify_images()
        self.assertIn("budget-phone.png", made)
        self.assertIn("overview-top-desktop.png", made)
        self.assertIn("flow-more-sheet-focus-landscape.png", made)
        self.assertFalse([n for n in made if "tablet" in n.rsplit("-", 1)[-1]])

    def test_every_committed_screenshot_is_one_make_verify_writes(self):
        committed = [str(p.relative_to(ROOT)) for p in (ROOT / "screenshots").glob("*.png")]
        self.assertEqual(fc.check_images(committed, fc.verify_images()), [])

    def test_a_screenshot_make_verify_writes_passes(self):
        self.assertEqual(self.problems("screenshots/budget-phone.png", "screenshots/flow-emoji-open-desktop.png",
                                       "screenshots/flow-lock-locked-landscape.png"), [])

    def test_a_file_in_screenshots_make_verify_would_not_write_fails(self):
        for name in ("before-after.png", "budget-tablet.png", "budget-phone-top.png", "nope-phone.png", "flow-emoji-open-landscape.png"):
            self.assertEqual(len(self.problems(f"screenshots/{name}")), 1, name)

    def test_the_screenshots_must_be_in_the_screenshots_folder(self):
        self.assertEqual(len(self.problems("docs/src/assets/screenshots/budget-phone.png")), 1)
        self.assertEqual(len(self.problems("budget-phone.png")), 1)

    def test_icons_assets_and_the_docs_site_logo_pass(self):
        self.assertEqual(self.problems("extension/icons/icon-16.png", "runway/static/icon-192.png", "docs/src/assets/logo.png"), [])

    def test_a_hand_taken_docs_screenshot_fails(self):
        self.assertEqual(len(self.problems("docs/src/assets/screenshots/overview.png")), 1)

    def test_a_hand_made_comparison_image_fails_wherever_it_is(self):
        problems = self.problems("docs/src/assets/tabbar-before-after.png", "docs/src/assets/screenshots/before-after.png",
                                 "screenshots/budget-phone.png")
        self.assertEqual(len(problems), 2)
        self.assertIn("docs/src/assets/tabbar-before-after.png: the only images a change commits", problems[0])

    def test_a_file_exception_is_exact_not_a_prefix(self):
        self.assertEqual(len(self.problems("docs/src/assets/logo.png-before.png", "docs/src/assets/logo.png.bak.png")), 2)
        self.assertEqual(self.problems("docs/src/assets/logo.png"), [])

    def test_every_raster_format_is_counted_and_vector_and_text_files_are_not(self):
        raster = ["a.png", "b.JPG", "c.jpeg", "d.gif", "e.webp", "f.avif", "g.ico", "h.heic", "i.bmp", "j.tif", "k.TIFF"]
        self.assertEqual([p for p in [*raster, "l.svg", "m.md"] if fc.IMAGE.search(p)], raster)

    def test_the_branch_s_added_images_are_read_from_git(self):
        done = mock.Mock(stdout="docs/a.png\0README.md\0frontend/a.svg\0screenshots/b-phone.PNG\0docs/é-ü.png\0")
        with mock.patch.object(fc.subprocess, "run", return_value=done) as run:
            self.assertEqual(fc.added_images("base"), ["docs/a.png", "screenshots/b-phone.PNG", "docs/é-ü.png"])
        self.assertEqual(run.call_args.args[0], ["git", "diff", "--name-only", "-z", "--diff-filter=AMR", "base", "HEAD", "--"])


class A11yBaseline(unittest.TestCase):
    VISITS = {"budget @ phone", "budget @ desktop", "flow lock @ phone", "flow lock @ landscape"}
    LINE = "budget @ phone | tap-target: button A is 32×44 px, under 44 | a small icon button"

    def problems(self, text):
        return fc.check_a11y_baseline(text, self.VISITS)

    def test_the_repository_passes(self):
        self.assertEqual(fc.check_a11y_baseline(fc.A11Y_BASELINE.read_text() if fc.A11Y_BASELINE.exists() else "", fc.verify_visits()), [])

    def test_the_real_visits_are_read(self):
        visits = fc.verify_visits()
        self.assertIn("budget @ phone", visits)
        self.assertIn("flow more-sheet-focus @ landscape", visits)
        self.assertFalse([v for v in visits if v.endswith("tablet")])

    def test_visits_come_from_the_pages_and_the_flows(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "lock.json").write_text(json.dumps({"name": "lock", "viewports": ["phone", "landscape"]}))
        (Path(tmp.name) / "emoji.json").write_text(json.dumps({"name": "emoji"}))
        verify = 'export const PAGES = ["budget"];\nexport const DEFAULT_VIEWPORTS = ["phone", "desktop"];\n'
        self.assertEqual(fc.verify_visits(verify, Path(tmp.name)), {
            "budget @ phone", "budget @ desktop", "flow lock @ phone", "flow lock @ landscape", "flow emoji @ phone", "flow emoji @ desktop"})

    def test_an_empty_baseline_and_comments_pass(self):
        self.assertEqual(self.problems(""), [])
        self.assertEqual(self.problems("# header\n\n"), [])

    def test_a_line_with_all_three_parts_passes(self):
        self.assertEqual(self.problems(f"# header\n{self.LINE}\nflow lock @ landscape | focus: a X shows no focus ring | why\n"), [])

    def test_a_line_without_a_reason_fails(self):
        self.assertEqual(len(self.problems("budget @ phone | tap-target: button A is 32×44 px, under 44")), 1)
        self.assertEqual(len(self.problems("budget @ phone | tap-target: button A is 32×44 px, under 44 |  ")), 1)

    def test_a_page_or_flow_that_is_gone_or_not_shot_at_that_width_fails(self):
        self.assertEqual(len(self.problems("setup @ phone | focus: a X shows no focus ring | why")), 1)
        self.assertEqual(len(self.problems("flow lock @ desktop | focus: a X shows no focus ring | why")), 1)
        self.assertEqual(len(self.problems("budget @ tablet | focus: a X shows no focus ring | why")), 1)

    def test_a_line_listed_twice_fails(self):
        problems = self.problems(f"{self.LINE}\n{self.LINE} again")
        self.assertEqual(len(problems), 1)
        self.assertIn("line 2: listed twice", problems[0])

    def test_a_new_baseline_line_fails_and_a_removed_one_passes(self):
        other = "budget @ desktop | focus: a X shows no focus ring | why"
        self.assertEqual(fc.check_a11y_shrinks(f"{self.LINE}\n{other}\n", f"{self.LINE}\n"), [])
        self.assertEqual(fc.check_a11y_shrinks("# header\n", ""), [])
        problems = fc.check_a11y_shrinks(f"{self.LINE}\n", f"# header\n{self.LINE}\n{other}\n")
        self.assertEqual(len(problems), 1)
        self.assertIn("is new", problems[0])


if __name__ == "__main__":
    unittest.main()
