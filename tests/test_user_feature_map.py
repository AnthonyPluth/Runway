"""tools/user_feature_map.py: how the user feature map is read from the verify pages and flows, merged with the hand-edited
gotchas, and that the committed .claude/skills/verify-runway/references/feature-map.md is current."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("user_feature_map", ROOT / "tools" / "user_feature_map.py")
assert _spec and _spec.loader
ufm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ufm)

NAV = '''export const MAIN_NAV: NavGroup = {
  label: "Day to day",
  items: [
    { page: "overview", label: "Overview", icon: House },
    { page: "budget", label: "Budget", icon: ChartPie },
  ],
};
'''
FLOW = {"name": "budget-edit", "page": "budget", "viewports": ["desktop", "phone"], "steps": [
    {"goto": "#budget"}, {"goto": "#setup/data"},
    {"click": "button[aria-label^='Edit Coffee budget']"},
    {"click": "[role=dialog] button:text-is('Cancel')"},
    {"expect_text": {"selector": "body", "text": "Used in months"}}]}


class Reading(unittest.TestCase):
    def test_pages_and_nav_come_from_the_code(self):
        self.assertEqual(ufm.pages('export const PAGES = ["overview", "budget"];'), ["overview", "budget"])
        self.assertEqual(ufm.nav_labels(NAV), {"overview": ("Overview", "Day to day"), "budget": ("Budget", "Day to day")})
        self.assertEqual(ufm.tab_pages('const TAB_PAGES = ["overview", "budget"];'), ["overview", "budget"])

    def test_a_flow_is_summed_up_by_what_it_visits_touches_and_expects(self):
        self.assertEqual(ufm.summary(FLOW, "#budget"), {
            "goes to": ["#setup/data"], "touches": ["Edit Coffee budget", "Cancel"], "expects to see": ["Used in months"]})
        self.assertEqual(ufm.widths(FLOW), ["phone", "desktop"])

    def test_reach_differs_for_a_tab_a_more_sheet_page_and_settings(self):
        nav = {"budget": ("Budget", "Day to day"), "reports": ("Reports", "Day to day")}
        self.assertEqual(ufm.reach("budget", nav, ["budget"]), "desktop: sidebar > Day to day > Budget; phone: the tab bar")
        self.assertIn("tab bar > More", ufm.reach("reports", nav, ["budget"]))
        self.assertIn("Settings link", ufm.reach("setup", nav, ["budget"]))


class Gotchas(unittest.TestCase):
    def test_bullets_are_merged_under_their_name(self):
        notes, problems = ufm.gotchas("# T\n\nintro\n\n## budget\n- one\n- two\n\n## budget-edit\n- three\n", {"budget", "budget-edit"})
        self.assertEqual((notes, problems), ({"budget": ["one", "two"], "budget-edit": ["three"]}, []))

    def test_a_name_that_is_no_page_or_flow_a_repeat_or_loose_text_is_a_problem(self):
        _, problems = ufm.gotchas("## gone\n- x\n", {"budget"})
        self.assertIn("`## gone` is not a page or flow name", problems[0])
        _, problems = ufm.gotchas("## budget\n- x\n## budget\n- y\n", {"budget"})
        self.assertIn("appears twice", problems[0])
        _, problems = ufm.gotchas("## budget\nloose text\n", {"budget"})
        self.assertIn("only `- ` bullets", problems[0])


class Rendering(unittest.TestCase):
    def render(self, notes):
        with tempfile.TemporaryDirectory() as tmp:
            return ufm.render(["budget"], {"budget": ("Budget", "Day to day")}, ["budget"], [FLOW], notes, Path(tmp))

    def test_a_page_and_its_flow_say_how_to_reach_and_drive_them(self):
        text = self.render({"budget": ["Scope matters."], "budget-edit": ["Cancel it."]})
        for want in ("## Budget (`budget`)", "tools/control goto budget", "### Flows on Budget", "#### `budget-edit`",
                     "tools/control flow budget-edit", "  - Scope matters.", "  - Cancel it.", "It is not `docs/feature-map.json`"):
            self.assertIn(want, text)

    def test_a_signed_in_flow_says_to_start_signed_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = ufm.render(["setup"], {}, [], [{**FLOW, "page": "setup", "signed_in": True}], {}, Path(tmp))
        self.assertIn("`tools/control start --signed-in`, then `tools/control flow budget-edit`", text)


class Committed(unittest.TestCase):
    def test_the_committed_map_is_what_the_script_generates(self):
        text, problems = ufm.generate()
        self.assertEqual(problems, [])
        self.assertEqual(ufm.check(ufm.OUT.read_text(), text), [])

    def test_every_page_and_flow_is_in_it(self):
        text = ufm.OUT.read_text()
        for page in ufm.pages(ufm.VERIFY.read_text()):
            self.assertIn(f"(`{page}`)", text)
        for flow in ufm.flows():
            self.assertIn(f"#### `{flow['name']}`", text)

    def test_a_stale_or_missing_file_fails(self):
        self.assertIn("make user-feature-map", ufm.check("old\n", "new\n")[0])
        self.assertIn("missing", ufm.check(None, "new\n")[0])


if __name__ == "__main__":
    unittest.main()
