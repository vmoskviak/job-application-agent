"""Джерело HN "Ask HN: Who is hiring?" через Algolia API.

Детермінований, без LLM: знаходить поточну гілку, бере лише прямі коментарі
(це і є вакансії), парсить заголовок `Company | Role | Location | ...` і
віддає `JobData`. Побачені id пам'ятає у JSON-файлі, щоб щодня віддавати
лише нові коментарі. Чисті функції не залежать від клієнта.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from bot.generators.intake import JobData, extract_email
from bot.market import description_limit

SEARCH_URL = ("https://hn.algolia.com/api/v1/search_by_date"
              "?tags=story,author_whoishiring")
ITEM_URL = "https://hn.algolia.com/api/v1/items/{id}"
HN_ITEM = "https://news.ycombinator.com/item?id={id}"
TITLE_RE = re.compile(r"^Ask HN: Who is hiring\? \((\w+) (\d{4})\)$")
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")

# Остання помилка останнього collect(); порожній рядок = без помилок.
last_error: str = ""
# Остання знайдена гілка (для діагностики в CLI).
last_thread: Optional["Thread"] = None


class Thread(NamedTuple):
    story_id: int
    title: str
    month: str  # "<Month> <Year>" з заголовка


def _get_json(url: str) -> dict:
    import httpx
    resp = httpx.get(url, timeout=20)
    resp.raise_for_status()
    return resp.json()


def find_current_thread(fetch: Callable[[str], dict]) -> Optional[Thread]:
    """Найновіша гілка "Who is hiring?" (не "Who wants to be hired?")."""
    hits = fetch(SEARCH_URL).get("hits", [])
    best = None
    for h in hits:
        m = TITLE_RE.match(h.get("title") or "")
        if not m or h.get("author", "whoishiring") != "whoishiring":
            continue
        ts = h.get("created_at_i", 0)
        if best is None or ts > best[0]:
            best = (ts, Thread(int(h["objectID"]), h["title"],
                               f"{m.group(1)} {m.group(2)}"))
    return best[1] if best else None


_HREF_RE = re.compile(r"""<a\s[^>]*?href\s*=\s*["']([^"']+)["']""", re.I)
_NEG_REMOTE_RE = re.compile(
    r"\b(?:no|not|non)[\s-]+remote\b|\bremote\b[^|\n]{0,15}\b(?:not|no)\b", re.I)


def _hrefs(raw: str) -> list[str]:
    return [html.unescape(h) for h in _HREF_RE.findall(raw or "")]


def _decode(raw: str) -> str:
    text = re.sub(r"<\s*(?:p|br)\s*/?\s*>", "\n", raw or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _clean_url(u: str) -> str:
    return u.rstrip(".,;:")


def parse_comment(comment: dict) -> JobData:
    """Розбирає коментар-вакансію в JobData."""
    text = _decode(comment.get("text", ""))
    header = text.split("\n", 1)[0]
    parts = [p.strip() for p in header.split("|")]
    if len(parts) > 1:
        company = parts[0]
        title = parts[1]
        location = parts[2] if len(parts) > 2 else ""
    else:
        company = " ".join(header.split()[:6])
        title = ""
        location = ""
    if (re.search(r"\bREMOTE\b", header, re.I)
            and not _NEG_REMOTE_RE.search(header)
            and "remote" not in location.lower()):
        location = (location + " (Remote)").strip()
    # Той самий шаблон, що й discover.extract_urls, без імпорту клієнтського конфігу.
    # href з HTML надійніші за скорочений HN текст посилання.
    found = _hrefs(comment.get("text", "")) + _URL_RE.findall(text)
    urls = [_clean_url(u) for u in dict.fromkeys(found)
            if u.startswith(("http://", "https://"))]
    return JobData(
        title=title, company=company, location=location, description=text,
        email=extract_email(text), application_url=urls[0] if urls else None,
        source_url=HN_ITEM.format(id=comment["id"]), is_portal=False)


def _fallback_title(description: str) -> str:
    """Перший рядок коментаря (до 80 символів) для вакансії без `|`, щоб
    фільтр оцінив її, а не відкинув як scrape_failed_title."""
    return description.split("\n", 1)[0].strip()[:80].strip()


def _load_seen(path: Path) -> set:
    try:
        return set(json.loads(path.read_text()))
    except (OSError, ValueError):
        return set()


def collect(thread_fetch: Callable[[str], dict],
            item_fetch: Callable[[str], dict],
            state_path: Path) -> tuple[list[JobData], set[int]]:
    """Нові прямі коментарі й їхні id; стан НЕ пишеться (див. commit_seen)."""
    global last_error, last_thread
    last_error = ""
    last_thread = None
    try:
        thread = find_current_thread(thread_fetch)
        if thread is None:
            last_error = "гілку Who is hiring не знайдено"
            return [], set()
        last_thread = thread
        tree = item_fetch(ITEM_URL.format(id=thread.story_id))
    except Exception as exc:  # мережа/JSON: не валимо ранок
        last_error = f"{type(exc).__name__}: {exc}"
        return [], set()
    seen = _load_seen(state_path)
    jobs, new_ids = [], set()
    for ch in tree.get("children", []):
        cid = ch.get("id")
        if cid is None or cid in seen or not ch.get("text"):
            continue
        jobs.append(parse_comment(ch))
        new_ids.add(cid)
    return jobs, new_ids


def commit_seen(state_path: Path, ids: set[int]) -> None:
    """Атомарно дописує id до стану (тимчасовий файл + os.replace)."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_name(state_path.name + ".tmp")
    tmp.write_text(json.dumps(sorted(_load_seen(state_path) | set(ids))))
    os.replace(tmp, state_path)


def main(argv: Optional[list[str]] = None) -> int:
    from bot import client
    from bot.generators.discover import score_job
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--state")
    args = ap.parse_args(argv)
    state = Path(args.state) if args.state else \
        client.active().var / "state" / "hn_seen.json"
    jobs, new_ids = collect(_get_json, _get_json, state)
    desc_limit = description_limit(client.active().market)
    rows = []
    for j in jobs:
        title = j.title or _fallback_title(j.description)
        score, cat = score_job(title, j.description)
        rows.append({
            "score": score, "category": cat, "title": title,
            "company": j.company, "location": j.location, "email": j.email,
            "application_url": j.application_url, "source_url": j.source_url,
            "description": j.description[:desc_limit], "liveness": "live",
            "intake_method": "httpx", "source": "hn_whoishiring",
            "is_portal": False})
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    if not last_error:
        commit_seen(state, new_ids)  # лише після запису дайджесту
    tid = last_thread.story_id if last_thread else "-"
    print(f"hn_whoishiring: гілка {tid}, {len(rows)} нових", file=sys.stderr)
    if last_error:
        print(f"hn_whoishiring: помилка: {last_error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
