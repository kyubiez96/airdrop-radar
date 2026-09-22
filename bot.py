#!/usr/bin/env python3
"""
AirdropRadar Telegram Bot — browse, search, and get step-by-step claim
guides for every tracked airdrop.

Commands
  /start            help + status
  /list [n]         latest airdrops (default 10)
  /search <term>    search by name/slug
  /guide <slug>     full how-to-claim steps for a drop (requires Firecrawl)
  /tools            GitHub farming tools
  /done <slug>      mark a drop as claimed/done
  /todo             airdrops not yet marked done
  /new              entries detected in the latest scan

Runs via Telegram long-polling (stdlib only). Credentials from .env:
  TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS, FIRECRAWL_API_KEY.
"""
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BOT_DIR, ".env")
DB_PATH = os.path.join(BOT_DIR, "airdrops.json")
DONE_PATH = os.path.join(BOT_DIR, "claimed.json")


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
            os.environ.setdefault(k, v)


load_env()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED = {uid.strip() for uid in os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",") if uid.strip()}
FIRECRAWL_KEY = os.getenv("FIRECRAWL_API_KEY", "")


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


def load_db():
    db = load_json(DB_PATH, {"airdrops": []})
    ads = db.get("airdrops", [])
    ads.sort(key=lambda e: e.get("first_seen", ""), reverse=True)
    return db, ads


# ---------------------------------------------------------------- telegram
def tg(method, payload):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def esc(s):
    """Escape a user/DB-derived string for Telegram HTML parse_mode."""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def reply(chat_id, text):
    # Callers may already include <b>/<a> tags; text must be pre-escaped
    # with esc() anywhere untrusted data (names, search terms) is inserted.
    # Split on paragraph boundaries to stay under Telegram's 4096 limit.
    chunks = []
    cur = ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 4000:
            chunks.append(cur.rstrip())
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur.rstrip())
    for c in chunks:
        try:
            tg("sendMessage", {"chat_id": chat_id, "text": c,
                               "parse_mode": "HTML",
                               "disable_web_page_preview": "true"})
        except Exception as e:
            print(f"[!] sendMessage failed @{chat_id}: {e}")


# ---------------------------------------------------------------- data helpers
def find_entry(slug):
    _, ads = load_db()
    for e in ads:
        if e.get("slug", "").lower() == slug.lower():
            return e
        if e.get("id", "").endswith("/" + slug):
            return e
    return None


def find_entries(term):
    """Exact slug match first, then substring over name+slug."""
    term_l = term.lower()
    _, ads = load_db()
    exact = [e for e in ads if e.get("slug", "").lower() == term_l]
    if exact:
        return exact
    return [e for e in ads if term_l in (e.get("slug", "") + " " + e.get("name", "")).lower()]


def fmt_entry(e):
    t = e.get("kind", "?")
    name = esc(e.get("name", ""))
    url = esc(e.get("url", ""))
    slug = esc(e.get("slug", ""))
    if t == "drop":
        return f"🎁 {name} — /guide {slug}\n   {url}"
    return f"🛠 {name}\n   {url}"


