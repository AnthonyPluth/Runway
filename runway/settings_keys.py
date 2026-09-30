"""The names of the rows in the settings table, in one place.

Settings are plain key/value rows (db.get_setting / db.set_setting), so a mistyped key isn't an error: it just reads
as never set. Every key the app uses is named here, and tests/test_settings_keys.py fails if code passes a string
literal instead. Renaming a value here orphans what's already saved, so a rename needs a migration too.
"""
from __future__ import annotations

from datetime import date

# Syncing: when each kind of sync last ran (ISO timestamps), for the schedule and for Settings.
LAST_SYNC_OK = "last_sync_ok"                      # the last bank sync that worked
LAST_AUTO_SYNC_ATTEMPT = "last_auto_sync_attempt"  # the last sync started by itself, worked or not
LAST_INV_SYNC = "last_inv_sync"
LAST_PLAID_BANK_SYNC = "last_plaid_bank_sync"
LAST_PLAID_INV_SYNC = "last_plaid_inv_sync"
LAST_PLAID_REFRESH = "last_plaid_refresh"

# SimpleFIN
SIMPLEFIN_ACCESS_URL = "simplefin_access_url"
SIMPLEFIN_BACKFILL = "simplefin_backfill"            # JSON: accounts seen, and those whose history was fetched
SIMPLEFIN_HOLDINGS_SEEN = "simplefin_holdings_seen"  # JSON: investment accounts that reported holdings
SF_RAW_PREFIX = "sf_raw:"                            # + account id: the last raw holdings, for re-reading them


def sf_raw(acct_id: str) -> str:
    return f"{SF_RAW_PREFIX}{acct_id}"


# Plaid
PLAID_CLIENT_ID = "plaid_client_id"
PLAID_SECRET = "plaid_secret"
PLAID_ENV = "plaid_env"
PLAID_REDIRECT_URI = "plaid_redirect_uri"
PLAID_PENDING_LINK = "plaid_pending_link"      # JSON: the Link session in progress
DEDUPE_SIMPLEFIN_V2 = "dedupe_simplefin_v2"    # set once the one-time SimpleFIN/Plaid duplicate cleanup ran


def plaid_stmt_note(item_id: str) -> str:
    """Why a Plaid item's statements couldn't be fetched, if they couldn't."""
    return f"plaid_stmt_note:{item_id}"


# AI categorizing (OpenRouter)
OPENROUTER_API_KEY = "openrouter_api_key"
LLM_MODEL = "llm_model"
LAST_LLM_ERROR = "last_llm_error"
AUTO_AI_ON_SYNC = "auto_ai_on_sync"   # "1"/"0"; on unless switched off

# General preferences
PRIMARY_ACCOUNT = "primary_account"
HORIZON_DAYS = "horizon_days"
SETUP_DISMISSED = "setup_dismissed"
MIGRATED_DAILY_SPEND_OFF = "migrated_daily_spend_off"   # the v4 one-time switch-off in db.init has run

# Notifications
NOTIFY_PREFS = "notify_prefs"             # JSON
VAPID_PRIVATE_KEY = "vapid_private_key"   # web push signing key, made on first use

# Retirement planner
RETIREMENT_PLAN = "retirement_plan"   # JSON: the whole plan


def fire(field: str) -> str:
    """A figure changed on the old financial-independence card (read only, to seed a new plan)."""
    return f"fire_{field}"


# Carta (API sign-in)
CARTA_ENV = "carta_env"
CARTA_CLIENT_ID = "carta_client_id"
CARTA_CLIENT_SECRET = "carta_client_secret"
CARTA_ACCESS_TOKEN = "carta_access_token"
CARTA_REFRESH_TOKEN = "carta_refresh_token"
CARTA_TOKEN_EXPIRES = "carta_token_expires"
CARTA_OAUTH_STATE = "carta_oauth_state"
CARTA_REDIRECT_URI = "carta_redirect_uri"
CARTA_MOCK_ON = "carta_mock_on"
CARTA_LAST_SYNC = "carta_last_sync"
CARTA_LAST_ERROR = "carta_last_error"
# Carta (read from the website)
CARTA_WEB_CAPTURE = "carta_web_capture"   # JSON: what was read so far
CARTA_WEB_LAST = "carta_web_last"
CARTA_WEB_LAST_ERROR = "carta_web_last_error"

# Realie (home values)
REALIE_API_KEY = "realie_api_key"


def realie_calls(day: date) -> str:
    """Realie lookups made in the month of day (its free tier is counted per month)."""
    return f"realie_calls:{day:%Y-%m}"


# Live stock prices (Finnhub)
FINNHUB_API_KEY = "finnhub_api_key"


# Merchant logos (Logo.dev)
LOGODEV_TOKEN = "logodev_token"     # the publishable key (pk_...)
LOGODEV_SECRET = "logodev_secret"   # the secret key (sk_...), optional: Brand Search, for better name matches
LOGODEV_LAST_ERROR = "logodev_last_error"            # why the last lookup by website failed
LOGODEV_LAST_ERROR_NAME = "logodev_last_error_name"  # ... and by name
LOGODEV_THEME = "logodev_theme"     # the theme (merchants.THEME) the stored Logo.dev logos were fetched for

# Retailer order import
RETAIL_TOKEN_HASH = "retail_token_hash"
RETAIL_TOKEN_CREATED = "retail_token_created"
RETAIL_AI = "retail_ai"   # "1"/"0"; on unless switched off

# MCP server (runway/mcp_server.py): its read-only key
MCP_TOKEN_HASH = "mcp_token_hash"
MCP_TOKEN_CREATED = "mcp_token_created"


def retail_last(retailer: str) -> str:
    return f"retail_last_{retailer}"


def retail_summary(retailer: str) -> str:
    return f"retail_summary_{retailer}"


# Rows that hold secrets: stored encrypted (runway/secretbox.py) and left out of backups unless asked for.
SECRETS = frozenset({SIMPLEFIN_ACCESS_URL, PLAID_SECRET, OPENROUTER_API_KEY, REALIE_API_KEY, FINNHUB_API_KEY, LOGODEV_TOKEN, LOGODEV_SECRET,
                     VAPID_PRIVATE_KEY, CARTA_CLIENT_SECRET, CARTA_ACCESS_TOKEN, CARTA_REFRESH_TOKEN, PLAID_PENDING_LINK})
