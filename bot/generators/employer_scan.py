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
    # контрактний ринок: ATS англомовних роботодавців (сповіщення з окремих доменів)
    "greenhouse-mail.io",
    "ashbyhq.com",
    "workablemail.com",
    "lever.co",
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


# Скільки листів максимум читає прочісування jobs-інбоксу (sweep_query).
SWEEP_LIMIT = 200


def sweep_query(account: str, exclude_senders: Iterable[str], days: int = 7) -> str | None:
    """Прочісування всього інбоксу jobs без фільтра за відправником.

    Ловить роботодавця, чий домен ніде не записаний (подача через дошку,
    відповідь з корпоративного домену). Скринька jobs — під пошук, тож без
    алертів листів тут небагато; main особиста, і там такий запит тягнув би все.
    Що з цього звіт, вирішує keep_sweep_item: лише лист із класом відповіді,
    який зіставився з компанією з трекера.
    """
    if account != "jobs":
        return None
    negatives = " ".join(f"-from:{s}" for s in exclude_senders if s)
    return f"newer_than:{days}d in:inbox {negatives}".strip()


def keep_sweep_item(item: dict) -> bool:
    """Лист із прочісування — відповідь роботодавця, лише якщо клас відомий
    (відмова/співбесіда/підтвердження/запит) і компанія є в трекері.
    Розсилка з «leider» без компанії з трекера сюди не проходить."""
    return bool(item.get("company")) and item.get("classification") not in (None, "", "unknown")


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


# ── Домени відправників, з яких може прийти відповідь роботодавця ─────────────
# tracker_domains вище бачить лише legacy-рядки «Email sent» з колонкою email,
# якої в нинішніх трекерах нема, тож на реальних трекерах повертає порожньо.
# Роботодавець, що відповідає з власного домену (не ATS), знаходився лише
# ручним переглядом (22.10.2026: відмови 08.09 і 18.09, знайдені через місяць).
# Тому домени збираються з усього, що відомо про подачі: адреси й URL у
# трекері, email/URL у пакетах заявок, плюс ручний список у accounts.yaml.

# Не роботодавці: дошки, агрегатори, фриланс-платформи, скорочувачі. З їхніх
# доменів ідуть алерти, а не відповіді, і from:<дошка> затопив би скан.
NON_EMPLOYER_DOMAINS: frozenset[str] = frozenset({
    "stepstone.de", "indeed.com", "indeed.de", "linkedin.com", "lnkd.in",
    "xing.com", "arbeitsagentur.de", "interamt.de", "talent.com", "jobijoba.com",
    "jobijoba.de", "glassdoor.com", "glassdoor.de", "get-in.de", "kleinanzeigen.de",
    "hokify.de", "jobware.de", "monster.de", "kununu.com", "meinestadt.de",
    "jobs.de", "gulp.de", "freelancermap.de", "freelance.de", "malt.de",
    "upwork.com", "ycombinator.com", "google.com", "goo.gl", "bit.ly",
    "github.com", "rp-online.de", "stellenanzeigen.de", "yourjob.de",
})

# Поштові сервіси: from:<домен> означав би всю пошту, тож тут лише повна адреса.
FREEMAIL_DOMAINS: frozenset[str] = frozenset({
    "gmail.com", "googlemail.com", "web.de", "gmx.de", "gmx.net", "gmx.at",
    "outlook.com", "outlook.de", "hotmail.com", "hotmail.de", "live.com", "live.de",
    "yahoo.com", "yahoo.de", "t-online.de", "icloud.com", "me.com", "posteo.de",
    "mail.de", "freenet.de", "aol.com", "proton.me", "protonmail.com",
})

_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gv", "or", "gov", "edu"}
_EMAIL_RE = r"[A-Za-z0-9._%+\-]+@([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+)"
_URL_HOST_RE = r"https?://([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+)"


def registrable_domain(host: str) -> str:
    """karriere.beispiel.de → beispiel.de; beispiel.co.uk лишається як є."""
    labels = host.lower().strip(".").split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def senders_from_text(text: str, own: Iterable[str] = ()) -> list[str]:
    """Домени (для freemail — повні адреси) з email-адрес і URL у тексті."""
    import re

    own_l = {o.lower() for o in own if o}
    out: list[str] = []

    def add(s: str) -> None:
        if s and s not in out:
            out.append(s)

    for m in re.finditer(_EMAIL_RE, text):
        addr, dom = m.group(0).lower().strip("."), registrable_domain(m.group(1))
        if addr in own_l or dom in NON_EMPLOYER_DOMAINS:
            continue
        add(addr if dom in FREEMAIL_DOMAINS else dom)
    for m in re.finditer(_URL_HOST_RE, text):
        dom = registrable_domain(m.group(1))
        if dom not in NON_EMPLOYER_DOMAINS and dom not in FREEMAIL_DOMAINS:
            add(dom)
    return out


def application_texts(applications_dir) -> list[str]:
    """email/URL-поля з пакетів заявок: email_draft.json і generate.py."""
    import re
    from pathlib import Path as _Path

    root = _Path(applications_dir) if applications_dir else None
    if root is None or not root.is_dir():
        return []
    texts: list[str] = []
    for f in sorted(root.glob("*/*/email_draft.json")):
        m = re.search(r'"to":\s*"([^"]*)"', f.read_text(encoding="utf-8", errors="replace"))
        if m:
            texts.append(m.group(1))
    field_re = re.compile(r"\b(?:email|application_url|source_url)=(['\"])(.*?)\1")
    for f in sorted(root.glob("*/*/generate.py")):
        texts += [m.group(2) for m in field_re.finditer(
            f.read_text(encoding="utf-8", errors="replace"))]
    return texts


def employer_senders(tracker_path, applications_dir=None, extra: Iterable[str] = (),
                     own: Iterable[str] = ()) -> list[str]:
    """Усі відомі домени/адреси роботодавців: accounts.yaml → employer_senders,
    трекер, пакети заявок. Домени ATS додає build_employer_queries сам."""
    from pathlib import Path as _Path

    own = list(own)
    out: list[str] = []
    for s in extra or ():
        s = str(s).strip().lstrip("@").lower()
        if s and s not in out:
            out.append(s)
    p = _Path(tracker_path)
    sources = [p.read_text(encoding="utf-8")] if p.exists() else []
    sources += application_texts(applications_dir)
    for text in sources:
        for s in senders_from_text(text, own):
            if s not in out and s not in ATS_DOMAINS:
                out.append(s)
    return out