# ---------------------------------------------------------------- guide (firecrawl)
def scrape_guide(url):
    if not FIRECRAWL_KEY:
        return ""
    try:
        req = urllib.request.Request(
            "https://api.firecrawl.dev/v1/scrape",
            data=json.dumps({"url": url, "formats": ["markdown"]}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {FIRECRAWL_KEY}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as r:
            res = json.loads(r.read().decode("utf-8", "replace"))
            if res.get("success"):
                return res.get("data", {}).get("markdown", "")
    except Exception as e:
        print(f"[!] firecrawl error: {e}")
    return ""


def extract_howto(md, name):
    """Pull the actionable 'how to' / step region out of a project page."""
    if not md:
        return None
    # find the participation section
    for heading in ("How to Participate", "How to Claim", "How to Get",
                    "Steps to", "How It Works", "How to"):
        idx = md.find(heading)
        if idx != -1:
            break
    else:
        return None
    body = md[idx:idx + 4000]
    # stop at FAQ / Tips boundary when present
    for stop in ("## Frequently Asked", "## FAQ", "## Tips for"):
        j = body.find(stop)
        if j != -1:
            body = body[:j]
            break
    return body.strip()


def md_to_tg_html(text):
    """Convert common Firecrawl Markdown to Telegram-safe HTML.

    Order matters: escape user text first, then inject tags.
    """
    if not text:
        return ""
    # 1. escape HTML-sensitive chars in the raw markdown
    t = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 2. convert strong **x** -> <b>x</b>
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    # 3. convert headings (line-start #) -> <b>line</b>
    t = re.sub(r"(?m)^#{1,6}\s*(.+)$", r"<b>\1</b>", t)
    # 4. convert inline links [text](url) -> <a href="url">text</a>
    #    (hrefs were already &-escaped, so no raw & in URL breaks tags)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    return t


# ---------------------------------------------------------------- command handlers
def cmd_start(chat_id, uid):
    _, ads = load_db()
    done = load_json(DONE_PATH, {}).get(str(uid), [])
    text = (
        "🤖 <b>AirdropRadarBot</b>\n\n"
        f"Tracked airdrops: {len(ads)}\n"
        f"You've marked done: {len(done)}\n\n"
        "/list — latest airdrops\n"
        "/search term — find a drop\n"
        "/guide slug — step-by-step claim guide\n"
        "/tools — GitHub farming tools\n"
        "/done slug — mark as claimed\n"
        "/todo — not-done drops\n"
        "/new — latest scan finds"
    )
    reply(chat_id, text)


def cmd_list(chat_id, args):
    n = 10
    if args:
        try:
            n = max(1, min(30, int(args[0])))
        except ValueError:
            pass
    _, ads = load_db()
    drops = [e for e in ads if e.get("kind") == "drop"][:n]
    if not drops:
        reply(chat_id, "No airdrops tracked yet.")
        return
    lines = [f"<b>Latest {len(drops)} airdrops:</b>"]
    for e in drops:
        lines.append(fmt_entry(e))
    reply(chat_id, "\n\n".join(lines))


def cmd_search(chat_id, args):
    term = " ".join(args).strip()
    if not term:
        reply(chat_id, "Usage: /search <name-or-slug>")
        return
    hits = find_entries(term)[:15]
    t = esc(term)
    if not hits:
        reply(chat_id, f"No match for “{t}”.")
        return
    lines = [f"<b>Matches for “{t}”:</b>"]
    for e in hits:
        lines.append(fmt_entry(e))
    reply(chat_id, "\n\n".join(lines))


def cmd_guide(chat_id, args):
    slug = " ".join(args).strip().lower()
    if not slug:
        reply(chat_id, "Usage: /guide <slug>")
        return
    e = find_entry(slug)
    if not e:
        reply(chat_id, f"No airdrop with slug “{esc(slug)}”. Try /search.")
        return
    name = esc(e["name"])
    url = esc(e["url"])
    reply(chat_id, f"⏳ Fetching guide for <b>{name}</b> … this may take a few seconds.")
    md = scrape_guide(e["url"])
    howto = extract_howto(md, e["name"])
    if not howto:
        reply(chat_id, f"<b>{name}</b>\n{url}\n\n(no How-to section found — check the link directly.)")
        return
    reply(chat_id, f"<b>{name}</b> — claim guide\n\n{md_to_tg_html(howto)}\n\nMore: {url}")


def cmd_tools(chat_id):
    _, ads = load_db()
    tools = [e for e in ads if e.get("kind") == "tooling"]
    if not tools:
        reply(chat_id, "No tools tracked yet.")
        return
    lines = ["<b>GitHub farming tools:</b>"]
    for e in tools[:15]:
        lines.append(fmt_entry(e))
    reply(chat_id, "\n\n".join(lines))


def cmd_done(chat_id, uid, args):
    slug = " ".join(args).strip().lower()
    e = find_entry(slug)
    if not e:
        reply(chat_id, f"No drop “{esc(slug)}”. /search to find the right slug.")
        return
    data = load_json(DONE_PATH, {})
    my = data.get(str(uid), [])
    if slug in my:
        reply(chat_id, f"{esc(e['name'])} was already marked done.")
        return
    my.append(slug)
    data[str(uid)] = my
    save_json(DONE_PATH, data)
    reply(chat_id, f"✅ Marked <b>{esc(e['name'])}</b> as claimed/done.")


def cmd_todo(chat_id, uid):
    _, ads = load_db()
    done = set(load_json(DONE_PATH, {}).get(str(uid), []))
    drops = [e for e in ads if e.get("kind") == "drop" and e.get("slug") not in done]
    if not drops:
        reply(chat_id, "All tracked airdrops marked done. 🎉")
        return
    lines = [f"<b>Remaining ({len(drops)}):</b>"]
    for e in drops[:20]:
        lines.append(fmt_entry(e))
    reply(chat_id, "\n\n".join(lines))


def cmd_new(chat_id):
    _, ads = load_db()
    one = load_json(os.path.join(BOT_DIR, "state.json"), {})
    seen = set(one.get("seen", []))
    # newest entries by first_seen (a proxy for "new this scan" when none flagged)
    drops = [e for e in ads if e.get("kind") == "drop"]
    drops.sort(key=lambda x: x.get("first_seen", ""), reverse=True)
    top = drops[:10]
    if not top:
        reply(chat_id, "Nothing new.")
        return
    lines = ["<b>Newest in the radar:</b>"]
    for e in top:
        lines.append(fmt_entry(e))
    reply(chat_id, "\n\n".join(lines))


# ---------------------------------------------------------------- dispatch
def handle(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    text = (msg.get("text") or "").strip()
    chat_id = msg["chat"]["id"]
    uid = str(msg.get("from", {}).get("id", ""))

    if ALLOWED and uid not in ALLOWED:
        reply(chat_id, "⛔ Not authorized.")
        return

    if not text.startswith("/"):
        reply(chat_id, "Send a command: /start /list /search /guide /tools /done /todo /new")
        return

    parts = text[1:].split()
    cmd = parts[0].split("@")[0].lower()  # strip @botname suffix
    args = parts[1:]

    if cmd == "start":
        cmd_start(chat_id, uid)
    elif cmd == "list":
        cmd_list(chat_id, args)
    elif cmd == "search":
        cmd_search(chat_id, args)
    elif cmd == "guide":
        cmd_guide(chat_id, args)
    elif cmd == "tools":
        cmd_tools(chat_id)
    elif cmd == "done":
        cmd_done(chat_id, uid, args)
    elif cmd == "todo":
        cmd_todo(chat_id, uid)
    elif cmd == "new":
        cmd_new(chat_id)
    else:
        reply(chat_id, "Unknown command. Try /start.")


def poll():
    offset = 0
    print(f"🤖 AirdropRadarBot polling… allowed users: {sorted(ALLOWED) or 'any'}")
    while True:
        try:
            res = tg("getUpdates", {"offset": offset, "timeout": 30})
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                handle(upd)
        except Exception as e:
            print(f"[!] poll error: {e}")
            time.sleep(3)


if __name__ == "__main__":
    if not TOKEN:
        print("Missing TELEGRAM_BOT_TOKEN in .env")
        raise SystemExit(1)
    poll()