"""The API's routes: which handler answers each method and path."""
from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from typing import Any

from .api.accounts import api_account_update, api_accounts
from .api.budget import api_budget, api_budget_set
from .api.categories import (
    api_categories, api_category_add, api_category_look, api_category_move, api_category_remove, api_category_rename,
    api_rule_add, api_rule_apply, api_rule_delete, api_rule_preview, api_rule_update, api_rules
)
from .api.churning import (
    api_bank_bonus_add, api_bank_bonus_remove, api_bank_bonus_update, api_churn_balance, api_churn_benefit_add,
    api_churn_benefit_remove, api_churn_benefit_unuse, api_churn_benefit_update, api_churn_benefit_use,
    api_churn_card_add, api_churn_card_remove, api_churn_card_update, api_churn_currency, api_churn_currency_remove,
    api_churn_plan_done, api_churn_plan_undo, api_churn_rate, api_churn_task_add, api_churn_task_remove,
    api_churn_score, api_churn_task_snooze, api_churn_task_update, api_churn_wish_add, api_churn_wish_applied,
    api_churn_wish_remove, api_churn_wish_update, api_churning, api_churning_best
)
from .api.connections import (
    api_connect, api_inv_account, api_plaid_exchange, api_plaid_item_remove, api_plaid_item_sync,
    api_plaid_link_token, api_plaid_match, api_plaid_oauth_resume, api_plaid_settings, api_plaid_status
)
from .api.equity import (
    api_carta_connect, api_carta_disconnect, api_carta_settings, api_carta_sync, api_equity, api_equity_company_add,
    api_equity_company_remove, api_equity_company_update, api_equity_grant_add, api_equity_grant_remove,
    api_equity_grant_update
)
from .api.investments import (
    api_cost_basis, api_finnhub_settings, api_finnhub_status, api_investments, api_live_quotes, api_plan_save, api_tracked_get, api_tracked_save
)
from .api.mcp import api_mcp_revoke, api_mcp_settings, api_mcp_writes
from .api.merchants import (
    api_logodev_fetch, api_logodev_settings, api_logodev_status, api_merchant_logo, api_merchant_logo_options
)
from .api.networth import (
    api_asset_add, api_asset_refresh, api_asset_remove, api_asset_update, api_networth, api_realie_settings
)
from .api.notifications import api_push, api_push_prefs, api_push_subscribe, api_push_test, api_push_unsubscribe
from .api.recurring import (
    api_recurring, api_recurring_add, api_recurring_delete, api_recurring_dismiss, api_recurring_missed,
    api_recurring_suggestion_dismiss, api_recurring_suggestions, api_recurring_update, api_tx_recurring
)
from .api.reports import (
    api_cashflow, api_month_pace, api_report_breakdown, api_report_income, api_report_merchant, api_report_merchants,
    api_report_spending, api_report_transactions
)
from .api.retail import (
    api_retail, api_retail_apply, api_retail_candidates, api_retail_item, api_retail_link, api_retail_match,
    api_retail_order, api_retail_settings, api_retail_suggest, api_retail_token, api_retail_token_remove, api_retail_unlink
)
from .api.state import api_override_delete, api_override_set, api_overview, api_settings, api_state
from .api.transactions import (
    api_ai_apply, api_ai_log, api_ai_suggest, api_recategorize, api_transactions, api_tx_accept, api_tx_bulk,
    api_tx_category, api_tx_split
)


