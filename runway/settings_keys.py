"""The names of the rows in the settings table, in one place.

Settings are plain key/value rows (db.get_setting / db.set_setting), so a mistyped key isn't an error: it just reads
as never set. Every key the app uses is named here, and tests/test_settings_keys.py fails if code passes a string
literal instead. Renaming a value here orphans what's already saved, so a rename needs a migration too.
"""
from __future__ import annotations

from datetime import date

# Syncing: when each kind of sync last ran (ISO timestamps), for the schedule and for Settings.
LAST_SYNC_OK = "last_sync_ok"                      # the last bank sync that worked
LAST_SYNC_WARNINGS = "last_sync_warnings"          # JSON: what the banks said on that sync (a login to renew, say)
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


def plaid_stmt_note(item_id: str) -> str:
    """Why a Plaid item's statements couldn't be fetched, if they couldn't."""
    return f"plaid_stmt_note:{item_id}"


# AI categorizing (OpenRouter)
OPENROUTER_API_KEY = "openrouter_api_key"
LLM_MODEL = "llm_model"
CARD_AI_MODEL = "card_ai_model"   # Churning's card lookups; unset means categorize.DEFAULT_CARD_MODEL
LAST_LLM_ERROR = "last_llm_error"
AUTO_AI_ON_SYNC = "auto_ai_on_sync"   # "1"/"0"; on unless switched off

# General preferences
PRIMARY_ACCOUNT = "primary_account"
HORIZON_DAYS = "horizon_days"
SETUP_DISMISSED = "setup_dismissed"
LAST_BACKUP = "last_backup"   # when a backup was last downloaded from Settings (ISO, the machine's local time)


# How each credit card's statements are paid, for the forecast (runway/forecast.py's payment_plan)
def card_pay_mode(card_id: str) -> str:
    """How the card is paid: "full" (the default: every statement paid in full), "minimum" or "fixed"."""
    return f"card_pay_mode:{card_id}"


def card_pay_amount(card_id: str) -> str:
    """With "fixed": what's paid toward each statement, in dollars."""
    return f"card_pay_amount:{card_id}"


def card_apr(card_id: str) -> str:
    """The card's APR, in percent, for the interest on a balance carried from one statement to the next."""
    return f"card_apr:{card_id}"


# Merchant names
BRAND_NAMES_OFF = "brand_names_off"   # JSON: brands whose transactions keep the bank's name (categorize.keep_bank_name)

# Recurring items
RECURRING_SUGGESTIONS_DISMISSED = "recurring_suggestions_dismissed"   # JSON: keys of suggestions marked "not recurring"

# Churning
CHURN_FOUND_DISMISSED = "churn_found_dismissed"   # JSON: ids of credit card accounts marked "not a churning card" (found on your accounts)
CHURN_AI_WEB = "churn_ai_web"   # "1"/"0": "Fill in the rest with AI" searches the web (runway/churn_found.py); on unless switched off

# Notifications
NOTIFY_PREFS = "notify_prefs"             # JSON: without sign-in, and where a person who hasn't chosen yet starts from


def notify_prefs(user_sub: str) -> str:
    """What one signed-in person wants to be told about (JSON, like NOTIFY_PREFS)."""
    return f"notify_prefs:{user_sub}"


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

# Retailer order import: the browser extension's key (runway/retail.py), who made it and when it was last used
RETAIL_TOKEN_HASH = "retail_token_hash"
RETAIL_TOKEN_CREATED = "retail_token_created"
RETAIL_TOKEN_OWNER = "retail_token_owner"       # JSON: {"sub", "email"} of the person who made it (nothing without sign-in)
RETAIL_TOKEN_USED = "retail_token_used"         # ISO timestamp of the last call that carried it
RETAIL_AI = "retail_ai"   # "1"/"0"; on unless switched off

# MCP (runway/mcp_access.py). ("mcp_token_hash" and "mcp_token_created", the old key's, were removed by migration 0024.)
MCP_ALLOW_WRITES = "mcp_allow_writes"   # "1": assistants allowed churning:write may make the changes in mcp_access.WRITABLE (off unless switched on)
MCP_ALLOW_CATEGORIZE = "mcp_allow_categorize"   # "1": assistants allowed categorize:write may make the changes in mcp_access.CATEGORIZABLE (off unless switched on)
MCP_ALLOW_ALL = "mcp_allow_all"   # "1": assistants allowed "write" may make any change mcp_access.writable_routes opens (off unless switched on)


def retail_last(retailer: str) -> str:
    return f"retail_last_{retailer}"


def retail_summary(retailer: str) -> str:
    return f"retail_summary_{retailer}"


# Rows that hold secrets, or what was read from a service you signed in to: stored encrypted (runway/secretbox.py),
# and encrypted in backups too (runway/backup.py).
SECRETS = frozenset({SIMPLEFIN_ACCESS_URL, PLAID_SECRET, OPENROUTER_API_KEY, REALIE_API_KEY, FINNHUB_API_KEY, LOGODEV_TOKEN, LOGODEV_SECRET,
                     VAPID_PRIVATE_KEY, CARTA_CLIENT_SECRET, CARTA_ACCESS_TOKEN, CARTA_REFRESH_TOKEN, PLAID_PENDING_LINK,
                     CARTA_WEB_CAPTURE})
