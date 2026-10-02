# Fast Download — setup

Adds a **⚡ Fast Download** button under every delivered file. Tapping it swaps
the button for a **⬇️ Download** link (signed, expires in 6 h) that streams the
file straight from Telegram with resume support. Nothing is stored on the
server. With the variables below unset, the bot behaves exactly as before.

## 1. Minimum setup (Koyeb / Render only)
> No buttons until all three of BIN_CHANNEL, STREAM_SECRET and STREAM_BASE_URL are set (on Render: Dashboard → Environment).
1. Create a **private channel** (BIN_CHANNEL) and make your bot an **admin**.
2. Set on your deployment:
   - `BIN_CHANNEL` = the channel ID (e.g. `-100123...`)
   - `STREAM_SECRET` = any long random string
   - `STREAM_BASE_URL` = your public URL, e.g. `https://my-bot.koyeb.app`
3. Redeploy. Done — the button appears on delivered files.

**More speed:** create 2–4 extra bots in @BotFather, make each an **admin of
BIN_CHANNEL**, and set `HELPER_BOT_TOKENS="tok1 tok2 tok3"`.

## 2. Oracle server (optional, recommended for 2–3 GB files)
Oracle Always Free gives ~10 TB/month outbound (verify current terms).
Only the stream server runs there; your bot stays on Koyeb/Render.

1. Create an Always-Free **Ampere A1** VM (Ubuntu). Open ports 80 and 443 in
   the VM's security list **and** in its firewall.
2. Get a free domain (e.g. DuckDNS) pointing to the VM's IP. Telegram buttons
   need an `https://` domain, not a raw IP.
3. Install Docker + Caddy (Caddy gives free automatic HTTPS):
   `Caddyfile` → `your-name.duckdns.org { reverse_proxy localhost:8080 }`
4. Clone this repo on the VM, build and run:
   ```
   docker build -t fastdl .
   docker run -d --restart always -p 8080:8080 --env-file stream.env fastdl python3 stream_server.py
   ```
   `stream.env`:
   ```
   API_ID=...
   API_HASH=...
   BIN_CHANNEL=...            # same as the bot
   STREAM_SECRET=...          # same as the bot
   HELPER_BOT_TOKENS=tok1 tok2   # admins of BIN_CHANNEL; do NOT reuse the main bot token only
   PORT=8080
   STREAM_PREFETCH=8
   STREAM_MAX_CONNECTIONS=40
   ```
5. On the bot deployment add:
   - `ORACLE_STREAM_URL=https://your-name.duckdns.org`
   - `ORACLE_BANDWIDTH_LIMIT_GB=10000` (lower it if you want extra margin)

Leave `ORACLE_STREAM_URL` empty and Oracle stays disabled.

## How host switching works
New links go to Oracle first. The bot reserves each file's size against the
host's monthly limit when it issues a link, and checks `/health` (cached 30 s).
Oracle is skipped when it's down or past `STREAM_SWITCH_PERCENT` (85 %) of its
limit — new links then use Koyeb/Render (`STREAM_BASE_URL`). If both are full,
the user gets a "busy" notice and keeps the file already sent in chat.
Existing links keep working on the host that issued them.

## Limits and notes
- Per user: `STREAM_DAILY_LIMIT` links/day (premium: `STREAM_PREMIUM_DAILY_LIMIT`).
- Don't delete messages in BIN_CHANNEL — each file is copied there once and reused.
- Render free instances sleep when idle; the first click after a sleep can be slow.
- Free hosts may restrict heavy transfer; check Render/Koyeb terms.
- **▶️ Watch** appears next to Download for video files. It opens a player page; MKV/HEVC/AC3 often won't play in a browser, so the page also has **Open in VLC / MX Player** buttons that stream the same link.
- On startup the log says `Fast Download is ON` or `Fast Download is OFF — Missing: ...`. If you see no buttons, check that line first.
