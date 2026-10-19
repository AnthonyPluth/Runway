"""tools/screenshot_diff.py: the pixels a pull request changed in screenshots/, found exactly, boxed and summed up for the
pull request comment. The images here are small and made in the tests; the git tests commit them to a repository of their own."""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("screenshot_diff", ROOT / "tools" / "screenshot_diff.py")
assert _spec and _spec.loader
sd = importlib.util.module_from_spec(_spec)
sys.modules["screenshot_diff"] = sd   # a dataclass looks its module up here
_spec.loader.exec_module(sd)

GRAY = (40, 44, 52, 255)


def image(size=(100, 80), color=GRAY, **pixels):
    """A flat image with the pixels at the (x, y) keys of `pixels` (named p_x_y, e.g. p_3_4=(255, 0, 0, 255)) set."""
    img = Image.new("RGBA", size, color)
    for name, value in pixels.items():
        _, x, y = name.split("_")
        img.putpixel((int(x), int(y)), value)
    return img


def changed(old, new):
    mask = sd.changed_mask(old, new)
    return mask.histogram()[255], sd.regions(mask)


class Comparing(unittest.TestCase):
    def test_identical_images_have_no_diff(self):
        count, boxes = changed(image(), image())
        self.assertEqual((count, boxes), (0, []))

    def test_one_changed_pixel_is_counted_and_boxed_tightly(self):
        count, boxes = changed(image(), image(p_37_21=(255, 255, 255, 255)))
        self.assertEqual(count, 1)
        self.assertEqual(boxes, [(37, 21, 38, 22)])

    def test_a_pixel_in_the_last_partial_cell_is_found(self):
        # 100×80 is not a multiple of the 16-pixel grid: the last column and row are cells of their own
        count, boxes = changed(image(), image(p_99_79=(0, 0, 0, 255)))
        self.assertEqual((count, boxes), (1, [(99, 79, 100, 80)]))

    def test_the_smallest_difference_in_any_channel_counts(self):
        for channel in range(4):
            new = list(GRAY)
            new[channel] += 1 if new[channel] < 255 else -1
            with self.subTest(channel="RGBA"[channel]):
                self.assertEqual(changed(image(), image(p_5_5=tuple(new)))[0], 1)

    def test_two_far_apart_changes_are_two_boxes(self):
        new = image((400, 300), p_10_10=(255, 0, 0, 255), p_12_11=(255, 0, 0, 255), p_380_280=(0, 255, 0, 255))
        count, boxes = changed(image((400, 300)), new)
        self.assertEqual(count, 3)
        self.assertEqual(boxes, [(10, 10, 13, 12), (380, 280, 381, 281)])

    def test_boxes_come_in_reading_order_by_their_top_edge(self):
        # both pixels are in the grid's row 5, but the right one is higher: found left to right, listed top to bottom
        count, boxes = changed(image((600, 200)), image((600, 200), p_5_95=(255, 0, 0, 255), p_400_80=(255, 0, 0, 255)))
        self.assertEqual((count, boxes), (2, [(400, 80, 401, 81), (5, 95, 6, 96)]))

    def test_changes_close_together_are_one_box(self):
        count, boxes = changed(image(), image(p_10_10=(1, 2, 3, 255), p_30_10=(1, 2, 3, 255)))
        self.assertEqual((count, boxes), (2, [(10, 10, 31, 11)]))

    def test_a_scattered_change_is_a_few_boxes_that_hold_every_changed_pixel(self):
        spots = [(x, y) for x in range(5, 600, 60) for y in range(5, 400, 60)]   # 70 spots, none near another
        new = image((600, 400))
        for x, y in spots:
            new.putpixel((x, y), (255, 0, 0, 255))
        count, boxes = changed(image((600, 400)), new)
        self.assertEqual(count, len(spots))
        self.assertLessEqual(len(boxes), sd.MAX_BOXES)
        for x, y in spots:
            self.assertTrue(any(b[0] <= x < b[2] and b[1] <= y < b[3] for b in boxes), (x, y))

    def test_a_very_scattered_change_stays_quick_and_bounded(self):
        new = image((1280, 3000))
        for x in range(3, 1280, 20):
            for y in range(3, 3000, 20):
                new.putpixel((x, y), (255, 0, 0, 255))
        count, boxes = changed(image((1280, 3000)), new)
        self.assertEqual(count, 64 * 150)
        self.assertEqual(len(boxes), 1)

    def test_a_different_size_compares_the_area_both_have(self):
        old, new = image((100, 80)), image((100, 120), p_50_70=(255, 0, 0, 255), p_50_100=(255, 0, 0, 255))
        mask = sd.changed_mask(old, new)
        self.assertEqual(mask.size, (100, 80))
        self.assertEqual(mask.histogram()[255], 1)   # the pixel below the old image is not a change


