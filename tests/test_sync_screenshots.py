"""tools/sync_screenshots.py: screenshots/ takes the full-page screenshots of the last `make verify` that passed: all of a
full run's (and nothing else), a limited run's without touching the rest, and nothing at all from a run that failed."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sync_screenshots", ROOT / "tools" / "sync_screenshots.py")
assert _spec and _spec.loader
sync_screenshots = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_screenshots)


def png(body: bytes) -> bytes:
    """A stand-in for an image: what the sync checks of a PNG (its signature and its last chunk) around `body`."""
    return sync_screenshots.PNG + body + sync_screenshots.PNG_END


class Sync(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.src, self.dest = Path(tmp.name) / "verify", Path(tmp.name) / "screenshots"
        self.src.mkdir()

    def shot(self, folder, name, data):
        (folder / name).write_bytes(png(data) if isinstance(data, bytes) else data)

    def report(self, ok=True, limited=False, files=None, failed=None):
        """The report make verify (runway/verify.py) writes: files are the src images, unless given."""
        names = sorted(p.name for p in self.src.glob("*.png") if "-FAILED-" not in p.name) if files is None else files
        (self.src / "report.json").write_text(json.dumps({"ok": ok, "limited": limited, "files": names if ok else [],
                                                          "failed": failed or {}}))

    def test_copies_the_screenshots_and_only_those(self):
        self.shot(self.src, "budget-phone.png", b"a")
        self.shot(self.src, "flow-x-FAILED-phone.png", b"b")
        self.report()
        written, removed = sync_screenshots.sync(self.src, self.dest)
        self.assertEqual((written, removed), (["budget-phone.png"], []))
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["budget-phone.png"])

    def test_leaves_unchanged_files_alone_and_removes_stale_ones(self):
        self.dest.mkdir()
        self.shot(self.src, "a-phone.png", b"same")
        self.shot(self.src, "b-phone.png", b"new")
        self.shot(self.dest, "a-phone.png", b"same")
        self.shot(self.dest, "b-phone.png", b"old")
        self.shot(self.dest, "gone-phone.png", b"x")
        self.report()
        written, removed = sync_screenshots.sync(self.src, self.dest)
        self.assertEqual((written, removed), (["b-phone.png"], ["gone-phone.png"]))
        self.assertEqual((self.dest / "b-phone.png").read_bytes(), png(b"new"))

    def test_refuses_when_there_is_nothing_to_copy(self):
        self.dest.mkdir()
        self.shot(self.dest, "keep-phone.png", b"x")
        with self.assertRaises(SystemExit):
            sync_screenshots.sync(self.src, self.dest)   # no report at all
        self.report(files=[])
        with self.assertRaises(SystemExit):
            sync_screenshots.sync(self.src, self.dest)   # a report with no images
        self.assertTrue((self.dest / "keep-phone.png").exists())   # a missing verify run doesn't empty the directory

    def test_removes_a_leftover_tablet_screenshot_as_it_has_no_counterpart(self):
        self.dest.mkdir()
        self.shot(self.src, "budget-phone.png", b"a")
        self.shot(self.dest, "budget-tablet.png", b"old")
        self.shot(self.dest, "flow-y-tablet.png", b"old")
        self.report()
        written, removed = sync_screenshots.sync(self.src, self.dest)
        self.assertEqual((written, removed), (["budget-phone.png"], ["budget-tablet.png", "flow-y-tablet.png"]))
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["budget-phone.png"])

    def test_copies_nothing_from_a_run_that_failed(self):
        """All or nothing: the images a failed run did take (a page that passed beside one that didn't) stay out."""
        self.dest.mkdir()
        self.shot(self.dest, "budget-phone.png", b"old")
        self.shot(self.dest, "setup-phone.png", b"old")
        self.shot(self.src, "budget-phone.png", b"new")
        self.report(ok=False, failed={"page:setup": ["setup @ phone: timed out"]})
        with self.assertRaisesRegex(SystemExit, "page:setup"):
            sync_screenshots.sync(self.src, self.dest)
        self.assertEqual({p.name: p.read_bytes() for p in self.dest.iterdir()},
                         {"budget-phone.png": png(b"old"), "setup-phone.png": png(b"old")})
        with mock.patch.object(sync_screenshots, "VERIFY_DIR", self.src), mock.patch.object(sync_screenshots, "DEST", self.dest), \
                mock.patch("builtins.print") as said:
            self.assertEqual(sync_screenshots.main([]), 1)   # make screenshots fails, and says why
        self.assertIn("the last make verify failed", said.call_args.args[0])

    def test_a_limited_run_updates_its_own_images_and_leaves_the_rest(self):
        """PAGES="budget": the budget images are updated; the other pages' aren't removed for not being retaken."""
        self.dest.mkdir()
        self.shot(self.dest, "budget-phone.png", b"old")
        self.shot(self.dest, "setup-phone.png", b"setup")
        self.shot(self.dest, "flow-budget-edit-usual-phone.png", b"flow")
        self.shot(self.src, "budget-phone.png", b"new")
        self.shot(self.src, "budget-desktop.png", b"new")
        self.report(limited=True)
        written, removed = sync_screenshots.sync(self.src, self.dest)
        self.assertEqual((written, removed), (["budget-desktop.png", "budget-phone.png"], []))
        self.assertEqual({p.name: p.read_bytes() for p in self.dest.iterdir()},
                         {"budget-phone.png": png(b"new"), "budget-desktop.png": png(b"new"), "setup-phone.png": png(b"setup"),
                          "flow-budget-edit-usual-phone.png": png(b"flow")})

    def test_copies_only_the_images_the_report_lists(self):
        """A stray image left in artifacts/verify/ (from another run, or put there by hand) isn't one of this run's."""
        self.shot(self.src, "budget-phone.png", b"a")
        self.shot(self.src, "stray-phone.png", b"b")
        self.report(files=["budget-phone.png"])
        self.assertEqual(sync_screenshots.sync(self.src, self.dest), (["budget-phone.png"], []))
        self.assertFalse((self.dest / "stray-phone.png").exists())

    def test_refuses_a_cut_short_image_or_a_failed_step_s(self):
        self.dest.mkdir()
        self.shot(self.dest, "budget-phone.png", b"old")
        (self.src / "budget-phone.png").write_bytes(png(b"new")[:-4])   # written part way
        self.report(files=["budget-phone.png"])
        with self.assertRaisesRegex(SystemExit, "isn't a whole PNG"):
            sync_screenshots.sync(self.src, self.dest)
        self.shot(self.src, "x-FAILED-phone.png", b"f")
        self.report(files=["x-FAILED-phone.png"])
        with self.assertRaisesRegex(SystemExit, "no screenshot"):
            sync_screenshots.sync(self.src, self.dest)
        self.assertEqual((self.dest / "budget-phone.png").read_bytes(), png(b"old"))

    def test_a_copy_that_fails_part_way_changes_nothing(self):
        """Every file is staged beside screenshots/ before any is moved in: a disk that fills up part way leaves it as it was."""
        self.dest.mkdir()
        for name in ("a-phone.png", "b-phone.png"):
            self.shot(self.dest, name, b"old")
            self.shot(self.src, name, b"new")
        self.shot(self.dest, "stale-phone.png", b"x")
        self.report()
        real, copied = sync_screenshots.shutil.copyfile, []

        def copy(src, dst):
            if copied:
                raise OSError(28, "No space left on device")
            copied.append(dst)
            return real(src, dst)
        with mock.patch.object(sync_screenshots.shutil, "copyfile", side_effect=copy), self.assertRaises(OSError):
            sync_screenshots.sync(self.src, self.dest)
        self.assertEqual({p.name: p.read_bytes() for p in self.dest.iterdir()},
                         {"a-phone.png": png(b"old"), "b-phone.png": png(b"old"), "stale-phone.png": png(b"x")})
        self.assertEqual([p.name for p in self.dest.parent.iterdir() if p.name.startswith(".screenshots-sync-")], [])


if __name__ == "__main__":
    unittest.main()
