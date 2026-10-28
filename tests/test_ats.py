"""Резолвер каналу подачі: ATS за URL, вид подачі з гостьової сторінки LinkedIn,
публічні API дощок Greenhouse/Lever/Ashby/Workable без логіну."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.ats import (
    ATS,
    BOARD_URLS,
    AtsPosting,
    ats_from_url,
    company_slugs,
    decode_safety_url,
    external_apply_url,
    find_posting,
    linkedin_apply_kind,
    resolve_channel,
)

FIX = Path(__file__).parent / "fixtures" / "ats"


def fixtures(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class NoBoard:
    def get_json(self, url: str):
        return None


class FakeBoard:
    """Відповідає JSON-фікстурою на точний URL дошки, інакше None."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        self.calls = 0

    def get_json(self, url: str):
        self.calls += 1
        name = self.mapping.get(url)
        return json.loads(fixtures(name)) if name else None


class DictBoard:
    """Відповідає готовими даними дошки на точний URL, інакше None."""

    def __init__(self, mapping: dict[str, object]) -> None:
        self.mapping = mapping

    def get_json(self, url: str):
        return self.mapping.get(url)


def ashby_board(*titles: str) -> DictBoard:
    """Дошка Ashby компанії contractco з постингами в заданому порядку."""
    jobs = [{"id": str(i), "title": t, "jobUrl": f"https://jobs.ashbyhq.com/contractco/{i}"}
            for i, t in enumerate(titles)]
    return DictBoard({BOARD_URLS["ashby"].format(slug="contractco"): {"jobs": jobs}})


def fake_board() -> FakeBoard:
    return FakeBoard({
        BOARD_URLS["greenhouse"].format(slug="contractco"): "greenhouse_jobs.json",
        BOARD_URLS["lever"].format(slug="leverco"): "lever_postings.json",
        BOARD_URLS["ashby"].format(slug="ashbyco"): "ashby_board.json",
        BOARD_URLS["workable"].format(slug="workableco"): "workable_widget.json",
    })


def test_ats_from_url():
    assert ats_from_url("https://boards.greenhouse.io/contractco/jobs/123") == "greenhouse"
    assert ats_from_url("https://job-boards.greenhouse.io/contractco/jobs/123") == "greenhouse"
    assert ats_from_url("https://jobs.lever.co/contractco/9f1") == "lever"
    assert ats_from_url("https://jobs.ashbyhq.com/contractco/9f1") == "ashby"
    assert ats_from_url("https://apply.workable.com/contractco/j/ABC/") == "workable"
    assert ats_from_url("https://www.linkedin.com/jobs/view/1/") is None
    assert ats_from_url("") is None


def test_linkedin_apply_kind():
    assert linkedin_apply_kind(fixtures("linkedin_offsite.html")) == "offsite"
    assert linkedin_apply_kind(fixtures("linkedin_onsite.html")) == "onsite"
    assert linkedin_apply_kind(fixtures("linkedin_authwall.html")) == "unknown"
    assert linkedin_apply_kind("") == "unknown"


def test_company_slugs():
    assert company_slugs("Contract Co GmbH") == ["contractco", "contract-co", "contract"]
    assert company_slugs("Acme") == ["acme"]
    assert company_slugs("Lever Co, Inc.") == ["leverco", "lever-co", "lever"]


def test_find_posting_greenhouse():
    p = find_posting("Contract Co", "Senior Android Engineer", fake_board())
    assert p == AtsPosting("greenhouse", "https://boards.greenhouse.io/contractco/jobs/123",
                           "Senior Android Engineer (Kotlin)", "contractco")


def test_find_posting_other_boards():
    assert find_posting("Lever Co", "Android Engineer", fake_board()).ats == "lever"
    assert find_posting("Ashby Co", "Android Developer", fake_board()).apply_url == "https://jobs.ashbyhq.com/ashbyco/abc"
    assert find_posting("Workable Co", "Android Developer", fake_board()).ats == "workable"


