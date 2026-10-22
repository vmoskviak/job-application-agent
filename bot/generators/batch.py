"""Batch render: take a list of {job, tailored} dicts, render all, report."""

from __future__ import annotations
import json
import sys
from pathlib import Path


def load_batch_file(path: Path) -> list[dict]:
    """Load batch JSON file. Format: [{job: {...}, tailored: {...}}, ...]"""
    return json.loads(path.read_text(encoding="utf-8"))


def split_email_and_portal(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split batch items into email-sendable vs portal-only.

    Компанії з EMAIL_DEAD_COMPANIES (Beispielwerk, Alpenwerk — вкладення з пошти
    видаляють, DSGVO) втрачають email прямо в job-dict: далі той самий dict іде
    в render_from_data, і без цієї мутації там записався б email_draft.json."""
    from bot.filters.rules import email_channel_blocked

    email_items, portal_items = [], []
    for it in items:
        job = it["job"]
        if job.get("email") and email_channel_blocked(job.get("company", "")):
            print(f"[batch] ⛔ {job.get('company')}: email-канал мертвий "
                  f"(портал-only, DSGVO) — {job['email']} ігнорується")
            job["email"] = None
        (email_items if job.get("email") else portal_items).append(it)
    return email_items, portal_items


def batch_summary_report(
    items: list[dict],
    ready: list[str],
    portal: list[str],
) -> str:
    """Звіт рендеру. Рендер нічого не відправляє — відправка це окремий крок,
    і формулювання мусить це показувати (інцидент 27.07: звіт і трекер писали
    «Email sent» ще до відправки)."""
    lines = [
        "=== Batch Render Report ===",
        f"Всього: {len(items)} | email-пакети готові: {len(ready)} | портальні: {len(portal)}",
        "",
        "📧 Email-пакети готові до відправки (email_draft.json):",
    ]
    for name in ready:
        lines.append(f"   • {name}")
    lines.append("")
    lines.append("🌐 Portal — подати через apply_queue або вручну:")
    for name in portal:
        lines.append(f"   • {name}")
    return "\n".join(lines)


def render_all(batch_file: Path, applications_dir: Path) -> None:
    """Render all items in batch file. Prints report when done."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from new_app import render_from_data

    items = load_batch_file(batch_file)
    email_items, portal_items = split_email_and_portal(items)

    print(f"[batch] {len(items)} заявок: {len(email_items)} email + {len(portal_items)} portal")
    ready_names: list[str] = []

    for i, item in enumerate(items, 1):
        company = item["job"]["company"]
        title   = item["job"]["title"]
        print(f"\n[{i}/{len(items)}] {company} — {title}")
        app_dir = render_from_data(item["job"], item["tailored"])
        if app_dir and item["job"].get("email"):
            ready_names.append(company)

    portal_names = [it["job"]["company"] for it in portal_items]
    print("\n" + batch_summary_report(items, ready_names, portal_names))
