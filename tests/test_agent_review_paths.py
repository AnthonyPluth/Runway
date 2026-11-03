"""The second review's risky paths (.github/workflows/agent-review.yml, `second-review-paths`): a short list of exact
files, the code that directly implements .github/agent-review/invariants.md. Every entry is a file that exists, so a
renamed module doesn't leave a dead entry behind, and none is a glob, so the list can't grow back into whole
directories. Every module under runway/ that encrypts or decrypts through secretbox is on the list, so a new place
that handles a secret in the clear is reviewed (invariant 1)."""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/agent-review.yml"


def _is_secret_function(name: str) -> bool:
    return name.startswith(("encrypt", "decrypt"))


def uses_secretbox(source: str) -> bool:
    """Whether the module refers to secretbox's encrypt or decrypt functions (`encrypt`, `decrypt`, `encrypt_stored`, ...)
    at all: called or passed as a callback (`_convert(..., secretbox.encrypt)`), through any name the module is bound to
    (`from ..storage import secretbox as sb`, `import runway.storage.secretbox as sb`), or imported by name, in one line
    or a parenthesised list (`from runway.storage.secretbox import (\n    encrypt,\n)`)."""
    tree = ast.parse(source)
    aliases = set()     # local names bound to the secretbox module
    dotted = set()      # `import runway.storage.secretbox` (no alias): referred to by the full dotted name
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == "secretbox" and any(_is_secret_function(a.name) for a in node.names):
                return True
            for a in node.names:
                if a.name == "secretbox":
                    aliases.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[-1] == "secretbox":
                    if a.asname:
                        aliases.add(a.asname)
                    else:
                        dotted.add(a.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and _is_secret_function(node.attr):
            if isinstance(node.value, ast.Name) and node.value.id in aliases:
                return True
            if ast.unparse(node.value) in dotted:
                return True
    return False


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
                         if uses_secretbox(path.read_text(encoding="utf-8")))
        self.assertIn("runway/providers/plaid.py", callers)   # the pattern still finds a known caller
        self.assertEqual([path for path in callers if path not in risky_paths()], [])


class UsesSecretbox(unittest.TestCase):
    def test_finds_every_form_of_reference(self):
        for source in [
            "from ..storage import db, secretbox\nx = secretbox.encrypt(token)\n",
            "from . import secretbox\nrows = _convert(t, cols, rows, secretbox.encrypt)\n",   # a callback, not a call
            "from . import secretbox\nsecretbox.encrypt_stored(conn)\n",
            "from ..storage import secretbox as sb\nf = sb.decrypt\n",
            "import runway.storage.secretbox as sb\nsb.encrypt(x)\n",
            "import runway.storage.secretbox\nrunway.storage.secretbox.decrypt(x)\n",
            "from runway.storage.secretbox import encrypt\n",
            "from .secretbox import decrypt as open_secret\n",
            "from runway.storage.secretbox import (\n    other,\n    decrypt,\n)\n",
        ]:
            with self.subTest(source=source):
                self.assertTrue(uses_secretbox(source))

    def test_ignores_other_modules_and_names(self):
        for source in [
            "from ..storage import db, secretbox\nsecretbox.is_encrypted(x)\n",   # imported, but no encrypt or decrypt
            "from cryptography.fernet import Fernet\nFernet(key).encrypt(x)\n",
            "def encrypt(x):\n    return x\nencrypt(1)\n",
            "from runway.storage.secretbox import is_encrypted\n",
            "# secretbox.encrypt(token) in a comment\n",
        ]:
            with self.subTest(source=source):
                self.assertFalse(uses_secretbox(source))


if __name__ == "__main__":
    unittest.main()
