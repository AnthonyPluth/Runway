"""The second review's risky paths (.github/workflows/agent-review.yml, `second-review-paths`) against the layout: every
provider module and every HTTP handler is matched by an entry, so a pull request confined to one can't get the passing
"Second review" a pull request that isn't risky gets, and every entry still matches a file that exists, so a renamed
module doesn't leave a dead entry behind. fleet matches the globs with `**` spanning directories, as `PurePath.full_match` does."""
import re
import subprocess
import unittest
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/agent-review.yml"
# Areas whose every file must be risky: the outside services and the credentials they hold, and every HTTP handler.
WHOLE_AREAS = ("runway/providers/", "runway/server/api/")


def risky_globs() -> list[str]:
    block = re.search(r"^ {6}second-review-paths: \|\n((?: {8}\S.*\n)+)", WORKFLOW.read_text(), re.MULTILINE)
    assert block, "no second-review-paths block in agent-review.yml"
    return [line.strip() for line in block.group(1).splitlines()]


def tracked(prefix: str) -> list[str]:
    out = subprocess.run(["git", "ls-files", "--", prefix], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [path for path in out.splitlines() if path.endswith(".py") and not path.endswith("__init__.py")]


def matches(path: str, globs: list[str]) -> bool:
    return any(PurePosixPath(path).full_match(glob) for glob in globs)


class SecondReviewPaths(unittest.TestCase):
    def test_every_provider_and_every_handler_is_risky(self):
        globs = risky_globs()
        for area in WHOLE_AREAS:
            files = tracked(area)
            self.assertTrue(files, f"nothing tracked under {area}")
            self.assertEqual([path for path in files if not matches(path, globs)], [], f"not risky under {area}")

    def test_every_entry_matches_a_file_that_exists(self):
        all_files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
        for glob in risky_globs():
            self.assertTrue(any(PurePosixPath(path).full_match(glob) for path in all_files), f"{glob} matches no tracked file")

    def test_a_file_outside_the_list_is_not_risky_and_the_globs_span_directories(self):
        globs = risky_globs()
        self.assertFalse(matches("runway/domain/budgets.py", globs))
        self.assertTrue(matches("runway/providers/finnhub.py", globs))
        self.assertTrue(matches("runway/server/api/transactions.py", globs))
        self.assertTrue(matches("extension/src/background.js", globs))


if __name__ == "__main__":
    unittest.main()
