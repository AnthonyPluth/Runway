"""Category management: add, rename, move and remove, with one level of subcategories (Parent > Sub)."""
from __future__ import annotations

from sqlalchemy import delete, func, insert, select, update

from . import db, rules
from .models import Budget, Category, RetailItem, RetailItemMemory, Transaction, TxSplit

MAX_DEPTH = 2   # levels including the top one: Food > Restaurants


class CategoryError(ValueError):
    pass


# The colors a category can wear (the chart palette, validated for the dark surface, plus a teal and a gray).
PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767", "#1c9aa8", "#8a8a86"]
BLUE, ORANGE, GREEN, AMBER, PINK, LEAF, VIOLET, CORAL, TEAL, GRAY = PALETTE

# Default emoji and color for the built-in categories, and for common names people give their own.
DEFAULT_LOOKS: dict[str, tuple[str, str]] = {
    "groceries": ("🛒", LEAF), "restaurants": ("🍽️", ORANGE), "coffee & snacks": ("☕", AMBER),
    "shopping": ("🛍️", PINK), "travel": ("✈️", TEAL), "public transit": ("🚆", TEAL), "rideshare & taxi": ("🚕", AMBER),
    "auto & gas": ("⛽", CORAL), "parking & tolls": ("🅿️", BLUE), "utilities": ("💡", AMBER),
    "subscriptions": ("🔁", VIOLET), "technology": ("💻", BLUE), "medical": ("🩺", CORAL), "pharmacy": ("💊", CORAL),
    "home improvement": ("🔨", ORANGE), "mortgage": ("🏠", BLUE), "rent": ("🏠", BLUE), "loans": ("🏦", GRAY),
    "taxes": ("🧾", GRAY), "entertainment": ("🎬", VIOLET), "extra-curriculars": ("🎨", PINK),
    "gifts & donations": ("🎁", PINK), "fees & interest": ("💸", CORAL), "other": ("📦", GRAY),
    "income": ("💰", GREEN), "paycheck": ("💰", GREEN), "refunds": ("↩️", GREEN), "credit card payment": ("💳", GRAY),
    "transfer": ("🔄", GRAY), "ignore": ("🚫", GRAY),
}
# For names not listed above: the first word that appears in the name decides.
KEYWORD_LOOKS: list[tuple[tuple[str, ...], tuple[str, str]]] = [
    (("grocer", "food"), ("🛒", LEAF)), (("restaurant", "dining", "takeout", "fast food"), ("🍽️", ORANGE)),
    (("coffee", "cafe", "snack"), ("☕", AMBER)), (("bar", "alcohol", "wine", "beer"), ("🍷", PINK)),
    (("cloth", "apparel"), ("👕", PINK)), (("shop", "amazon"), ("🛍️", PINK)), (("pet", "vet"), ("🐾", ORANGE)),
    (("child", "kid", "baby", "daycare"), ("🧸", PINK)), (("school", "education", "tuition", "book"), ("📚", BLUE)),
    (("gym", "fitness", "sport"), ("🏋️", CORAL)), (("health", "doctor", "dental", "medic"), ("🩺", CORAL)),
    (("insur",), ("🛡️", BLUE)), (("phone", "mobile", "internet", "cable"), ("📱", VIOLET)),
    (("electric", "water", "gas bill", "utilit"), ("💡", AMBER)), (("car", "auto", "fuel", "gas"), ("⛽", CORAL)),
    (("travel", "hotel", "flight", "vacation"), ("✈️", TEAL)), (("home", "house", "garden", "furnit"), ("🏡", ORANGE)),
    (("beauty", "hair", "personal"), ("💅", PINK)), (("game", "hobby", "music", "movie", "stream"), ("🎮", VIOLET)),
    (("gift", "donat", "charit"), ("🎁", PINK)), (("invest", "saving", "retire"), ("📈", GREEN)),
    (("salary", "pay", "bonus", "interest", "dividend", "income"), ("💰", GREEN)), (("tax",), ("🧾", GRAY)),
    (("fee", "bank"), ("💸", CORAL)), (("business", "work", "office"), ("💼", BLUE)),
]


def default_look(name: str, parent_color: str | None = None) -> tuple[str, str]:
    """(emoji, color) for a category nobody has set one for. A subcategory wears its parent's color."""
    key = (name or "").strip().lower()
    look = DEFAULT_LOOKS.get(key)
    if not look:
        look = next((lk for words, lk in KEYWORD_LOOKS if any(w in key for w in words)), None)
    if not look:   # something steady for this name: the same name always gets the same color
        look = ("🏷️", PALETTE[sum(map(ord, key)) % (len(PALETTE) - 1)])
    return look[0], parent_color or look[1]


def set_look(conn, name: str, icon: str | None, color: str | None) -> None:
    """Set a category's emoji and color. An empty value goes back to the default."""
    if not _exists(conn, name):
        raise CategoryError("Category not found")
    icon = (icon or "").strip() or None
    if icon and (len(icon) > 16 or any(ch.isalnum() and ch.isascii() for ch in icon)):
        raise CategoryError("Pick an emoji for the icon")
    color = (color or "").strip().lower() or None
    if color and not (len(color) == 7 and color[0] == "#" and all(ch in "0123456789abcdef" for ch in color[1:])):
        raise CategoryError("Pick a color")
    conn.execute(update(Category).where(Category.name == name).values(icon=icon, color=color))


def _exists(conn, name: str) -> bool:
    return conn.execute(select(Category.name).where(Category.name == name)).fetchone() is not None