def test_find_posting_rejects_other_role():
    board = fake_board()
    p = find_posting("Contract Co", "Senior Android Engineer", board)
    assert p is not None and p.title != "Senior iOS Engineer"
    assert find_posting("Contract Co", "Senior iOS Engineer", board).title == "Senior iOS Engineer"
    assert find_posting("Contract Co", "Data Engineer", board) is None
    assert find_posting("Nobody Ltd", "Android", board) is None


def test_find_posting_stops_at_12_requests():
    board = FakeBoard({})
    find_posting("Three Words Here GmbH", "Android", board)
    assert board.calls <= 12
    assert len(ATS) == 4


def test_resolve_email_first():
    r = resolve_channel({"email": "jobs@contract.example", "company": "Contract Co", "title": "Android",
                         "source_url": "https://boards.greenhouse.io/x/jobs/1"}, lambda u: "", NoBoard())
    assert r.channel == "email" and r.ats is None


def test_resolve_ats_url_is_form():
    r = resolve_channel({"company": "Contract Co", "title": "Android",
                         "source_url": "https://jobs.lever.co/contractco/9f1"}, lambda u: "", NoBoard())
    assert r.channel == "form" and r.ats == "lever" and r.apply_url == "https://jobs.lever.co/contractco/9f1"


def test_resolve_linkedin_onsite():
    r = resolve_channel({"company": "Contract Co", "title": "Android", "source_url": "https://www.linkedin.com/jobs/view/1/"},
                        lambda u: fixtures("linkedin_onsite.html"), NoBoard())
    assert r.channel == "linkedin_easy" and r.apply_url == "https://www.linkedin.com/jobs/view/1/"


def test_resolve_linkedin_offsite_via_board():
    r = resolve_channel({"company": "Contract Co", "title": "Senior Android Engineer",
                         "source_url": "https://www.linkedin.com/jobs/view/1/"},
                        lambda u: fixtures("linkedin_offsite.html"), fake_board())
    assert r.channel == "form" and r.ats == "greenhouse"
    assert r.apply_url == "https://boards.greenhouse.io/contractco/jobs/123"


def test_resolve_authwall_falls_back_to_boards():
    r = resolve_channel({"company": "Contract Co", "title": "Senior Android Engineer",
                         "source_url": "https://www.linkedin.com/jobs/view/1/"},
                        lambda u: fixtures("linkedin_authwall.html"), fake_board())
    assert r.channel == "form" and r.ats == "greenhouse"
    r2 = resolve_channel({"company": "Nobody Ltd", "title": "Android", "source_url": "https://www.linkedin.com/jobs/view/2/"},
                         lambda u: fixtures("linkedin_authwall.html"), fake_board())
    assert r2.channel == "linkedin_offsite" and r2.note == "linkedin unknown, board not found"
    assert r2.apply_url == "https://www.linkedin.com/jobs/view/2/" and r2.ats is None


def test_resolve_platform_login():
    r = resolve_channel({"company": "X", "title": "Y", "source_url": "https://www.freelancermap.de/nproj/1.html"},
                        lambda u: "", NoBoard())
    assert r.channel == "platform_login" and r.apply_url == "https://www.freelancermap.de/nproj/1.html"


def test_resolve_fetch_failure_is_unknown():
    r = resolve_channel({"company": "Contract Co", "title": "Senior Android Engineer",
                         "source_url": "https://www.linkedin.com/jobs/view/1/"}, lambda u: None, fake_board())
    assert r.channel == "form"


def test_resolve_other_host_tries_boards_then_none():
    r = resolve_channel({"company": "Contract Co", "title": "Senior Android Engineer",
                         "source_url": "https://contract.example/careers/1"}, lambda u: "", fake_board())
    assert r.channel == "form" and r.ats == "greenhouse"
    r2 = resolve_channel({"company": "Nobody Ltd", "title": "Android", "source_url": "https://nobody.example/jobs/1"},
                         lambda u: "", fake_board())
    assert r2.channel == "none" and r2.apply_url == "https://nobody.example/jobs/1"


def test_company_slugs_accents_short_and_stop_words():
    """Рев'ю 2b, Important 6: без однолітерних слагів і стоп-слів, акценти знімаються."""
    assert company_slugs("Müller GmbH") == ["muller"]
    assert company_slugs("Zühlke Engineering AG") == ["zuhlkeengineering", "zuhlke-engineering", "zuhlke"]
    assert company_slugs("Ørsted") == ["orsted"]
    assert company_slugs("The Trade Desk") == ["tradedesk", "trade-desk", "trade"]
    assert company_slugs("A") == []
    assert company_slugs("Contract Co GmbH") == ["contractco", "contract-co", "contract"]


