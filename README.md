# Autofilter Bot

A Telegram autofilter bot: index files from your channels, let people search
for them by name in a group (or PM), tap a result, get the file in their DM
— gated by force-subscribe, verification, and premium as you configure them.

## Features

- **Indexing** — auto (every file posted to a source channel is indexed the
  moment it lands) and manual (`/index`, backfills an entire channel's
  history). Both save `file_name` and `caption`.
- **Search** — type a query in a group (or PM, if enabled) and get back a
  paginated list, as buttons or plain text depending on admin settings. No
  match → a clear "not found" message.
- **File delivery** — tapping a result opens a deep link to the bot's PM,
  which runs every gate (premium → force-sub → verification) and sends the
  file. Groups stay clean; no file spam in chat.
- **Force-subscribe** — require membership in one or more channels before a
  file is delivered. Manage channels entirely from `/settings`.
- **Premium** — grant users a bypass for force-sub and verification for a
  fixed duration. `/add_premium`, `/remove_premium`, `/myplan`.
- **Verification** — three-tier shortener-link verification (same behaviour
  as the reference bot: tier 1 once a day, tiers 2/3 kick in the more often
  someone requests files within that day). Configured via `/set_shortener`,
  `/set_verify_time`, `/set_tutorial`.
- **`/settings`** — one panel to flip any of the above on/off, inspect its
  detail (channel list / premium users / shortener config), and switch
  search results between button and text display.

## Project layout

```
config.py              all env-driven settings
strings.py               every user-facing message, in one place
utils.py                  small shared helpers (temp state, formatting, IST/UTC)
shortlink.py                Shortzy wrapper used by verification
bot.py                       entrypoint — starts pyrogram + the health-check server
database/
  client.py                  the one shared Motor client every collection uses
  filters_db.py                indexed files: save + search
  settings_db.py                 global toggles (force-sub/premium/verify/result mode), cached in memory
  premium_db.py                    premium users + expiry
  verify_db.py                       3-tier verification state + short-lived tokens
plugins/
  start.py                     /start (dispatches file_/notcopy_ deep links), /help, /stats, /myplan
  index.py                       auto-index + manual /index
  search.py                        query handling, pagination, result cache, button/text rendering
  force_sub.py                       membership checks + admin-permission probe for a channel
  deliver.py                           the one path every "send this file" route goes through
  verify.py                              completes a verification step from its deep link
  premium.py                               /add_premium, /remove_premium
  settings.py                                /settings panel + verification config commands
web/
  __init__.py                                  aiohttp app — just a health-check route
```

Each concern lives in exactly one file. `deliver.py` is the one place that
sequences premium → force-sub → verification → send, so every entry point
(the initial deep link, the fsub "Try Again" button, the post-verification
button) goes through identical logic — there's no way for one of them to
accidentally skip a gate the others enforce.

## `/settings` panel

Each row has two buttons: the left one shows and toggles that feature's
on/off state (or button/text for result display); the right one opens that
feature's detail view.

- **Force-Subscribe** → list of channels (tap one to remove it), plus
  "Add New Channel". Adding forwards a message from the channel (or its
  @username/-100 ID) and the bot verifies it actually has admin rights
  there (via a permission probe) before accepting it.
- **Premium** → list of active premium users with their expiry.
- **Verification** → each tier's shortener domain (API key masked),
  configured tutorial link, and the two escalation gaps.
- **Result Format** → toggle button/text search results directly; the name
  button explains the setting.

## Admin commands

```
/index                              index an entire channel (auto + manual)
/stats                               indexed file count
/settings                             the panel described above
/add_premium <user_id> <duration>       e.g. /add_premium 123456 1month
/remove_premium <user_id>
/set_shortener <tier 1|2|3> <domain> <api_key>
/set_verify_time <1|2> <seconds>          1 = tier1→tier2 gap, 2 = tier2→tier3 gap
/set_tutorial <tier 1|2|3> <url>
```

`/myplan` is available to everyone, to check their own premium status.

## Why it's built this way (performance on Koyeb's free plan)

Koyeb's free tier gives you a small, shared amount of CPU and RAM, so the
things that matter most are: don't block the event loop, don't scan the
whole database on every search, and don't add a per-request database cost
for features most users never trigger.

- **All-async database layer, one shared connection pool.** Every
  `database/*.py` module gets its collection from `database/client.py`'s
  single Motor client instead of opening its own — fewer connections
  against Mongo Atlas's free-tier connection cap, no blocking pymongo calls
  anywhere.
- **Indexed search**, unaffected by the new features. A MongoDB text index
  (weighted: filename > caption) backs every search; a capped regex scan is
  only a fallback for zero-hit queries. Search performance is identical to
  before force-sub/premium/verification existed.
- **Settings are read from an in-memory cache**, not the database, on every
  search and every file request. The cache only refreshes when an admin
  actually changes something in `/settings` — a user searching or
  requesting a file never causes a settings DB read.
- **Force-sub and verification only run at file-delivery time**, never
  during search — so search stays exactly as fast as it was without these
  features, and the (unavoidable) Telegram API calls for membership checks
  only happen once per file request, not once per search result.
- **Duplicate-safe indexing** and **single-use, TTL-expiring verification
  tokens** (Mongo auto-deletes them after 30 minutes — no cleanup job to
  run yourself).

## Setup

1. Copy `.env.sample` to `.env` and fill it in (or set the same variables
   in Koyeb's dashboard). Force-sub channels, premium, and verification
   shorteners are *not* environment variables — configure those from
   `/settings` and the commands above after the bot is running.
2. Add the bot as an admin to every channel listed in `CHANNELS` (for
   indexing) and to any channel you plan to use for force-subscribe (it
   needs invite-link permission there — `/settings` checks this for you
   when you add one).
3. Deploy. Koyeb picks up the `Dockerfile` automatically; `Procfile` is
   there too if you use a platform that reads it instead.
4. In any group the bot is in, send a movie/show name to test search. In
   PM, run `/index` (as an admin) to backfill a channel, then `/settings`
   to turn on whichever of force-sub/premium/verification you want.

## Extending it

Want more features later — broadcast, TMDB posters, a web UI? Add a new
file under `plugins/`, or a new module under `database/` if it needs its
own collection. Keep it out of `filters_db.py`, `index.py`, `search.py`,
and `deliver.py` unless it's genuinely part of indexing, searching, or the
delivery pipeline — that separation is what keeps the core fast as the bot
grows.
