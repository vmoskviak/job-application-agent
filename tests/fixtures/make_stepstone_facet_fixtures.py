"""Генерує синтетичні сторінки результатів StepStone для tests/test_stepstone_facet.py.

Розмітка картки повторює структуру справжньої сторінки (data-at="job-item",
вкладені span у назві компанії), але всі вакансії, компанії й ID вигадані.

    python tests/fixtures/make_stepstone_facet_fixtures.py
"""
from __future__ import annotations

from html import escape
from pathlib import Path

HERE = Path(__file__).parent

TITLES = ["Referent (m/w/d) der Geschäftsführung", "Teamassistenz (m/w/d)",
          "Office Manager (m/w/d)", "Sachbearbeitung Einkauf (m/w/d)",
          "Empfangsmitarbeiter (m/w/d)"]
CITIES = ["Düsseldorf", "Köln", "Essen", "Neuss", "Duisburg"]
KINDS = ["Gebäudetechnik", "Logistik", "Immobilien", "Beratung", "Handel"]


def card(job_id: int, title: str, company: str, location: str) -> str:
    slug = "-".join(title.replace("(", "").replace(")", "").replace("/", "-").split())
    return f'''<article data-genesis-element="CARD" role="button" id="job-item-{job_id}" data-at="job-item" data-testid="job-item">
<div data-genesis-element="CARD_CONTENT"><h2 data-genesis-element="BASE"><a data-genesis-element="ANCHOR" href="/stellenangebote--{slug}--{job_id}-inline.html" data-testid="job-item-title" data-at="job-item-title" target="_blank"><div data-genesis-element="BASE"><div data-genesis-element="BASE">{escape(title)}</div></div></a></h2>
<div data-genesis-element="BASE"><span data-genesis-element="TEXT" data-at="job-item-company-name"><span data-genesis-element="BASE"><span data-genesis-element="ICON_CONTAINER"></span><span data-genesis-element="TEXT"><div data-genesis-element="BASE">{escape(company)}</div></span></span></span></div>
<div data-genesis-element="BASE"><span data-genesis-element="BASE" data-at="job-item-location"><span data-genesis-element="ICON_CONTAINER"></span><span data-genesis-element="TEXT">{escape(location)}</span></span></div>
<span data-genesis-element="TEXT" data-at="job-item-timeago"><time datetime="2026-07-23T04:28:54+02:00">vor 2 Tagen</time></span>
</div></article>'''


def page(first_id: int, n: int, first: tuple | None = None) -> str:
    cards = []
    for i in range(n):
        if i == 0 and first:
            cards.append(card(*first))
            continue
        k = first_id + i
        cards.append(card(k, TITLES[i % len(TITLES)],
                          f"Beispiel {KINDS[i % len(KINDS)]} {i:02d} GmbH",
                          f"{CITIES[i % len(CITIES)]}, {CITIES[(i + 2) % len(CITIES)]}"))
    return ("<!doctype html><html><head><meta charset='utf-8'><title>Jobs</title></head><body>"
            "<main>" + "\n".join(cards) + "</main></body></html>\n")


if __name__ == "__main__":
    (HERE / "stepstone_facet_admin_p1.html").write_text(page(
        14185205, 25, first=(14185204, "Referent (m/w/d) der Geschäftsführung",
                             "Beispiel Gebäudetechnik West GmbH",
                             "Borken, Metzingen, Frankfurt am Main, Essen")), encoding="utf-8")
    (HERE / "stepstone_facet_admin_p2.html").write_text(page(14300000, 25), encoding="utf-8")
