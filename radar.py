#!/usr/bin/env python3
"""
AirdropRadarBot — autonomous airdrop/testnet intelligence radar.

Sources:
  1. airdrops.io  (Firecrawl markdown scrape)
  2. GitHub repo search: airdrop/testnet farming tools + retroactive trackers

State (kept inside the repo):
  state.json     -> seen-set for dedup + run counter
  airdrops.json  -> structured database of every known entry
  REPORT.md      -> the live report pushed to GitHub

Every run:
  - scrapes both sources
  - diffs against the seen-set, merges new entries into the DB
  - rewrites REPORT.md
  - commits + pushes to origin (auth via the `gh` credential helper)
  - fires a termux-notification only when NEW entries appeared
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BOT_DIR, "state.json")
DB_PATH = os.path.join(BOT_DIR, "airdrops.json")
REPORT_PATH = os.path.join(BOT_DIR, "REPORT.md")
ENV_PATH = os.path.join(BOT_DIR, ".env")

# ---------------------------------------------------------------- env
def load_env():
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            # skip blank/stub/placeholder values
            if not v or v.startswith("#") or v.startswith("REDACTED") or "..." in v:
                continue
            os.environ[k] = v

load_env()
FIRECRAWL_KEY = os.getenv("FIRECRAWL_API_KEY", "")

# Repos identified as phishing/token-drainer "download" farms (unlocktool.click
# redirect templates). New clones of the same scam skip automatically.
BLOCKED_REPOS = {
    "onoffgrid/kite-ai-automata-suite",
    "deepakkankure/gopher-governance-toolkit",
    "yarzarhyo/blum-airdrop-assistant",
    "202303334/diamante-automation-suite",
    "ariyan45160/dkargo-dispenser",
    "chaloyeee/auto-stake-sentinel",
    "ghaderhassan38-sudo/interlink-claim-optimizer",
    "jask177/ekox-claim-assistant",
    "badawi2023/boxxer-airdrop-automator",
    "rmd122e/dusted-referral-suite",
    "adealta/humanity-protocol-daily-claimer",
    "abhirajb-debug/acki-nacki-harvester",
}

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ---------------------------------------------------------------- http
def http_json(url, method="GET", payload=None, headers=None, timeout=30):
    hdrs = {"User-Agent": "AirdropRadarBot/2.0"}
    if headers:
        hdrs.update(headers)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

# ---------------------------------------------------------------- sources
def scrape_airdrops_io():
    """Full markdown of airdrops.io homepage via Firecrawl."""
    if not FIRECRAWL_KEY:
        print("[!] no FIRECRAWL_API_KEY — skipping airdrops.io")
        return ""
    print("[*] scraping airdrops.io via firecrawl ...")
    try:
        res = http_json(
            "https://api.firecrawl.dev/v1/scrape",
            method="POST",
            payload={"url": "https://airdrops.io/", "formats": ["markdown"]},
            headers={"Authorization": f"Bearer {FIRECRAWL_KEY}"},
            timeout=45,
        )
        if res.get("success"):
            md = res.get("data", {}).get("markdown", "")
            print(f"[+] firecrawl returned {len(md)} chars")
            return md
        print(f"[!] firecrawl failure: {res.get('errors')}")
    except Exception as e:
        print(f"[!] firecrawl error: {e}")
    return ""

def gh_search(query, per_page=15):
    """GitHub repo search (unauthenticated public endpoint, rate-limited)."""
    url = (
        "https://api.github.com/search/repositories?q="
        + urllib.parse.quote(query)
        + f"&sort=updated&order=desc&per_page={per_page}"
    )
    try:
        data = http_json(url, headers={"Accept": "application/vnd.github+json"}, timeout=25)
        return data.get("items", [])
    except Exception as e:
        print(f"[!] gh search error ({query[:40]}): {e}")
        return []

def fetch_github_repos():
    """Tooling repos: farming scripts, claim bots, testnet automation."""
    print("[*] searching github for airdrop tooling ...")
    out = []
    seen_names = set()
    for q in (
        "airdrop testnet farm pushed:>2026-08-01",
        "airdrop bot claim pushed:>2026-08-01",
        "retroactive airdrop qualifier pushed:>2026-08-01",
    ):
        for item in gh_search(q):
            full = item.get("full_name", "")
            if not full or full in seen_names:
                continue
            if full.lower() in BLOCKED_REPOS:
                continue
            seen_names.add(full)
            out.append(
                {
                    "id": "gh:" + full.lower(),
                    "kind": "tooling",
                    "name": full,
                    "url": item.get("html_url", ""),
                    "desc": (item.get("description") or "")[:220],
                    "stars": item.get("stargazers_count", 0),
                    "updated": item.get("updated_at", ""),
                    "first_seen": utc_stamp(),
                }
            )
        time.sleep(2)  # search api is rate-limited
    return out

# ---------------------------------------------------------------- parse airdrops.io
ENTRY_RE = re.compile(
    r"\[([^\]]{3,90})\]\((https?://airdrops\.io/[^\s)]+)\)"  # [Title](url)
)
TICKER_RE = re.compile(r"\*\*\s*([A-Z0-9]{2,12})\s*\*\*")

def parse_airdrops_io(md):
    """Pull structured entries out of the airdrops.io markdown."""
    if not md:
        return []
    body = md
    for marker in ("## Featured", "## Latest", "# Airdrops"):
        if marker in body:
            body = body.split(marker, 1)[-1]
            break
    entries = []
    seen_urls = set()
    for m in ENTRY_RE.finditer(body):
        title, url = m.group(1).strip(), m.group(2).strip()
        # keep only real project pages, not visit-redirect or logo/assets
        if not re.fullmatch(r"https?://airdrops\.io/[a-z0-9\-]+/?", url):
            continue
        if url in seen_urls or len(title) < 3:
            continue
        seen_urls.add(url)
        slug = url.rstrip("/").split("/")[-1].replace("-", " ")
        entries.append(
            {
                "id": "io:" + url.lower(),
                "kind": "drop",
                "name": title.strip(" *#"),
                "url": url,
                "slug": slug,
                "desc": "",
                "first_seen": utc_stamp(),
            }
        )
    return entries

# ---------------------------------------------------------------- state
def load_json(path, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)

# ---------------------------------------------------------------- report
def build_report(db, new_ids):
    lines = []
    lines.append("# Airdrop Radar - Live Intelligence\n")
    lines.append(
        f"**Last scan:** {now()} WIB **| Total tracked:** {len(db['airdrops'])} "
        f"**| New this cycle:** {len(new_ids)}\n"
    )
    lines.append("> Auto-generated by AirdropRadarBot (Termux). "
                 "Scans airdrops.io + GitHub every 2h.\n")

    by_kind = {}
    for e in db["airdrops"]:
        by_kind.setdefault(e.get("kind", "?"), []).append(e)

    if new_ids:
        lines.append("## New this cycle\n")
        for e in db["airdrops"]:
            if e["id"] in new_ids:
                lines.append(f"- **[{e['name']}]({e['url']})** `{e.get('slug', '')}`")
        lines.append("")

    drops = sorted(by_kind.get("drop", []), key=lambda x: x.get("first_seen", ""), reverse=True)
    if drops:
        lines.append("## Active & upcoming airdrops (airdrops.io)\n")
        lines.append("| Project | Link | First seen |")
        lines.append("|---|---|---|")
        for e in drops[:60]:
            lines.append(f"| {e['name']} | [open]({e['url']}) | {e['first_seen'][:10]} |")
        lines.append("")

    tools = sorted(by_kind.get("tooling", []), key=lambda x: x.get("stars", 0), reverse=True)
    if tools:
        lines.append("## Airdrop farming tools (GitHub)\n")
        lines.append("| Repo | Stars | Updated | Description |")
        lines.append("|---|---|---|---|")
        for e in tools[:40]:
            d = (e.get("desc") or "").replace("|", "/")[:90]
            lines.append(f"| **[{e['name']}]({e['url']})** | {e.get('stars', 0)} | {e.get('updated', '')[:10]} | {d} |")
        lines.append("")

    txt = "\n".join(lines)
    with open(REPORT_PATH, "w") as f:
        f.write(txt)
    return txt

# ---------------------------------------------------------------- notify
def notify(title, body):
    print(f"[ALERT] {title} :: {body}")
    subprocess.run(
        ["termux-notification", "--title", title, "--content", body[:450],
         "--priority", "high"],
        capture_output=True,
    )
    # Telegram (best effort; skips silently if no token configured)
    try:
        import tbnotify
        tbnotify.send_telegram(f"🚀 {title}\n{body}")
    except Exception as e:
        print(f"[!] telegram notify error: {e}")

# ---------------------------------------------------------------- git
def git_push():
    print("[*] pushing report to github ...")
    subprocess.run(["git", "add", "REPORT.md", "state.json", "airdrops.json"],
                   cwd=BOT_DIR, capture_output=True)
    st = subprocess.run(["git", "status", "--porcelain"], cwd=BOT_DIR,
                        capture_output=True, text=True)
    if not st.stdout.strip():
        print("[=] no changes to push")
        return False
    subprocess.run(["git", "commit", "-m", f"radar update {now()}"],
                   cwd=BOT_DIR, capture_output=True)
    r = subprocess.run(["git", "push", "origin", "main"], cwd=BOT_DIR,
                       capture_output=True, text=True, timeout=60)
    ok = r.returncode == 0
    tail = (r.stderr or "").strip().splitlines()
    print(f"[{'+' if ok else '!'}] git push rc={r.returncode} "
          f"{(tail[-1] if tail else '')}")
    return ok

# ---------------------------------------------------------------- main
def main():
    print(f"\n=== AirdropRadarBot scan {now()} ===")
    os.makedirs(BOT_DIR, exist_ok=True)

    db = load_json(DB_PATH, {"airdrops": [], "last_scan": None})
    state = load_json(STATE_PATH, {"seen": [], "runs": 0, "last_new": 0})
    seen = set(state.get("seen", []))

    io_md = scrape_airdrops_io()
    io_entries = parse_airdrops_io(io_md)
    gh_entries = fetch_github_repos()
    print(f"[+] airdrops.io entries={len(io_entries)} github entries={len(gh_entries)}")

    current_ids = {e["id"] for e in io_entries} | {e["id"] for e in gh_entries}
    new_ids = current_ids - seen

    by_id = {e["id"]: e for e in db["airdrops"]}
    for e in io_entries + gh_entries:
        if e["id"] not in by_id:
            by_id[e["id"]] = e
    # drop any previously-known entry that is now blacklisted
    for eid in list(by_id):
        if eid.startswith("gh:") and eid[3:].lower() in BLOCKED_REPOS:
            del by_id[eid]
    db["airdrops"] = list(by_id.values())
    db["last_scan"] = utc_stamp()

    save_json(DB_PATH, db)

    state["seen"] = sorted((seen | current_ids) - {eid for eid in current_ids if eid.startswith("gh:") and eid[3:].lower() in BLOCKED_REPOS})
    state["runs"] = state.get("runs", 0) + 1
    state["last_new"] = len(new_ids)
    save_json(STATE_PATH, state)

    build_report(db, new_ids)
    git_push()

    if new_ids:
        fresh = [e for e in db["airdrops"] if e["id"] in new_ids]
        names = ", ".join(e["name"] for e in fresh[:4])
        notify(
            f"{len(new_ids)} NEW airdrop(s) detected",
            f"{names} +{max(0, len(fresh) - 4)} more - report pushed to airdrop-radar",
        )
    else:
        print("[=] no new airdrops this cycle")

    print(f"=== done. tracked={len(db['airdrops'])} new={len(new_ids)} ===\n")
    return 0

if __name__ == "__main__":
    sys.exit(main())