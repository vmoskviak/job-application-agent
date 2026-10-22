"""Пошук відповідей роботодавців у обох скриньках — детермінований.

Інцидент 25.07.2026: запрошення на співбесіду від Falk & Falk пролежало 29 годин
у скриньці `jobs`, ранковий ран відзвітував `responses=2 interviews=0`. Причини:

1. `build_gmail_search_query` шукала лише `from:<домен із трекера> label:Employer`.
   Відповідь прийшла з `notifications@app.softgarden.io`, а подавались на
   `recruiting@<скорочення фірми>.com` — збіг за доменом неможливий у принципі, бо через
   ATS відповідає ATS, а не компанія.
2. Список доменів різався на `domains[:20]`, тобто при 100+ заявках більшість
   роботодавців не шукалась узагалі.
3. Лейбл `Employer` існує лише в main; у jobs Gmail-фільтра з ним не було.

Тому тут: домени ATS як самостійний канал, партії замість обрізання, обидві
скриньки з trash для main (власниця може видаляти відповіді), і `exit 2`, коли є
непроброблена дія — щоб ран падав, а не «звітував успіх».
"""

from __future__ import annotations

import collections
from typing import Iterable, Optional

# Домени ATS, з яких фактично приходять відповіді (спостережено в реальній пошті).
ATS_DOMAINS: tuple[str, ...] = (
    "softgarden.io",
    "softgarden.de",
    "successfactors.com",
    "rexx-hr.com",
    "rexx-recruitment.com",
    "jobs2web.com",
    "dvinci-hr.com",
    "d-vinci.de",
    "personio.de",
    "myworkday.com",
    "avature.net",
    "greenhouse.io",
    "smartrecruiters.com",
    "recruitee.com",
    "join.com",
    "instaffo.com",
)

# Класи, які потребують людини: співбесіда і запит документів/даних.
ACTION_CLASSES: frozenset[str] = frozenset({"interview", "info_request"})

_SCOPES = {
    # jobs: інбокс + лейбл; сюди ж падають ATS-листи, які не мають лейбла
    "jobs": ("in:inbox", "label:Employer"),
    # main: інбокс + кошик, бо власниця може видаляти відповіді
    "main": ("in:inbox", "in:trash"),
}


def scan_accounts(configured: Iterable[str], requested: str = "all") -> list[str]:
    """Які скриньки сканувати.

    `all` = усі налаштовані з відомих scope'ів (jobs, main). Явно названа
    скринька, якої нема в accounts.yaml, — помилка, а не тихий пропуск.
    """
    have = [a for a in _SCOPES if a in set(configured)]
    if requested == "all":
        if not have:
            raise ValueError("у accounts.yaml немає жодної скриньки jobs/main")
        return have
    if requested == "both":          # сумісність зі старим прапором
        return have
    if requested not in _SCOPES:
        raise ValueError(f"невідомий акаунт {requested!r}, очікую jobs|main|all")
    if requested not in have:
        raise ValueError(f"скриньки {requested!r} нема в accounts.yaml цього клієнта")
    return [requested]


def build_employer_queries(
    account: str,
    domains: Iterable[str],
    exclude_senders: Iterable[str],
    days: int = 7,
    chunk: int = 20,
) -> list[str]:
    """Gmail-запити для пошуку відповідей роботодавців.

    Один запит на (партія доменів × scope). Домени з трекера й домени ATS
    об'єднуються, дублікати прибираються, обрізання немає — партії.
    """
    if account not in _SCOPES:
        raise ValueError(f"невідомий акаунт {account!r}, очікую jobs|main")
    if chunk < 1:
        raise ValueError("chunk мусить бути >= 1")

    all_domains: list[str] = []
    for d in list(domains) + list(ATS_DOMAINS):
        d = d.strip().lstrip("@")
        if d and d not in all_domains:
            all_domains.append(d)

    negatives = " ".join(f"-from:{s}" for s in exclude_senders if s)
    window = f"newer_than:{days}d"

    queries: list[str] = []
    for scope in _SCOPES[account]:
        for i in range(0, len(all_domains), chunk):
            batch = all_domains[i:i + chunk]
            senders = " OR ".join(f"from:{d}" for d in batch)
            q = f"{window} {scope} ({senders})"
            if negatives:
                q = f"{q} {negatives}"
            queries.append(q)
    return queries


def needs_action(classification: str) -> bool:
    """Чи потрібна людина: співбесіда або запит інформації."""
    return classification in ACTION_CLASSES


def summarize(items: list[dict]) -> dict:
    """Порахувати класи і зібрати те, що чекає на людину.

    `answered=True` в елементі означає, що відповідь уже надіслана (є наш лист у
    треді), тому дія закрита і будити нікого не треба.
    """
    counts = collections.Counter(i.get("classification", "unknown") for i in items)
    action = [
        i for i in items
        if needs_action(i.get("classification", "")) and not i.get("answered")
    ]
    return {
        "counts": dict(counts),
        "action_needed": action,
        "total": len(items),
    }


# Статуси, які означають «дію вже зафіксовано»: далі веде людина за трекером.
_CLOSED_STATUSES = ("interview", "offer", "rejected", "hired", "declined")


def tracker_status_by_company(tracker_path) -> dict[str, str]:
    """Статус останнього рядка трекера по компанії (обидва формати таблиць)."""
    import re
    from pathlib import Path as _Path

    p = _Path(tracker_path)
    if not p.exists():
        return {}
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}")
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 5:
            continue
        if date_re.match(cells[1]):
            # legacy: | date | title | company, location | status |
            out[cells[3].split(",")[0].strip()] = cells[4].lower()
        elif len(cells) >= 8 and date_re.match(cells[2]):
            # основна: | # | Date | Company | Position | Source | Salary | Status | ...
            out[cells[3]] = cells[7].lower()
    return out


def tracker_known_msgids(tracker_path) -> frozenset[str]:
    """Msg-ID, які вже записані в трекер: ці листи хтось прочитав і вніс."""
    import re
    from pathlib import Path as _Path

    p = _Path(tracker_path)
    if not p.exists():
        return frozenset()
    return frozenset(re.findall(r"Msg\s+([0-9a-f]{12,})", p.read_text(encoding="utf-8")))


def is_closed(item: dict, statuses: dict[str, str],
              known_msgids: frozenset[str] = frozenset()) -> bool:
    """Чи дія вже закрита: є наша відповідь у треді або трекер це вже знає.

    Лист із Msg-ID у трекері закритий, навіть коли відправника не вдалося
    зіставити з компанією (Personio, SmartRecruiters шлють зі свого домену).
    """
    if item.get("thread_answered") or item.get("answered"):
        return True
    if item.get("message_id") and item["message_id"] in known_msgids:
        return True
    company = item.get("company")
    if not company:
        return False
    status = (statuses.get(company) or "").lower()
    return any(s in status for s in _CLOSED_STATUSES)


def exit_code(summary: dict) -> int:
    """2 — є незакрита дія (співбесіда/запит), 0 — нічого не висить."""
    return 2 if summary.get("action_needed") else 0


def tracker_domains(tracker_path, parse_fn: Optional[callable] = None) -> list[str]:
    """Домени роботодавців із трекера (з рядків, де є email)."""
    from bot.generators.responses import extract_domain, parse_tracker_sent

    parse = parse_fn or parse_tracker_sent
    out: list[str] = []
    for e in parse(tracker_path):
        d = extract_domain(e.get("email"))
        if d and d not in out:
            out.append(d)
    return out
