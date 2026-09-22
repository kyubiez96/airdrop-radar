#!/usr/bin/env python3
"""Airdrop Radar daemon — runs radar.py on a fixed interval."""
import os
import subprocess
import sys
import time

INTERVAL_SECONDS = 2 * 60 * 60  # 2 hours
BOT_DIR = os.path.dirname(os.path.abspath(__file__))

def run_once():
    print(f"[daemon] triggering scan at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return subprocess.run(
        [sys.executable, os.path.join(BOT_DIR, "radar.py")], cwd=BOT_DIR
    ).returncode

def main():
    print(f"Airdrop Radar daemon started (interval={INTERVAL_SECONDS}s)")
    if "--once" in sys.argv:
        sys.exit(run_once())

    while True:
        try:
            rc = run_once()
            print(f"[daemon] scan finished rc={rc}")
        except Exception as e:
            print(f"[daemon] error: {e}")
        time.sleep(INTERVAL_SECONDS)

if __name__ == "__main__":
    main()