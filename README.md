# Simple Autofilter Bot

A stripped-down Telegram autofilter bot: index files from your channels, let
people search for them by name in a group (or PM), tap a result, get the
file in their DM. Nothing else.

## Features

- **Indexing** — auto (every file posted to a source channel is indexed the
  moment it lands) and manual (`/index`, backfills an entire channel's
  history). Both save `file_name` and `caption`.
- **Search** — type a query in a group (or PM, if enabled) and get back a
  paginated button list. No match → a clear "not found" message.
- **File delivery** — tapping a result opens a deep link to the bot's PM,
  which sends the file. Groups stay clean; no file spam in chat.

That's it. No force-subscribe, verification links, premium plans,
broadcast, TMDB posters, or web frontend — this is meant to be a clean base
you extend, not a finished product with everything switched on.

## Project layout

```
config.py            all settings, read from environment variables
strings.py            every user-facing message, in one place
utils.py               small shared helpers (temp state, formatting)
bot.py                  entrypoint — starts pyrogram + the health-check server
database/
  filters_db.py          the only file that touches MongoDB
plugins/
  start.py                 /start (incl. deep-link file delivery), /help, /stats
  index.py                  auto-index + manual /index
  search.py                 query handling, pagination, result cache
web/
  __init__.py               aiohttp app — just a health-check route
```

Each concern lives in exactly one file. Adding a feature later (e.g. a
`/broadcast` command, or force-subscribe) means adding a new file under
`plugins/` — you won't need to touch indexing or search to do it, and
indexing/search won't get slower because of it.

## Why it's built this way (performance on Koyeb's free plan)

Koyeb's free tier gives you a small, shared amount of CPU and RAM, so the
things that matter most are: don't block the event loop, and don't scan the
whole database on every search.

- **All-async database layer.** `database/filters_db.py` uses `motor`
  exclusively — no synchronous `pymongo` calls wrapped in thread pools. One
  fewer moving part, no thread-pool contention under load.
- **Indexed search.** A MongoDB text index (weighted: filename > caption)
  backs every search, so query time doesn't grow linearly with your
  collection size. A plain regex scan only runs as a fallback when the text
  index finds nothing (e.g. a partial word), and it's capped with a `limit`
  so a bad query can't scan the whole collection.
  MongoDB Atlas's free M0 tier supports this out of the box.
- **One query per search, not one per page.** Pagination is served from an
  in-memory cache keyed by the query, so tapping "Next" costs zero database
  calls.
- **Duplicate-safe indexing.** `file_unique_id` has a unique index, so
  auto-index and a manual `/index` backfill can never double-save the same
  file — duplicates become a cheap no-op instead of dead weight in your
  collection (and in every search result).
- **Small dependency footprint.** `requirements.txt` only has what the
  three features above need. Fewer packages means a smaller image and a
  faster cold start — which matters on a free plan where your instance may
  sleep and restart.

## Setup

1. Copy `.env.sample` to `.env` and fill it in (or set the same variables
   in Koyeb's dashboard).
2. Add the bot as an admin to every channel listed in `CHANNELS` — it needs
   to read new posts for auto-index, and message history for `/index`.
3. Deploy. Koyeb picks up the `Dockerfile` automatically; `Procfile` is
   there too if you use a platform that reads it instead.
4. In any group the bot is in, send a movie/show name to test search. In
   PM, run `/index` (as an admin) to backfill a channel.

## Extending it

Want more features later — force-subscribe, a web UI, TMDB posters,
broadcast? Add a new file under `plugins/`, or a new module under
`database/` if it needs its own collection. Keep it out of
`filters_db.py`, `index.py`, and `search.py` unless it's genuinely part of
indexing or searching — that separation is what keeps the core fast as the
bot grows.
