"""Amazon, Target and Costco orders: read them, match them to card transactions, and split each transaction by what was in it.

None of these stores offers an API for your order history, so Runway's browser extension (extension/) reads it with the
sign-in you already have in your browser, the way the stores' own pages do, and sends what it gets here. All the
reading of those pages happens on this side, so when a store changes its pages the fix is an update to Runway
rather than to the extension:

- Amazon: the Payments → Transactions pages list every card charge with its order number (Amazon charges each
  shipment separately), and each order's details page lists its items. Both are read with the `amazon-orders`
  library's parsers.
- Target: its order history API, for online orders and in-store purchases (anything tied to your Target account:
  Target Circle, a saved card, the Wallet barcode). Each order counts as one charge of its total.
- Costco: the receipts costco.com's Orders & Purchases page lists (warehouse and gas station), which its GraphQL
  service returns with every line in one reply. Each receipt counts as one charge of its total.

Each charge is matched to a bank transaction with the same amount a few days later, from a merchant that looks like
the store. The order's items are categorized (what you picked for the same item before, then the AI model if one is
set up), and the transaction is split across those categories in proportion to what the items cost, so tax,
shipping and discounts are shared out fairly. If everything lands in one category the transaction just gets it.

A transaction you categorized or split yourself is left alone.

The parts: `store` (the stores, and keeping orders, items and charges), `token` (the extension's key), `parsers/`
(one module per store), `items` (categorizing items), `split` (splitting a transaction by its order), `match`
(pairing charges with transactions, and finishing an import), `undo` (what Undo saves and puts back) and `view` (what
the app shows). Everything the rest of Runway uses is here, as `retail.<name>`.
"""
from __future__ import annotations

from .items import AI_BATCH, ai_title, categorize_items, item_prompt, set_item_category, suggest_for_order
from .match import (MATCH_AFTER, MATCH_BEFORE, MERCHANT, candidates, categorize_and_apply, finish, link, match,
                    match_and_apply, unlink, unmatched_count)
from .parsers.amazon import amazon_order, amazon_transactions
from .parsers.costco import COSTCO_GRAPHQL, COSTCO_MAX_DAYS, COSTCO_QUERY, costco_history
from .parsers.target import target_history, target_order
from .split import allocate, apply, recategorize_part, set_transaction_category
from .store import FIRST_IMPORT_DAYS, MAX_ATTEMPTS, NAMES, OVERLAP_DAYS, RETAILERS, RetailError, item_key, order_key, since
from .token import REFUSALS, TOKEN_DAYS, TOUCH_EVERY, new_token, remove_token, token_check, token_expires, token_problem
from .undo import (charge_state, charges_of_transactions, item_transactions, item_undo_state, items_of_transactions,
                   order_mates, restore_charge, restore_charges, restore_item_state, restore_items)
from .view import for_transactions, order_detail, status

__all__ = [
    "AI_BATCH", "COSTCO_GRAPHQL", "COSTCO_MAX_DAYS", "COSTCO_QUERY", "FIRST_IMPORT_DAYS", "MATCH_AFTER", "MATCH_BEFORE",
    "MAX_ATTEMPTS", "MERCHANT", "NAMES", "OVERLAP_DAYS", "REFUSALS", "RETAILERS", "TOKEN_DAYS", "TOUCH_EVERY",
    "RetailError", "ai_title", "allocate", "amazon_order", "amazon_transactions", "apply", "candidates",
    "categorize_and_apply", "categorize_items", "charge_state", "charges_of_transactions", "costco_history", "finish",
    "for_transactions", "item_key", "item_prompt", "item_transactions", "item_undo_state", "items_of_transactions", "link",
    "match", "match_and_apply", "new_token", "order_detail", "order_key", "order_mates", "recategorize_part",
    "remove_token", "restore_charge", "restore_charges", "restore_item_state", "restore_items", "set_item_category",
    "set_transaction_category", "since", "status", "suggest_for_order", "target_history", "target_order", "token_check",
    "token_expires", "token_problem", "unlink", "unmatched_count",
]
