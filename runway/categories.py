"""Category management: add, rename, move and remove, with one level of subcategories (Parent > Sub)."""
from __future__ import annotations

from . import db

MAX_DEPTH = 2   # levels including the top one: Food > Restaurants


class CategoryError(ValueError):
    pass


def _parents(conn) -> dict[str, str | None]:
    return {r["name"]: r["parent"] for r in conn.execute("SELECT name, parent FROM categories")}


def path(conn_or_parents, name: str | None) -> list[str]:
    """[top, ..., name]. Stops safely on a broken or looping chain."""
    parents = conn_or_parents if isinstance(conn_or_parents, dict) else _parents(conn_or_parents)
    out: list[str] = []
    while name and name in parents and name not in out and len(out) <= MAX_DEPTH:
        out.insert(0, name)
        name = parents[name]
    return out


def descendants(conn, name: str) -> list[str]:
    parents = _parents(conn)
    return [c for c in parents if c != name and name in path(parents, c)[:-1]]


def _subtree_height(parents: dict, name: str) -> int:
    """1 for a leaf, 2 if it has children, and so on."""
    kids = [c for c, p in parents.items() if p == name]
    return 1 + max((_subtree_height(parents, k) for k in kids), default=0)


def all_categories(conn) -> list[dict]:
    """Every category in tree order (each followed by its subcategories), with depth, path and top-level name."""
    cats = db.rows(conn.execute("SELECT name, is_transfer, is_income, parent FROM categories"))
    names = {c["name"] for c in cats}
    for c in cats:   # an orphan (parent deleted by hand) shows at the top level
        if c["parent"] and c["parent"] not in names:
            c["parent"] = None
    kids: dict[str | None, list[dict]] = {}
    for c in cats:
        kids.setdefault(c["parent"], []).append(c)
    out: list[dict] = []

    def walk(parent: str | None, trail: list[str]) -> None:
        group = kids.get(parent, [])
        key = (lambda c: (c["is_transfer"], c["is_income"], c["name"].lower())) if parent is None else (lambda c: c["name"].lower())
        for c in sorted(group, key=key):
            if c["name"] in trail:
                continue
            c["path"] = trail + [c["name"]]
            c["depth"] = len(trail)
            c["top"] = c["path"][0]
            c["protected"] = c["name"] in db.PROTECTED_CATEGORIES
            c["has_children"] = bool(kids.get(c["name"]))
            out.append(c)
            walk(c["name"], c["path"])

    walk(None, [])
    return out


def top_level(conn, name: str | None) -> str | None:
    """The top-level category a category rolls up into (itself if it has no parent)."""
    if name is None:
        return None
    p = path(conn, name)
    return p[0] if p else name


def add(conn, name: str, parent: str | None = None, is_transfer: bool = False, is_income: bool = False) -> None:
    name = (name or "").strip()
    if not name:
        raise CategoryError("Give the category a name")
    if len(name) > 60:
        raise CategoryError("That name is too long")
    if conn.execute("SELECT 1 FROM categories WHERE lower(name)=lower(?)", (name,)).fetchone():
        raise CategoryError(f"There's already a category called {name}")
    if parent:
        p = conn.execute("SELECT * FROM categories WHERE name=?", (parent,)).fetchone()
        if not p:
            raise CategoryError("Parent category not found")
        if len(path(conn, parent)) >= MAX_DEPTH:
            raise CategoryError("Subcategories can't have subcategories of their own")
        is_transfer, is_income = bool(p["is_transfer"]), bool(p["is_income"])  # a subcategory is the same kind as its parent
    conn.execute(
        "INSERT INTO categories(name, is_transfer, is_income, parent) VALUES (?,?,?,?)",
        (name, int(is_transfer), int(is_income), parent or None),
    )


