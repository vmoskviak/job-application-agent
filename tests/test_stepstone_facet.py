"""Скрейпер списку результатів StepStone під Berufsfeld-фасет (di/sdi).

Email-алерт StepStone зберігає лише what/where/radius — фасети губляться, тому
384 позиції Assistenz+Sekretariat+Empfang (22 нові/добу) не приходять поштою
взагалі. Цей шар читає сторінку результатів і віддає URL у наявний intake_url.
"""
from pathlib import Path

import pytest

from bot.scrapers.stepstone_facet import (
    ADMIN_ASSISTENZ_FACET,
    FacetHit,
    build_page_url,
    new_hits,
    parse_results,
    scan_facet,
)

FIXTURES = Path(__file__).parent / "fixtures"
P1 = (FIXTURES / "stepstone_facet_admin_p1.html").read_text(encoding="utf-8")
P2 = (FIXTURES / "stepstone_facet_admin_p2.html").read_text(encoding="utf-8")


class TestParseResults:
    def test_finds_all_cards_on_page(self):
        hits = parse_results(P1)
        assert len(hits) == 25

    def test_hit_has_absolute_url_and_job_id(self):
        hits = parse_results(P1)
        h = hits[0]
        assert isinstance(h, FacetHit)
        assert h.url.startswith("https://www.stepstone.de/stellenangebote--")
        assert h.job_id.isdigit()
        assert h.job_id in h.url

    def test_hit_carries_title_company_location(self):
        by_id = {h.job_id: h for h in parse_results(P1)}
        h = by_id["14185204"]
        assert h.title == "Referent (m/w/d) der Geschäftsführung"
        assert h.company == "Beispiel Gebäudetechnik West GmbH"
        assert h.location  # напр. «Borken, Metzingen, …»

    def test_no_duplicate_job_ids(self):
        hits = parse_results(P1)
        assert len({h.job_id for h in hits}) == len(hits)

    def test_empty_html_gives_no_hits(self):
        assert parse_results("") == []
        assert parse_results("<html><body>nichts</body></html>") == []

    def test_pages_do_not_overlap(self):
        ids1 = {h.job_id for h in parse_results(P1)}
        ids2 = {h.job_id for h in parse_results(P2)}
        assert ids1 and ids2
        assert not (ids1 & ids2)


class TestPagination:
    def test_build_page_url_adds_page_param(self):
        url = build_page_url(ADMIN_ASSISTENZ_FACET, 3)
        assert "page=3" in url
        # фасети мусять вижити, інакше сторінка віддасть усі вакансії в радіусі
        assert "di=Administration" in url
        assert url.count("sdi=") == 3

    def test_build_page_url_replaces_existing_page(self):
        url = build_page_url(ADMIN_ASSISTENZ_FACET + "&page=7", 2)
        assert "page=2" in url
        assert "page=7" not in url

    def test_first_page_has_no_page_param(self):
        assert "page=" not in build_page_url(ADMIN_ASSISTENZ_FACET, 1)


class TestDedup:
    def test_new_hits_filters_seen_ids(self):
        hits = parse_results(P1)
        seen = {hits[0].job_id, hits[1].job_id}
        fresh = new_hits(hits, seen)
        assert len(fresh) == 23
        assert all(h.job_id not in seen for h in fresh)

    def test_new_hits_dedups_within_batch(self):
        hits = parse_results(P1)
        fresh = new_hits(hits + hits, set())
        assert len(fresh) == 25

    def test_new_hits_empty_seen_keeps_all(self):
        hits = parse_results(P1)
        assert len(new_hits(hits, set())) == 25


class TestScanFacet:
    def test_walks_pages_until_max(self):
        pages = {1: P1, 2: P2}
        calls = []

        def fake_fetch(url):
            calls.append(url)
            n = 2 if "page=2" in url else 1
            return pages[n]

        hits = scan_facet(ADMIN_ASSISTENZ_FACET, max_pages=2, fetch=fake_fetch)
        assert len(calls) == 2
        assert len(hits) == 50

    def test_stops_early_when_page_empty(self):
        def fake_fetch(url):
            return P1 if "page=" not in url else "<html></html>"

        hits = scan_facet(ADMIN_ASSISTENZ_FACET, max_pages=5, fetch=fake_fetch)
        assert len(hits) == 25

    def test_stops_when_page_repeats_known_ids(self):
        """StepStone за межею останньої сторінки віддає ту саму сторінку."""
        def fake_fetch(url):
            return P1

        hits = scan_facet(ADMIN_ASSISTENZ_FACET, max_pages=5, fetch=fake_fetch)
        assert len(hits) == 25

    def test_skips_seen_ids(self):
        seen = {h.job_id for h in parse_results(P1)}

        def fake_fetch(url):
            return P1 if "page=" not in url else "<html></html>"

        hits = scan_facet(ADMIN_ASSISTENZ_FACET, max_pages=3, fetch=fake_fetch, seen=seen)
        assert hits == []

    def test_max_pages_must_be_positive(self):
        with pytest.raises(ValueError):
            scan_facet(ADMIN_ASSISTENZ_FACET, max_pages=0, fetch=lambda u: P1)


class TestSeenState:
    def test_load_seen_missing_file_is_empty(self, tmp_path):
        from bot.scrapers.stepstone_facet import load_seen

        assert load_seen(tmp_path / "nope.json") == set()

    def test_save_then_load_roundtrip(self, tmp_path):
        from bot.scrapers.stepstone_facet import load_seen, save_seen

        p = tmp_path / "seen.json"
        save_seen(p, {"111", "222"})
        assert load_seen(p) == {"111", "222"}

    def test_save_merges_with_existing(self, tmp_path):
        from bot.scrapers.stepstone_facet import load_seen, save_seen

        p = tmp_path / "seen.json"
        save_seen(p, {"111"})
        save_seen(p, {"222"})
        assert load_seen(p) == {"111", "222"}

    def test_load_seen_survives_broken_file(self, tmp_path):
        from bot.scrapers.stepstone_facet import load_seen

        p = tmp_path / "seen.json"
        p.write_text("{ це не json", encoding="utf-8")
        assert load_seen(p) == set()
