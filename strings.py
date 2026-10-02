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
/req or /request — request a file if not found

<b>Admin commands</b>
/index — index an entire channel (auto + manual)
/stats — indexed file count
/settings — force-sub, premium, verification, result display
/add_premium, /remove_premium — manage premium users
/set_shortener, /set_verify_time, /set_tutorial — verification setup
"""

NOT_FOUND_TXT = "❌ No results found for <b>{query}</b>."

RESULT_HEADER_TXT = "🔎 Results for <b>{query}</b> — found <b>{total}</b>:"
RESULT_HEADER_CORRECTED_TXT = (
    "🔎 Results for <b>{query}</b> <i>(auto-corrected from \"{original}\")</i> — found <b>{total}</b>:"
)
POSTER_CAPTION_TXT = "🎬 <b>{query}</b>"

# ── progressive search-stage status message (edited in place — one stage
# replaces the previous line each time, never accumulated into a paragraph)
STATUS_STAGE1_TXT = "🔎 Searching {query}..."
STATUS_STAGE2_TXT = "🔁 Checking fuzzy match..."
STATUS_STAGE3_TXT = "🤖 Asking AI..."

# ── Stage 3 suggestion buttons (shown when nothing auto-resolves) ──────────
SUGGESTIONS_HEADER_TXT = "🤔 Couldn't find an exact match for <b>{query}</b>. Did you mean:"
SUGGESTION_NOT_FOUND_TXT = "❌ <b>{title}</b> isn't in the database yet."

SEARCH_EXPIRED_TXT = "⏳ This search has expired. Please search again."

# ── Result-page filters (season / language / episode / year / quality) ──────
FILTER_LABELS = {
    "season": "📅 Season",
    "language": "🌐 Language",
    "episode": "▶️ Episode",
    "year": "📆 Year",
    "quality": "🎞 Quality",
}
FILTER_MENU_TXT = "Choose a {label}:"
FILTER_CLEAR_BTN = "♻️ Any"
HOME_BTN = "🏠 Back to Home"
NO_MATCH_TXT = "😕 No files match the filters you picked."

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

# ── Movie Update Notification (settings panel) ────────────────────────────────
MOVIE_UPDATE_MENU_HEADER = (
    "🎬 <b>Movie Update Notifications</b>\n\n"
    "Status: {status}\n\n"
    "Automatically posts a formatted update to the update channel whenever a "
    "new file lands in one of the fetch channels."
)
MOVIE_UPDATE_FETCH_HEADER = "📥 <b>Fetch Channels</b>\n\nFiles posted in these channels trigger movie updates.\n\n"
MOVIE_UPDATE_FETCH_ROW = "{icon} {title}\n"
MOVIE_UPDATE_FETCH_EMPTY = "📥 <b>Fetch Channels</b>\n\nNo fetch channels configured."
MOVIE_UPDATE_FETCH_ADDED = "✅ Added <b>{title}</b> to fetch channels — I'm an admin there and will pick up new files."
MOVIE_UPDATE_FETCH_REMOVED = "✅ Removed from fetch channels."
MOVIE_UPDATE_FETCH_PROMPT = "Forward a message from the channel to watch for new files, or send its @username / -100 ID."
MOVIE_UPDATE_BOT_NOT_IN = "❌ I'm not a member of <b>{title}</b>. Add me to the channel as an <b>admin</b>, then try again."
MOVIE_UPDATE_BOT_NOT_ADMIN = "❌ I'm in <b>{title}</b> but not an admin. Make me an <b>admin</b> there (Telegram only sends channel posts to admin bots), then try again."
MOVIE_UPDATE_BOT_CHECK_FAILED = "❌ Couldn't verify my permissions in <b>{title}</b>: <code>{error}</code>"
MOVIE_UPDATE_FETCH_LEGEND = "\n⚠️ = I'm not an admin there, so I can't see new files. Fix it, then reopen this menu."
MOVIE_UPDATE_WARN_NO_POST = "\n\n⚠️ <b>I can't post in the update channel.</b> Make me an admin there with <i>Post Messages</i> permission."

EXPORT_CAPTIONS_PREPARING = "📄 Preparing export…"
EXPORT_CAPTIONS_CAPTION_TXT = "📄 Exported <b>{count}</b> file captions from the database."
EXPORT_CAPTIONS_EMPTY_TXT = "⚠️ No files are indexed yet — nothing to export."

BACKFILL_RUNNING = "🔁 Backfilling search index — this may take a moment…"
BACKFILL_DONE_TXT = (
    "✅ Backfill complete — updated <b>{count}</b> file(s).\n\n"
    "Files indexed before this update needed this one-time step so search "
    "can find them; anything indexed from now on gets it automatically."
)

# ── Request feature ─────────────────────────────────────────────────────────────
REQUEST_SENT_TXT = "✅ <b>Your request has been sent to admin!</b>\n\n📮 Requested: <code>{query}</code>"
REQUEST_RECEIVED_TXT = (
    "#FILE_REQUEST\n\n"
    "👤 User: {user_mention}\n"
    "🆔 ID: <code>{user_id}</code>\n"
    "🔍 Query: <code>{query}</code>\n\n"
    "Please check if this file is available and add it to the database."
)
REQUEST_NOT_CONFIGURED_TXT = "⚠️ Request feature is not configured by admin."
REQUEST_BTN_TXT = "📮 Request to Admin"
REQUEST_AUTO_TIMEOUT_TXT = "⏳ No interaction — auto-sending request to admin..."

# ── Admin response messages ─────────────────────────────────────────────────────
ALREADY_AVAILABLE_TXT = "📌 Requested – <code>{requested_name}</code>\n\nYour request is already available 😋, just re-send movie name in group."
NOT_RELEASED_TXT = "📌 Requested – <code>{requested_name}</code>\n\nSorry your request is not released yet 😢. Admin keep monitor your requests, wait for release and then send requested file name in group."
NOT_AVAILABLE_TXT = "❌ Your requested movie is not available on the internet.\n\n📌 Requested – <code>{requested_name}</code>"
UPLOADED_TXT = "Your request is uploaded ☺️, just re-send movie name in group"
CHECK_SPELLING_TXT = "📌 Requested – <code>{requested_name}</code>\n\nAdmin can't find any movie and series of this name. Make sure, your spelling is correct ⚠️. Check spelling on google and then request again ❗"
YEAR_LANGUAGE_TXT = "📌 Requested – <code>{requested_name}</code>\n\nBro please tell me years, language, bollywood or hollywood etc., then I will upload 😬. Just re-send request with more info."
WRONG_SPELLING_TXT = "✏️ Admin provided correct spelling: <code>{correct_spelling}</code>\n\nPlease request again with correct spelling."
CUSTOM_REPLY_TXT = "💬 Admin replied to your request:\n\n{custom_message}"

# ── Movie Update Notification ───────────────────────────────────────────────
MOVIE_UPDATE_NOTIFY_TXT = """<b>{tag} ➤ {filename}</b>

