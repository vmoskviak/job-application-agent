#!/usr/bin/env python3
"""One-time Upwork login for auto-submit (bot.upwork_submit), done by the owner through VNC.

    JOBSEARCH_CLIENT=<slug> DISPLAY=:99 .venv/bin/python scripts/upwork_login.py [--timeout 3600]

Starts a plain Google Chrome (no automation: under Playwright, Cloudflare's "Verify you are human"
on the login page loops forever, 02.11.2026) on the server display with the profile the submit
pass uses (clients/<slug>/var/browser/upwork), at the Upwork login page. The owner logs in through
VNC (password, 2FA, "Keep me logged in") and closes the window; at the timeout the script closes
Chrome with SIGTERM, which still writes the cookies to disk. The script never sees the password.
Run it while no watcher pass is using the same profile (the pass takes a minute at :10).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot import client as _client

LOGIN_URL = "https://www.upwork.com/ab/account-security/login"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=3600, help="seconds before Chrome is closed")
    args = ap.parse_args()
    profile_dir = _client.active().var / "browser" / "upwork"
    profile_dir.mkdir(parents=True, exist_ok=True)
    chrome = shutil.which("google-chrome") or "google-chrome"
    cmd = [chrome, f"--user-data-dir={profile_dir}", "--password-store=basic", "--no-first-run",
           "--no-default-browser-check", "--window-position=0,0", "--window-size=1280,1000", LOGIN_URL]
    if os.geteuid() == 0:   # Chrome refuses to run as root without it; the server runs as root
        cmd.insert(1, "--no-sandbox")
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY") or ":99"}
    print("Log in to Upwork through VNC (tick 'Keep me logged in'), then close the window", flush=True)
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        proc.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait(timeout=30)
    print(f"Chrome closed; profile {profile_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
