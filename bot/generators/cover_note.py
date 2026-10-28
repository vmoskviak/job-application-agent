"""Збірка короткого листа (cover note) для контрактного ринку.

Чисті функції: LLM не викликається, тіло листа приходить від агента.
Мова й ставка визначаються детерміновано (специфікація R1, R3).

CLI для вечірнього промпта контрактного ринку:
    python -m bot.generators.cover_note --lang-only filtered.json
        друкує JSON {slug: мова} для selected, щоб агент писав тіло потрібною мовою
    python -m bot.generators.cover_note --assemble filtered.json bodies.json
        bodies.json = {slug: тіло}; пише var/run/notes_<дата>/<slug>.txt і друкує шляхи
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date as _date
from pathlib import Path
from typing import Literal, NamedTuple
from urllib.parse import urlsplit

from bot import market
from bot.generators.engagement import detect_lang

_USD_RE = re.compile(r"\$\s?\d|USD|US-based|United States")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Тире як сполучник (— і –) у листі заборонене: замінюємо комою. Діапазон
# цифр «2019–2023» стає дефісом, тире на початку рядка просто прибирається.
# Діапазони «90–100», «€90 – 100/h», «€90–€100»: тире стає дефісом, не комою.
_RANGE_DASH_RE = re.compile(r"(?<=\d) ?\u2013 ?(?=[€$]?\d)")
_LINE_DASH_RE = re.compile(r"^[ \t]*[\u2014\u2013][ \t]*", re.M)
_DASH_RE = re.compile(r"[ \t]*[\u2014\u2013][ \t]*")
_COMMAS_RE = re.compile(r",(?:[ \t]*,)+")
_SPACES_RE = re.compile(r"[ \t]{2,}")
_TRAILING_RE = re.compile(r"[ \t]+$", re.M)


class Note(NamedTuple):
    lang: str
    subject: str
    text: str


def _agency_match(entry: dict, agencies_de: list[str]) -> bool:
    """Чи джерело або email належить німецькому агентству зі списку."""
    hosts = []
    url = (entry.get("source_url") or "").strip()
    if url:
        try:
            host = urlsplit(url).hostname
            if not host or "//" not in url:
                host = urlsplit("//" + url).hostname
            hosts.append((host or "").lower())
        except ValueError:
            pass
    email = entry.get("email") or ""
    if "@" in email:
        hosts.append(email.rsplit("@", 1)[1].strip().lower())
    suffixes = [a.lower().lstrip(".") for a in agencies_de if a]
    return any(h and (h == a or h.endswith("." + a)) for h in hosts for a in suffixes)


def note_language(entry: dict, agencies_de: list[str]) -> Literal["de", "en"]:
    """Агентство -> de; інакше мова тексту вакансії."""
    if _agency_match(entry, agencies_de):
        return "de"
    text = f"{entry.get('title') or ''} {entry.get('description') or ''}"
    return detect_lang(text)


def currency_for(entry: dict) -> Literal["EUR", "USD"]:
    if entry.get("remote_scope") == "us_only":
        return "USD"
    text = " ".join(str(entry.get(k) or "") for k in ("title", "description", "company"))
    return "USD" if _USD_RE.search(text) else "EUR"


def rate_sentence(rate: dict, lang: str, currency: str,
                  first_contact: bool, to_agency: bool) -> str:
    """Речення про ставку; hourly_min сюди не потрапляє ніколи."""
    if rate.get("quote_policy") == "omit_first_contact" and first_contact and not to_agency:
        return ""
    if currency == "USD":
        v = rate.get("usd_hourly_target")
        return f"My rate is ${v}/hour, invoiced monthly as a B2B contractor." if v else ""
    v = rate.get("hourly_target")
    if not v:
        return ""
    if lang == "de":
        return f"Mein Stundensatz liegt bei {v} € netto zzgl. USt."
    return f"My rate for a full-time B2B engagement is €{v}/hour."


def _no_dashes(text: str) -> str:
    """— і – як сполучники → «, »; подвоєні коми й пробіли згортаються."""
    text = _RANGE_DASH_RE.sub("-", text)
    text = _LINE_DASH_RE.sub("", text)
    text = _DASH_RE.sub(", ", text)
    text = _COMMAS_RE.sub(",", text)
    text = _SPACES_RE.sub(" ", text)
    return _TRAILING_RE.sub("", text)


def build_note(entry: dict, body: str, profile: dict, targeting: dict,
               first_contact: bool = True) -> Note:
    agencies = list(targeting.get("agencies_de") or [])
    lang = note_language(entry, agencies)
    to_agency = _agency_match(entry, agencies)
    rate = profile.get("rate") or {}
    rate_line = rate_sentence(
        rate, lang, "EUR" if lang == "de" else currency_for(entry), first_contact, to_agency)

    name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
    if lang == "de":
        greeting, closing = "Guten Tag,", "Mit freundlichen Grüßen"
    else:
        company = (entry.get("company") or "").strip()
        greeting = f"Hello {company} team," if company else "Hello,"
        closing = "Best regards"
    sign = [name, profile.get("city") or ""]
    extra = profile.get("cv_contact_extra") or []
    if extra:
        sign.append(str(extra[0]))
    signoff = "\n".join([closing, *[s for s in sign if s]])

    blocks = [greeting, _no_dashes(body).strip(), rate_line, signoff]
    text = "\n\n".join(b for b in blocks if b)
    subject = market.get("contract").subject_templates[lang].format(
        role=_no_dashes(entry.get("title") or "").strip(), name=name)
    return Note(lang, subject, text)


def write_notes(notes: list[tuple[str, Note]], run_dir: Path, date: str) -> list[Path]:
    out_dir = Path(run_dir) / f"notes_{date}"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for slug, note in notes:
        p = out_dir / f"{slug}.txt"
        p.write_text(f"{note.subject}\n\n{note.text}\n", encoding="utf-8")
        paths.append(p)
    return paths


def entry_slug(entry: dict) -> str:
    """Безпечне для файлової системи ім'я ліда: company-title, до 60 символів."""
    raw = f"{entry.get('company') or ''}-{entry.get('title') or ''}".lower()
    return _SLUG_RE.sub("-", raw)[:60].strip("-") or "lead"


