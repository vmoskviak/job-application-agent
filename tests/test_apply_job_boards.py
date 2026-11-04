"""Job-board pages (Indeed, Glassdoor, StepStone, Arbeitsagentur and other aggregators) on the way to the
employer's form: pure helpers."""
from bot.apply.job_boards import (
    blocked_page,
    board_of,
    challenge_page,
    contact_captcha,
    employer_link,
    login_wall,
    own_apply,
)

JK = "0123456789abcdef"
PAGE = f"https://de.indeed.com/viewjob?jk={JK}"


def _cand(text, href="", **kw):
    return {"text": text, "href": href, "id": "", "testid": "", "in_apply_box": False, "own_apply_box": False, **kw}


def test_board_of_indeed_and_glassdoor_hosts():
    assert board_of(PAGE) == "indeed"
    assert board_of(f"https://de.indeed.com/rc/clk?jk={JK}&bb=x") == "indeed"
    assert board_of(f"https://www.indeed.com/pagead/clk?mo=r&ad=x&jk={JK}") == "indeed"
    assert board_of("https://smartapply.indeed.com/beta/indeedapply/form/resume") == "indeed"
    assert board_of("https://indeed.de/viewjob?jk=1") == "indeed"
    assert board_of("https://www.glassdoor.de/job-listing/android-entwickler-contractco-JV_IC1.htm?jl=1") == "glassdoor"
    assert board_of("https://www.glassdoor.com/partner/jobListing.htm?jl=1") == "glassdoor"


def test_board_of_employer_pages_is_none():
    assert board_of("https://careers.example.com/job/1") is None
    assert board_of("https://careers.example.com/jobs/indeed.com/1") is None
    assert board_of("https://notindeed.com/viewjob") is None
    assert board_of("") is None


def test_employer_link_from_the_apply_button():
    cands = [_cand("Job speichern"), _cand("Auf Unternehmenswebsite bewerben", "https://careers.example.com/job/1")]
    assert employer_link("indeed", PAGE, "", cands) == "https://careers.example.com/job/1"
    cands = [_cand("Apply on company site", "/applystart?jk=" + JK)]
    assert employer_link("indeed", PAGE, "", cands) == f"https://de.indeed.com/applystart?jk={JK}"
    cands = [_cand("Bewerben auf der Unternehmenswebsite", "https://careers.example.com/job/2")]
    assert employer_link("indeed", PAGE, "", cands) == "https://careers.example.com/job/2"


def test_employer_link_from_the_apply_box_whatever_its_text():
    cands = [_cand("Weiter zur Bewerbung", f"https://de.indeed.com/applystart?jk={JK}&from=vj", in_apply_box=True)]
    assert employer_link("indeed", PAGE, "", cands) == f"https://de.indeed.com/applystart?jk={JK}&from=vj"


def test_employer_link_from_embedded_page_data():
    """Indeed's React page has no href on the button; the redirect sits in its JSON, slashes escaped."""
    html = ('<script>window._initialData={"viewJobButtonLinkModel":{"contentHtml":"Weiter zur Bewerbung",'
            f'"href":"https:\\u002F\\u002Fde.indeed.com\\u002Fapplystart?jk={JK}&from=vj&pos=bottom&spon=0"}}}}'
            '</script>')
    assert employer_link("indeed", PAGE, html, []) == f"https://de.indeed.com/applystart?jk={JK}&from=vj&pos=bottom&spon=0"
    html2 = f'<a href="https://de.indeed.com/applystart?jk={JK}&amp;from=vj">x</a>'
    assert employer_link("indeed", PAGE, html2, []) == f"https://de.indeed.com/applystart?jk={JK}&from=vj"


def test_applystart_of_another_job_is_ignored():
    html = '"href":"https:\\u002F\\u002Fde.indeed.com\\u002Fapplystart?jk=ffffffffffffffff&from=vj"'
    assert employer_link("indeed", PAGE, html, []) is None