def test_find_posting_requires_company_agreement_when_board_names_one():
    """Дошка з ім'ям іншої компанії не віддає свій постинг чужому ліду."""
    board = FakeBoard({BOARD_URLS["workable"].format(slug="workableco"): "workable_widget.json",
                       BOARD_URLS["greenhouse"].format(slug="contractco"): "greenhouse_jobs.json"})
    assert find_posting("Workable Co", "Android Developer", board).ats == "workable"
    assert find_posting("Workable Co Ltd", "Android Developer", board).ats == "workable"
    board2 = FakeBoard({BOARD_URLS["workable"].format(slug="other"): "workable_widget.json"})
    assert find_posting("Other", "Android Developer", board2) is None     # дошка каже «Workable Co»


def test_find_posting_rejects_generic_only_overlap():
    """Рев'ю 2b, Important 7: «Senior Engineer» не збігається з «Senior Account Executive»."""
    from bot.ats import same_role
    assert same_role("Senior Engineer", "Senior Account Executive", strict=True) is False
    assert same_role("Software Engineer", "Senior Android Engineer", strict=True) is False
    assert same_role("Senior Android Engineer", "Senior Android Engineer (Kotlin)", strict=True) is True
    assert same_role("Android Engineer", "Mobile Engineer, Android", strict=True) is True
    board = FakeBoard({BOARD_URLS["greenhouse"].format(slug="contractco"): "greenhouse_jobs.json"})
    assert find_posting("Contract Co", "Senior Engineer", board) is None


def test_platform_login_hosts_match_by_suffix_only():
    """Рев'ю 2b, Important 8: teamtailor не є a.team, boxing.com не є xing.com."""
    for url in ("https://nova.teamtailor.com/jobs/1", "https://www.boxing.com/jobs/1", "https://marc.dev/j/1"):
        r = resolve_channel({"company": "Nobody Ltd", "title": "Android", "source_url": url}, lambda u: "", NoBoard())
        assert r.channel != "platform_login", url
    for url in ("https://www.malt.de/project/1", "https://app.a.team/missions/1", "https://arc.dev/j/1",
                "https://www.xing.com/jobs/1"):
        r = resolve_channel({"company": "X", "title": "Y", "source_url": url}, lambda u: "", NoBoard())
        assert r.channel == "platform_login", url


def test_find_posting_prefers_exact_title_over_first_similar():
    """Живий прохід 27.10: перший схожий постинг (titles_similar 0.75) стояв на дошці перед
    точним, і резолвер брав його, хоч слова ліда «Imaging» у ньому нема."""
    board = ashby_board("Android Engineer, Chat Library Team", "Android Engineer, Chat Imaging")
    p = find_posting("Contract Co", "Android Engineer, Chat Imaging", board)
    assert p is not None and p.title == "Android Engineer, Chat Imaging"
    assert p.apply_url == "https://jobs.ashbyhq.com/contractco/1"


def test_find_posting_ambiguous_board_gives_none():
    """Кілька постингів, що містять слова ліда, або той самий заголовок у кількох локаціях:
    пропущена подача краща за подачу не на ту вакансію."""
    board = ashby_board("Android Engineer, Ads", "Android Engineer, Payments")
    assert find_posting("Contract Co", "Android Engineer", board) is None
    board = ashby_board("Senior Android Engineer", "Senior Android Engineer")
    assert find_posting("Contract Co", "Senior Android Engineer", board) is None


