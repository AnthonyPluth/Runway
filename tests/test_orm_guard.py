"""Runway is moving from SQL text to SQLAlchemy statements built from runway/models.py
(docs/src/content/docs/contributing/orm.md). This counts the SQL text still passed to `execute()`/`executemany()` in
each module and fails if any module has more than tests/orm_allowlist.json allows, so the counts only go down (to 0).

What counts as SQL text: a string, f-string, string concatenation or formatting (`"..." + x`, `"..." % x`,
`"...".format()`, `", ".join()`), or a variable assigned one in the same function, as the first argument. Also any
`text(...)` without a `# raw SQL: <why>` comment on its line or the line above: text() is allowed only for SQL that
can't be written with SQLAlchemy, and must say why. Not counted: runway/migrations (history, written once), and the
driver-level SQL on a raw DB-API connection (`dbapi_conn.execute("PRAGMA ...")` in db.py's engine setup).

After converting a module, lower its count: `python -m tests.test_orm_guard --write` rewrites the allowlist from the
code as it is now (commit it with the conversion).
"""
import ast
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWLIST = os.path.join(ROOT, "tests", "orm_allowlist.json")
EXEMPT_RECEIVERS = {"dbapi_conn"}


def _is_sql_text(node, string_names: set[str]) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return _is_sql_text(node.left, string_names) or _is_sql_text(node.right, string_names)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("format", "join"):
        return _is_sql_text(node.func.value, string_names)
    if isinstance(node, ast.IfExp):
        return _is_sql_text(node.body, string_names) or _is_sql_text(node.orelse, string_names)
    if isinstance(node, ast.Name):
        return node.id in string_names
    return False


def _string_names(fn) -> set[str]:
    """Names assigned SQL text anywhere in a function (or module)."""
    names: set[str] = set()
    for _ in range(2):   # twice, so `q = base + " WHERE ..."` after `base = "SELECT ..."` counts
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and _is_sql_text(n.value, names):
                names.update(t.id for t in n.targets if isinstance(t, ast.Name))
            elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name) and _is_sql_text(n.value, names):
                names.add(n.target.id)
    return names


def count(path: str) -> int:
    with open(path, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    lines = src.splitlines()
    scopes = {id(tree): _string_names(tree)}
    parents = {}
    for p in ast.walk(tree):
        for ch in ast.iter_child_nodes(p):
            parents[id(ch)] = p
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scopes[id(ch)] = _string_names(ch)

    def names_for(node) -> set[str]:
        p = parents.get(id(node))
        while p is not None and id(p) not in scopes:
            p = parents.get(id(p))
        return scopes[id(p) if p is not None else id(tree)]

    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in ("execute", "executemany") and node.args:
            if isinstance(f.value, ast.Name) and f.value.id in EXEMPT_RECEIVERS:
                continue
            if _is_sql_text(node.args[0], names_for(node)):
                n += 1
        elif (isinstance(f, ast.Name) and f.id == "text") or (isinstance(f, ast.Attribute) and f.attr == "text"
                                                             and isinstance(f.value, ast.Name) and f.value.id == "sa"):
            around = " ".join(lines[max(0, node.lineno - 2):node.lineno])
            parent = parents.get(id(node))
            if isinstance(parent, ast.keyword) and parent.arg == "server_default":   # a column default in schema.py
                continue
            if "# raw SQL:" not in around:
                n += 1
    return n


def counts() -> dict[str, int]:
    out = {}
    base = os.path.join(ROOT, "runway")
    for d, dirs, files in os.walk(base):
        dirs[:] = sorted(x for x in dirs if x not in ("migrations", "static", "__pycache__"))
        for fn in sorted(files):
            if fn.endswith(".py"):
                p = os.path.join(d, fn)
                c = count(p)
                if c:
                    out[os.path.relpath(p, ROOT).replace(os.sep, "/")] = c
    return dict(sorted(out.items()))


class OrmGuardTests(unittest.TestCase):
    def test_no_new_sql_text(self):
        with open(ALLOWLIST) as f:
            allowed = json.load(f)
        now = counts()
        over = {m: (c, allowed.get(m, 0)) for m, c in now.items() if c > allowed.get(m, 0)}
        self.assertEqual(over, {}, "SQL text passed to execute() went up (module: (now, allowed)). Write the query with "
                                   "SQLAlchemy and the models instead: see docs/src/content/docs/contributing/orm.md.")

    def test_counter(self):
        src = '''
def f(conn, x):
    conn.execute("SELECT 1")
    conn.execute(f"SELECT {x}")
    conn.execute("SELECT " + x)
    q = "SELECT 1"
    q += " WHERE 1"
    conn.execute(q, ())
    conn.executemany("INSERT INTO t VALUES (?)", [])
    conn.execute(select(T))
    stmt = select(T)
    conn.execute(stmt)
    dbapi_conn.execute("PRAGMA foreign_keys=ON")
    text("SELECT 2")
    # raw SQL: a window function SQLAlchemy can express, but not readably
    text("SELECT 3")
'''
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(src)
        try:
            self.assertEqual(count(f.name), 6)
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    if "--write" in sys.argv:
        with open(ALLOWLIST, "w") as f:
            f.write("{\n" + ",\n".join(f'  "{m}": {c}' for m, c in counts().items()) + "\n}\n")
        print(f"wrote {ALLOWLIST}")
    else:
        unittest.main()
