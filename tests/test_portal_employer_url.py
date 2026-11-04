"""The employer's apply URL behind a StepStone or XING posting that has no quick apply.

StepStone's job page carries the employer's apply link inside escaped JSON (slashes as `\\/`),
next to its own URLs, CDN assets and trackers. The extractor picks the employer's form; the
resolver of the employment market turns such a posting into a `form` lead for the browser worker,
and the runner records the URL when it meets an external application.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.apply import detect, forms
from bot.apply.job_boards import employer_apply_url
from bot.apply.models import ApplyResult, external_application, load_result
from bot.ats import resolve_channel

APPLY = "https://karriere.example.org/de/jobposting/3fa9c1e07b/apply"
SS_URL = "https://www.stepstone.de/stellenangebote--Sachbearbeitung-Koeln-Example-GmbH--1234567-inline.html"
XING_URL = "https://www.xing.com/jobs/koeln-sachbearbeitung-123456789"


def _escaped(url: str) -> str:
    return url.replace("/", "\\/")


# A trimmed StepStone page: own links, assets, trackers, the employer's homepage and,
# in the page state JSON, the escaped apply link.
STEPSTONE_HTML = f"""<!doctype html><html><head>
<link rel="stylesheet" href="https://www.stepstone.de/public-assets/main.css">
<script src="https://www.googletagmanager.com/gtm.js?id=GTM-XXXX"></script>
<script src="https://cdn.cookielaw.org/scripttemplates/otSDKStub.js"></script>
</head><body>
<a href="https://www.stepstone.de/cmp/de/example-gmbh-123/jobs">Alle Jobs</a>
<img src="https://static.example.org/logo/example.png">
<a href="https://www.example.org/">Example GmbH</a>
<script>window.__PRELOADED_STATE__ = {{"listing":{{"id":1234567,
"applyUrl":"{_escaped(APPLY)}?utm_source=stepstone&utm_medium=jobad&channel=stepstone",
"logo":"https:\\/\\/www.stepstone.de\\/upload_de\\/logo\\/example.gif",
"similar":"https:\\/\\/www.stepstone.de\\/stellenangebote--Other--7654321-inline.html"}}}};</script>
</body></html>"""


def test_escaped_apply_url_is_found_among_board_and_asset_urls():
    assert employer_apply_url(STEPSTONE_HTML) == APPLY + "?channel=stepstone"


def test_unicode_escaped_slashes_are_read_too():
    html = '{"applyUrl":"https:\\u002F\\u002Fjobs.example.org\\u002Fbewerbung\\u002F42"}'
    assert employer_apply_url(html) == "https://jobs.example.org/bewerbung/42"


def test_only_tracking_parameters_are_stripped():
    html = f'"{_escaped(APPLY)}?utm_campaign=x&jobId=42&gclid=abc&lang=de"'
    assert employer_apply_url(html) == APPLY + "?jobId=42&lang=de"


def test_page_without_an_employer_link_gives_none():
    html = """<a href="https://www.stepstone.de/jobs/sachbearbeitung">Suche</a>
    <script src="https://www.googletagmanager.com/gtm.js"></script>
    <a href="https://www.example.org/">Example GmbH</a>"""
    assert employer_apply_url(html) is None
    assert employer_apply_url("") is None


def test_board_and_aggregator_links_are_never_taken():
    html = ('"https:\\/\\/de.indeed.com\\/applystart?jk=1" "https://www.xing.com/jobs/apply/1" '
            '"https://www.arbeitsagentur.de/jobsuche/bewerbung/1" "https://www.linkedin.com/jobs/view/1/apply"')
    assert employer_apply_url(html) is None


def test_asset_files_are_not_apply_urls():
    html = '"https://karriere.example.org/assets/apply-button.svg" "https://karriere.example.org/bewerben.js"'
    assert employer_apply_url(html) is None


def test_apply_link_beats_a_career_page_on_another_site():
    html = ('"https://www.example.org/karriere" '
            '"https://example-gmbh.softgarden.io/job/4711/apply"')
    assert employer_apply_url(html) == "https://example-gmbh.softgarden.io/job/4711/apply"


def test_two_employer_sites_with_apply_links_are_ambiguous():
    html = '"https://karriere.example.org/job/1/apply" "https://jobs.example.net/job/2/apply"'
    assert employer_apply_url(html) is None


def test_bare_career_homepage_is_not_an_apply_url():
    assert employer_apply_url('"https://karriere.example.org/"') is None


def test_strict_minimum_skips_plain_career_pages():
    html = '"https://www.example.org/karriere/stellen"'
    assert employer_apply_url(html) == "https://www.example.org/karriere/stellen"
    assert employer_apply_url(html, min_score=2) is None


# ── resolve_channel on the employment market ──

class NoBoard:
    def get_json(self, url):
        raise AssertionError("no board search on the employment market")


def _entry(url: str) -> dict:
    return {"company": "Example GmbH", "title": "Sachbearbeitung", "source_url": url}


def test_stepstone_posting_with_employer_link_resolves_to_form():
    r = resolve_channel(_entry(SS_URL), lambda u: STEPSTONE_HTML, NoBoard(), market="de_employment")
    assert r.channel == "form" and r.apply_url == APPLY + "?channel=stepstone" and r.ats is None
    assert "stepstone" in r.note


def test_stepstone_posting_without_employer_link_is_not_a_form():
    for html in ("<html>Schnelle Bewerbung</html>", None):
        r = resolve_channel(_entry(SS_URL), lambda u, h=html: h, NoBoard(), market="de_employment")
        assert r.channel == "none" and r.apply_url == SS_URL
        assert "no employer apply url" in r.note


def test_stepstone_posting_with_a_known_ats_link_carries_the_ats():
    html = '"https:\\/\\/boards.greenhouse.io\\/examplegmbh\\/jobs\\/42"'
    r = resolve_channel(_entry(SS_URL), lambda u: html, NoBoard(), market="de_employment")
    assert r.channel == "form" and r.ats == "greenhouse"


def test_xing_posting_needs_an_apply_link_not_just_a_career_page():
    career = '"https://www.example.org/karriere/stellen"'
    r = resolve_channel(_entry(XING_URL), lambda u: career, NoBoard(), market="de_employment")
    assert r.channel == "none"
    r = resolve_channel(_entry(XING_URL), lambda u: f'"{APPLY}"', NoBoard(), market="de_employment")
    assert r.channel == "form" and r.apply_url == APPLY


def test_contract_market_xing_stays_platform_login():
    r = resolve_channel(_entry(XING_URL), lambda u: f'"{APPLY}"', NoBoard())
    assert r.channel == "platform_login"


# ── runner results ──

def test_external_application_results():
    ext = [("stepstone", "pre-flight classified as external_ats"),
           ("stepstone", "external application — redirects to company website"),
           ("stepstone", "StepStone передає на сайт роботодавця, не подано: https://jobs.example.org/1"),
           ("xing", "pre-flight classified as external_ats"),
           ("xing", "під заголовком немає Easy Apply: зовнішня подача на сайті роботодавця")]
    for portal, reason in ext:
        assert external_application(ApplyResult("form_unsupported", portal, SS_URL, reason=reason)), reason
    not_ext = [ApplyResult("form_unsupported", "stepstone", SS_URL, reason="submit button not found"),
               ApplyResult("form_unsupported", "stepstone", SS_URL, reason="pre-flight classified as unknown"),
               ApplyResult("submitted", "stepstone", SS_URL, reason="external application"),
               ApplyResult("captcha", "xing", XING_URL, reason="pre-flight: captcha")]
    for res in not_ext:
        assert not external_application(res), res
    assert not external_application(None)


def test_old_result_file_without_employer_url_still_loads(tmp_path):
    (tmp_path / "apply_result.json").write_text(
        '{"status": "form_unsupported", "portal": "stepstone", "url": "u", "screenshots": [], '
        '"confirmation_text": "", "reason": "pre-flight classified as external_ats", "timestamp": "t"}',
        encoding="utf-8")
    res = load_result(tmp_path)
    assert res.employer_url == "" and external_application(res)


class _Page:
    """Enough of a Playwright page for the pre-flight branch (no context: no login, no button check)."""

    def __init__(self, html: str, url: str):
        self._html, self.url = html, url

    def content(self):
        return self._html


def test_stepstone_runner_records_the_employer_url_of_an_external_posting(monkeypatch, tmp_path):
    import bot.apply.stepstone as stepstone

    monkeypatch.setattr(detect, "detect", lambda page, url: "external_ats")
    monkeypatch.setattr(forms, "screenshot", lambda page, app_dir, name: name)
    res = stepstone.apply(_Page(STEPSTONE_HTML, SS_URL), SS_URL, tmp_path, [], dry_run=False)
    assert res.status == "form_unsupported" and external_application(res)
    assert res.employer_url == APPLY + "?channel=stepstone"


class _Btn:
    def __init__(self, href):
        self.href = href

    def get_attribute(self, name, timeout=None):
        return self.href if name == "href" else None


class _XingPage(_Page):
    def locator(self, sel):
        page = self

        class _H1:
            first = None

            def inner_text(self, timeout=None):
                return "Sachbearbeitung (m/w/d)"
        h1 = _H1()
        h1.first = h1
        return h1 if sel == "h1" else page


def test_xing_runner_records_the_employer_link_under_the_title(monkeypatch, tmp_path):
    import bot.apply.xing as xing

    monkeypatch.setattr(detect, "detect", lambda page, url: "xing_easy")
    monkeypatch.setattr(forms, "screenshot", lambda page, app_dir, name: name)
    monkeypatch.setattr(forms, "accept_cookies", lambda page: None)
    for href, html, want in ((APPLY, "", APPLY),
                             # a link back to XING is no employer site; the page data still may have one
                             ("https://www.xing.com/jobs/redirect/1", f'"{_escaped(APPLY)}"', APPLY),
                             (None, "", "")):
        monkeypatch.setattr(xing, "_main_apply_button", lambda page, h=href: (_Btn(h), "external"))
        res = xing.apply(_XingPage(html, XING_URL), XING_URL, tmp_path, [], dry_run=False)
        assert res.status == "form_unsupported" and external_application(res)
        assert res.employer_url == want, href