def test_no_employer_link_on_an_indeed_apply_page():
    cands = [_cand("Jetzt bewerben", id="indeedApplyButton", own_apply_box=True), _cand("Job speichern")]
    html = '{"indeedApplyButtonContainer":{"indeedApplyAttributes":{"data-indeed-apply-jobid":"1"}}}'
    assert employer_link("indeed", PAGE, html, cands) is None
    assert own_apply(html, cands) and own_apply("", cands) and own_apply(html, [])
    assert own_apply("", [_cand("Schnellbewerbung")])


def test_external_page_is_not_own_apply():
    html = '{"indeedApplyButtonContainer":null,"viewJobButtonLinkModel":{"href":null}}'
    assert not own_apply(html, [_cand("Auf Unternehmenswebsite bewerben"), _cand("Job speichern")])


def test_mailto_and_javascript_links_are_not_employer_links():
    cands = [_cand("Auf Unternehmenswebsite bewerben", "javascript:void(0)"),
             _cand("Apply on company site", "mailto:jobs@example.com")]
    assert employer_link("indeed", PAGE, "", cands) is None


def test_login_wall_and_challenge():
    assert login_wall("https://secure.indeed.com/auth?hl=de_DE&continue=x")
    assert login_wall("https://www.glassdoor.de/profile/login_input.htm")
    assert not login_wall(PAGE)
    assert challenge_page("Additional Verification Required\nYour Ray ID for this request is 1")
    assert challenge_page("Just a moment...")
    assert challenge_page("Bestätigen Sie, dass Sie ein Mensch sind")
    assert not challenge_page("Softwareentwickler (m/w/d) - Berlin")


def test_blocked_page():
    """What a fresh headless browser gets from de.indeed.com (title "Blocked - Indeed.com")."""
    assert blocked_page("Anfrage blockiert\n\nSie wurden gesperrt. Wenn Sie glauben, dass dies ein Irrtum ist")
    assert blocked_page("Request Blocked\nYou have been blocked.")
    assert not blocked_page("Softwareentwickler (m/w/d) - Berlin")


# ---- More boards: aggregators that reach the form path as "form" leads

BA = "https://www.arbeitsagentur.de/jobsuche/jobdetail/13635-0a1b2c3d_JB1234567-S"
PARTNER = "https://www.partner-board.example/job/1234567.html?exp=81"


def test_board_of_more_boards():
    cases = {
        "https://www.stepstone.de/stellenangebote--Android-Entwickler-Contract-Co--123-inline.html": "stepstone",
        "https://click.stepstone.de/f/a/abc": "stepstone",
        "https://www.stepstone.at/stellenangebote--x--1-inline.html": "stepstone",
        BA: "arbeitsagentur",
        "https://www.jobware.de/stellenangebote/x": "jobware",
        "https://www.monster.de/job-angebote/x": "monster",
        "https://www.kununu.com/de/jobs/x": "kununu",
        "https://www.kimeta.de/stellenangebote/x": "kimeta",
        "https://www.stellenanzeigen.de/job/x": "stellenanzeigen",
        "https://jobs.meinestadt.de/berlin/stellenangebote/x": "meinestadt",
        "https://www.jobs.de/job/x": "jobs_de",
        "https://www.xing.com/jobs/berlin-android-123": "xing",
        "https://www.linkedin.com/jobs/view/123": "linkedin",
        "https://de.linkedin.com/jobs/view/123": "linkedin",
        "https://www.freelance.de/projekte/x": "freelance_de",
    }
    assert {url: board_of(url) for url in cases} == cases


def test_hosts_that_only_look_like_boards_are_not_boards():
    for url in ("https://monster.energy/careers", "https://mystepstone.de/x", "https://notxing.com/jobs",
                "https://careers.example.com/stepstone/1", "https://lnkd.in/abc"):
        assert board_of(url) is None, url


def test_every_board_the_mail_scan_knows_stops_the_form_worker():
    """employer_scan lists the boards whose mail is alerts; the form worker must never fill any of them.
    The rest of that list are shorteners and sites that are not job pages."""
    from bot.generators.employer_scan import NON_EMPLOYER_DOMAINS

    not_boards = {"lnkd.in", "bit.ly", "goo.gl", "google.com", "github.com", "ycombinator.com"}
    missing = [d for d in sorted(NON_EMPLOYER_DOMAINS - not_boards) if board_of(f"https://www.{d}/job/1") is None]
    assert missing == []