class Highlighting(unittest.TestCase):
    def test_changed_pixels_are_painted_grown_and_boxed_over_a_faded_baseline(self):
        old = image((100, 80), (0, 0, 0, 255))
        new = image((100, 80), (0, 0, 0, 255), p_50_40=(255, 255, 255, 255))
        mask = sd.changed_mask(old, new)
        out = sd.highlight(old, mask, sd.regions(mask), new.size)
        self.assertEqual(out.size, (100, 80))
        self.assertEqual(out.getpixel((50, 40)), sd.CHANGED)
        self.assertEqual(out.getpixel((52, 42)), sd.CHANGED)      # grown by two pixels
        self.assertEqual(out.getpixel((53, 40)), sd.OUTLINE)      # the box is drawn around it (4 pixels out, 2 wide)
        self.assertEqual(out.getpixel((10, 10)), (178, 178, 178))  # black at 30%, over white
        self.assertEqual(out.getpixel((1, 1)), (178, 178, 178))

    def test_a_bigger_new_image_gets_a_bigger_canvas_with_the_extra_area_marked(self):
        old, new = image((100, 80)), image((100, 120), p_5_5=(255, 0, 0, 255))
        mask = sd.changed_mask(old, new)
        out = sd.highlight(old, mask, sd.regions(mask), new.size)
        self.assertEqual(out.size, (100, 120))
        self.assertEqual(out.getpixel((50, 110)), sd.BEYOND)


