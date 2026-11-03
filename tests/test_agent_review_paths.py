"""The second review's risky paths (.github/workflows/agent-review.yml, `second-review-paths`): a short list of exact
files, the code that directly implements .github/agent-review/invariants.md. Every entry is a file that exists, so a
renamed module doesn't leave a dead entry behind, and none is a glob, so the list can't grow back into whole
directories. Every module under runway/ that encrypts or decrypts through secretbox is on the list, and so is every
module that reads a secret setting (a key in settings_keys.SECRETS, which db.get_setting and db.get_settings decrypt
on the way out), so a new place that handles a secret in the clear is reviewed (invariant 1)."""
import ast
import re
import unittest
from pathlib import Path

from runway.storage import settings_keys

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


# The accessors that hand back a settings row decrypted (db.get_setting, db.get_settings).
SETTING_READERS = ("get_setting", "get_settings")
SETTING_WRITER = "set_setting"   # encrypts a secret on the way in; what it is given is the plaintext
SECRET_KEY_NAMES = {name: value for name, value in vars(settings_keys).items()
                    if isinstance(value, str) and value in settings_keys.SECRETS}


def _bindings(tree: ast.AST) -> tuple[set[str], dict[str, str], dict[str, ast.expr]]:
    """What a module calls settings_keys, the secret keys it imports by name, and its module-level constants
    (`STATE_SETTINGS = (sk.A, *OTHERS)`): ({alias}, {local name: secret key's value}, {name: its expression})."""
    aliases: set[str] = set()
    by_name: dict[str, str] = {}
    consts: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "settings_keys":
                    aliases.add(a.asname or a.name)
                elif (node.module or "").split(".")[-1] == "settings_keys" and a.name in SECRET_KEY_NAMES:
                    by_name[a.asname or a.name] = SECRET_KEY_NAMES[a.name]
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[-1] == "settings_keys":
                    aliases.add(a.asname or a.name)   # without `as`, the full dotted name is what the module writes
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            consts[node.targets[0].id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            consts[node.target.id] = node.value
    return aliases, by_name, consts


def _secret_keys(expr: ast.AST, bindings, seen: frozenset[str] = frozenset()) -> set[str]:
    """The secret keys an expression names: `sk.PLAID_SECRET`, a key imported by name, a string that is one, and any of
    those inside a tuple, list or comprehension or behind a module-level constant. A name it can't trace names none."""
    aliases, by_name, consts = bindings
    found: set[str] = set()
    for node in ast.walk(expr):
        if isinstance(node, ast.Attribute) and ast.unparse(node.value) in aliases:
            if node.attr in SECRET_KEY_NAMES:
                found.add(SECRET_KEY_NAMES[node.attr])
        elif isinstance(node, ast.Name):
            if node.id in by_name:
                found.add(by_name[node.id])
            elif node.id in consts and node.id not in seen:
                found |= _secret_keys(consts[node.id], bindings, seen | {node.id})
        elif isinstance(node, ast.Constant) and node.value in settings_keys.SECRETS:
            found.add(node.value)
    return found


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _reads_secret(call: ast.Call, bindings) -> bool:
    """Whether the call is db.get_setting / db.get_settings (or the function imported by name) with a secret key."""
    if _called_name(call) not in SETTING_READERS:
        return False
    key_args = call.args[1:2] + [k.value for k in call.keywords if k.arg in ("key", "keys")]
    return any(_secret_keys(arg, bindings) for arg in key_args)


def _plain_reads(expr: ast.AST, bindings, held: frozenset[str] = frozenset()) -> bool:
    """Whether the expression is a secret read itself, or `read or default` / `a if c else read`: the setting comes back
    as it is (`bool(read)`, a comparison or `json.loads(read)` doesn't hand the secret on)."""
    if isinstance(expr, ast.BoolOp):
        return any(_plain_reads(v, bindings, held) for v in expr.values)
    if isinstance(expr, ast.IfExp):
        return _plain_reads(expr.body, bindings, held) or _plain_reads(expr.orelse, bindings, held)
    if isinstance(expr, ast.Name):
        return expr.id in held
    return isinstance(expr, ast.Call) and _reads_secret(expr, bindings)


def _writes_secret(call: ast.Call, bindings) -> bool:
    """Whether the call is db.set_setting with a secret key and a value that isn't None or a literal: a key typed into
    Settings (from the request body), or a token a provider just got, which the module holds in the clear before it is
    stored. (A literal is a seed, like demo.py's made-up key; None clears the setting.)"""
    if _called_name(call) != SETTING_WRITER or len(call.args) < 3:
        return False
    value = call.args[2]
    return not isinstance(value, ast.Constant) and bool(_secret_keys(call.args[1], bindings))


def secret_returning_functions(source: str) -> set[str]:
    """Functions that hand back a secret setting they read (`return db.get_setting(conn, sk.OPENROUTER_API_KEY) or None`,
    or `key = db.get_setting(...)` and later `return key`): whoever calls one gets the plaintext too. Not followed: a
    secret that is put in a tuple, dict or object before it is returned, passed on through another function, or handed
    to a caller as an argument (PLAINTEXT_RECEIVERS names those by hand)."""
    tree = ast.parse(source)
    bindings = _bindings(tree)
    names = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            held = frozenset(   # `key = db.get_setting(...)`, returned further down
                t.id for node in ast.walk(fn) if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
                and _plain_reads(node.value, bindings) for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
                if isinstance(t, ast.Name))
            if any(isinstance(node, ast.Return) and node.value is not None and _plain_reads(node.value, bindings, held)
                   for node in ast.walk(fn)):
                names.add(fn.name)
    return names


def reads_secret_setting(source: str, wrappers: frozenset[str] = frozenset()) -> bool:
    """Whether the module handles a secret setting in the clear: a call to get_setting or get_settings whose key is in
    settings_keys.SECRETS (named as `sk.X`, imported by name, written as the string, or reached through a module-level
    tuple), a call to one of `wrappers`, the functions that return such a setting, or a set_setting that stores one
    it was given (the handlers that take a key from the request body and only write it)."""
    tree = ast.parse(source)
    bindings = _bindings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (_reads_secret(node, bindings) or _writes_secret(node, bindings)
                                           or _called_name(node) in wrappers):
            return True
    return False


def reads_settings_rows(source: str) -> bool:
    """Whether the module selects the settings table's value column itself (`select(Setting.key, Setting.value)`), which
    skips the decrypting that get_setting does and returns a secret as stored."""
    return any(isinstance(node, ast.Attribute) and node.attr == "value" and isinstance(node.value, ast.Name)
               and node.value.id == "Setting" for node in ast.walk(ast.parse(source)))


def _runway_modules():
    for path in sorted((ROOT / "runway").rglob("*.py")):
        if "migrations" not in path.relative_to(ROOT).parts:
            yield path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")


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

    def test_every_module_that_reads_a_secret_setting_is_listed(self):
        modules = dict(_runway_modules())
        wrappers = frozenset().union(*(secret_returning_functions(src) for src in modules.values()))
        self.assertIn("ai_access", wrappers)   # the tracing still finds a known wrapper
        readers = sorted(path for path, src in modules.items() if reads_secret_setting(src, wrappers))
        for known in ("runway/providers/carta.py", "runway/domain/categorize.py", "runway/server/api/state.py",
                      "runway/server/sync.py"):
            self.assertIn(known, readers)   # the tracing still finds known readers
        self.assertEqual([path for path in readers if path not in risky_paths()], [])

    def test_every_module_that_is_handed_a_secret_is_listed(self):
        for path in PLAINTEXT_RECEIVERS:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())
                self.assertIn(path, risky_paths())

    def test_no_module_reads_settings_rows_around_get_setting_unreviewed(self):
        direct = sorted(path for path, src in _runway_modules() if reads_settings_rows(src))
        self.assertEqual([path for path in direct if path not in risky_paths() and path not in ROWS_NOT_SECRET], [])
        for path in ROWS_NOT_SECRET:
            self.assertIn(path, direct)   # an entry that stopped reading rows is dropped


