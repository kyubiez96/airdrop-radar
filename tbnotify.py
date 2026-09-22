#!/usr/bin/env python3
"""
Telegram notifier for AirdropRadarBot.

send_telegram(text) -> None
Reads TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USERS from .env (or env).
Used by radar.py (new-drop alerts) and importable by bot.py.
"""
import json
import os
import urllib.parse
import urllib.request

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BOT_DIR, ".env")


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
ALLOWED = [uid.strip() for uid in os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",") if uid.strip()]


def _api(method, payload):
    if not TOKEN:
        print("[!] no TELEGRAM_BOT_TOKEN — notification skipped")
        return None
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        print(f"[!] telegram api error ({method}): {e}")
        return None


def send_telegram(text):
    """Send to every allowed user. Splits long messages at 4000 chars."""
    for uid in ALLOWED:
        for chunk in _chunks(text, 4000):
            _api("sendMessage", {"chat_id": uid, "text": chunk,
                                 "disable_web_page_preview": "true"})


def _chunks(text, size):
    return [text[i:i + size] for i in range(0, len(text), size)]


if __name__ == "__main__":
    send_telegram("Test from AirdropRadarBot ✅")