def _parents(conn) -> dict[str, str | None]:
    return {r["name"]: r["parent"] for r in conn.execute(select(Category.name, Category.parent))}


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
    cats = db.rows(conn.execute(select(Category.name, Category.is_transfer, Category.is_income, Category.parent,
                                       Category.icon, Category.color)))
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
            c["path"] = [*trail, c["name"]]
            c["depth"] = len(trail)
            c["top"] = c["path"][0]
            c["protected"] = c["name"] in db.PROTECTED_CATEGORIES
            c["has_children"] = bool(kids.get(c["name"]))
            c["custom_icon"], c["custom_color"] = c.pop("icon"), c.pop("color")
            parent_color = next((o["color"] for o in reversed(out) if o["name"] == parent), None) if parent else None
            icon, color = default_look(c["name"], parent_color)
            c["icon"], c["color"] = c["custom_icon"] or icon, c["custom_color"] or color
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
    if conn.execute(select(Category.name).where(func.lower(Category.name) == func.lower(name))).fetchone():
        raise CategoryError(f"There's already a category called {name}")
    if parent:
        p = conn.execute(select(Category).where(Category.name == parent)).fetchone()
        if not p:
            raise CategoryError("Parent category not found")
        if len(path(conn, parent)) >= MAX_DEPTH:
            raise CategoryError("Subcategories can't have subcategories of their own")
        is_transfer, is_income = bool(p["is_transfer"]), bool(p["is_income"])  # a subcategory is the same kind as its parent
    conn.execute(insert(Category).values(name=name, is_transfer=int(is_transfer), is_income=int(is_income),
                                         parent=parent or None))


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
    conn.execute(update(Category).where(Category.name == name).values(parent=new_parent))
    if new_parent:  # the moved branch takes on its new parent's kind (spending, money in, not spending)
        p = conn.execute(select(Category.is_transfer, Category.is_income).where(Category.name == new_parent)).fetchone()
        conn.execute(update(Category).where(Category.name.in_([name, *descendants(conn, name)]))
                     .values(is_transfer=p["is_transfer"], is_income=p["is_income"]))


def rename(conn, old: str, new: str) -> None:
    new = (new or "").strip()
    if old in db.PROTECTED_CATEGORIES:
        raise CategoryError(f"{old} is used by Runway itself and can't be renamed")
    if not _exists(conn, old):
        raise CategoryError("Category not found")
    if not new:
        raise CategoryError("Give the category a name")
    if new == old:
        return
    if conn.execute(select(Category.name).where(func.lower(Category.name) == func.lower(new), Category.name != old)).fetchone():
        raise CategoryError(f"There's already a category called {new}")
    conn.execute(update(Category).where(Category.name == old).values(name=new))
    conn.execute(update(Category).where(Category.parent == old).values(parent=new))
    _recategorize(conn, Transaction, old, new)
    _recategorize(conn, TxSplit, old, new)
    _recategorize(conn, RetailItem, old, new)   # order items, and what's remembered
    _recategorize(conn, RetailItemMemory, old, new)
    rules.rename_category(conn, old, new)
    _recategorize(conn, Budget, old, new)


def _recategorize(conn, model, old: str, new: str) -> int:
    """Move a table's rows from one category to another; returns how many."""
    return conn.execute(update(model).where(model.category == old).values(category=new)).rowcount


def remove(conn, name: str, move_to: str | None = None) -> int:
    """Remove a category. Its transactions and rules move to `move_to`, or (without one) the transactions
    go back to Review uncategorized and its rules are deleted. Returns how many transactions moved."""
    if name in db.PROTECTED_CATEGORIES:
        raise CategoryError(f"{name} is used by Runway itself and can't be removed")
    if not _exists(conn, name):
        raise CategoryError("Category not found")
    if conn.execute(select(Category.name).where(Category.parent == name)).fetchone():
        raise CategoryError(f"Remove or move the subcategories of {name} first")
    if move_to:
        if move_to == name or not _exists(conn, move_to):
            raise CategoryError("Pick a different category to move things to")
        n = _recategorize(conn, Transaction, name, move_to)
        n += _recategorize(conn, TxSplit, name, move_to)
        _recategorize(conn, RetailItem, name, move_to)
        _recategorize(conn, RetailItemMemory, name, move_to)
        rules.rename_category(conn, name, move_to)
    else:
        t = Transaction
        n = conn.execute(update(t).where(t.category == name).values(
            category=None, category_source=None, confidence=None, needs_review=1)).rowcount
        # parts of a split lose their category too, and the transaction goes back to Review
        for tx_id in conn.execute(select(TxSplit.tx_id).where(TxSplit.category == name).distinct()).scalars():
            conn.execute(delete(TxSplit).where(TxSplit.tx_id == tx_id))
            conn.execute(update(t).where(t.id == tx_id).values(
                is_split=0, category=None, category_source=None, confidence=None, needs_review=1))
            n += 1
        # order items go back to being decided (by the model, or the store's department) next time
        conn.execute(update(RetailItem).where(RetailItem.category == name).values(
            category=None, category_source=None, confidence=None))
        conn.execute(delete(RetailItemMemory).where(RetailItemMemory.category == name))
        rules.forget_category(conn, name)
    conn.execute(delete(Budget).where(Budget.category == name))
    conn.execute(delete(Category).where(Category.name == name))
    return n


def flatten(conn) -> int:
    """Move anything nested deeper than MAX_DEPTH up so it sits directly under its top-level category.
    (Deeper nesting was briefly allowed.) Returns how many categories moved."""
    parents = _parents(conn)
    moved = 0
    for name in list(parents):
        p = path(parents, name)
        if len(p) > MAX_DEPTH:
            conn.execute(update(Category).where(Category.name == name).values(parent=p[MAX_DEPTH - 2]))
            moved += 1
    return moved
