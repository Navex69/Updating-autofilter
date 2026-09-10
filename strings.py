START_TXT = """👋 <b>Hi {mention}!</b>

I'm an autofilter bot — add me to a group, index a channel's files into me, \
and members can just type a movie/show name to search.

<b>Commands:</b>
/help — how search works
/myplan — check your premium status
"""

HELP_TXT = """<b>How to search</b>
Just type a name in the group, e.g. <code>Inception 2010</code>.
I'll show matching files as buttons (or a text list, depending on admin \
settings) — tap/open one and I'll send it to you here in PM.

<b>Everyone</b>
/myplan — check your premium status

<b>Admin commands</b>
/index — index an entire channel (auto + manual)
/stats — indexed file count
/settings — force-sub, premium, verification, result display
/add_premium, /remove_premium — manage premium users
/set_shortener, /set_verify_time, /set_tutorial — verification setup
"""

NOT_FOUND_TXT = "❌ No results found for <b>{query}</b>."

RESULT_HEADER_TXT = "🔎 Results for <b>{query}</b> — found <b>{total}</b>:"

SEARCH_EXPIRED_TXT = "⏳ This search has expired. Please search again."

FILE_SEND_CAPTION = "<code>{file_name}</code>"
FILE_SEND_CAPTION_WITH_LIMIT = "<code>{file_name}</code>\n\n📊 Free file {used}/{limit} for today."

FILE_NOT_FOUND_TXT = "❌ That file is no longer available."

# ── Auto-delete ────────────────────────────────────────────────────────────────
QUERY_AUTODELETE_NOTE = "\n\n⏳ <i>This message will self-destruct in {seconds}s.</i>"
FILE_AUTODELETE_NOTICE = "🗑 This file will be deleted in <b>{seconds}</b> seconds to avoid copyright issues. Forward or save it now."
FILE_AUTODELETE_DONE = "🗑 File deleted."

# ── Force-subscribe ──────────────────────────────────────────────────────────
FSUB_REQUIRED_TXT = (
    "👋 <b>Hey {mention}!</b>\n\n"
    "🛑 You must join the channel(s) below before I can send you this file.\n"
    "👉 Join all of them, then tap <b>Try Again</b>."
)
TRY_AGAIN_BTN = "🔄 Try Again"

# ── Verification ──────────────────────────────────────────────────────────────
VERIFY_PROMPT_TXT = (
    "👋 <b>Hey {mention}!</b>\n\n"
    "📌 You need to complete verification (step {tier}/3) before I can send this file.\n"
    "Tap <b>Verify</b>, follow the page, then come back — I'll send the file automatically."
)
VERIFY_BTN = "♻️ Verify"
VERIFY_TUTORIAL_BTN = "❓ How to verify"
VERIFY_DONE_TXT = "✅ Verification complete! Tap below to get your file."
VERIFY_GET_FILE_BTN = "📥 Get my file"
VERIFY_EXPIRED_TXT = "⚠️ This verification link has expired or was already used. Please request the file again."

# ── Premium ────────────────────────────────────────────────────────────────────
MYPLAN_ACTIVE_TXT = "💎 <b>You have an active premium plan.</b>\n\n⌛ Expires: <code>{expiry}</code>"
MYPLAN_NONE_TXT = "You don't have an active premium plan."
PREMIUM_ADDED_TXT = "✅ Premium granted to <code>{user_id}</code> until <code>{expiry}</code>."
PREMIUM_REMOVED_TXT = "✅ Premium removed from <code>{user_id}</code>."
PREMIUM_NOT_FOUND_TXT = "That user doesn't have an active premium plan."
PREMIUM_USAGE_TXT = "Usage: <code>/add_premium user_id 1month</code>\n\nDuration examples: <code>1day</code>, <code>2hours</code>, <code>1month</code>, <code>1year</code>."

# ── /settings panel ────────────────────────────────────────────────────────────
SETTINGS_MAIN_TXT = "⚙️ <b>Bot Settings</b>\n\nTap a name to see its details, or the status button to toggle it."