# (method, path pattern, handler): each handler takes (conn, query, body, *path params) and returns the JSON reply.
ROUTES: list[tuple[str, str, Callable[..., Any]]] = [
    ("GET", "/api/state", api_state),
    ("GET", "/api/overview", api_overview),
    ("GET", "/api/accounts", api_accounts),
    ("POST", "/api/accounts/{id}", api_account_update),
    ("GET", "/api/transactions", api_transactions),
    ("POST", "/api/transactions/bulk", api_tx_bulk),
    ("POST", "/api/transactions/{id}/category", api_tx_category),
    ("POST", "/api/transactions/{id}/accept", api_tx_accept),
    ("POST", "/api/transactions/{id}/split", api_tx_split),
    ("POST", "/api/transactions/{id}/recurring", api_tx_recurring),
    ("POST", "/api/overrides", api_override_set),
    ("DELETE", "/api/overrides", api_override_delete),
    ("GET", "/api/push", api_push),
    ("POST", "/api/push/subscribe", api_push_subscribe),
    ("POST", "/api/push/unsubscribe", api_push_unsubscribe),
    ("POST", "/api/push/prefs", api_push_prefs),
    ("POST", "/api/push/test", api_push_test),
    ("GET", "/api/budget", api_budget),
    ("POST", "/api/budget", api_budget_set),
    ("POST", "/api/ai/suggest", api_ai_suggest),
    ("POST", "/api/ai/apply", api_ai_apply),
    ("GET", "/api/ai/log", api_ai_log),
    ("GET", "/api/categories", api_categories),
    ("POST", "/api/categories", api_category_add),
    ("POST", "/api/categories/rename", api_category_rename),
    ("POST", "/api/categories/remove", api_category_remove),
    ("POST", "/api/categories/move", api_category_move),
    ("POST", "/api/categories/look", api_category_look),
    ("GET", "/api/cashflow", api_cashflow),
    ("GET", "/api/month_pace", api_month_pace),
    ("GET", "/api/reports/spending", api_report_spending),
    ("GET", "/api/reports/income", api_report_income),
    ("GET", "/api/reports/merchants", api_report_merchants),
    ("GET", "/api/reports/merchant", api_report_merchant),
    ("GET", "/api/reports/breakdown", api_report_breakdown),
    ("GET", "/api/reports/transactions", api_report_transactions),
    ("GET", "/api/rules", api_rules),
    ("POST", "/api/rules", api_rule_add),
    ("POST", "/api/rules/preview", api_rule_preview),
    ("DELETE", "/api/rules/{id}", api_rule_delete),
    ("POST", "/api/rules/{id}", api_rule_update),
    ("POST", "/api/rules/{id}/apply", api_rule_apply),
    ("GET", "/api/recurring", api_recurring),
    ("POST", "/api/recurring", api_recurring_add),
    ("GET", "/api/recurring/suggestions", api_recurring_suggestions),
    ("POST", "/api/recurring/suggestions/dismiss", api_recurring_suggestion_dismiss),
    ("GET", "/api/recurring/missed", api_recurring_missed),
    ("POST", "/api/recurring/dismiss", api_recurring_dismiss),
    ("POST", "/api/recurring/{id}", api_recurring_update),
    ("DELETE", "/api/recurring/{id}", api_recurring_delete),
    ("POST", "/api/connect", api_connect),
    ("GET", "/api/plaid/status", api_plaid_status),
    ("POST", "/api/plaid/settings", api_plaid_settings),
    ("POST", "/api/plaid/link_token", api_plaid_link_token),
    ("POST", "/api/plaid/exchange", api_plaid_exchange),
    ("POST", "/api/plaid/items/{id}/sync", api_plaid_item_sync),
    ("POST", "/api/plaid/items/{id}/remove", api_plaid_item_remove),
    ("POST", "/api/plaid/accounts/{id}", api_inv_account),
    ("POST", "/api/plaid/match", api_plaid_match),
    ("GET", "/api/plaid/oauth_resume", api_plaid_oauth_resume),
    ("GET", "/api/investments", api_investments),
    ("GET", "/api/networth", api_networth),
    ("POST", "/api/assets", api_asset_add),
    ("POST", "/api/assets/{id}", api_asset_update),
    ("POST", "/api/assets/{id}/remove", api_asset_remove),
    ("POST", "/api/assets/{id}/refresh", api_asset_refresh),
    ("POST", "/api/realie/settings", api_realie_settings),
    ("POST", "/api/finnhub/settings", api_finnhub_settings),
    ("GET", "/api/finnhub/status", api_finnhub_status),
    ("POST", "/api/logodev/settings", api_logodev_settings),
    ("GET", "/api/logodev/status", api_logodev_status),
    ("GET", "/api/merchants/logo-options", api_merchant_logo_options),
    ("POST", "/api/merchants/logo", api_merchant_logo),
    ("POST", "/api/logodev/fetch", api_logodev_fetch),
    ("GET", "/api/investments/live", api_live_quotes),
    ("POST", "/api/investments/plan", api_plan_save),
    ("GET", "/api/tracked/{id}", api_tracked_get),
    ("POST", "/api/tracked/{id}", api_tracked_save),
    ("POST", "/api/investments/cost", api_cost_basis),
    ("POST", "/api/settings", api_settings),
    ("GET", "/api/equity", api_equity),
    ("POST", "/api/equity/companies", api_equity_company_add),
    ("POST", "/api/equity/companies/{id}", api_equity_company_update),
    ("POST", "/api/equity/companies/{id}/remove", api_equity_company_remove),
    ("POST", "/api/equity/companies/{id}/grants", api_equity_grant_add),
    ("POST", "/api/equity/grants/{id}", api_equity_grant_update),
    ("POST", "/api/equity/grants/{id}/remove", api_equity_grant_remove),
    ("GET", "/api/churning", api_churning),
    ("GET", "/api/churning/best", api_churning_best),
    ("POST", "/api/churning/cards", api_churn_card_add),
    ("POST", "/api/churning/cards/{id}", api_churn_card_update),
    ("POST", "/api/churning/cards/{id}/remove", api_churn_card_remove),
    ("POST", "/api/churning/cards/{id}/rates", api_churn_rate),
    ("POST", "/api/churning/cards/{id}/plan/done", api_churn_plan_done),
    ("POST", "/api/churning/cards/{id}/plan/undo", api_churn_plan_undo),
    ("POST", "/api/churning/cards/{id}/benefits", api_churn_benefit_add),
    ("POST", "/api/churning/benefits/{id}", api_churn_benefit_update),
    ("POST", "/api/churning/benefits/{id}/remove", api_churn_benefit_remove),
    ("POST", "/api/churning/benefits/{id}/use", api_churn_benefit_use),
    ("POST", "/api/churning/benefits/{id}/unuse", api_churn_benefit_unuse),
    ("POST", "/api/churning/currencies", api_churn_currency),
    ("POST", "/api/churning/currencies/{id}/remove", api_churn_currency_remove),
    ("POST", "/api/churning/balances", api_churn_balance),
    ("POST", "/api/churning/tasks", api_churn_task_add),
    ("POST", "/api/churning/tasks/{id}", api_churn_task_update),
    ("POST", "/api/churning/tasks/{id}/remove", api_churn_task_remove),
    ("POST", "/api/churning/tasks/{id}/snooze", api_churn_task_snooze),
    ("POST", "/api/churning/wishlist", api_churn_wish_add),
    ("POST", "/api/churning/wishlist/{id}", api_churn_wish_update),
    ("POST", "/api/churning/wishlist/{id}/remove", api_churn_wish_remove),
    ("POST", "/api/churning/wishlist/{id}/applied", api_churn_wish_applied),
    ("POST", "/api/churning/scores", api_churn_score),
    ("POST", "/api/churning/bank", api_bank_bonus_add),
    ("POST", "/api/churning/bank/{id}", api_bank_bonus_update),
    ("POST", "/api/churning/bank/{id}/remove", api_bank_bonus_remove),
    ("POST", "/api/carta/settings", api_carta_settings),
    ("POST", "/api/carta/connect", api_carta_connect),
    ("POST", "/api/carta/sync", api_carta_sync),
    ("POST", "/api/carta/disconnect", api_carta_disconnect),
    ("GET", "/api/retail", api_retail),
    ("GET", "/api/mcp-settings", api_mcp_settings),
    ("POST", "/api/mcp-settings/writes", api_mcp_writes),
    ("POST", "/api/mcp-settings/connections/{id}/revoke", api_mcp_revoke),
    ("POST", "/api/retail/token", api_retail_token),
    ("POST", "/api/retail/token/remove", api_retail_token_remove),
    ("POST", "/api/retail/settings", api_retail_settings),
    ("POST", "/api/retail/match", api_retail_match),
    ("GET", "/api/retail/orders/{id}", api_retail_order),
    ("POST", "/api/retail/items/{id}", api_retail_item),
    ("POST", "/api/retail/orders/{id}/suggest", api_retail_suggest),
    ("POST", "/api/retail/charges/{id}/unlink", api_retail_unlink),
    ("POST", "/api/retail/charges/{id}/link", api_retail_link),
    ("POST", "/api/retail/charges/{id}/apply", api_retail_apply),
    ("GET", "/api/retail/charges/{id}/candidates", api_retail_candidates),
    ("POST", "/api/recategorize", api_recategorize),
]


def _match(pattern: str, path: str):
    p_parts, parts = pattern.strip("/").split("/"), path.strip("/").split("/")
    if len(p_parts) != len(parts):
        return None
    params = []
    for a, b in zip(p_parts, parts, strict=True):
        if a == "{id}":
            params.append(urllib.parse.unquote(b))
        elif a != b:
            return None
    return params