def test_board_workplace_and_location_from_ashby():
    """Тіньовий прохід 27.10: гібридний постинг у США став карткою hand-off. Дошка знає формат
    роботи й локацію, резолвер несе їх далі; isRemote гібридного постингу Ashby теж true."""
    p = find_posting("Ashby Co", "Android Developer", fake_board())
    assert p.workplace == "hybrid" and p.location == "San Francisco, United States"
    r = resolve_channel({"company": "Ashby Co", "title": "Android Developer",
                         "source_url": "https://www.linkedin.com/jobs/view/3/"},
                        lambda u: fixtures("linkedin_offsite.html"), fake_board())
    assert r.channel == "form" and r.ats == "ashby"
    assert r.board_workplace == "hybrid" and r.board_location == "San Francisco, United States"
    direct = resolve_channel({"company": "Ashby Co", "title": "Android Developer",
                              "source_url": "https://jobs.ashbyhq.com/ashbyco/abc"}, lambda u: "", NoBoard())
    assert direct.board_workplace is None and direct.board_location is None


def test_board_workplace_mapping_per_ats():
    """Ashby і Lever: workplaceType; Greenhouse: лише «remote» у назві локації; Workable: лише
    telecommuting true. Чого дошка не каже, те None; кілька локацій через «; »."""
    from bot.ats import _postings
    ashby = {"jobs": [
        {"title": "A", "jobUrl": "u1", "workplaceType": "OnSite", "location": "London",
         "address": {"postalAddress": {"addressCountry": "United Kingdom"}},
         "secondaryLocations": [{"location": "Berlin", "address": {"postalAddress": {"addressCountry": "Germany"}}}]},
        {"title": "B", "jobUrl": "u2", "workplaceType": None, "isRemote": True, "location": "Munich, Germany",
         "address": {"postalAddress": {"addressCountry": "Germany"}}},
        {"title": "C", "jobUrl": "u3"},
    ]}
    assert [(p.workplace, p.location) for p in _postings("ashby", ashby)] == [
        ("onsite", "London, United Kingdom; Berlin, Germany"), (None, "Munich, Germany"), (None, None)]
    lever = [{"text": "A", "hostedUrl": "u1", "workplaceType": "hybrid",
              "categories": {"location": "London", "allLocations": ["London", "Berlin"]}},
             {"text": "B", "hostedUrl": "u2", "workplaceType": "unspecified", "categories": {"location": "Toronto"}},
             {"text": "C", "hostedUrl": "u3"}]
    assert [(p.workplace, p.location) for p in _postings("lever", lever)] == [
        ("hybrid", "London; Berlin"), (None, "Toronto"), (None, None)]
    greenhouse = {"jobs": [{"title": "A", "absolute_url": "u1", "location": {"name": "Remote - Europe"}},
                           {"title": "B", "absolute_url": "u2", "location": {"name": "New York, NY "}},
                           {"title": "C", "absolute_url": "u3"}]}
    assert [(p.workplace, p.location) for p in _postings("greenhouse", greenhouse)] == [
        ("remote", "Remote - Europe"), (None, "New York, NY"), (None, None)]
    workable = {"jobs": [{"title": "A", "url": "u1", "telecommuting": True, "city": "", "country": "France"},
                         {"title": "B", "url": "u2", "telecommuting": False, "city": "Austin",
                          "country": "United States"},
                         {"title": "C", "url": "u3"}]}
    assert [(p.workplace, p.location) for p in _postings("workable", workable)] == [
        ("remote", "France"), (None, "Austin, United States"), (None, None)]


def test_find_posting_keeps_seniority():
    """Рев'ю live-fixes, C2: для same_role senior і staff загальні слова, але вибір постингу їх
    розрізняє: «Senior …» не бере «Android Engineer» чи «Staff …» замість свого постингу."""
    def pick(lead: str, *titles: str) -> str | None:
        p = find_posting("Contract Co", lead, ashby_board(*titles))
        return p.title if p else None

    assert pick("Senior Android Engineer", "Senior Android Engineer (Kotlin)", "Android Engineer") \
        == "Senior Android Engineer (Kotlin)"
    assert pick("Senior Android Engineer", "Senior Android Engineer", "Android Engineer") == "Senior Android Engineer"
    assert pick("Senior Android Engineer", "Android Engineer", "Senior Android Engineer") == "Senior Android Engineer"
    lead = "Senior Software Engineer, Android"
    assert pick(lead, "Senior Software Engineer, Android (Kotlin)", "Staff Software Engineer, Android") \
        == "Senior Software Engineer, Android (Kotlin)"
    assert pick(lead, "Senior Software Engineer, Android", "Staff Software Engineer, Android") \
        == "Senior Software Engineer, Android"
    assert pick("Android Engineer", "Senior Android Engineer", "Android Engineer") == "Android Engineer"


