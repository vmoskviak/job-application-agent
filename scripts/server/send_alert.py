#!/usr/bin/env python3
"""Failure alert without Claude or MCP: direct Gmail REST.

Refreshes the access token via the refresh_token from ~/.gmail-mcp (creds of the
active client's `send_from` account) and mails the operator. Works even when the
claude CLI / npx / MCP are down.

Recipient: env JOBSEARCH_ALERT_TO (set in /root/.claude-env). If the client has
Telegram configured (accounts.yaml -> telegram), the alert goes there right away and
neither mail nor the summary-mail queue is used.

Usage:
    send_alert.py --run morning --message "timeout 3h" [--log-tail "..."]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from bot import client as _client  # noqa: E402

_c = _client.active()
_sender = _c.gmail(_c.send_from)
CREDENTIALS = _c.credentials_path(_c.send_from)
OAUTH_KEYS = _c.oauth_keys_path(_c.send_from)
ALERT_TO = os.environ.get("JOBSEARCH_ALERT_TO", "")
ALERT_FROM = _sender["address"]


def _oauth_client() -> tuple[str, str]:
    keys = json.loads(OAUTH_KEYS.read_text())
    block = keys.get("installed") or keys.get("web")
    return block["client_id"], block["client_secret"]


def _access_token() -> str:
    creds = json.loads(CREDENTIALS.read_text())
    client_id, client_secret = _oauth_client()
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


RUN_NAMES = {"morning": "Morgenlauf", "evening": "Abendlauf"}


def alert_subject(slug: str, run: str, message: str) -> str:
    """Subject without "run failed": an alert can also be about a mail awaiting a reply."""
    return f"[jobsearch ALERT] {slug} {RUN_NAMES.get(run, run)}: {message[:70]}"


def send_alert(run: str, message: str, log_tail: str = "") -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    # The operator reads the mail; all engine mail is in German. The log tail stays as is.
    body = (
        f"Klient: {_c.slug}\n"
        f"Lauf: {RUN_NAMES.get(run, run)}\n"
        f"Zeit: {now}\n"
        f"Grund: {message}\n\n"
        f"Log: {_c.var / 'logs'}\n"
        f"Runbook: docs/server_setup.md\n"
    )
    if log_tail:
        body += f"\n--- letzte Logzeilen ---\n{log_tail}\n"

    mime = MIMEText(body, "plain", "utf-8")
    mime["To"] = ALERT_TO
    mime["From"] = ALERT_FROM
    mime["Subject"] = alert_subject(_c.slug, run, message)
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()

    token = _access_token()
    resp = httpx.post(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        headers={"Authorization": f"Bearer {token}"},
        json={"raw": raw},
        timeout=30,
    )
    resp.raise_for_status()
    print(f"[alert] sent: {resp.json().get('id')}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--log-tail", default="")
    parser.add_argument("--queue", action="store_true",
                        help="do not send, queue for the evening mail instead")
    args = parser.parse_args()
    from bot import notify as _notify

    if _notify.configured(_c):
        # Telegram exists: the alert goes out right away and enters neither the queue
        # nor the mail (the operator reads Telegram, engine mail is not needed)
        try:
            _notify.notify(_c, _notify.alert_text(args.run, args.message, args.log_tail, _c.telegram["lang"]))
            print(f"[alert] telegram: {args.message[:80]}")
            return 0
        except Exception as e:  # noqa: BLE001, Telegram failed: fall back to the old channel
            print(f"[alert] telegram FAILED ({e}), falling back to mail", file=sys.stderr)
    if args.queue:
        from bot import operator_report as rep
        rep.queue_alert(_c.var / "run", args.run, args.message, args.log_tail)
        print(f"[alert] queued: {args.message[:80]}")
        return 0
    if not ALERT_TO:
        print("[alert] JOBSEARCH_ALERT_TO not set, alert not sent", file=sys.stderr)
        return 1
    try:
        send_alert(args.run, args.message, args.log_tail)
        return 0
    except Exception as e:
        print(f"[alert] FAILED to send alert: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