<blockquote>🎭 ɢᴇɴʀᴇs     : <b>{genres}</b>
📺 ᴏᴛᴛ          : <b>{ott}</b>
🎞️ ǫᴜᴀʟɪᴛʏ  : <b>{quality}</b>
📐 ʀᴇsᴏʟᴜᴛɪᴏɴ : <b>{resolution}</b>
🎧 ᴀᴜᴅɪᴏ       : <b>{language}</b>
🔥 ʀᴀᴛɪɴɢ      : <b>{rating} ⭐</b>
{episodes}</blockquote>"""

MANUAL_UPDATE_NOTIFY_TXT = """<b>{tag} ➤ {filename}</b>

<blockquote>🎭 ɢᴇɴʀᴇs     : <b>{genres}</b>
🎞️ ǫᴜᴀʟɪᴛʏ  : <b>{quality}</b>
📐 ʀᴇsᴏʟᴜᴛɪᴏɴ : <b>{resolution}</b>
🎧 ᴀᴜᴅɪᴏ       : <b>{language}</b>
🔥 ʀᴀᴛɪɴɢ      : <b>{rating} ⭐</b>
{episodes}</blockquote>"""

# ── restart notice (admin DM) and log-channel messages ──────────────────────
RESTART_TXT = """🔄 <b>Bot Restarted</b>

🤖 @{username} is back online.
🕐 <b>Time:</b> {time}"""

NEW_USER_LOG_TXT = """#NewUser
👤 <b>New user started the bot</b>

<b>Name:</b> {mention}
<b>ID:</b> <code>{user_id}</code>
<b>Username:</b> {username}
<b>Total users:</b> {total}
<b>Time:</b> {time}
<b>Bot:</b> @{bot}"""

USER_VERIFIED_LOG_TXT = """#UserVerified
✅ <b>User verified</b> — {ordinal} verification ({tier}/3)

<b>Name:</b> {mention}
<b>ID:</b> <code>{user_id}</code>
<b>Username:</b> {username}
<b>Time:</b> {time}"""

# ── Fast Download ─────────────────────────────────────────────────────────────
FAST_DOWNLOAD_BTN = "⚡ Fast Download"
FAST_DOWNLOAD_LINK_BTN = "⬇️ Download"
FAST_LINK_READY_TXT = (
    "✅ Your link is ready!\n\n"
    "⏳ Valid for {hours} hours, then it expires.\n"
    "🔒 Personal link — please don't share it.\n\n"
    "Tap Download or Watch below."
)
FAST_LIMIT_REACHED_TXT = "⚠️ You've used all {limit} fast-download links for today. Try again tomorrow."
FAST_UNAVAILABLE_TXT = "⚠️ Fast download is busy right now. Please use the file above."
FAST_ERROR_TXT = "❌ Couldn't create the link. Please try again in a moment."
FAST_WATCH_BTN = "▶️ Watch"

# Posted in BIN_CHANNEL (as a reply to the file) every time a link is generated.
BIN_USER_INFO_TXT = """📥 <b>Fast download link generated</b>

📁 <b>File:</b> <code>{file_name}</code>
🆔 <b>User ID:</b> <code>{user_id}</code>
👤 <b>User:</b> {user_link}"""
