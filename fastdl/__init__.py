"""
Fast Download — streams files out of Telegram over HTTP so users can download
them in a browser / download manager at full speed, with resume support.

Layout:
  tokens.py  signed, expiring link tokens (no database needed to verify)
  pool.py    Telegram client pool + parallel chunk fetching
  server.py  aiohttp routes (/dl/<token>/<name>, /health) with Range support
  hosts.py   picks Oracle / this deployment, tracks monthly bandwidth
  links.py   button + feature-flag helpers used by the bot side

Everything here is dormant unless the matching env variables are set
(see FASTDL_SETUP.md) — with none of them set, the bot behaves exactly as before.
"""