def test_arbeitsagentur_partner_offer_links_to_the_external_page():
    """Markup of a partner offer on the BA job page, 29.10.2026: the link button, then the partner's home page."""
    cands = [_cand("Info zur Bewerbung"),
             _cand("Externe Seite öffnen", PARTNER, id="detail-beschreibung-externe-url-btn"),
             _cand("partner-board.example", "https://www.partner-board.example/", id="externe-url-quelle")]
    assert employer_link("arbeitsagentur", BA, "", cands) == PARTNER
    assert employer_link("arbeitsagentur", BA, "", [_cand("", PARTNER, id="detail-beschreibung-externe-url-btn")]) == PARTNER


def test_arbeitsagentur_own_offer_hides_the_contacts_behind_a_captcha():
    html = ('<p>Bitte geben Sie die dargestellten Zeichen in das Textfeld ein, um die Kontaktdaten des '
            'Arbeitgebers anzuzeigen.</p><input id="kontaktdaten-captcha-input">'
            '<button type="submit" id="kontaktdaten-captcha-absenden-button">Absenden</button>')
    assert contact_captcha(html)
    assert not contact_captcha("<html><body>Softwareentwickler (m/w/d)</body></html>")
    assert employer_link("arbeitsagentur", BA, html, [_cand("Info zur Bewerbung"), _cand("Absenden")]) is None


def test_stepstone_apply_button_has_no_employer_link():
    """StepStone, 29.10.2026: the apply control is a <button> without any URL, also for external jobs."""
    cands = [_cand("Ich bin interessiert", testid="harmonised-apply-button"), _cand("Job merken")]
    page = "https://www.stepstone.de/stellenangebote--x--14675901-inline.html"
    assert employer_link("stepstone", page, '{"applyNow":{"applyData":{"isApplyBlocked":false}}}', cands) is None


def test_linkedin_company_site_link_comes_from_the_apply_control():
    view = "https://www.linkedin.com/jobs/view/123"
    html = ('<a data-view-name="job-apply-button" href="https://www.linkedin.com/safety/go/?url='
            'https%3A%2F%2Fcareers.example.com%2Fjob%2F1&amp;urlhash=x">Apply</a>')
    assert employer_link("linkedin", view, html, []) == "https://careers.example.com/job/1"
    assert employer_link("linkedin", view, '<button data-view-name="job-apply-button">Easy Apply</button>', []) is None


def test_xing_employer_site_link():
    cands = [_cand("Einfach bewerben"), _cand("Zur Arbeitgeber-Website", "https://careers.example.com/job/1")]
    assert employer_link("xing", "https://www.xing.com/jobs/berlin-x-1", "", cands) == "https://careers.example.com/job/1"


def test_employer_links_to_two_different_sites_are_ambiguous():
    """A board page with a similar-jobs block: two employer links that disagree are never followed."""
    page = "https://www.jobware.de/stellenangebote/x"
    two = [_cand("Zur Arbeitgeber-Website", "https://careers.example.com/job/1"),
           _cand("Zur Arbeitgeber-Website", "https://jobs.other-employer.example/2")]
    assert employer_link("jobware", page, "", two) is None
    same = [_cand("Zur Arbeitgeber-Website", "https://careers.example.com/job/1?src=top"),
            _cand("Auf Unternehmenswebsite bewerben", "https://jobs.example.com/job/1?src=bottom")]
    assert employer_link("jobware", page, "", same) == "https://careers.example.com/job/1?src=top"


def test_direct_employer_link_wins_over_the_board_redirect():
    cands = [_cand("Weiter zur Bewerbung", f"https://de.indeed.com/applystart?jk={JK}", in_apply_box=True),
             _cand("Auf Unternehmenswebsite bewerben", "https://careers.example.com/job/1")]
    assert employer_link("indeed", PAGE, "", cands) == "https://careers.example.com/job/1"


def test_login_walls_of_the_new_boards():
    assert login_wall("https://www.linkedin.com/authwall?trk=x&sessionRedirect=y")
    assert login_wall("https://login.xing.com/?dest_url=x")
    assert login_wall("https://www.stepstone.de/de-DE/candidate/login?x=1")
    assert not login_wall(BA)