def move(conn, name: str, new_parent: str | None) -> None:
    """Put a category (and everything under it) under another category, or at the top level."""
    if name in db.PROTECTED_CATEGORIES:
        raise CategoryError(f"{name} is used by Runway itself and can't be moved")
    parents = _parents(conn)
    if name not in parents:
        raise CategoryError("Category not found")
    new_parent = new_parent or None
    if new_parent == parents[name]:
        return
    if new_parent:
        if new_parent not in parents:
            raise CategoryError("Parent category not found")
        if new_parent == name or name in path(parents, new_parent):
            raise CategoryError("A category can't go inside itself or one of its own subcategories")
        if len(path(parents, new_parent)) + _subtree_height(parents, name) > MAX_DEPTH:
            raise CategoryError("A subcategory can't go under another subcategory, and a category with subcategories can't become one")
    conn.execute("UPDATE categories SET parent=? WHERE name=?", (new_parent, name))
    if new_parent:  # the moved branch takes on its new parent's kind (spending, money in, not spending)
        p = conn.execute("SELECT is_transfer, is_income FROM categories WHERE name=?", (new_parent,)).fetchone()
        for c in [name] + descendants(conn, name):
            conn.execute("UPDATE categories SET is_transfer=?, is_income=? WHERE name=?", (p["is_transfer"], p["is_income"], c))


def rename(conn, old: str, new: str) -> None:
    new = (new or "").strip()
    if old in db.PROTECTED_CATEGORIES:
        raise CategoryError(f"{old} is used by Runway itself and can't be renamed")
    if not conn.execute("SELECT 1 FROM categories WHERE name=?", (old,)).fetchone():
        raise CategoryError("Category not found")
    if not new:
        raise CategoryError("Give the category a name")
    if new == old:
        return
    if conn.execute("SELECT 1 FROM categories WHERE lower(name)=lower(?) AND name<>?", (new, old)).fetchone():
        raise CategoryError(f"There's already a category called {new}")
    conn.execute("UPDATE categories SET name=? WHERE name=?", (new, old))
    conn.execute("UPDATE categories SET parent=? WHERE parent=?", (new, old))
    conn.execute("UPDATE transactions SET category=? WHERE category=?", (new, old))
    conn.execute("UPDATE rules SET category=? WHERE category=?", (new, old))
    conn.execute("UPDATE budgets SET category=? WHERE category=?", (new, old))


def remove(conn, name: str, move_to: str | None = None) -> int:
    """Remove a category. Its transactions and rules move to `move_to`, or (without one) the transactions
    go back to Review uncategorized and its rules are deleted. Returns how many transactions moved."""
    if name in db.PROTECTED_CATEGORIES:
        raise CategoryError(f"{name} is used by Runway itself and can't be removed")
    if not conn.execute("SELECT 1 FROM categories WHERE name=?", (name,)).fetchone():
        raise CategoryError("Category not found")
    if conn.execute("SELECT 1 FROM categories WHERE parent=?", (name,)).fetchone():
        raise CategoryError(f"Remove or move the subcategories of {name} first")
    if move_to:
        if move_to == name or not conn.execute("SELECT 1 FROM categories WHERE name=?", (move_to,)).fetchone():
            raise CategoryError("Pick a different category to move things to")
        n = conn.execute("UPDATE transactions SET category=? WHERE category=?", (move_to, name)).rowcount
        conn.execute("UPDATE rules SET category=? WHERE category=?", (move_to, name))
    else:
        n = conn.execute(
            "UPDATE transactions SET category=NULL, category_source=NULL, confidence=NULL, needs_review=1 WHERE category=?",
            (name,),
        ).rowcount
        conn.execute("DELETE FROM rules WHERE category=?", (name,))
    conn.execute("DELETE FROM budgets WHERE category=?", (name,))
    conn.execute("DELETE FROM categories WHERE name=?", (name,))
    return n


def flatten(conn) -> int:
    """Move anything nested deeper than MAX_DEPTH up so it sits directly under its top-level category.
    (Deeper nesting was briefly allowed.) Returns how many categories moved."""
    parents = _parents(conn)
    moved = 0
    for name in list(parents):
        p = path(parents, name)
        if len(p) > MAX_DEPTH:
            conn.execute("UPDATE categories SET parent=? WHERE name=?", (p[MAX_DEPTH - 2], name))
            moved += 1
    return moved
