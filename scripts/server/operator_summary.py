#!/usr/bin/env python3
"""Зведений стан клієнта: текст у консоль або один вечірній лист оператору.

    js scripts/server/operator_summary.py            стан у консоль (ранкова перевірка)
    js scripts/server/operator_summary.py --send     лист оператору, черга алертів очищається
    ... --send --run evening|morning                 з обгортки: лист лише після «свого» рану

report_mode клієнта (profile.yaml): operator — щоденний Tagesbericht після
вечірнього рану; candidate — після ранкового рану лише «Zu tun» (ручні
подачі, збої, протухлі сесії) і лише якщо є що робити.

Якщо у клієнта налаштовано Telegram (accounts.yaml → telegram), лист не шлеться
взагалі: оператор отримує події (співбесіди, збої) у Telegram одразу.

Лист збирається без Claude: лічильники з трекера, звіти агента за сьогодні з
var/run/operator_report_<дата>*.html і operator_evening_<дата>*.html, алерти
з черги. Тому він доходить і тоді, коли вечірній ран упав.
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
    ap.add_argument("--send", action="store_true", help="надіслати лист оператору")
    ap.add_argument("--day", default=date.today().isoformat())
    ap.add_argument("--run", choices=("evening", "morning"),
                    help="після якого рану викликано (обгортка); без нього лист шлеться завжди")
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
        # Оператор читає Telegram: співбесіди й збої вже дійшли туди (notify_run.py,
        # send_alert.py), зведений лист не шлеться. Файли «Zu tun» позначаються
        # надісланими, щоб не накопичуватись.
        rep.mark_todo_sent(todo_files)
        print(f"[summary] telegram налаштовано, run={args.run}: лист не потрібен")
        return 0
    todo = [p.read_text(encoding="utf-8") for p in todo_files]
    if not rep.should_send(mode, args.run, alerts, todo, summary["manual_open"]):
        print(f"[summary] report_mode={mode}, run={args.run}: лист не потрібен")
        return 0

    to = os.environ.get("JOBSEARCH_OPERATOR_EMAIL") or os.environ.get("JOBSEARCH_ALERT_TO", "")
    if not to:
        print("[summary] JOBSEARCH_OPERATOR_EMAIL не задано, лист не надіслано", file=sys.stderr)
        return 1
    first = c.profile.get("first_name", c.slug)
    if mode == "none":
        # лише збої: та сама верстка «Zu tun», без ручних подач і нотаток агента
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

    import gmail_rest  # noqa: E402 — читає конфіг клієнта під час імпорту
    sent = gmail_rest.api(c.send_from, "POST", "messages/send",
                          json={"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode()})
    rep.clear_alerts(var_run)
    rep.mark_todo_sent(todo_files)
    print(f"[summary] sent: {sent.get('id')} alerts={len(alerts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