def slugged(entries: list[dict]) -> list[tuple[str, dict]]:
    """Пари (slug, entry); однакові slug отримують суфікс -2, -3 ..."""
    seen: dict[str, int] = {}
    out = []
    for e in entries:
        base = entry_slug(e)
        seen[base] = seen.get(base, 0) + 1
        out.append((base if seen[base] == 1 else f"{base}-{seen[base]}", e))
    return out


def _selected(path: Path) -> list[dict]:
    """selected із filtered.json (вихід bot.filters.rules) або простий список."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(data.get("selected", [])) if isinstance(data, dict) else list(data)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Мова й збірка cover note для контрактного ринку")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--lang-only", type=Path, metavar="FILTERED",
                      help="надрукувати JSON {slug: мова} для selected")
    mode.add_argument("--assemble", type=Path, nargs=2, metavar=("FILTERED", "BODIES"),
                      help="зібрати листи з тіл {slug: тіло}")
    ap.add_argument("--out-dir", type=Path, help="куди писати notes_<дата>/ (дефолт var/run клієнта)")
    ap.add_argument("--date", default=None, help="дата в назві каталогу, YYYY-MM-DD")
    args = ap.parse_args(argv)

    from bot import client
    c = client.active()
    agencies = list(c.targeting.get("agencies_de") or [])

    if args.lang_only:
        langs = {slug: note_language(e, agencies) for slug, e in slugged(_selected(args.lang_only))}
        print(json.dumps(langs, ensure_ascii=False, indent=2))
        return 0

    filtered, bodies_path = args.assemble
    bodies = json.loads(bodies_path.read_text(encoding="utf-8"))
    notes = []
    for slug, e in slugged(_selected(filtered)):
        body = (bodies.get(slug) or "").strip()
        if not body:
            print(f"cover_note: нема тіла для {slug}, пропущено", file=sys.stderr)
            continue
        notes.append((slug, build_note(e, body, c.profile, c.targeting)))
    paths = write_notes(notes, args.out_dir or c.run_dir, args.date or _date.today().isoformat())
    for p in paths:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
