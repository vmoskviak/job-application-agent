#!/usr/bin/env python3
"""Client status summary: text to the console or one evening mail to the operator.

    js scripts/server/operator_summary.py            status to the console (morning check)
    js scripts/server/operator_summary.py --send     mail to the operator, the alert queue is cleared
    ... --send --run evening|morning                 from the wrapper: mail only after "its own" run

The client's report_mode (profile.yaml): operator means a daily Tagesbericht after the
evening run; candidate means only "Zu tun" after the morning run (manual applications,
failures, expired sessions) and only when there is something to do.

If the client has Telegram configured (accounts.yaml -> telegram), no mail is sent at
all: the operator gets the events (interviews, failures) in Telegram right away.

The mail is built without Claude: counters from the tracker, today's agent reports from
var/run/operator_report_<date>*.html and operator_evening_<date>*.html, alerts from the
queue. So it still arrives when the evening run failed.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import client as _client  # noqa: E402
from bot import operator_report as rep  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--send", action="store_true", help="send the mail to the operator")
    ap.add_argument("--day", default=date.today().isoformat())
    ap.add_argument("--run", choices=("evening", "morning"),
                    help="which run invoked it (the wrapper); without it the mail is always sent")
    args = ap.parse_args(argv)

    c = _client.active()
    var_run = c.var / "run"
    summary = rep.summarize(rep.parse_rows(c.tracker), args.day)
    alerts = rep.read_alerts(var_run)

    if not args.send:
        print(rep.build_text(args.day, summary, alerts))
        return 0

    mode = c.report_mode
    todo_files = rep.unsent_todo(var_run) if mode == "candidate" else []
    from bot import notify as _notify

    if _notify.configured(c):
        # The operator reads Telegram: interviews and failures already went there
        # (notify_run.py, send_alert.py), the summary mail is not sent. The "Zu tun"
        # files are marked as sent so they do not pile up.
        rep.mark_todo_sent(todo_files)
        print(f"[summary] telegram configured, run={args.run}: no mail needed")
        return 0
    todo = [p.read_text(encoding="utf-8") for p in todo_files]
    if not rep.should_send(mode, args.run, alerts, todo, summary["manual_open"]):
        print(f"[summary] report_mode={mode}, run={args.run}: no mail needed")
        return 0

    to = os.environ.get("JOBSEARCH_OPERATOR_EMAIL") or os.environ.get("JOBSEARCH_ALERT_TO", "")
    if not to:
        print("[summary] JOBSEARCH_OPERATOR_EMAIL not set, mail not sent", file=sys.stderr)
        return 1
    first = c.profile.get("first_name", c.slug)
    if mode == "none":
        # failures only: the same "Zu tun" layout, without manual applications and agent notes
        bare = {**summary, "manual_open": []}
        page = rep.build_todo_html(c.full_name, args.day, bare, alerts, [])
        subject = rep.alerts_subject(first, args.day, alerts)
        text = rep.build_todo_text(args.day, bare, alerts)
    elif mode == "candidate":
        page = rep.build_todo_html(c.full_name, args.day, summary, alerts, todo)
        subject = rep.todo_subject(first, args.day, summary, alerts)
        text = rep.build_todo_text(args.day, summary, alerts)
    else:
        page = rep.build_html(c.full_name, args.day, summary, alerts,
                              rep.run_fragments(var_run, "operator_report", args.day),
                              rep.run_fragments(var_run, "operator_evening", args.day))
        subject = rep.subject(first, args.day, summary, alerts)
        text = rep.build_text(args.day, summary, alerts)
    mime = MIMEMultipart("alternative")
    mime["To"], mime["From"] = to, c.send_address
    mime["Subject"] = subject
    mime.attach(MIMEText(text, "plain", "utf-8"))
    mime.attach(MIMEText(page, "html", "utf-8"))

    import gmail_rest  # noqa: E402, reads the client config at import time
    sent = gmail_rest.api(c.send_from, "POST", "messages/send",
                          json={"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode()})
    rep.clear_alerts(var_run)
    rep.mark_todo_sent(todo_files)
    print(f"[summary] sent: {sent.get('id')} alerts={len(alerts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
