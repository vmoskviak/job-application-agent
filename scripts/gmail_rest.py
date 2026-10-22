#!/usr/bin/env python3
"""Мінімальний Gmail REST хелпер без MCP (робоча машина й сервер).

Працює з ~/.gmail-mcp/<creds_dir>/credentials.json активного клієнта
(accounts.yaml → gmail.<alias>.creds_dir) + ключ OAuth-клієнта: свій у
клієнта (~/.gmail-mcp/<creds_dir>/gcp-oauth.keys.json) або спільний на машину
(~/.gmail-mcp/gcp-oauth.keys.json)
(ті самі creds, що юзає Gmail MCP). Клієнт: JOBSEARCH_CLIENT або --client.

Використання:
  gmail_rest.py check jobs|main                 — refresh токена, профіль
  gmail_rest.py get <acc> <msg_id>              — метадані листа
  gmail_rest.py body <acc> <msg_id>             — текст листа
  gmail_rest.py thread <acc> <thread_id>        — учасники треду
  gmail_rest.py search <acc> "<gmail query>"    — msg id + From/Subject
  gmail_rest.py reply <acc> <msg_id> <body_file> [to] [--attach p1,p2]
                                                — reply у тому ж thread (з вкладеннями)
  gmail_rest.py send <acc> <draft_json>         — нова заявка з вкладеннями
       (draft_json: {to, subject, body, attachments:[шляхи]})

Правила: delete тут не реалізовано взагалі; reply — лише роботодавцю в
існуючому thread; send — нові заявки лише з акаунта `send_from` клієнта.
"""
import base64
import json
import mimetypes
import sys
from datetime import datetime
from email.encoders import encode_base64
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import client as _client  # noqa: E402

_c = _client.active()
_accounts = _c.accounts
GMAIL_DIR = _c.gmail_dir()
OAUTH_KEYS = {alias: _c.oauth_keys_path(alias) for alias in _c.gmail_aliases}
CREDS = {alias: _c.credentials_path(alias) for alias in _c.gmail_aliases}
SEND_FROM = _accounts.get("send_from", "jobs")