FSUB_MENU_HEADER = "📢 <b>Force-Subscribe Channels</b>\n\nTap a channel to remove it."
FSUB_MENU_EMPTY = "📢 <b>Force-Subscribe Channels</b>\n\nNo channels added yet."
FSUB_ADD_PROMPT = "Forward a message from the channel, or send its @username / -100 ID."
FSUB_ADD_NOT_CHANNEL = "That's not a channel."
FSUB_ADD_NOT_ADMIN = "I need to be an admin there (with permission to invite users) before I can enforce this."
FSUB_ADD_OK = "✅ Added <b>{title}</b> to force-subscribe."
FSUB_ADD_FAILED = "Couldn't resolve that channel: <code>{error}</code>"
FSUB_REMOVED_TXT = "✅ Removed from force-subscribe."

PREMIUM_MENU_EMPTY = "💎 <b>Premium Users</b>\n\nNo active premium users."
PREMIUM_MENU_ROW = "👤 <code>{user_id}</code> — expires <code>{expiry}</code>\n"

VERIFY_MENU_HEADER = "🔗 <b>Verification Shorteners</b>\n\n{body}\n\nUse /set_shortener, /set_verify_time and /set_tutorial to change these."
VERIFY_TIER_ROW = (
    "<b>Tier {tier}</b>\n"
    "Domain: <code>{domain}</code>\n"
    "API key: <code>{api}</code>\n"
    "Tutorial: {tutorial}\n"
)
SET_SHORTENER_USAGE = "Usage: <code>/set_shortener tier domain api_key</code>\n\nExample: <code>/set_shortener 1 shortner.in abc123</code>"
SET_SHORTENER_OK = "✅ Tier {tier} shortener saved.\nDemo link: {demo}"
SET_SHORTENER_WARN = "⚠️ Saved, but I couldn't generate a test link — double check the domain and API key."
SET_VERIFY_TIME_USAGE = "Usage: <code>/set_verify_time gap seconds</code>\n\n<code>gap</code> is <code>1</code> (tier1→tier2) or <code>2</code> (tier2→tier3)."
SET_VERIFY_TIME_OK = "✅ Gap {gap} set to <code>{seconds}</code> seconds."
SET_TUTORIAL_USAGE = "Usage: <code>/set_tutorial tier url</code>"
SET_TUTORIAL_OK = "✅ Tier {tier} tutorial link saved."

INDEX_MENU_HEADER = "📚 <b>Indexed Channels</b>\n\nTap a channel to remove it from auto-indexing."
INDEX_MENU_EMPTY = "📚 <b>Indexed Channels</b>\n\nNo channels added yet."
INDEX_MENU_ROW = "🗑 {title} — {count} files"
INDEX_ADD_PROMPT = "Forward a message from the channel, or send its @username / -100 ID."
INDEX_ADD_NOT_CHANNEL = "That's not a channel."
INDEX_ADD_NOT_ADMIN = "I need to be an admin there so I can reliably receive its posts."
INDEX_ADD_OK = "✅ Added <b>{title}</b> for auto-indexing."
INDEX_ADD_FAILED = "Couldn't resolve that channel: <code>{error}</code>"
INDEX_REMOVED_TXT = "✅ Removed from auto-indexing."

ASK_NUMBER_TIMEOUT = "⌛ Timed out — no changes made."
ASK_NUMBER_INVALID = "That's not a valid number — no changes made."

ASK_QUERY_DELAY_PROMPT = "Send how many seconds a search-result message should stay before I delete it."
QUERY_DELAY_SET_TXT = "✅ Search results will now self-delete after <code>{seconds}</code> seconds."

ASK_FILE_DELAY_PROMPT = "Send how many seconds a delivered file should stay before I delete it."
FILE_DELAY_SET_TXT = "✅ Delivered files will now self-delete after <code>{seconds}</code> seconds."

ASK_FILE_LIMIT_PROMPT = "Send how many free files a non-premium user can get per day (e.g. <code>2</code>)."
FILE_LIMIT_SET_TXT = "✅ Free daily file limit set to <code>{count}</code>."
FILE_LIMIT_NEEDS_VERIFY_NOTE = "\n\n⚠️ File limit only takes effect while <b>Verification</b> is also on — it's currently off, so this has no effect yet."