# Modules that never read a secret setting themselves but are handed one already decrypted (a caller reads it and passes it
# on), so no trace of the accessors finds them: kept by hand, and listed for the same reason as the readers.
PLAINTEXT_RECEIVERS = {
    "runway/providers/simplefin.py": "sync.py reads the access URL and passes it to simplefin.sync",
    "runway/providers/finnhub.py": "investments.py reads the API key and passes it to the shared live connection",
    "runway/providers/prices.py": "the live quote stream takes the Finnhub key (live_key) and hands it to finnhub's feed",
}

# Modules that select settings rows themselves but never a secret one, and why.
ROWS_NOT_SECRET = {
    "runway/providers/sfinvest.py": "reads the sf_raw: rows (the last raw holdings), which aren't in settings_keys.SECRETS",
}


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


class ReadsSecretSetting(unittest.TestCase):
    def test_finds_every_form_of_secret_read(self):
        for source in [
            "from ..storage import db, settings_keys as sk\nx = db.get_setting(conn, sk.OPENROUTER_API_KEY)\n",
            "from ..storage import settings_keys as sk\nbool(db.get_setting(conn, sk.CARTA_CLIENT_SECRET) or '')\n",
            "import runway.storage.settings_keys as keys\ndb.get_setting(conn, keys.PLAID_SECRET, 'x')\n",
            "import runway.storage.settings_keys\ndb.get_setting(conn, runway.storage.settings_keys.PLAID_SECRET)\n",
            "from ..storage.settings_keys import FINNHUB_API_KEY\ndb.get_setting(conn, FINNHUB_API_KEY)\n",
            "from ..storage.settings_keys import LOGODEV_TOKEN as TOKEN\ndb.get_setting(conn, key=TOKEN)\n",
            "from ..storage import settings_keys as sk\ndb.get_settings(conn, [sk.PRIMARY_ACCOUNT, sk.REALIE_API_KEY])\n",
            "from ..storage import settings_keys as sk\nKEYS = (sk.SIMPLEFIN_ACCESS_URL,)\nALL = (sk.LLM_MODEL, *KEYS)\n"
            "db.get_settings(conn, ALL)\n",   # through module-level tuples, as api_state does
            "from ..storage import settings_keys as sk\ndb.set_setting(conn, sk.REALIE_API_KEY, key)\n",   # a key from the request
            "from ..storage import settings_keys as sk\ndb.set_setting(conn, sk.LOGODEV_SECRET, body['s'].strip())\n",
            "db.get_setting(conn, 'vapid_private_key')\n",   # the string itself
            "from ..storage import settings_keys as sk\nget_setting(conn, sk.CARTA_ACCESS_TOKEN)\n",   # imported by name
        ]:
            with self.subTest(source=source):
                self.assertTrue(reads_secret_setting(source))

    def test_ignores_settings_that_are_not_secrets(self):
        for source in [
            "from ..storage import settings_keys as sk\ndb.get_setting(conn, sk.LLM_MODEL)\n",
            "from ..storage import settings_keys as sk\ndb.get_settings(conn, (sk.HORIZON_DAYS, sk.CARTA_CLIENT_ID))\n",
            "from ..storage import settings_keys as sk\ndb.get_setting(conn, sk.retail_last(name))\n",
            "from ..storage import settings_keys as sk\ndb.get_setting(conn, key)\n",   # a key it can't trace
            "from ..storage import settings_keys as sk\nx = sk.PLAID_SECRET\n",   # named, never read
            "from ..storage import settings_keys as sk\ndb.set_setting(conn, sk.PLAID_SECRET, None)\n",   # clearing one
            "from ..storage import settings_keys as sk\ndb.set_setting(conn, sk.OPENROUTER_API_KEY, 'sk-demo')\n",   # a seed
            "from ..storage import settings_keys as sk\ndb.set_setting(conn, sk.LLM_MODEL, model)\n",   # not a secret
            "db.other_setting(conn, 'plaid_secret')\n",
            "# db.get_setting(conn, sk.PLAID_SECRET) in a comment\n",
        ]:
            with self.subTest(source=source):
                self.assertFalse(reads_secret_setting(source))

    def test_a_function_that_returns_a_secret_makes_its_callers_readers(self):
        wrapper = ("from ..storage import settings_keys as sk\n"
                   "def ai_access(conn):\n    return db.get_setting(conn, sk.OPENROUTER_API_KEY) or None\n"
                   "def mode(conn):\n    return db.get_setting(conn, sk.AI_PROVIDER)\n"
                   "def configured(conn):\n    return bool(db.get_setting(conn, sk.OPENROUTER_API_KEY))\n")
        names = secret_returning_functions(wrapper)
        self.assertEqual(names, {"ai_access"})
        held = ("from ..storage import settings_keys as sk\ndef key(conn):\n    k = db.get_setting(conn, sk.REALIE_API_KEY)\n"
                "    return k\ndef model(conn):\n    m = db.get_setting(conn, sk.LLM_MODEL)\n    return m\n"
                "def has(conn):\n    k = db.get_setting(conn, sk.REALIE_API_KEY)\n    return bool(k)\n")
        self.assertEqual(secret_returning_functions(held), {"key"})   # through a local variable
        self.assertTrue(reads_secret_setting("key = categorize.ai_access(conn)\n", frozenset(names)))
        self.assertTrue(reads_secret_setting("key = ai_access(conn)\n", frozenset(names)))
        self.assertFalse(reads_secret_setting("key = categorize.using_ollama(conn)\n", frozenset(names)))
        self.assertFalse(reads_secret_setting("key = categorize.ai_access(conn)\n"))

    def test_finds_a_read_of_the_settings_table_itself(self):
        self.assertTrue(reads_settings_rows("select(Setting.key, Setting.value).where(Setting.key.in_(keys))\n"))
        self.assertFalse(reads_settings_rows("select(Setting.key).where(Setting.key == 'x')\n"))
        self.assertFalse(reads_settings_rows("select(Other.value)\n"))


class ListedPathsAreChecked(unittest.TestCase):
    """The list's tests fail when an entry goes: each rule is run against the list with one module taken off."""

    def test_removing_a_reader_from_the_list_fails_the_check(self):
        modules = dict(_runway_modules())
        wrappers = frozenset().union(*(secret_returning_functions(src) for src in modules.values()))
        readers = {path for path, src in modules.items() if reads_secret_setting(src, wrappers)}
        for path in ("runway/providers/carta.py", "runway/domain/categorize.py", "runway/server/api/state.py",
                     "runway/providers/realie.py", "runway/domain/churn_found.py"):
            with self.subTest(path=path):
                trimmed = [p for p in risky_paths() if p != path]
                self.assertEqual(sorted(p for p in readers if p not in trimmed), [path])


if __name__ == "__main__":
    unittest.main()