class GitRepository(unittest.TestCase):
    """screenshots/ in a repository of its own, where main moves on after the branch starts."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        (self.repo / "screenshots").mkdir()
        self.git("init", "-q", "-b", "main")
        self.save("screenshots/same-phone.png", image())
        self.save("screenshots/edited-phone.png", image())
        self.save("screenshots/gone-phone.png", image())
        self.save("screenshots/taller-phone.png", image())
        self.save("screenshots/main-only-phone.png", image())
        (self.repo / "screenshots/notes.txt").write_text("one")
        self.commit("start")

    def git(self, *args):
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, env=env, text=True).stdout.strip()

    def save(self, name, img):
        img.save(self.repo / name)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def branch(self):
        """A branch that edits, adds, deletes and resizes, and main moves on meanwhile (its change must not show)."""
        self.git("checkout", "-q", "-b", "pr")
        self.save("screenshots/edited-phone.png", image(p_20_30=(255, 0, 0, 255)))
        self.save("screenshots/taller-phone.png", image((100, 100), p_1_1=(255, 0, 0, 255)))
        self.save("screenshots/new-phone.png", image((50, 60)))
        (self.repo / "screenshots/gone-phone.png").unlink()
        (self.repo / "screenshots/notes.txt").write_text("two")
        (self.repo / "other.png").write_bytes(b"not in screenshots/")
        head = self.commit("the change")
        self.git("checkout", "-q", "main")
        self.save("screenshots/main-only-phone.png", image(p_0_0=(255, 0, 0, 255)))
        base = self.commit("main moves on")
        return base, head

    def test_identical_trees_have_no_changes(self):
        sha = self.git("rev-parse", "HEAD")
        report = sd.diff(sha, sha, repo=self.repo)
        self.assertFalse(report["changed"])
        self.assertEqual(report["files"], [])

    def test_added_deleted_modified_and_resized_files(self):
        base, head = self.branch()
        out = self.repo / "out"
        report = sd.diff(base, head, out, repo=self.repo)
        by_path = {f["path"]: f for f in report["files"]}
        self.assertEqual(sorted(by_path), ["screenshots/edited-phone.png", "screenshots/gone-phone.png",
                                           "screenshots/new-phone.png", "screenshots/taller-phone.png"])
        edited = by_path["screenshots/edited-phone.png"]
        self.assertEqual((edited["status"], edited["changed_pixels"], edited["compared_pixels"]), ("modified", 1, 8000))
        self.assertEqual(edited["boxes"], [(20, 30, 21, 31)])
        self.assertEqual(edited["highlight"], "edited-phone.diff.png")
        taller = by_path["screenshots/taller-phone.png"]
        self.assertEqual((taller["old_size"], taller["new_size"], taller["changed_pixels"]), ((100, 80), (100, 100), 1))
        self.assertEqual((by_path["screenshots/gone-phone.png"]["status"], by_path["screenshots/gone-phone.png"]["old_size"]),
                         ("deleted", (100, 80)))
        self.assertEqual((by_path["screenshots/new-phone.png"]["status"], by_path["screenshots/new-phone.png"]["new_size"]),
                         ("added", (50, 60)))
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["edited-phone.diff.png", "taller-phone.diff.png"])

    def test_a_png_with_the_same_pixels_but_other_bytes_is_listed_without_a_highlight(self):
        self.git("checkout", "-q", "-b", "pr")
        image().save(self.repo / "screenshots/edited-phone.png", compress_level=0)
        head = self.commit("re-encoded")
        out = self.repo / "out"
        report = sd.diff("main", head, out, repo=self.repo)
        (entry,) = report["files"]
        self.assertEqual((entry["status"], entry["changed_pixels"], entry["highlight"]), ("modified", 0, None))
        self.assertEqual(list(out.iterdir()), [])
        self.assertIn("same pixels (re-encoded)", sd.render(report))

    def test_a_file_that_is_not_an_image_is_reported_not_fatal(self):
        self.git("checkout", "-q", "-b", "pr")
        (self.repo / "screenshots/edited-phone.png").write_bytes(b"not a png")
        head = self.commit("broken")
        (entry,) = sd.diff("main", head, None, repo=self.repo)["files"]
        self.assertIn("UnidentifiedImageError", entry["error"])
        self.assertIn("could not be compared", sd.render({"files": [entry]}))

    def test_a_ref_that_does_not_exist_is_an_error(self):
        with self.assertRaises(SystemExit):
            sd.diff("no-such-ref", "HEAD", repo=self.repo)

    def test_the_command_prints_the_comment_with_dry_run_and_writes_nothing(self):
        _, head = self.branch()
        out = self.repo / "out"
        shown = io.StringIO()
        with contextlib.redirect_stdout(shown):
            code = sd.main(["--base", "main", "--head", head, "--out", str(out), "--dry-run", "--repo", str(self.repo)])
        self.assertEqual(code, 0)
        self.assertTrue(shown.getvalue().startswith(sd.MARKER))
        self.assertIn("edited-phone.png", shown.getvalue())
        self.assertFalse(out.exists())

    def test_the_command_writes_the_images_and_both_summaries(self):
        _, head = self.branch()
        out = self.repo / "out"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sd.main(["--base", "main", "--head", head, "--out", str(out), "--run-url", "https://x.invalid/r/1", "--repo", str(self.repo)]), 0)
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         ["edited-phone.diff.png", "summary.json", "summary.md", "taller-phone.diff.png"])
        self.assertTrue((out / "summary.md").read_text().startswith(sd.MARKER))
        self.assertIn("https://x.invalid/r/1#artifacts", (out / "summary.md").read_text())
        self.assertTrue(json.loads((out / "summary.json").read_text())["changed"])


def entry(name, changed_pixels=10, status="modified", **extra):
    base = {"path": f"screenshots/{name}", "status": status, "old_size": [440, 956], "new_size": [440, 956],
            "changed_pixels": changed_pixels, "compared_pixels": 440 * 956, "percent": 100 * changed_pixels / (440 * 956),
            "boxes": [(1, 2, 11, 12)] if changed_pixels else [], "highlight": name.replace(".png", ".diff.png"), "error": None}
    return base | extra


class Summary(unittest.TestCase):
    def test_no_changes_says_so_and_keeps_the_marker(self):
        text = sd.render({"files": []})
        self.assertTrue(text.startswith(sd.MARKER))
        self.assertIn("No screenshot changes", text)

    def test_the_table_lists_the_biggest_change_first_with_its_figures(self):
        text = sd.render({"files": [entry("a-phone.png", 5), entry("b-phone.png", 4400)]}, "https://x.invalid/runs/9")
        self.assertLess(text.index("b-phone.png"), text.index("a-phone.png"))
        self.assertIn("| `b-phone.png` | modified | 4,400 | 1.05% | `(1, 2)` 10×10 |", text)
        self.assertIn("[this run](https://x.invalid/runs/9#artifacts)", text)
        self.assertIn("leak or a nondeterminism bug", text)

    def test_a_tiny_share_is_not_shown_as_zero(self):
        self.assertEqual(sd._percent(0.0002), "<0.01%")
        self.assertEqual(sd._percent(12.3456), "12.35%")

    def test_a_resized_screenshot_shows_both_sizes(self):
        text = sd.render({"files": [entry("a.png", 7, new_size=[440, 1000])]})
        self.assertIn("resized 440×956 → 440×1000", text)

    def test_added_and_deleted_files_are_listed_with_their_size_only(self):
        files = [entry("new-phone.png", 0, "added", old_size=None, new_size=[440, 900], boxes=[], highlight=None),
                 entry("old-phone.png", 0, "deleted", new_size=None, boxes=[], highlight=None)]
        text = sd.render({"files": files})
        self.assertIn("**Added**\n\n- `new-phone.png` (440×900)", text)
        self.assertIn("**Deleted**\n\n- `old-phone.png` (440×956)", text)
        self.assertNotIn("| `new-phone.png`", text)
        self.assertNotIn("artifact", text)   # nothing was highlighted: no pointer to images that aren't there

    def test_a_long_list_is_cut_to_the_cap_with_the_biggest_diffs_kept(self):
        files = [entry(f"page-{i:04d}-phone.png", 100_000 - i) for i in range(2000)]
        text = sd.render({"files": files}, max_chars=5000)
        self.assertLessEqual(len(text), 5000)
        self.assertIn("page-0000-phone.png", text)
        self.assertNotIn("page-1999-phone.png", text)
        left = int(text.split("…and ")[1].split(" more modified")[0])
        self.assertEqual(text.count("| `page-") + left, 2000)
        self.assertIn(sd.REMINDER, text)

    def test_the_default_cap_is_well_under_githubs_limit_even_for_thousands_of_files(self):
        files = [entry(f"flow-{i:05d}-with-a-long-name-desktop.png", 50 + i, boxes=[(0, 0, 9, 9)] * 3) for i in range(5000)]
        files += [entry(f"new-{i:05d}-phone.png", 0, "added", boxes=[], highlight=None) for i in range(3000)]
        files += [entry(f"old-{i:05d}-phone.png", 0, "deleted", boxes=[], highlight=None) for i in range(3000)]
        text = sd.render({"files": files})
        self.assertLessEqual(len(text), sd.MAX_COMMENT_CHARS)
        self.assertLess(sd.MAX_COMMENT_CHARS, 65_536 // 2 + 1)
        self.assertIn("**Added**", text)
        self.assertIn("**Deleted**", text)
        self.assertRegex(text, r"…and \d+ more")


class Workflow(unittest.TestCase):
    def test_the_workflow_looks_for_the_marker_the_tool_writes(self):
        text = (ROOT / ".github/workflows/screenshot-diff.yml").read_text()
        self.assertIn(sd.MARKER, text)
        self.assertIn(f"retention-days: {sd.RETENTION_DAYS}", text)
        self.assertIn(f"name: {sd.ARTIFACT}", text)


if __name__ == "__main__":
    unittest.main()
