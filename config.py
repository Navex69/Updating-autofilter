"""
Central configuration. Everything is read from environment variables so the
bot never needs a code change to deploy — set these in Koyeb's dashboard
(or a local .env file for testing).

Only variables the bot actually uses are defined here. Keeping this file
short is deliberate: every extra setting is one more thing that can be
mis-configured on deploy.
"""
import os
import re

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_id_pattern = re.compile(r'^-?\d+$')


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on", "enable", "y")


def _int_list(name: str, default: str = "") -> list:
    raw = os.environ.get(name, default)
    out = []
    for item in raw.split():
        item = item.strip()
        if not item:
            continue
        out.append(int(item) if _id_pattern.match(item) else item)
    return out


# ── Telegram credentials ─────────────────────────────────────────────────────
API_ID = int(os.environ.get("API_ID", "29453152"))
API_HASH = os.environ.get("API_HASH", "2302adc174dbc954ae5081eda5131166")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# ── Admins (space-separated Telegram user IDs) ───────────────────────────────
ADMINS = _int_list("6541030917 1052054451")

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URI = os.environ.get("DATABASE_URI", "mongodb+srv://gd3251791_db_user:GDPQbmyXAEFDGpbL@cluster0.6jxsnxc.mongodb.net/?appName=Cluster0")
DATABASE_NAME = os.environ.get("DATABASE_NAME", "AutofilterBot")
COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "files")

# ── Source channels the bot indexes files from (space-separated IDs) ────────
# The bot must be an admin/member of every channel listed here.
CHANNELS = _int_list("CHANNELS")

# ── Search behaviour ──────────────────────────────────────────────────────────
# Search works in groups always. Private-chat search can be switched off here
# (e.g. to force people into a group) without touching any code.
ENABLE_PM_SEARCH = _bool("ENABLE_PM_SEARCH", True)
RESULTS_PER_PAGE = int(os.environ.get("RESULTS_PER_PAGE", "8"))
MIN_QUERY_LEN = int(os.environ.get("MIN_QUERY_LEN", "2"))

# ── Optional: channel the bot logs indexing activity to ─────────────────────
log_channel = os.environ.get("LOG_CHANNEL", "")
LOG_CHANNEL = int(log_channel) if log_channel and _id_pattern.match(log_channel) else None

# ── Web server (Koyeb requires the app to bind $PORT) ────────────────────────
PORT = int(os.environ.get("PORT", "8080"))

# ── Fail fast on missing essentials instead of crashing deep in pyrogram ────
_REQUIRED = {
    "API_ID": API_ID,
    "API_HASH": API_HASH,
    "BOT_TOKEN": BOT_TOKEN,
    "DATABASE_URI": DATABASE_URI,
}
_missing = [k for k, v in _REQUIRED.items() if not v]
if _missing:
    raise SystemExit(
        f"Missing required environment variable(s): {', '.join(_missing)}. "
        f"Set them in your environment (see .env.sample)."
    )
