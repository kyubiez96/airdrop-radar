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
   - Fires a `termux-notification` only when new entries appear.
2. **`daemon.py`** — runs `radar.py` every 2 hours.
3. **`monitor_worker/`** — Cloudflare Worker that serves a dashboard at
   `eksi.biz.id/monitor/*`.

## Files

| Path | Purpose |
|---|---|
| `radar.py` | Canonical scanner |
| `daemon.py` | Scheduler loop |
| `state.json` | Dedup seen-set + run counter |
| `airdrops.json` | Full entry database |
| `REPORT.md` | Live report (committed to GitHub) |
| `.env` | Secrets (gitignored) |
| `monitor_worker/` | CF Worker dashboard |

## Setup

```sh
# Termux
pkg install python git termux-api gh
gh auth login                 # git uses the gh credential helper (no PAT in repo)

# secrets
cp .env.example .env          # fill FIRECRAWL_API_KEY
pip install nothing           # stdlib only

# one scan
python3 radar.py

# continuous
python3 daemon.py
# or: termux-wake-lock && nohup python3 daemon.py >/dev/null 2>&1 &
```

## Cron alternative

```sh
crontab -e
0 */2 * * * cd ~/airdrop_bot && python3 radar.py
```

Requires `cronie`/`termux-services`.