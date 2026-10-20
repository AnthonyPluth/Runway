"""tools/screenshot_diff.py: the pixels a pull request changed in screenshots/, found exactly, boxed and summed up for the
pull request comment. The images here are small and made in the tests; the git tests commit them to a repository of their own."""
import contextlib
import importlib.util
import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
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
                         ["edited-phone.diff.png", "report.html", "summary.json", "summary.md", "taller-phone.diff.png"])
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


def noise(size, seed=1):
    """An image that does not compress, so the size budget is felt."""
    rnd = random.Random(seed)
    return Image.frombytes("RGBA", size, rnd.randbytes(size[0] * size[1] * 4))


def cards(page):
    """The report's cards by the path they show."""
    return {re.search(r"<h2><a [^>]*>([^<]+)</a>", c).group(1): c for c in page.split("<article ")[1:]}


def tabs(card):
    return re.findall(r'role="tab"[^>]*>([^<]+)<', card)


class Report(GitRepository):
    """report.html: one self-contained page with the tabs for each changed screenshot."""

    def report(self, base, head, **kwargs):
        data = sd.diff(base, head, repo=self.repo)
        page, notes = sd.build_report(data, self.repo, **kwargs)
        return data, page, notes

    def on_main(self, branch, **files):
        """Commit `files` (name -> image) on a branch, merge it into main and return main's sha: a base to change from."""
        self.git("checkout", "-q", "-b", branch)
        for name, img in files.items():
            self.save(f"screenshots/{name}", img)
        self.commit(branch)
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", branch)
        return self.git("rev-parse", "HEAD")

    def test_every_changed_file_has_a_card_and_the_tabs_its_status_allows(self):
        base, head = self.branch()
        _, page, notes = self.report(base, head)
        self.assertEqual(notes, [])
        by = cards(page)
        self.assertEqual(sorted(by), ["screenshots/edited-phone.png", "screenshots/gone-phone.png",
                                      "screenshots/new-phone.png", "screenshots/taller-phone.png"])
        self.assertIn("4 images changed (1 added, 1 deleted, 1 modified, 1 resized)", page)
        self.assertEqual(tabs(by["screenshots/edited-phone.png"]), ["Diff", "Reference", "Actual", "Slider"])
        self.assertEqual(tabs(by["screenshots/taller-phone.png"]), ["Diff", "Reference", "Actual", "Slider"])
        self.assertEqual(tabs(by["screenshots/new-phone.png"]), ["Actual"])          # added: nothing to compare with
        self.assertEqual(tabs(by["screenshots/gone-phone.png"]), ["Reference"])      # deleted: nothing it became
        self.assertIn("Screenshot does not match the stored reference. 1 pixels (ratio 0.0001) differ.",
                      by["screenshots/edited-phone.png"])
        self.assertIn("New screenshot: there is no stored reference.", by["screenshots/new-phone.png"])
        self.assertIn("Screenshot removed", by["screenshots/gone-phone.png"])
        self.assertIn('role="tablist"', page)
        self.assertIn('role="tabpanel"', page)
        self.assertIn('aria-selected="true"', page)
        self.assertRegex(page, r'<img src="data:image/webp;base64,[^"]+" alt="Actual screenshot screenshots/new-phone.png')

    def test_the_page_is_self_contained(self):
        base, head = self.branch()
        _, page, _ = self.report(base, head)
        self.assertNotRegex(page, r"https?://|//[a-z]")
        self.assertNotRegex(page, r'(?:src|href|action|poster)="(?!data:image/|#)')
        for needle in ("<link", "@import", "url(http", "<iframe", "fetch(", "XMLHttpRequest", "import("):
            self.assertNotIn(needle, page)
        self.assertEqual(len(re.findall(r"<script", page)), 1)      # the one inline script
        self.assertEqual(len(re.findall(r"<style", page)), 1)

    def test_a_resized_image_that_got_shorter_is_marked_on_the_diff_and_shows_reference_and_actual(self):
        self.git("checkout", "-q", "-b", "pr")
        self.save("screenshots/taller-phone.png", image((100, 50), p_1_1=(255, 0, 0, 255)))     # 100×80 -> 100×50
        head = self.commit("shorter")
        data, page, _ = self.report("main", head)
        card = cards(page)["screenshots/taller-phone.png"]
        self.assertEqual(data["files"][0]["old_size"], (100, 80))
        self.assertIn("Screenshot size changed from 100×80 to 100×50. 1 pixels", card)
        self.assertIn("Dimensions differ: reference 100×80, actual 100×50.", card)
        self.assertIn("The reference has 30 more rows than the actual image.", card)
        self.assertEqual(tabs(card), ["Diff", "Reference", "Actual", "Slider"])
        old, new = image((100, 80)), image((100, 50), p_1_1=(255, 0, 0, 255))
        diff = sd.report_diff(old, new, sd.changed_mask(old, new), [])
        self.assertEqual(diff.size, (100, 80))
        stripes = {diff.getpixel((x, y)) for x in range(100) for y in range(50, 80)}
        self.assertIn(sd.OLD_ONLY, stripes)             # the rows only the reference has are striped amber
        self.assertNotIn(sd.NEW_ONLY, stripes)

    def test_a_resized_image_that_got_taller_stripes_the_new_rows_green(self):
        base, head = self.branch()
        _, page, _ = self.report(base, head)
        card = cards(page)["screenshots/taller-phone.png"]
        self.assertIn("The actual image has 20 more rows than the reference.", card)
        old, new = image((100, 80)), image((100, 100))
        diff = sd.report_diff(old, new, sd.changed_mask(old, new), [])
        self.assertIn(sd.NEW_ONLY, {diff.getpixel((x, y)) for x in range(100) for y in range(80, 100)})
        self.assertNotIn(sd.OLD_ONLY, {diff.getpixel((x, y)) for x in range(100) for y in range(100)})

    def test_the_comment_starts_with_the_link_when_the_report_has_a_url(self):
        base, head = self.branch()
        data = sd.diff(base, head, repo=self.repo)
        plain = sd.render(data, "https://x.invalid/runs/9")
        linked = sd.render(data, "https://x.invalid/runs/9", report_url="https://x.invalid/artifacts/7")
        self.assertEqual(linked.splitlines()[:5], [sd.MARKER, "### Screenshot changes", "",
                                                   "**View the visual diff: [report.html](https://x.invalid/artifacts/7)**", ""])
        self.assertIn("logged in to GitHub", linked)
        self.assertIn("| `edited-phone.png` | modified | 1 |", linked)       # the table stays below
        self.assertNotIn("View the visual diff", plain)

    def test_only_the_changed_bands_are_embedded_with_their_labels(self):
        base = self.on_main("tall", **{"tall-desktop.png": image((120, 3000))})
        self.git("checkout", "-q", "-b", "change")
        self.save("screenshots/tall-desktop.png", image((120, 3000), p_5_400=(255, 255, 255, 255), p_6_2500=(255, 255, 255, 255)))
        head = self.commit("two changes")
        _, page, _ = self.report(base, head, full_budget=0)
        card = cards(page)["screenshots/tall-desktop.png"]
        self.assertIn("Rows y 250–551 of 3,000 (unchanged rows omitted)", card)
        self.assertIn("Rows y 2,350–2,651 of 3,000 (unchanged rows omitted)", card)
        self.assertIn("250 unchanged rows (y 0–250) omitted from this report", card)
        self.assertIn("1,799 unchanged rows (y 551–2,350) omitted from this report", card)
        self.assertNotIn('class="allbtn"', card)                                # whole images did not fit the budget given
        self.assertEqual(len(re.findall(r"data:image/webp", card)), 2 * 3)      # two bands, three images each
        _, whole, _ = self.report(base, head)
        card = cards(whole)["screenshots/tall-desktop.png"]
        self.assertIn('class="allbtn"', card)
        self.assertIn("Show all 3,000 rows", card)
        self.assertEqual(len(re.findall(r"data:image/webp", card)), 3)          # whole images: one per picture, windows onto them
        self.assertIn("(y 551–2,350) hidden: use “Show all rows”", card)

    def test_a_smaller_budget_cuts_context_first_then_scales_down_and_says_so(self):
        base = self.on_main("noise", **{f"noise{n}-phone.png": noise((120, 900), n) for n in range(3)})
        self.git("checkout", "-q", "-b", "change")
        for n in range(3):
            img = noise((120, 900), n)
            img.putpixel((3, 450), (1, 2, 3, 255))
            img.putpixel((4, 40 + n), (1, 2, 3, 255))
            self.save(f"screenshots/noise{n}-phone.png", img)
        head = self.commit("changes")
        data = sd.diff(base, head, repo=self.repo)
        full, notes = sd.build_report(data, self.repo, full_budget=0)
        self.assertEqual(notes, [])
        with unittest.mock.patch.object(sd, "LEVELS", [(0, 1.0)]):
            less, _ = sd.build_report(data, self.repo, full_budget=0)
        with unittest.mock.patch.object(sd, "LEVELS", [(0, 0.5)]):
            small, _ = sd.build_report(data, self.repo, full_budget=0)
        self.assertLess(len(less), len(full))
        self.assertLess(len(small), len(less))
        budget = (len(less) + len(full)) // 2
        page, notes = sd.build_report(data, self.repo, budget=budget, full_budget=0)
        self.assertLessEqual(len(page), budget)
        self.assertEqual(len(notes), 1)
        self.assertRegex(notes[0], r"context around each change was cut from 150 to (60|20|0) rows\.$")
        budget = (len(small) + len(less)) // 2
        page, notes = sd.build_report(data, self.repo, budget=budget, full_budget=0)
        self.assertLessEqual(len(page), budget)
        self.assertRegex(notes[0], r"cut from 150 to 0 rows and the images were scaled down to \d+%\.$")
        self.assertIn(notes[0], page)                                   # the report itself says what was cut

    def test_a_budget_nothing_fits_in_leaves_images_out_instead_of_failing(self):
        self.git("checkout", "-q", "-b", "change")
        self.save("screenshots/edited-phone.png", noise((300, 300)))
        self.save("screenshots/taller-phone.png", noise((300, 300), 2))
        head = self.commit("noise")
        _, page, notes = self.report("main", head, budget=90_000)
        self.assertTrue(any("without their images" in n for n in notes))
        self.assertIn("The images of this file are left out", page)
        self.assertEqual(sorted(cards(page)), ["screenshots/edited-phone.png", "screenshots/taller-phone.png"])
        self.assertIn("Screenshot size changed from 100×80 to 300×300", page)     # the facts stay

    def test_a_file_that_is_not_an_image_and_a_same_pixels_file_get_a_card_without_tabs(self):
        self.git("checkout", "-q", "-b", "pr")
        (self.repo / "screenshots/edited-phone.png").write_bytes(b"not a png")
        image().save(self.repo / "screenshots/same-phone.png", compress_level=0)
        head = self.commit("odd")
        _, page, _ = self.report("main", head)
        by = cards(page)
        self.assertIn("Could not be compared", by["screenshots/edited-phone.png"])
        self.assertIn("Same pixels as the stored reference", by["screenshots/same-phone.png"])
        self.assertEqual(tabs(by["screenshots/same-phone.png"]), [])

    def test_the_command_writes_report_html_and_from_json_adds_the_link(self):
        _, head = self.branch()
        out = self.repo / "out"
        shown = io.StringIO()
        with contextlib.redirect_stdout(shown):
            sd.main(["--base", "main", "--head", head, "--out", str(out), "--repo", str(self.repo)])
        self.assertIn("report.html:", shown.getvalue())
        self.assertTrue((out / "report.html").read_text(encoding="utf-8").startswith("<!doctype html>"))
        self.assertNotIn("View the visual diff", (out / "summary.md").read_text())
        with contextlib.redirect_stdout(io.StringIO()):
            sd.main(["--from-json", str(out / "summary.json"), "--out", str(out), "--report-url", "https://x.invalid/a/1"])
        self.assertIn("**View the visual diff: [report.html](https://x.invalid/a/1)**", (out / "summary.md").read_text())

    def test_no_changes_make_no_report(self):
        sha = self.git("rev-parse", "HEAD")
        out = self.repo / "out"
        with contextlib.redirect_stdout(io.StringIO()):
            sd.main(["--base", sha, "--head", sha, "--out", str(out), "--repo", str(self.repo)])
        self.assertFalse((out / "report.html").exists())


