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
DATABASE_URI = os.environ.get("DATABASE_URI", "mongodb+srv://autofilter:filter@cluster0.iitbepl.mongodb.net/?appName=Cluster0")
DATABASE_NAME = os.environ.get("DATABASE_NAME", "Cluster0")
COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "navex")

# ── Source channels the bot indexes files from (space-separated IDs) ────────
# The bot must be an admin/member of every channel listed here.
CHANNELS = _int_list("CHANNELS", "-1002066489726 -1002445793312 -1002407564854 -1002467109334 -1003941255241")

# ── Search behaviour ──────────────────────────────────────────────────────────
# Search works in groups always. Private-chat search can be switched off here
# (e.g. to force people into a group) without touching any code.
ENABLE_PM_SEARCH = _bool("ENABLE_PM_SEARCH", True)
RESULTS_PER_PAGE = int(os.environ.get("RESULTS_PER_PAGE", "8"))

# ── Automatic poster fetch (TMDB primary, OMDb fallback) ───────────────────
# Both optional — leave unset to disable poster fetching entirely. Get a free
# key at https://www.themoviedb.org/settings/api and https://www.omdbapi.com/
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "e166a67b9b21ee3bd84bc189d567057b")
OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "436aac9c")
POSTER_FETCH_TIMEOUT = int(os.environ.get("POSTER_FETCH_TIMEOUT", "6"))

# ── Typo-correction fallback (only runs when exact search finds nothing) ───
# Stage 2 (fuzzy match against your own DB) needs no setup and no keys.
# Stage 3 (AI) only activates for whichever key(s) you actually set — leave
# both empty to run fuzzy-match-only, set one or both to race them.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "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")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6IkIRyZr_PlKTFG8KLhxpArWz3yOFlJpCNqpc3fwlpuNg")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
AI_FETCH_TIMEOUT = int(os.environ.get("AI_FETCH_TIMEOUT", "6"))
FUZZY_MATCH_THRESHOLD = int(os.environ.get("FUZZY_MATCH_THRESHOLD", "82"))

# ── Optional: channel the bot logs indexing activity to ─────────────────────
log_channel = os.environ.get("LOG_CHANNEL", "-1003073036876")
LOG_CHANNEL = int(log_channel) if log_channel and _id_pattern.match(log_channel) else None

# ── Send a "bot restarted" DM to every admin on each boot ──────────────────
# Admins who never started the bot are skipped automatically.
RESTART_NOTIFY = _bool("RESTART_NOTIFY", True)

# ── Request channel for user file requests ─────────────────────────────────
request_channel = os.environ.get("REQUEST_CHANNEL", "-1003073036876")
REQUEST_CHANNEL = int(request_channel) if request_channel and _id_pattern.match(request_channel) else None

# ── File not found channel for timeout notifications ───────────────────────
not_found_channel = os.environ.get("NOT_FOUND_FILE_CHANNEL", "-1002279624678")
NOT_FOUND_FILE_CHANNEL = int(not_found_channel) if not_found_channel and _id_pattern.match(not_found_channel) else None

# ── Auto-request timeout settings ─────────────────────────────────────────
SUGGESTION_TIMEOUT = int(os.environ.get("SUGGESTION_TIMEOUT", "120"))  # seconds

# ── Movie Update Notification Settings ───────────────────────────────────
MOVIE_UPDATE_NOTIFICATION = _bool("MOVIE_UPDATE_NOTIFICATION", True)  # Notification On/Off
movie_update_channel = os.environ.get("MOVIE_UPDATE_CHANNEL", "-1002333962739")
MOVIE_UPDATE_CHANNEL = int(movie_update_channel) if movie_update_channel and _id_pattern.match(movie_update_channel) else None
fetch_update_channels = os.environ.get("FETCH_MOVIE_UPDATE", "-1002028282135")  # Movie Update Fetch Channels (space-separated)
FETCH_MOVIE_UPDATE = int(fetch_update_channels) if fetch_update_channels and _id_pattern.match(fetch_update_channels) else None

# ── Movie Update Display Settings ─────────────────────────────────────────
LINK_PREVIEW = _bool("LINK_PREVIEW", False)  # Shows link preview instead of image
ABOVE_PREVIEW = _bool("ABOVE_PREVIEW", True)  # Shows link preview above text if True
TMDB_POSTER = _bool("TMDB_POSTER", True)  # Shows TMDB poster in notification
LANDSCAPE_POSTER = _bool("LANDSCAPE_POSTER", True)  # Shows landscape poster