def test_find_posting_strips_lead_title_noise():
    """Рев'ю live-fixes, I1: LinkedIn дописує до заголовка «(all genders)», «- Hybrid», країну чи
    місто ліда. Без них заголовок ліда точно збігається з дошкою; довільний хвіст не знімається."""
    assert find_posting("Contract Co", "Senior Android Engineer (all genders)",
                        ashby_board("Senior Android Engineer")).title == "Senior Android Engineer"
    assert find_posting("Contract Co", "Android Developer (m/w/div)", ashby_board("Android Developer")) is not None
    assert find_posting("Contract Co", "Senior Android Engineer - Hybrid (Remote, Germany)",
                        ashby_board("Senior Android Engineer")) is not None
    board = ashby_board("Android Engineer")
    assert find_posting("Contract Co", "Android Engineer, Berlin", board, location="Berlin, Germany").title \
        == "Android Engineer"
    assert find_posting("Contract Co", "Android Engineer, Berlin", board) is None   # без локації ліда «Berlin» не шум
    assert find_posting("Contract Co", "Android Engineer (all genders)", ashby_board("Android Engineer, Payments")) is None
    assert find_posting("Contract Co", "Android Engineer, Chat Imaging",
                        ashby_board("Android Engineer, Chat Library Team")) is None


def test_resolve_channel_passes_lead_location_to_board():
    r = resolve_channel({"company": "Contract Co", "title": "Android Engineer, Berlin", "location": "Berlin, Germany",
                         "source_url": "https://www.linkedin.com/jobs/view/4/"},
                        lambda u: fixtures("linkedin_offsite.html"), ashby_board("Android Engineer"))
    assert r.channel == "form" and r.apply_url == "https://jobs.ashbyhq.com/contractco/0"


def test_lever_country_code_joins_single_location():
    """Рев'ю live-fixes, M2: Lever пише «Milano, MI» і окремо country IT; без країни «, MI» читалось
    як штат Мічиган. Країна з коду дописується до єдиної локації, як у Ashby; кілька локацій і
    невідомий код лишаються як є."""
    from bot.ats import _postings
    from bot.presend import location_country
    lever = [{"text": "A", "hostedUrl": "u1", "workplaceType": "hybrid", "country": "IT",
              "categories": {"location": "Milano, MI", "allLocations": ["Milano, MI"]}},
             {"text": "B", "hostedUrl": "u2", "workplaceType": "hybrid", "country": "GB",
              "categories": {"location": "London"}},
             {"text": "C", "hostedUrl": "u3", "workplaceType": "onsite", "country": "US",
              "categories": {"location": "New York, NY"}},
             {"text": "D", "hostedUrl": "u4", "workplaceType": "hybrid", "country": "ZZ",
              "categories": {"location": "Springfield"}},
             {"text": "E", "hostedUrl": "u5", "workplaceType": "hybrid", "country": "GB",
              "categories": {"location": "London", "allLocations": ["London", "Berlin"]}}]
    places = [p.location for p in _postings("lever", lever)]
    assert places == ["Milano, MI, Italy", "London, United Kingdom", "New York, NY, United States",
                      "Springfield", "London; Berlin"]
    assert location_country(places[0]) == "italy" and location_country(places[1]) == "united kingdom"


