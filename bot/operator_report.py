"""Зведений звіт оператору: один лист увечері замість листа після кожного рану.

Нічний ран нічого не шле оператору. Його звіт лягає у файл
`var/run/operator_report_<дата>.html`, алерти обгортки стають у чергу
`var/run/alerts_pending.jsonl`. Після вечірнього рану все це разом із
лічильниками з трекера йде одним листом (scripts/server/operator_summary.py).
Той самий модуль дає текстовий стан для ранкової перевірки з консолі.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from bot.textclean import strip_invisible

ALERTS_FILE = "alerts_pending.jsonl"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_URL_RE = re.compile(r"https?://[^\s|)]+")
_SUBMITTED_ON_RE = re.compile(r"(?:sent|portal_submitted(?:\s*\([^)]*\))?)\s+(\d{4}-\d{2}-\d{2})")

SUBMITTED = ("sent", "portal_submitted", "confirmed", "interview", "offer")
MANUAL = ("manual", "ready", "🔄")
REJECTED = ("rejected", "ghosted", "declined")  # подано, але відповідь негативна або тиша
CLOSED = ("skipped", "withdrew", "hired")


@dataclass
class Row:
    day: str
    title: str
    company: str
    status: str

    @property
    def kind(self) -> str:
        """submitted | interview | rejected | manual | closed | other — за першим словом статусу."""
        # Агенти й оператор пишуть «✅ **confirmed 16.10**», «🔴 rejected»: емодзі й
        # жирний шрифт перед словом статусу не мають ховати подачу від звіту.
        low = re.sub(r"^\W+", "", self.status).lower()
        if low.startswith("interview") or low.startswith("offer"):
            return "interview"
        if low.startswith(SUBMITTED):
            return "submitted"
        if self.status.startswith(MANUAL) or low.startswith(MANUAL):
            return "manual"
        if low.startswith(REJECTED):
            return "rejected"
        if low.startswith(CLOSED):
            return "closed"
        return "other"

    @property
    def submitted_on(self) -> str:
        """День подачі: дата після sent/portal_submitted, інакше дата рядка."""
        m = _SUBMITTED_ON_RE.search(self.status)
        return m.group(1) if m else self.day

    @property
    def url(self) -> str:
        m = _URL_RE.search(self.status)
        return m.group(0).rstrip(".,;") if m else ""


def parse_rows(tracker_path: Path) -> list[Row]:
    """Рядки трекера в обох форматах таблиць; статус у legacy-рядку — остання клітинка."""
    rows: list[Row] = []
    if not Path(tracker_path).exists():
        return rows
    for line in strip_invisible(Path(tracker_path).read_text(encoding="utf-8")).splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if len(cells) >= 4 and _DATE_RE.match(cells[0]):
            # legacy: date | title | [source |] company, location | status
            rows.append(Row(cells[0], cells[1], cells[-2].split(",")[0].strip(), cells[-1]))
        elif len(cells) >= 7 and _DATE_RE.match(cells[1]):
            # основна: # | Date | Company | Position | Source | Salary | Status | ...
            notes = cells[9] if len(cells) > 9 else ""
            rows.append(Row(cells[1], cells[3], cells[2], f"{cells[6]} {notes}".strip()))
    return rows


def summarize(rows: list[Row], today: str) -> dict:
    sent = [r for r in rows if r.kind in ("submitted", "interview", "rejected")]
    return {
        "submitted_total": len(sent),
        "rejected": [r for r in rows if r.kind == "rejected"],
        "submitted_today": [r for r in sent if r.submitted_on == today],
        "interviews": [r for r in rows if r.kind == "interview"],
        "manual_open": [r for r in rows if r.kind == "manual"],
    }


# ── черга алертів ────────────────────────────────────────────────────────────

def queue_alert(var_run: Path, run: str, message: str, log_tail: str = "") -> None:
    var_run.mkdir(parents=True, exist_ok=True)
    rec = {"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "run": run,
           "message": message, "log_tail": log_tail[-1500:]}
    with (var_run / ALERTS_FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_alerts(var_run: Path) -> list[dict]:
    """Алерти з черги; однакова причина з кількох ранів лишається один раз, найсвіжіша."""
    path = var_run / ALERTS_FILE
    if not path.exists():
        return []
    latest: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        latest[rec.get("message", "")] = rec
    return list(latest.values())


def clear_alerts(var_run: Path) -> None:
    (var_run / ALERTS_FILE).unlink(missing_ok=True)


def run_fragments(var_run: Path, prefix: str, day: str) -> list[str]:
    """HTML-звіти агента за день: operator_report_<day>.html і додаткові _HHMM."""
    return [p.read_text(encoding="utf-8") for p in sorted(var_run.glob(f"{prefix}_{day}*.html"))]


# ── лист і текстовий стан ────────────────────────────────────────────────────

def _day_de(day: str) -> str:
    return date.fromisoformat(day).strftime("%d.%m.%Y")


def subject(first_name: str, day: str, s: dict, alerts: list[dict]) -> str:
    warn = f", {len(alerts)} Hinweis{'e' if len(alerts) != 1 else ''}" if alerts else ""
    return (f"[{first_name}: Tagesbericht] {_day_de(day)}: {len(s['submitted_today'])} beworben, "
            f"{len(s['manual_open'])} manuell offen{warn}")


def build_html(full_name: str, day: str, s: dict, alerts: list[dict],
               morning: list[str], evening: list[str]) -> str:
    e = html.escape
    cell = ('<td style="padding:12px 4px;{b}"><div style="font-size:22px;font-weight:700;">{n}</div>'
            '<div style="font-size:12px;color:#59636e;">{t}</div></td>')
    border = "border-left:1px solid #d1d9e0;"
    out = [
        '<div style="max-width:680px;margin:0 auto;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#1f2328;">',
        '<div style="background:#24292f;color:#fff;border-radius:8px 8px 0 0;padding:16px 20px;">'
        f'<div style="font-size:18px;font-weight:700;">Tagesbericht {_day_de(day)}</div>'
        f'<div style="font-size:13px;opacity:.85;margin-top:2px;">Automatische Jobsuche · {e(full_name)}</div></div>',
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border:1px solid #d1d9e0;border-top:0;text-align:center;"><tr>',
        cell.format(b="", n=len(s["submitted_today"]), t="heute beworben"),
        cell.format(b=border, n=s["submitted_total"], t="insgesamt"),
        cell.format(b=border, n=len(s["manual_open"]), t="manuell offen"),
        cell.format(b=border, n=len(s["interviews"]), t="Einladungen"),
        cell.format(b=border, n=len(s["rejected"]), t="Absagen"),
        "</tr></table>",
    ]
    if alerts:
        items = "".join(
            f'<div style="padding:4px 0;font-size:13px;"><b>{e(a.get("message", ""))}</b>'
            f'<span style="color:#59636e;"> · {e(a.get("run", ""))}, {e(a.get("at", ""))}</span></div>'
            for a in alerts)
        out.append('<div style="border:1px solid #d1d9e0;border-top:0;border-left:4px solid #d4a72c;'
                   f'background:#fff8c5;padding:12px 20px;"><div style="font-weight:700;margin-bottom:4px;">'
                   f'Hinweise</div>{items}</div>')
    if s["interviews"]:
        items = "".join(f'<div style="padding:3px 0;font-size:13px;"><b>{e(r.company)}</b>: {e(r.title)}</div>'
                        for r in s["interviews"])
        out.append('<div style="border:1px solid #d1d9e0;border-top:0;border-left:4px solid #1a7f37;'
                   f'background:#f0fff4;padding:12px 20px;"><div style="font-weight:700;margin-bottom:4px;">'
                   f'Einladungen</div>{items}</div>')
    for title, parts in (("Nachtlauf", morning), ("Abendlauf", evening)):
        for part in parts:
            out.append('<div style="border:1px solid #d1d9e0;border-top:0;padding:14px 20px;">'
                       f'<div style="font-size:12px;color:#59636e;text-transform:uppercase;letter-spacing:.04em;'
                       f'margin-bottom:8px;">{title}</div>{part}</div>')
    if s["manual_open"]:
        lines = []
        for r in s["manual_open"]:
            link = f' · <a href="{e(r.url)}" style="color:#0969da;">öffnen</a>' if r.url else ""
            lines.append(f'<div style="padding:3px 0;border-top:1px solid #f0f3f6;font-size:13px;">'
                         f'<b>{e(r.company)}</b>: {e(r.title)}<span style="color:#59636e;"> · {e(r.day)}</span>{link}</div>')
        out.append('<div style="border:1px solid #d1d9e0;border-top:0;padding:14px 20px;">'
                   f'<div style="font-size:14px;font-weight:700;margin-bottom:8px;">Manuell offen ({len(s["manual_open"])})</div>'
                   + "".join(lines) + "</div>")
    out.append('<div style="border:1px solid #d1d9e0;border-top:0;border-radius:0 0 8px 8px;padding:10px 20px;'
               'font-size:11px;color:#59636e;">Ein Bericht pro Tag, nach dem Abendlauf. Nachts geht keine Mail raus.</div></div>')
    return "".join(out)


def build_text(day: str, s: dict, alerts: list[dict]) -> str:
    lines = [f"Stand {_day_de(day)}",
             f"  heute beworben:  {len(s['submitted_today'])}",
             f"  insgesamt:       {s['submitted_total']}",
             f"  manuell offen:   {len(s['manual_open'])}",
             f"  Einladungen:     {len(s['interviews'])}",
             f"  Absagen:         {len(s['rejected'])}"]
    if alerts:
        lines.append("\nHinweise:")
        lines += [f"  ! {a.get('message', '')}  ({a.get('run', '')}, {a.get('at', '')})" for a in alerts]
    if s["submitted_today"]:
        lines.append("\nHeute beworben:")
        lines += [f"  {r.company}: {r.title}" for r in s["submitted_today"]]
    if s["interviews"]:
        lines.append("\nEinladungen:")
        lines += [f"  {r.company}: {r.title}" for r in s["interviews"]]
    if s["manual_open"]:
        lines.append("\nManuell offen:")
        lines += [f"  {r.day}  {r.company}: {r.title}  {r.url}".rstrip() for r in s["manual_open"]]
    return "\n".join(lines)


# ── report_mode: candidate — оператору лише «Zu tun» ─────────────────────────

def unsent_todo(var_run: Path) -> list[Path]:
    """Нотатки агента «Zu tun» (вечірні й ранкові), ще не надіслані оператору."""
    return sorted(var_run.glob("operator_todo_*.html"))


def mark_todo_sent(paths: list[Path]) -> None:
    for p in paths:
        p.rename(p.with_name(p.name + ".sent"))


def should_send(report_mode: str, run: str | None, alerts: list[dict],
                todo: list[str], manual_open: list[Row]) -> bool:
    """Чи слати лист оператору після цього рану.

    operator: зведений Tagesbericht після вечірнього рану (run None = ручний виклик).
    candidate: «Zu tun» після ранкового рану (тоді ручні подачі вже відомі) і
    лише якщо є що робити; порожній день = жодного листа.
    none: лише збої рушія (alerts), після будь-якого рану; без них жодного листа."""
    if report_mode == "none":
        return bool(alerts)
    if report_mode == "candidate":
        return run in (None, "morning") and bool(alerts or todo or manual_open)
    return run in (None, "evening")


def alerts_subject(first_name: str, day: str, alerts: list[dict]) -> str:
    """Тема листа для report_mode none: лише кількість збоїв."""
    n = len(alerts)
    return f"[{first_name}: Hinweis] {_day_de(day)}: {n} Hinweis{'e' if n != 1 else ''}"


def todo_subject(first_name: str, day: str, s: dict, alerts: list[dict]) -> str:
    parts = [f"{len(s['manual_open'])} manuell"]
    if alerts:
        parts.append(f"{len(alerts)} Hinweis{'e' if len(alerts) != 1 else ''}")
    return f"[{first_name}: Zu tun] {_day_de(day)}: " + ", ".join(parts)


def build_todo_html(full_name: str, day: str, s: dict, alerts: list[dict], todo: list[str]) -> str:
    """Лист оператору без статистики: збої, нотатки агента, ручні подачі."""
    e = html.escape
    out = [
        '<div style="max-width:680px;margin:0 auto;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#1f2328;">',
        '<div style="background:#24292f;color:#fff;border-radius:8px 8px 0 0;padding:16px 20px;">'
        f'<div style="font-size:18px;font-weight:700;">Zu tun · {_day_de(day)}</div>'
        f'<div style="font-size:13px;opacity:.85;margin-top:2px;">Automatische Jobsuche · {e(full_name)}</div></div>',
    ]
    if alerts:
        items = "".join(
            f'<div style="padding:4px 0;font-size:13px;"><b>{e(a.get("message", ""))}</b>'
            f'<span style="color:#59636e;"> · {e(a.get("run", ""))}, {e(a.get("at", ""))}</span></div>'
            for a in alerts)
        out.append('<div style="border:1px solid #d1d9e0;border-top:0;border-left:4px solid #d4a72c;'
                   f'background:#fff8c5;padding:12px 20px;"><div style="font-weight:700;margin-bottom:4px;">'
                   f'Hinweise</div>{items}</div>')
    for part in todo:
        out.append(f'<div style="border:1px solid #d1d9e0;border-top:0;padding:14px 20px;">{part}</div>')
    if s["manual_open"]:
        lines = []
        for r in s["manual_open"]:
            link = f' · <a href="{e(r.url)}" style="color:#0969da;">öffnen</a>' if r.url else ""
            lines.append(f'<div style="padding:4px 0;border-top:1px solid #f0f3f6;font-size:13px;">'
                         f'<b>{e(r.company)}</b>: {e(r.title)}<span style="color:#59636e;"> · {e(r.day)}</span>{link}</div>')
        out.append('<div style="border:1px solid #d1d9e0;border-top:0;padding:14px 20px;">'
                   f'<div style="font-size:14px;font-weight:700;margin-bottom:8px;">Manuell bewerben ({len(s["manual_open"])})</div>'
                   + "".join(lines) + "</div>")
    out.append('<div style="border:1px solid #d1d9e0;border-top:0;border-radius:0 0 8px 8px;padding:10px 20px;'
               'font-size:11px;color:#59636e;">Nur was zu tun ist. Einladungen kommen separat als «Wichtig», '
               'den vollständigen Bericht bekommt die Kandidatin.</div></div>')
    return "".join(out)


def build_todo_text(day: str, s: dict, alerts: list[dict]) -> str:
    lines = [f"Zu tun {_day_de(day)}"]
    if alerts:
        lines.append("\nHinweise:")
        lines += [f"  ! {a.get('message', '')}  ({a.get('run', '')}, {a.get('at', '')})" for a in alerts]
    if s["manual_open"]:
        lines.append("\nManuell bewerben:")
        lines += [f"  {r.day}  {r.company}: {r.title}  {r.url}".rstrip() for r in s["manual_open"]]
    return "\n".join(lines)
