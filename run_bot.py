#!/usr/bin/env python3
"""
Telegram bot runner (Termux-friendly): single long-poll, auto-restarts.

Usage:
  python3 run_bot.py
  # or: nohup python3 run_bot.py >/tmp/airdrop_bot.log 2>&1 &
"""
import subprocess
import sys
import time

while True:
    print("=== starting bot.py ===", flush=True)
    rc = subprocess.run([sys.executable, "-u", "bot.py"]).returncode
    print(f"=== bot.py exited rc={rc}; restarting in 5s ===", flush=True)
    time.sleep(5)