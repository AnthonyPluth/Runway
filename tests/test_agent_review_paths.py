"""The second review's risky paths (.github/workflows/agent-review.yml, `second-review-paths`): a short list of exact
files, the code that directly implements .github/agent-review/invariants.md. Every entry is a file that exists, so a
renamed module doesn't leave a dead entry behind, and none is a glob, so the list can't grow back into whole
directories."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/agent-review.yml"


def risky_paths() -> list[str]:
    block = re.search(r"^ {6}second-review-paths: \|\n((?: {8}\S.*\n)+)", WORKFLOW.read_text(), re.MULTILINE)
    assert block, "no second-review-paths block in agent-review.yml"
    return [line.strip() for line in block.group(1).splitlines()]


class SecondReviewPaths(unittest.TestCase):
    def test_every_entry_is_a_file_that_exists(self):
        paths = risky_paths()
        self.assertTrue(paths)
        self.assertEqual([path for path in paths if not (ROOT / path).is_file()], [])

    def test_every_entry_is_an_exact_file_not_a_glob(self):
        self.assertEqual([path for path in risky_paths() if any(c in path for c in "*?[")], [])


if __name__ == "__main__":
    unittest.main()