def access_token(account: str) -> str:
    keys = json.loads(OAUTH_KEYS[account].read_text())
    key = keys.get("installed") or keys.get("web")
    creds = json.loads(CREDS[account].read_text())
    r = httpx.post("https://oauth2.googleapis.com/token", data={
        "client_id": key["client_id"],
        "client_secret": key["client_secret"],
        "refresh_token": creds["refresh_token"],
        "grant_type": "refresh_token",
    }, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def berlin_date() -> str:
    """Date-хедер у берлінському часі.

    Без нього Gmail підставляє свою зону, і в сирих заголовках одного треду
    час стрибає. На доставку не впливає, але читається
    як хаос, якщо адресат дивиться заголовки.
    """
    return format_datetime(datetime.now(ZoneInfo("Europe/Berlin")))


def api(account: str, method: str, path: str, **kw):
    tok = access_token(account)
    r = httpx.request(method, f"https://gmail.googleapis.com/gmail/v1/users/me/{path}",
                      headers={"Authorization": f"Bearer {tok}"}, timeout=60, **kw)
    r.raise_for_status()
    return r.json()


def main():
    cmd, account = sys.argv[1], sys.argv[2]
    if cmd == "check":
        prof = api(account, "GET", "profile")
        print(f"OK {prof['emailAddress']} (threads: {prof['threadsTotal']})")
    elif cmd == "get":
        msg = api(account, "GET", f"messages/{sys.argv[3]}",
                  params={"format": "metadata",
                          "metadataHeaders": ["Subject", "From", "To", "Message-ID", "Date"]})
        print(json.dumps({"threadId": msg["threadId"],
                          "headers": {h["name"]: h["value"] for h in msg["payload"]["headers"]},
                          "snippet": msg.get("snippet", "")}, ensure_ascii=False, indent=1))
    elif cmd == "search":
        res = api(account, "GET", "messages", params={"q": sys.argv[3], "maxResults": 15})
        for m in res.get("messages", []):
            meta = api(account, "GET", f"messages/{m['id']}",
                       params={"format": "metadata", "metadataHeaders": ["Subject", "From", "To", "Date"]})
            hdr = {h["name"]: h["value"] for h in meta["payload"]["headers"]}
            print(f"{m['id']}  {hdr.get('Date','')[:22]}  {hdr.get('From', hdr.get('To',''))[:45]}  {hdr.get('Subject','')[:60]}")
    elif cmd == "body":
        msg = api(account, "GET", f"messages/{sys.argv[3]}", params={"format": "full"})
        def walk(part):
            if part.get("mimeType", "").startswith("text/") and part.get("body", {}).get("data"):
                print(base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace"))
            for p in part.get("parts", []):
                walk(p)
        walk(msg["payload"])
    elif cmd == "thread":
        th = api(account, "GET", f"threads/{sys.argv[3]}",
                 params={"format": "metadata", "metadataHeaders": ["From", "Date", "Subject"]})
        for m in th["messages"]:
            hdr = {h["name"]: h["value"] for h in m["payload"]["headers"]}
            print(f"{m['id']}  {hdr.get('Date','')[:22]}  {hdr.get('From','')[:50]}")
    elif cmd == "reply":
        # reply <acc> <msg_id> <body_file> [to] [--attach шлях1,шлях2]
        argv = sys.argv[3:]
        attach_paths = []
        if "--attach" in argv:
            i = argv.index("--attach")
            attach_paths = [p for p in argv[i + 1].split(",") if p.strip()]
            argv = argv[:i] + argv[i + 2:]
        orig = api(account, "GET", f"messages/{argv[0]}",
                   params={"format": "metadata",
                           "metadataHeaders": ["Subject", "From", "To", "Reply-To",
                                               "Message-ID", "References"]})
        hdr = {h["name"]: h["value"] for h in orig["payload"]["headers"]}
        body = Path(argv[1]).read_text()
        # Без явного [to] відповідь іде відправнику оригіналу (Reply-To, далі From).
        # Раніше бралось To, а To вхідного листа — це власна адреса: 14.10.2026
        # відповідь роботодавцю пішла сама собі.
        to = argv[2] if len(argv) > 2 else hdr.get("Reply-To") or hdr.get("From")
        subj = hdr.get("Subject", "")
        if not subj.lower().startswith("re:"):
            subj = "Re: " + subj
        if attach_paths:
            mime = MIMEMultipart()
            mime.attach(MIMEText(body, "plain", "utf-8"))
            for path in attach_paths:
                fp = Path(path)
                if not fp.exists():
                    sys.exit(f"attachment missing: {fp}")
                ctype, _ = mimetypes.guess_type(str(fp))
                maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
                part = MIMEBase(maintype, subtype)
                part.set_payload(fp.read_bytes())
                encode_base64(part)
                part.add_header("Content-Disposition", "attachment", filename=fp.name)
                mime.attach(part)
        else:
            mime = MIMEText(body, "plain", "utf-8")
        mime["To"] = to
        mime["Subject"] = subj
        mime["Date"] = berlin_date()
        mime["In-Reply-To"] = hdr.get("Message-ID", "")
        mime["References"] = (hdr.get("References", "") + " " + hdr.get("Message-ID", "")).strip()
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        sent = api(account, "POST", "messages/send",
                   json={"raw": raw, "threadId": orig["threadId"]})
        print(f"SENT id={sent['id']} thread={sent['threadId']} to={to} subj={subj} "
              f"attachments={[Path(p).name for p in attach_paths]}")
    elif cmd == "send":
        if account != SEND_FROM:
            sys.exit(f"send дозволено лише з {SEND_FROM} (нові заявки не з інших акаунтів)")
        draft = json.loads(Path(sys.argv[3]).read_text())
        mime = MIMEMultipart()
        mime["To"] = draft["to"]
        mime["Subject"] = draft["subject"]
        mime["Date"] = berlin_date()
        mime.attach(MIMEText(draft["body"], "plain", "utf-8"))
        attached = []
        for path in draft.get("attachments", []):
            fp = Path(path)
            if not fp.exists():
                print(f"WARN attachment missing: {fp}", file=sys.stderr)
                continue
            ctype, _ = mimetypes.guess_type(str(fp))
            maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
            part = MIMEBase(maintype, subtype)
            part.set_payload(fp.read_bytes())
            encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=fp.name)
            mime.attach(part)
            attached.append(fp.name)
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        sent = api(account, "POST", "messages/send", json={"raw": raw})
        print(f"SENT id={sent['id']} to={draft['to']} subj={draft['subject']} "
              f"attachments={attached}")
    elif cmd == "label":
        # Додати/зняти мітку. Використання: label <acc> <msg_id> add|remove <LABEL>
        # (напр. INBOX). main: delete заборонено, зміна міток — ні.
        msg_id, op, lbl = sys.argv[3], sys.argv[4], sys.argv[5]
        key = "addLabelIds" if op == "add" else "removeLabelIds"
        r = api(account, "POST", f"messages/{msg_id}/modify", json={key: [lbl]})
        print(f"LABELED id={r['id']} {op} {lbl} -> {r.get('labelIds')}")
    elif cmd == "untrash":
        # Відновити лист із кошика назад в inbox (main дозволено: це не delete,
        # оборотна дія). Використання: untrash <acc> <msg_id>
        msg = api(account, "POST", f"messages/{sys.argv[3]}/untrash")
        meta = api(account, "GET", f"messages/{msg['id']}",
                   params={"format": "metadata",
                           "metadataHeaders": ["Subject", "From", "Date"]})
        hdr = {h["name"]: h["value"] for h in meta["payload"]["headers"]}
        print(f"UNTRASHED id={msg['id']} labels={msg.get('labelIds', [])} "
              f"from={hdr.get('From','')[:45]} subj={hdr.get('Subject','')[:60]}")
    else:
        sys.exit(f"unknown cmd {cmd}")


if __name__ == "__main__":
    main()
