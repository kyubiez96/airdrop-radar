# Airdrop Radar

Autonomous airdrop / testnet intelligence radar. Runs on Termux, scans
[airdrops.io](https://airdrops.io) (via Firecrawl) and GitHub, dedupes into a
permanent database, and publishes a live report.

## How it works

1. **`radar.py`** — the scanner (run this; it does one full cycle):
   - Scrapes `airdrops.io` markdown via the Firecrawl API.
   - Searches GitHub for airdrop/testnet tooling repos.
   - Diffs new entries against `state.json` (`seen` set), merges into
     `airdrops.json` (the full DB).
   - Rewrites `REPORT.md` and commits + pushes to GitHub.
   - Fires a `termux-notification` **and a Telegram message** only when new
     entries appear.
2. **`daemon.py`** — runs `radar.py` every 2 hours.
3. **`bot.py`** — interactive Telegram bot: browse/search airdrops, fetch
   step-by-step claim guides, track what you've claimed.
4. **`monitor_worker/`** — Cloudflare Worker that serves a dashboard at
   `eksi.biz.id/monitor/*`.

## Files

| Path | Purpose |
|---|---|
| `radar.py` | Canonical scanner |
| `daemon.py` | Scheduler loop |
| `bot.py` | Telegram bot (long-polling) |
| `run_bot.py` | Bot runner w/ auto-restart |
| `tbnotify.py` | Telegram notify helper (shared) |
| `patch_monitor.py` | Idempotent deploy patch for the `/monitor` report URL |
| `state.json` | Dedup seen-set + run counter |
| `airdrops.json` | Full entry database |
| `claimed.json` | Per-user claimed/done list (gitignored) |
| `REPORT.md` | Live report (committed to GitHub) |
| `.env` | Secrets (gitignored) |
| `monitor_worker/` | CF Worker dashboard |

## Setup

```sh
# Termux
pkg install python git termux-api gh
gh auth login                 # git uses the gh credential helper (no PAT in repo)

# secrets
cp .env.example .env          # fill FIRECRAWL_API_KEY + TELEGRAM_BOT_TOKEN
pip install nothing           # stdlib only

# one scan
python3 radar.py

# continuous scanner
python3 daemon.py
# or: termux-wake-lock && nohup python3 daemon.py >/dev/null 2>&1 &

# Telegram bot (separate process)
python3 run_bot.py
# or: nohup python3 run_bot.py >/tmp/airdrop_bot.log 2>&1 &
```

## Cron alternative

```sh
crontab -e
0 */2 * * * cd ~/airdrop_bot && python3 radar.py
```

Requires `cronie`/`termux-services`.

## Telegram bot

Commands:

| Command | What it does |
|---|---|
| `/start` | Status + help |
| `/list [n]` | Latest tracked airdrops |
| `/search term` | Find a drop by name/slug |
| `/guide slug` | Full step-by-step claim guide (Firecrawl, live) |
| `/tools` | GitHub farming tools |
| `/done slug` | Mark a drop as claimed |
| `/todo` | Drops not yet marked done |
| `/new` | Newest radar finds |

Access is restricted to `TELEGRAM_ALLOWED_USERS` (comma-separated IDs).

## Deploy the `/monitor` report URL

`eksi.biz.id/monitor` is served by the `api-dashboard` Cloudflare Worker,
whose inline HTML fetches the report via a client-side JS URL. If that URL
is ever regenerated or pointed at a dead host, re-point it:

```sh
python3 patch_monitor.py
# override the report URL:
REPORT_URL=https://raw.githubusercontent.com/kyubiez96/airdrop-radar/main/REPORT.md python3 patch_monitor.py
```

The script downloads the live worker bundle, patches the report fetch URL
in place, redeploys, and is idempotent. Credentials resolve from
`CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` env vars or the original
token files under `~/storage/downloads/tokeb/`.