class Bands(unittest.TestCase):
    def test_a_change_gets_context_and_stays_inside_the_image(self):
        self.assertEqual(sd.make_bands([(10, 12), (900, 901)], 1000, 150), [(0, 162), (750, 1000)])

    def test_changes_whose_context_touches_or_nearly_touches_are_one_band(self):
        self.assertEqual(sd.make_bands([(500, 501), (800, 801)], 2000, 150), [(350, 951)])         # 651 and 650 overlap
        self.assertEqual(sd.make_bands([(500, 501), (841, 842)], 2000, 150), [(350, 992)])         # 40 rows apart: joined
        self.assertEqual(len(sd.make_bands([(500, 501), (900, 901)], 2000, 150)), 2)

    def test_no_context_keeps_only_the_changed_rows(self):
        self.assertEqual(sd.make_bands([(500, 501), (900, 901)], 2000, 0), [(500, 501), (900, 901)])

    def test_a_very_scattered_change_is_capped_by_merging_the_nearest(self):
        runs = [(y, y + 1) for y in range(0, 20_000, 400)]           # 50 runs, 400 rows apart
        bands = sd.make_bands(runs, 20_000, 0)
        self.assertEqual(len(bands), sd.MAX_BANDS)
        self.assertTrue(all(bands[i][1] <= bands[i + 1][0] for i in range(len(bands) - 1)))
        self.assertEqual((bands[0][0], bands[-1][1]), (0, 19_601))

    def test_changed_rows_come_from_the_mask_and_the_extra_rows(self):
        old, new = image((100, 80)), image((100, 100), p_5_10=(255, 0, 0, 255), p_5_11=(255, 0, 0, 255), p_9_50=(255, 0, 0, 255))
        self.assertEqual(sd.changed_runs(old, new, sd.changed_mask(old, new)), [(10, 12), (50, 51), (80, 100)])

    def test_a_different_width_changes_every_row(self):
        old, new = image((100, 80)), image((90, 80))
        self.assertEqual(sd.changed_runs(old, new, sd.changed_mask(old, new)), [(0, 80)])

    def test_the_ratio_reads_like_vitests(self):
        self.assertEqual([sd._ratio(x) for x in (0, 0.02, 0.0213456, 0.00004)], ["0", "0.02", "0.0213", "<0.0001"])

    def test_big_images_are_png_not_webp(self):
        self.assertEqual(sd.encode(Image.new("RGB", (sd.WEBP_MAX + 1, 2)), 1.0)[0], "image/png")
        self.assertEqual(sd.encode(Image.new("RGBA", (30, 30), GRAY), 1.0)[0], "image/webp")


class Workflow(unittest.TestCase):
    def test_the_workflow_looks_for_the_marker_the_tool_writes(self):
        text = (ROOT / ".github/workflows/screenshot-diff.yml").read_text()
        self.assertIn(sd.MARKER, text)
        self.assertIn(f"retention-days: {sd.RETENTION_DAYS}", text)
        self.assertIn(f"name: {sd.ARTIFACT}", text)


if __name__ == "__main__":
    unittest.main()
