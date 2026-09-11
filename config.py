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
ADMINS = _int_list("ADMINS", "6541030917 1052054451")

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URI = os.environ.get("DATABASE_URI", "mongodb+srv://gd3251791_db_user:GDPQbmyXAEFDGpbL@cluster0.6jxsnxc.mongodb.net/?appName=Cluster0")
DATABASE_NAME = os.environ.get("DATABASE_NAME", "AutofilterBot")
COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "files")

# ── Source channels the bot indexes files from (space-separated IDs) ────────
# The bot must be an admin/member of every channel listed here.
CHANNELS = _int_list("CHANNELS", "-1002407564854")

# ── Search behaviour ──────────────────────────────────────────────────────────
# Search works in groups always. Private-chat search can be switched off here
# (e.g. to force people into a group) without touching any code.
ENABLE_PM_SEARCH = _bool("ENABLE_PM_SEARCH", True)
RESULTS_PER_PAGE = int(os.environ.get("RESULTS_PER_PAGE", "8"))
MIN_QUERY_LEN = int(os.environ.get("MIN_QUERY_LEN", "1"))

# ── Automatic poster fetch (TMDB primary, OMDb fallback) ───────────────────
# Both optional — leave unset to disable poster fetching entirely. Get a free
# key at https://www.themoviedb.org/settings/api and https://www.omdbapi.com/
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "e166a67b9b21ee3bd84bc189d567057b")
OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "436aac9c")
POSTER_FETCH_TIMEOUT = int(os.environ.get("POSTER_FETCH_TIMEOUT", "6"))

# ── Optional: channel the bot logs indexing activity to ─────────────────────
log_channel = os.environ.get("LOG_CHANNEL", "-1003073036876")
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