def test_find_posting_core_tier_rejects_board_qualifier():
    """Повторне рев'ю, регресія раунду 2: уточнення постингу («, US», «(Contract)», «, SF») для
    _pick_words невидиме, і щабель ядра вибирав постинг для іншої країни чи формату. На цьому
    щаблі постинг не має жодного слова, якого нема в заголовку ліда; «Hybrid» на початку
    заголовка це частина ролі, а не шум."""
    for lead, posting, location in (
            ("Senior Android Engineer, Germany", "Senior Android Engineer, US", ""),
            ("Senior Android Engineer (EMEA)", "Senior Android Engineer (US)", ""),
            ("Account Executive DACH", "Account Executive, UK", ""),
            ("Senior Android Engineer, Germany", "Senior Android Engineer (Contract)", ""),
            ("Senior Android Engineer - Hybrid", "Senior Android Engineer, SF", ""),
            ("Android Engineer, Berlin", "Android Engineer, NY", "Berlin, Germany"),
            ("Hybrid Cloud Engineer", "Cloud Engineer", ""),
            ("Hybrid App Developer", "App Developer", "")):
        assert find_posting("Contract Co", lead, ashby_board(posting), location=location) is None, (lead, posting)
    assert find_posting("Contract Co", "Senior Android Engineer, Germany",
                        ashby_board("Senior Android Engineer")).title == "Senior Android Engineer"
    assert find_posting("Contract Co", "Sr. Android Engineer",
                        ashby_board("Senior Android Engineer")).title == "Senior Android Engineer"
    assert find_posting("Contract Co", "Hybrid Cloud Engineer",
                        ashby_board("Cloud Engineer", "Hybrid Cloud Engineer")).title == "Hybrid Cloud Engineer"


def test_resolve_linkedin_offsite_without_board_is_linkedin_offsite():
    html = '<a data-tracking-control-name="public_jobs_apply-link-offsite">Apply</a>'
    r = resolve_channel({"company": "Contract Co", "title": "Android Engineer",
                         "source_url": "https://www.linkedin.com/jobs/view/1/"},
                        lambda u: html, FakeBoard({}))
    assert r.channel == "linkedin_offsite" and r.note == "linkedin offsite, board not found"
    onsite = resolve_channel({"company": "Contract Co", "title": "Android Engineer",
                              "source_url": "https://www.linkedin.com/jobs/view/1/"},
                             lambda u: fixtures("linkedin_onsite.html"), FakeBoard({}))
    assert onsite.channel == "linkedin_easy"


def test_external_apply_url_decodes_safety_link():
    html = ('<a class="x" href="https://www.linkedin.com/safety/go/?url=https%3A%2F%2Fjobs%2Eexample%2Ecom'
            '%2FContractCo%2F123-android%3Ftrid%3Dabc&amp;urlhash=q&amp;mt=z">Apply on company website</a>')
    assert external_apply_url(html) == "https://jobs.example.com/ContractCo/123-android?trid=abc"
    raw_amp = html.replace("&amp;", "&")
    assert external_apply_url(raw_amp) == "https://jobs.example.com/ContractCo/123-android?trid=abc"
    assert external_apply_url("<a href='https://www.linkedin.com/jobs/view/1/'>x</a>") is None
    assert external_apply_url('<a href="https://www.linkedin.com/safety/go/?urlhash=q&amp;mt=z">x</a>') is None


PLATFORMS = Path(__file__).parent / "fixtures" / "platforms"


def test_decode_safety_url():
    href = ("https://www.linkedin.com/safety/go/?url=https%3A%2F%2Fjobs%2Eexample%2Ecom%2Fcontract-co%2F1234567"
            "&amp;urlhash=abc&amp;mt=abc&amp;isSdui=true")
    assert decode_safety_url(href) == "https://jobs.example.com/contract-co/1234567"
    assert decode_safety_url(href.replace("&amp;", "&")) == "https://jobs.example.com/contract-co/1234567"
    assert decode_safety_url("https://www.linkedin.com/safety/go/?urlhash=q") is None
    assert decode_safety_url("") is None


def test_external_apply_url_takes_apply_anchor_not_first_safety_link():
    """Живий захват: опис вакансії теж має safety/go; брати лише якір job-apply-button."""
    page = (PLATFORMS / "linkedin_job_offsite.html").read_text(encoding="utf-8")
    decoy = '<a href="https://www.linkedin.com/safety/go/?url=https%3A%2F%2Fdecoy.example.com%2F&amp;urlhash=x">x</a>'
    assert external_apply_url(decoy + page) == "https://jobs.example.com/contract-co/1234567"
    # Easy Apply (кнопка job-apply-button) з safety/go в описі: зовнішнього Apply нема.
    easy = (PLATFORMS / "linkedin_job_easy.html").read_text(encoding="utf-8")
    assert external_apply_url(easy.replace("</body>", decoy + "</body>")) is None
