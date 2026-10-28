"""Черга лідів на відправку: var/state/queue.jsonl.

Запис на лід: queued (можна слати), held (перевірка заблокувала, лежить
мовчки), sending, handoff (форму доробляє оператор), sent, failed. Денний ліміт рахується з файла, не з пам'яті, тож
перезапуск процесу його не скидає. Дубль по компанії й схожій назві в межах
черги стає held, навіть з іншого дня; для platform_login дубль лише той самий
apply_url, бо агентура на платформі одна на багато різних проєктів. Кожен запис
несе platform (bot.platforms.platform_of), за нею воркер рахує ліміти платформ.
Жодного конфігу клієнта на імпорті.
"""
from __future__ import annotations

from bot.packages import Package
from bot.platforms import platform_of
from bot.state import JsonlStore

QUEUE_FILE = "queue.jsonl"
DRY_FILE = "queue_dry.jsonl"
DEFAULT_LIMITS: dict[str, int] = {"email": 5, "form": 5}
# Статуси, що є або стануть подачею; лише вони блокують той самий лід як дубль.
APPLICATION_STATUSES = ("queued", "sending", "sent", "handoff")


def limits_from(targeting: dict) -> dict[str, int]:
    """targeting.yaml → daily_limits поверх дефолтів спеки §6."""
    out = dict(DEFAULT_LIMITS)
    for k, v in (targeting.get("daily_limits") or {}).items():
        out[str(k)] = int(v)
    return out


def lead_id(date: str, slug: str) -> str:
    return f"lead-{date}-{slug}"


def _duplicate_of(records: list[dict], pkg: Package) -> bool:
    """Подача того самого ліда серед records: для platform_login той самий apply_url,
    для решти та сама компанія й схожа роль."""
    live = [r for r in records if r.get("status") in APPLICATION_STATUSES]
    if pkg.channel == "platform_login":
        return any(r.get("apply_url") == pkg.apply_url for r in live)
    from bot.ats import same_role
    from bot.filters.rules import normalize_company  # конфіг клієнта на імпорті

    key = normalize_company(pkg.lead.get("company") or "")
    title = pkg.lead.get("title") or ""
    return any(normalize_company(r.get("company") or "") == key and same_role(title, r.get("title") or "")
               for r in live)


def enqueue(store: JsonlStore, pkg: Package, reasons: list[str], date: str, created: str) -> dict:
    """Новий запис або наявний з тим самим id без змін."""
    rec_id = lead_id(date, pkg.slug)
    records = store.read()
    existing = next((r for r in records if r.get("id") == rec_id), None)
    if existing is not None and existing.get("status") in APPLICATION_STATUSES:
        return existing
    company, title = pkg.lead.get("company") or "", pkg.lead.get("title") or ""
    reasons = list(reasons)
    others = [r for r in records if r.get("id") != rec_id]
    if _duplicate_of(others, pkg):
        reasons.append("duplicate_in_queue")
    status = "held" if reasons else "queued"
    platform = platform_of(pkg.channel, pkg.apply_url)
    if existing is not None:   # held або failed: переоцінка після виправлення конфігу
        store.update(rec_id, status=status, reasons=reasons, channel=pkg.channel, apply_url=pkg.apply_url,
                     platform=platform, package_dir=str(pkg.dir), error=None)
        return store.get(rec_id)
    rec = {"id": rec_id, "kind": "lead", "slug": pkg.slug, "company": company, "title": title,
           "channel": pkg.channel, "apply_url": pkg.apply_url, "platform": platform, "package_dir": str(pkg.dir),
           "status": status, "reasons": reasons, "created": created,
           "attempts": 0, "sent_id": None, "sent_at": None}
    store.append(rec)
    return rec


def due(store: JsonlStore, channel: str) -> list[dict]:
    """Записи queued цього каналу, найстаріші першими (порядок файла)."""
    return [r for r in store.read() if r.get("status") == "queued" and r.get("channel") == channel]


def sent_today(store: JsonlStore, today: str, channel: str) -> int:
    return sum(1 for r in store.read()
               if r.get("status") == "sent" and r.get("channel") == channel
               and str(r.get("sent_at") or "").startswith(today))


def due_any(store: JsonlStore, channels: tuple[str, ...]) -> list[dict]:
    """Записи queued будь-якого з каналів, у порядку файла."""
    return [r for r in store.read() if r.get("status") == "queued" and r.get("channel") in channels]


def sent_today_platform(store: JsonlStore, today: str, platform: str) -> int:
    """Скільки записів цієї платформи стали sent сьогодні."""
    return sum(1 for r in store.read()
               if r.get("status") == "sent" and r.get("platform") == platform
               and str(r.get("sent_at") or "").startswith(today))


def mark(store: JsonlStore, rec_id: str, **fields) -> bool:
    return store.update(rec_id, **fields)


def summary(store: JsonlStore, today: str) -> dict:
    queued: dict[str, int] = {}
    sent: dict[str, int] = {}
    held = failed = handoff = 0
    for r in store.read():
        ch = r.get("channel") or "none"
        st = r.get("status")
        if st == "queued":
            queued[ch] = queued.get(ch, 0) + 1
        elif st == "held":
            held += 1
        elif st == "failed":
            failed += 1
        elif st == "handoff":
            handoff += 1
        elif st == "sent" and str(r.get("sent_at") or "").startswith(today):
            sent[ch] = sent.get(ch, 0) + 1
    return {"queued": queued, "held": held, "sent_today": sent, "failed": failed, "handoff": handoff}
