"""Читання сторінки результатів StepStone за Berufsfeld-фасетом (di/sdi).

Навіщо: email-алерт StepStone зберігає лише what/where/radius. Фасети
Berufsfeld (`di=`) і підфасети (`sdi=`) в алерт не зберігаються, тому підписка
«Administration → Assistenz + Sekretariat + Empfang» поштою неможлива: замість
неї приходять усі вакансії в радіусі. Пайплайн тягнув ліди тільки з пошти
(`bot/generators/discover.py`), отже цих 384 позицій (22 нові/добу, заміряно
25.07.2026) не бачив ніхто.

Розмітка тримається на `data-at`-атрибутах, вони стабільні між сторінками:
`job-item`, `job-item-title`, `job-item-company-name`, `job-item-location`.

Підфасети Administration: Assistenz 250803, Sekretariat 250846, Empfang 250955,
Administration generic 250882, Operativ 251262.
"""

from __future__ import annotations

import html as html_mod
import json
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

BASE = "https://www.stepstone.de"

ADMIN_ASSISTENZ_FACET = (
    "https://www.stepstone.de/jobs/in-duesseldorf?radius=50&di=Administration"
    "&sdi=250803&sdi=250846&sdi=250955"
)

_CARD_RE = re.compile(r'data-at="job-item"', re.IGNORECASE)
_JOB_ID_RE = re.compile(r"--(\d{5,})-inline\.html")


@dataclass(frozen=True)
class FacetHit:
    job_id: str
    url: str
    title: str
    company: str
    location: str


def _text_of(fragment: str) -> str:
    """Вміст вузла без тегів: StepStone кладе текст у вкладені div-и."""
    txt = re.sub(r"(?s)<[^>]+>", " ", fragment)
    return html_mod.unescape(re.sub(r"\s+", " ", txt)).strip()


def _field(card: str, data_at: str) -> str:
    """Текст елемента з data-at="<data_at>" у межах картки.

    Різати по першому `</span>` не можна: у назві компанії першим вкладеним іде
    порожній span-іконка, і вибірка виходила порожньою. Тому шукаємо парний
    закриваючий тег із підрахунком вкладеності.
    """
    m = re.search(rf'data-at="{re.escape(data_at)}"', card)
    if not m:
        return ""
    open_start = card.rfind("<", 0, m.start())
    tag_m = re.match(r"<([a-zA-Z][\w-]*)", card[open_start:])
    open_end = card.find(">", m.end())
    if open_start == -1 or not tag_m or open_end == -1:
        return ""
    tag = tag_m.group(1)
    depth = 1
    pos = open_end + 1
    token = re.compile(rf"<(/?){tag}\b", re.IGNORECASE)
    while depth and (t := token.search(card, pos)):
        depth += -1 if t.group(1) else 1
        pos = t.end()
    end = card.rfind("<", open_end + 1, pos) if depth == 0 else min(len(card), open_end + 800)
    return _text_of(card[open_end + 1:end])


def _split_cards(page_html: str) -> list[str]:
    """Розбити сторінку на HTML-фрагменти карток вакансій."""
    bounds = [m.start() for m in _CARD_RE.finditer(page_html)]
    if not bounds:
        return []
    bounds.append(len(page_html))
    return [page_html[bounds[i]:bounds[i + 1]] for i in range(len(bounds) - 1)]


def parse_results(page_html: str) -> list[FacetHit]:
    """Витягти вакансії зі сторінки результатів. Порядок як на сторінці."""
    hits: list[FacetHit] = []
    seen_ids: set[str] = set()
    for card in _split_cards(page_html or ""):
        m = re.search(r'data-at="job-item-title"[^>]*', card)
        href_m = re.search(r'href="(/stellenangebote--[^"]+)"', card)
        if not href_m:
            continue
        href = html_mod.unescape(href_m.group(1))
        id_m = _JOB_ID_RE.search(href)
        if not id_m:
            continue
        job_id = id_m.group(1)
        if job_id in seen_ids:
            continue
        seen_ids.add(job_id)
        hits.append(
            FacetHit(
                job_id=job_id,
                url=BASE + href,
                title=_field(card, "job-item-title"),
                company=_field(card, "job-item-company-name"),
                location=_field(card, "job-item-location"),
            )
        )
    return hits


def build_page_url(facet_url: str, page: int) -> str:
    """URL сторінки N із збереженням усіх фасетів (di/sdi/radius)."""
    if page < 1:
        raise ValueError(f"page мусить бути >= 1, отримано {page}")
    parts = urlsplit(facet_url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "page"]
    if page > 1:
        query.append(("page", str(page)))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def new_hits(hits: Iterable[FacetHit], seen: set[str]) -> list[FacetHit]:
    """Лише невідомі вакансії; дедуп і проти `seen`, і в межах партії."""
    out: list[FacetHit] = []
    batch: set[str] = set()
    for h in hits:
        if h.job_id in seen or h.job_id in batch:
            continue
        batch.add(h.job_id)
        out.append(h)
    return out


def scan_facet(
    facet_url: str = ADMIN_ASSISTENZ_FACET,
    max_pages: int = 3,
    fetch: Optional[Callable[[str], str]] = None,
    seen: Optional[set[str]] = None,
) -> list[FacetHit]:
    """Пройти сторінки фасета і віддати нові вакансії.

    Зупиняється, коли сторінка порожня або не принесла жодного нового id:
    StepStone за межею останньої сторінки віддає ту саму сторінку, тому лічильник
    сторінок сам по собі не є умовою виходу.
    """
    if max_pages < 1:
        raise ValueError(f"max_pages мусить бути >= 1, отримано {max_pages}")
    if fetch is None:
        fetch = _browser_fetch

    known = set(seen or ())
    collected: list[FacetHit] = []
    for page in range(1, max_pages + 1):
        page_html = fetch(build_page_url(facet_url, page))
        hits = parse_results(page_html)
        if not hits:
            break
        fresh = new_hits(hits, known | {h.job_id for h in collected})
        if not fresh:
            break
        collected.extend(fresh)
    return collected


def load_seen(path) -> set[str]:
    """Прочитати вже побачені job_id. Битий або відсутній файл = порожньо."""
    from pathlib import Path as _Path

    p = _Path(path)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return set()
    ids = data.get("job_ids", data) if isinstance(data, dict) else data
    return {str(x) for x in ids} if isinstance(ids, list) else set()


def save_seen(path, job_ids: Iterable[str]) -> int:
    """Долити id у стан (об'єднання з наявним). Повертає підсумковий розмір."""
    from pathlib import Path as _Path

    p = _Path(path)
    merged = load_seen(p) | {str(x) for x in job_ids}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"job_ids": sorted(merged)}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return len(merged)


def _browser_fetch(url: str) -> str:
    """Реальний фетч: StepStone під httpx віддає antibot-сторінку.

    Потрібен саме .html: .text у PageFetch — це innerText, у ньому немає
    data-at-атрибутів, і парсер повертав нуль позицій.
    """
    import time

    from bot.scrapers.fetch import fetch_page

    # StepStone інколи рве з'єднання (ERR_HTTP2_PROTOCOL_ERROR, 08.10.2026: усі
    # сторінки фасету за один ран). За хвилину той самий URL віддається нормально,
    # тому три спроби з паузою замість порожнього результату.
    last: Exception | None = None
    for attempt in range(3):
        try:
            return fetch_page(url, use_playwright=True).html
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < 2:
                time.sleep(8 * (attempt + 1))
    raise last if last else RuntimeError("fetch_page без результату")
