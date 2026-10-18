"""tools/sync_screenshots.py: screenshots/ ends up holding exactly the full-page screenshots of the last `make verify`."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sync_screenshots", ROOT / "tools" / "sync_screenshots.py")
assert _spec and _spec.loader
sync_screenshots = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_screenshots)


class Sync(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.src, self.dest = Path(tmp.name) / "verify", Path(tmp.name) / "screenshots"
        self.src.mkdir()

    def shot(self, folder, name, data):
        (folder / name).write_bytes(data)

    def test_copies_the_screenshots_and_only_those(self):
        self.shot(self.src, "budget-phone.png", b"a")
        self.shot(self.src, "flow-x-FAILED-phone.png", b"b")
        (self.src / "report.json").write_text("{}")
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
        written, removed = sync_screenshots.sync(self.src, self.dest)
        self.assertEqual((written, removed), (["b-phone.png"], ["gone-phone.png"]))
        self.assertEqual((self.dest / "b-phone.png").read_bytes(), b"new")

    def test_refuses_when_there_is_nothing_to_copy(self):
        self.dest.mkdir()
        self.shot(self.dest, "keep-phone.png", b"x")
        with self.assertRaises(SystemExit):
            sync_screenshots.sync(self.src, self.dest)
        self.assertTrue((self.dest / "keep-phone.png").exists())   # a missing verify run doesn't empty the directory

    def test_removes_a_leftover_tablet_screenshot_as_it_has_no_counterpart(self):
        self.dest.mkdir()
        self.shot(self.src, "budget-phone.png", b"a")
        self.shot(self.dest, "budget-tablet.png", b"old")
        self.shot(self.dest, "flow-y-tablet.png", b"old")
        written, removed = sync_screenshots.sync(self.src, self.dest)
        self.assertEqual((written, removed), (["budget-phone.png"], ["budget-tablet.png", "flow-y-tablet.png"]))
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["budget-phone.png"])
