START_TXT = """👋 <b>Hi {mention}!</b>

I'm an autofilter bot — add me to a group, index a channel's files into me, \
and members can just type a movie/show name to search.

<b>Commands:</b>
/help — how search works
"""

HELP_TXT = """<b>How to search</b>
Just type a name in the group, e.g. <code>Inception 2010</code>.
I'll show matching files as buttons — tap one and I'll send it to you here in PM.

<b>Admin commands</b>
/index — index an entire channel (auto + manual)
/stats — indexed file count
"""

NOT_FOUND_TXT = "❌ No results found for <b>{query}</b>."

RESULT_HEADER_TXT = "🔎 Results for <b>{query}</b> — found <b>{total}</b>:"

SEARCH_EXPIRED_TXT = "⏳ This search has expired. Please search again."

FILE_SEND_CAPTION = "<code>{file_name}</code>"

FILE_NOT_FOUND_TXT = "❌ That file is no longer available."
