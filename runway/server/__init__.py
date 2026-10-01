"""Runway's web server: the JSON API, the web app's files, and the background sync. Standard library only.

handler.py serves requests (routing, sign-in, security headers, limits, static files) and has serve(); routes.py maps
each API path to its handler in api/, one module per area; sync.py keeps the data fresh; common.py holds what they
share. The names below are re-exported so `from runway import server; server.serve()` and the tests keep working.
They are references, not the state itself: to change or patch a module's setting (sync.AUTO_SYNC, handler.STATIC,
...), do it on the module that owns it.
"""
# ruff: noqa: F401
from __future__ import annotations

from .common import ApiError, EXTRA_HOSTS, SAFE_SUFFIXES, host_allowed, request_ref
from .sync import (
    DAILY_SYNC_HOUR, PLAID_REFRESH_AT, PLAID_SYNC_HOUR, VISIT_SYNC_MINUTES, _inv_lock, _sync_lock, background_sync,
    bank_configured, daily_due, notify_now, plaid_banks, plaid_due, plaid_refresh_due, refresh_plaid, refresh_prices,
    run_investment_sync, run_sync, sync_on_visit
)
from .handler import (
    APP_DIR, APP_INDEX, HEADER_DEADLINE, Handler, MAX_CONCURRENT_REQUESTS, MAX_JSON_BODY, MAX_RESTORE_BODY,
    MIN_BODY_RATE, PLAID_API, PLAID_ORIGINS, PUBLIC_FILES, REQUEST_TIMEOUT, STATIC, Server, ThreadingHTTPServer,
    content_security_policy, serve
)
from .routes import ROUTES
from .api.accounts import ACCOUNT_FIELDS, KINDS, api_account_update, api_accounts
from .api.budget import api_budget, api_budget_set, budget_carry
from .api.categories import (
    api_categories, api_category_add, api_category_look, api_category_move, api_category_remove, api_category_rename,
    api_rule_add, api_rule_apply, api_rule_delete, api_rule_preview, api_rule_update, api_rules
)
from .api.connections import (
    LINK_SYNC_WAIT, api_connect, api_plaid_exchange, api_plaid_item_remove, api_plaid_item_sync,
    api_plaid_link_token, api_plaid_match, api_plaid_oauth_resume, api_plaid_settings, api_plaid_status
)
from .api.equity import (
    api_carta_connect, api_carta_disconnect, api_carta_settings, api_carta_sync, api_equity, api_equity_company_add,
    api_equity_company_remove, api_equity_company_update, api_equity_grant_add, api_equity_grant_remove,
    api_equity_grant_update, carta_redirect_uri
)
from .api.investments import (
    api_cost_basis, api_finnhub_settings, api_finnhub_status, api_investments, api_live_quotes, api_plan_save, api_tracked_get, api_tracked_save, live_tickers
)
from .api.merchants import (
    api_logodev_fetch, api_logodev_settings, api_logodev_status, api_merchant_logo, api_merchant_logo_options,
    start_logo_backfill
)
from .api.networth import (
    api_asset_add, api_asset_refresh, api_asset_remove, api_asset_update, api_networth, api_realie_settings
)
from .api.notifications import api_push, api_push_prefs, api_push_subscribe, api_push_test, api_push_unsubscribe
from .api.recurring import (
    FREQS, api_recurring, api_recurring_add, api_recurring_delete, api_recurring_dismiss, api_recurring_missed,
    api_recurring_suggestion_restore, api_recurring_suggestions, api_recurring_suggestions_dismissed, api_recurring_update,
    api_tx_recurring
)
from .api.reports import (
    api_cashflow, api_month_pace, api_report_breakdown, api_report_income, api_report_merchant, api_report_merchants,
    api_report_spending, api_report_transactions
)
from .api.retail import (
    COSTCO_GRAPHQL_CONFIG, EXTENSION_DIR, EXT_ROUTES, MAX_EXT_BODY, TARGET_DETAIL_URLS, TARGET_HISTORY, TARGET_ORDER_PAGES, _retail_categorize_lock,
    api_retail, api_retail_apply, api_retail_candidates, api_retail_item, api_retail_link, api_retail_match,
    api_retail_order, api_retail_settings, api_retail_token, api_retail_token_remove, api_retail_unlink,
    ext_amazon_order, ext_amazon_transactions, ext_carta_data, ext_finish, ext_start, ext_target_history,
    ext_target_order, ext_costco_history, extension_zip
)
from .api.state import (
    api_override_delete, api_override_set, api_overview, api_settings, api_state, owner_choices, setup_steps
)
from .api.transactions import (
    api_ai_apply, api_ai_log, api_ai_suggest, api_recategorize, api_transactions, api_tx_accept, api_tx_bulk,
    api_tx_brand_name, api_tx_category, api_tx_split, tx_logos
)