# ── Web server (Koyeb requires the app to bind $PORT) ────────────────────────
PORT = int(os.environ.get("PORT", "8080"))

# ── Fast Download (optional — fully disabled until configured) ───────────────
# See FASTDL_SETUP.md. With BIN_CHANNEL / STREAM_SECRET / a host URL unset, the
# bot behaves exactly as before: no button, no routes, no extra connections.
STREAM_ONLY = _bool("STREAM_ONLY", False)  # True on the Oracle server (no bot, just streaming)
bin_channel = os.environ.get("BIN_CHANNEL", "-1002262450769")
BIN_CHANNEL = int(bin_channel) if bin_channel and _id_pattern.match(bin_channel) else None
STREAM_SECRET = os.environ.get("STREAM_SECRET", "BQHBa2AAB2Gkf7fVzKe7laAj3-sVJdoVgs7kdqElm_ivE4bUGIML4SNioZOtM_oBIk-Gal_oszjfAT7QIumIVsCMXVuyD0Gh29p1204DwCQ03-H28cieNGmi7-q75p0LETReT3xm54yhXKu1lfcpwu5eNMs9YeI9uPD2yeplb1ma3HyEFnTgJLSGXSR6Ww2EcNvVvum25FElPQlQ___oEdfTMygfTOmILhxkk3ehTTg1a0TrbfdGooam7-1eggRmFHw4kOQbjWRIvvVegOwlt-PZEfHYBviqr0KQftEAjSJ2pS6kvVM5qioOyGbSK8iIKraNBRp6SWv9JZpkDxyRagtMhQbtaAAAAAHGKGRSAA").strip()   # must be identical on every host
HELPER_BOT_TOKENS = os.environ.get("HELPER_BOT_TOKENS", "").split()  # extra bots = more speed

# Public URL of THIS deployment (Koyeb/Render), e.g. https://my-bot.koyeb.app
STREAM_BASE_URL = os.environ.get("STREAM_BASE_URL", "https://precious-mab-filetokensender-fdc78bed.koyeb.app/").strip().rstrip("/")
STREAM_BANDWIDTH_LIMIT_GB = float(os.environ.get("STREAM_BANDWIDTH_LIMIT_GB", "100"))

# Oracle Always-Free stream server — leave ORACLE_STREAM_URL empty to keep it disabled.
ORACLE_STREAM_URL = os.environ.get("ORACLE_STREAM_URL", "").strip().rstrip("/")
ORACLE_BANDWIDTH_LIMIT_GB = float(os.environ.get("ORACLE_BANDWIDTH_LIMIT_GB", "10000"))

# Move new links to the next host once a host passes this % of its monthly limit.
STREAM_SWITCH_PERCENT = int(os.environ.get("STREAM_SWITCH_PERCENT", "85"))
STREAM_LINK_TTL_HOURS = int(os.environ.get("STREAM_LINK_TTL_HOURS", "6"))
STREAM_DAILY_LIMIT = int(os.environ.get("STREAM_DAILY_LIMIT", "5"))
STREAM_PREMIUM_DAILY_LIMIT = int(os.environ.get("STREAM_PREMIUM_DAILY_LIMIT", "20"))

# Server tuning. Defaults are safe for a 512 MB free instance; raise on Oracle.
STREAM_PREFETCH = int(os.environ.get("STREAM_PREFETCH", "4"))            # chunks in flight per download
STREAM_MAX_CONNECTIONS = int(os.environ.get("STREAM_MAX_CONNECTIONS", "12"))
STREAM_MAX_PER_LINK = int(os.environ.get("STREAM_MAX_PER_LINK", "8"))    # download managers use several

# Bot-side feature flag: the button only appears when everything needed is set.
FASTDL_ENABLED = bool(BIN_CHANNEL and STREAM_SECRET and (STREAM_BASE_URL or ORACLE_STREAM_URL))
# Server-side flag: this process can serve /dl/ links.
FASTDL_SERVER_ENABLED = bool(BIN_CHANNEL and STREAM_SECRET)

# ── Fail fast on missing essentials instead of crashing deep in pyrogram ────
_REQUIRED = {
    "API_ID": API_ID,
    "API_HASH": API_HASH,
    "DATABASE_URI": DATABASE_URI,
}
if not STREAM_ONLY:  # the Oracle stream server runs without the main bot token
    _REQUIRED["BOT_TOKEN"] = BOT_TOKEN
_missing = [k for k, v in _REQUIRED.items() if not v]
if _missing:
    raise SystemExit(
        f"Missing required environment variable(s): {', '.join(_missing)}. "
        f"Set them in your environment (see .env.sample)."
    )
