"""The second review's risky paths (.github/workflows/agent-review.yml, `second-review-paths`): a short list of exact
files, the code that directly implements .github/agent-review/invariants.md. Every entry is a file that exists, so a
renamed module doesn't leave a dead entry behind, and none is a glob, so the list can't grow back into whole
directories. Every module under runway/ that encrypts or decrypts through secretbox is on the list, so a new place
that handles a secret in the clear is reviewed (invariant 1)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/agent-review.yml"
# A call such as `secretbox.encrypt(`, `secretbox.decrypt(` or `secretbox.encrypt_stored(`, or a name imported from it.
SECRETBOX_CALL = re.compile(r"\bsecretbox\.(?:en|de)crypt\w*\s*\(|from\s+\S*secretbox\s+import\s+[^\n]*\b(?:en|de)crypt")


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

    def test_every_module_that_encrypts_or_decrypts_is_listed(self):
        callers = sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "runway").rglob("*.py")
                         if SECRETBOX_CALL.search(path.read_text(encoding="utf-8")))
        self.assertIn("runway/providers/plaid.py", callers)   # the pattern still finds a known caller
        self.assertEqual([path for path in callers if path not in risky_paths()], [])


if __name__ == "__main__":
    unittest.main()
