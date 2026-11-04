"""Job-board pages on the way to the employer's application form.

A job board's job page is never the form itself. Its fields are a job search, a location box, a
job-alert email or a sign-in popup, and its own apply button opens the board's apply flow
(Indeed Apply, StepStone's «Ich bin interessiert», XING's «Einfach bewerben») behind the board's
login, which may send a profile application by itself. The form worker therefore never fills or
clicks anything on such a page: it reads the link to the employer site («Auf Unternehmenswebsite
bewerben», Indeed's applystart redirect, the Arbeitsagentur's «Externe Seite öffnen», LinkedIn's
company-site apply control) and opens it by URL, because a cookie banner on these pages takes
every click. A board without such a link ends as a hand-off. Pure helpers here; the page driving
lives in `ats_form`.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlparse

from bot.ats import external_apply_url
from bot.generators.employer_scan import NON_EMPLOYER_DOMAINS, registrable_domain

# Any page on these hosts is a board page, whatever the path (viewjob, rc/clk, smartapply, click
# tracking). These boards run one site per country.
_BOARD_HOST = re.compile(r"(?:^|\.)(indeed|glassdoor|stepstone|monster)\.(?:[a-z]{2,3})(?:\.[a-z]{2})?$")
# The other boards, aggregators and platforms by domain (any subdomain), with the name used in hand-off
# reasons. Aligned with employer_scan.NON_EMPLOYER_DOMAINS (a test keeps them so). Freelance platforms
# have their own drivers and never come here as forms; listed so a redirect to one is never filled.
_BOARD_DOMAINS = {
    "arbeitsagentur.de": "arbeitsagentur", "jobware.de": "jobware", "kununu.com": "kununu",
    "kimeta.de": "kimeta", "stellenanzeigen.de": "stellenanzeigen", "meinestadt.de": "meinestadt",
    "jobs.de": "jobs_de", "xing.com": "xing", "linkedin.com": "linkedin", "interamt.de": "interamt",
    "talent.com": "talent", "jobijoba.com": "jobijoba", "jobijoba.de": "jobijoba", "get-in.de": "get_in",
    "kleinanzeigen.de": "kleinanzeigen", "hokify.de": "hokify", "yourjob.de": "yourjob",
    "rp-online.de": "rp_online", "gulp.de": "gulp", "freelancermap.de": "freelancermap",
    "freelance.de": "freelance_de", "malt.de": "malt", "upwork.com": "upwork",
    # Multiposter behind arbeitsagentur «Externe Seite öffnen» links (seen 29.10.2026): not the employer.
    "jobexport.de": "jobexport",
    # Aggregators (employer lookup, 02.11.2026), kept in step with employer_scan.NON_EMPLOYER_DOMAINS.
    "medjobs.de": "medjobs", "jooble.org": "jooble", "simplyhired.de": "simplyhired", "simplyhired.com": "simplyhired", "getbaito.com": "getbaito", "jobted.de": "jobted", "adzuna.de": "adzuna", "careerjet.de": "careerjet", "stellenonline.de": "stellenonline", "absolventa.de": "absolventa", "jobvector.de": "jobvector", "heyjobs.co": "heyjobs", "heyjobs.de": "heyjobs", "joblift.de": "joblift", "jobrapido.com": "jobrapido", "stellenmarkt.de": "stellenmarkt", "gigajob.de": "gigajob", "jobsintown.de": "jobsintown", "jobsora.com": "jobsora", "trovit.de": "trovit", "karriere.at": "karriere", "jobs.ch": "jobs", "jobup.ch": "jobup",
    # Aggregator that asks for an account with a password before it shows the employer (03.11.2026).
    "bebee.com": "bebee",
}

# Every link and button with what tells an employer link from the board's own apply.
BOARD_JS = r"""() => {
  const out = [];
  const els = document.querySelectorAll('a, button, [role=button], [role=link]');
  for (const el of els) {
    const text = (el.innerText || el.getAttribute('aria-label') || el.title || '').replace(/\s+/g, ' ').trim();
    const href = el.getAttribute('href') || el.getAttribute('data-href') || '';
    const inBox = !!el.closest('#applyButtonLinkContainer, #viewJobButtonLinkContainer');
    const ownBox = !!el.closest('#indeedApplyButton, #indeedApplyButtonContainer, [class*="IndeedApplyButton"], '
      + '.indeed-apply-widget, [data-indeed-apply-jobid]');
    if (!text && !href && !inBox && !ownBox) continue;
    out.push({text: text.slice(0, 80), href: href.slice(0, 1000), id: el.id || '',
              testid: el.getAttribute('data-testid') || '', in_apply_box: inBox, own_apply_box: ownBox});
    if (out.length >= 300) break;
  }
  return out;
}"""

# The employer link's text, German and English: Indeed, Glassdoor, XING («Zur Arbeitgeber-Website»),
# the Arbeitsagentur's partner offers («Externe Seite öffnen», 29.10.2026).
_EMPLOYER_TEXT = re.compile(
    r"unternehmens-?web-?s(?:ite|eite)|(?:website|webseite|seite) des (?:unternehmens|arbeitgebers)|"
    r"arbeitgeber-?(?:web-?)?s(?:ite|eite)|company'?s? (?:web)?site|employer'?s? (?:web)?site|"
    r"^externe seite (?:öffnen|oeffnen)$",
    re.IGNORECASE)
# The Arbeitsagentur's link button to a partner offer, whatever its text.
_EMPLOYER_IDS = {"detail-beschreibung-externe-url-btn"}
# The board's own apply: Indeed Apply, Glassdoor Easy Apply. Matched on the whole button text.
_OWN_APPLY_TEXT = re.compile(
    r"^(?:schnellbewerbung|einfach bewerben|jetzt bewerben|sofort bewerben|bewerben|"
    r"apply now|easily apply|easy apply|quick apply)$", re.IGNORECASE)
_OWN_APPLY_HTML = re.compile(
    r"\"indeedApplyButtonContainer\"\s*:\s*\{|smartapply\.indeed\.com|data-indeed-apply-jobid|"
    r"id=\"indeedApplyButton\"|\"isEasyApply\"\s*:\s*true", re.IGNORECASE)
# Indeed's redirect to the employer, in links or in the page's JSON with escaped slashes.
_SLASH = r"(?:/|\\/|\\u002[fF])"
_APPLYSTART = re.compile(rf"https?:{_SLASH}{_SLASH}[A-Za-z0-9.-]+{_SLASH}applystart\?[^\"'<>\s]*")
_LOGIN_URL = re.compile(r"secure\.indeed\.com|login\.xing\.com|/(?:auth|authwall|login\w*|signin|sign-in)\b",
                        re.IGNORECASE)
_CHALLENGE_TEXT = re.compile(
    r"additional verification required|verify you are human|just a moment\.\.\.|checking your browser|"
    r"zusätzliche verifizierung erforderlich|bestätigen sie, dass sie ein mensch sind", re.IGNORECASE)
# The Arbeitsagentur shows the employer's contacts of its own offers only after a captcha (29.10.2026).
_CONTACT_CAPTCHA = re.compile(r"kontaktdaten-captcha|um die kontaktdaten des arbeitgebers anzuzeigen", re.IGNORECASE)
# The board refused the browser outright (Indeed's "Blocked" page): no check to solve, only a hand-off.
_BLOCKED_TEXT = re.compile(r"anfrage blockiert|sie wurden gesperrt|request blocked|you have been blocked",
                           re.IGNORECASE)


def board_of(url: str) -> str | None:
    """The board's name ("indeed", "stepstone", "arbeitsagentur", ...) for a page on a job board,
    None for anything else."""
    host = (urlparse(url or "").hostname or "").lower()
    m = _BOARD_HOST.search(host)
    if m:
        return m.group(1)
    return next((name for domain, name in _BOARD_DOMAINS.items()
                 if host == domain or host.endswith("." + domain)), None)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("’", "'")).strip().lower()


def _unescape(href: str) -> str:
    for a, b in (("\\u002F", "/"), ("\\u002f", "/"), ("\\/", "/"), ("\\u0026", "&"), ("&amp;", "&")):
        href = href.replace(a, b)
    return href.rstrip("\\")


def _jk(url: str) -> str:
    q = parse_qs(urlparse(url or "").query)
    return (q.get("jk") or q.get("vjk") or [""])[0]


def employer_link(board: str, page_url: str, html: str, cands: list[dict]) -> str | None:
    """Absolute URL to the employer site, None when there is no such link or it is ambiguous.

    Candidates are the links with the employer text, in Indeed's apply box or with a known id. A
    direct link off the board wins over a redirect on the board (Indeed's applystart); direct links
    to two different sites (a similar-jobs block) are ambiguous. Then LinkedIn's company-site apply
    control and the applystart redirect in the page data. An applystart of another job is never taken."""
    page_jk = _jk(page_url)

    def usable(raw: str) -> str | None:
        raw = _unescape((raw or "").strip())
        if not raw or raw.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
            return None
        url = urljoin(page_url, raw)
        if not url.startswith(("http://", "https://")):
            return None
        if board_of(url) and page_jk and _jk(url) and _jk(url) != page_jk:
            return None
        return url

    links = [url for c in cands
             if _EMPLOYER_TEXT.search(_norm(c.get("text") or "")) or c.get("in_apply_box")
             or c.get("id") in _EMPLOYER_IDS
             for url in [usable(c.get("href") or "")] if url]
    direct = [u for u in links if not board_of(u)]
    if direct:
        sites = {registrable_domain(urlparse(u).hostname or "") for u in direct}
        return direct[0] if len(sites) == 1 else None
    if links:
        return links[0]
    if board == "linkedin":
        url = usable(external_apply_url(html or "") or "")
        if url and not board_of(url):
            return url
    for m in _APPLYSTART.finditer(html or ""):
        url = usable(m.group(0))
        if url and board_of(url) == board:
            return url
    return None


def own_apply(html: str, cands: list[dict]) -> bool:
    """The page offers the board's own apply flow (Indeed Apply, Easy Apply)."""
    if any(c.get("own_apply_box") or c.get("id") == "indeedApplyButton"
           or _OWN_APPLY_TEXT.match(_norm(c.get("text") or "")) for c in cands):
        return True
    return bool(_OWN_APPLY_HTML.search(html or ""))


# ── The employer's apply URL in a board posting's HTML (StepStone, XING) ──
# StepStone keeps the employer's apply link of an external posting in the page state JSON, slashes
# escaped (`https:\/\/karriere.example.org\/de\/jobposting\/<hex>\/apply`), next to its own links,
# CDN assets and trackers. The candidates are every absolute URL in the page.

_PAGE_URL = re.compile(r"https?://[^\s\"'<>\\]+")
_TRAILING = ".,;:)]}"
# Hosts of trackers, consent tools and CDNs that pages embed (registrable domains).
_TECH_DOMAINS = frozenset({
    "googletagmanager.com", "google-analytics.com", "gstatic.com", "googleapis.com", "doubleclick.net",
    "facebook.com", "facebook.net", "cookielaw.org", "onetrust.com", "usercentrics.eu", "cloudflare.com",
    "cloudfront.net", "akamaihd.net", "akamaized.net", "schema.org", "w3.org", "youtube.com", "twitter.com",
    "x.com", "instagram.com", "tiktok.com", "hotjar.com", "sentry.io", "nr-data.net", "newrelic.com",
    "stepstone.net", "xing.net", "apple.com", "microsoft.com", "bing.com", "trustpilot.com",
})
_ASSET_HOST = re.compile(r"^(?:cdn|static|assets?|img|images|media|fonts?)\d*[.-]|[.-]cdn[.-]")
_ASSET_PATH = re.compile(r"\.(?:m?js|css|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|eot|json|xml|pdf|mp4|webm)$",
                         re.IGNORECASE)
# Score of a candidate by its host and path: the apply form itself, an applicant tracking system,
# a careers page. Without any of these it is no apply URL (the employer's homepage, a product page).
_APPLY_END = re.compile(r"/(?:apply|bewerben|bewerbung)/?$", re.IGNORECASE)
_APPLY_WORD = re.compile(r"apply|bewerb|application", re.IGNORECASE)
_ATS_HOST = re.compile(
    r"softgarden|personio|dvinci|rexx-systems|umantis|successfactors|myworkdayjobs|smartrecruiters|recruitee|"
    r"join\.com|onlyfy|prescreen|b-ite|concludis|coveto|greenhouse\.io|lever\.co|ashbyhq|workable|teamtailor|"
    r"jobbase|kenjo|heyrecruit|talention|perbit|taleo|icims|jobvite|milchundzucker|eightfold", re.IGNORECASE)
_CAREER_WORD = re.compile(r"karriere|career|jobs?\b|stellen|jobposting|recruit|vacanc", re.IGNORECASE)
# Click and campaign tracking only; parameters an ATS needs (job id, source, language) stay.
_TRACKING_PARAM = re.compile(r"^(?:utm_\w+|gclid|fbclid|msclkid|yclid|mc_cid|mc_eid|_hsenc|_hsmi|_ga|_gl)$",
                             re.IGNORECASE)


def employer_url_ok(url: str) -> bool:
    """An absolute http(s) URL off every job board, aggregator and platform: a page the form worker
    may fill. Board pages (`board_of`) and `employer_scan.NON_EMPLOYER_DOMAINS` never are."""
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or "." not in host:
        return False
    return not board_of(url) and registrable_domain(host) not in NON_EMPLOYER_DOMAINS


def _strip_tracking(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    kept = [p for p in parsed.query.split("&") if p and not _TRACKING_PARAM.match(p.split("=", 1)[0])]
    return parsed._replace(query="&".join(kept)).geturl()


def _apply_score(url: str) -> int:
    """4 an apply form path, 3 apply in the URL, 2 an ATS host, 1 a careers page, 0 none of these."""
    parsed = urlparse(url)
    host, path = (parsed.hostname or "").lower(), parsed.path or ""
    if not path.strip("/") or _ASSET_PATH.search(path) or _ASSET_HOST.search(host):
        return 0
    if registrable_domain(host) in _TECH_DOMAINS:
        return 0
    if _APPLY_END.search(path):
        return 4
    if _APPLY_WORD.search(host + path):
        return 3
    if _ATS_HOST.search(host):
        return 2
    return 1 if _CAREER_WORD.search(host + path) else 0


def employer_apply_url(html: str, min_score: int = 1) -> str | None:
    """The employer's apply URL in a board posting's HTML, None when there is none or it is ambiguous.

    Every absolute URL of the page counts, escaped slashes (`\\/`, `\\u002F`) unescaped first. Board
    and aggregator links, trackers and assets drop out; the rest is scored by `_apply_score` and must
    reach min_score (2 skips plain careers pages). The best score wins; at that score all candidates
    must be on one site, else None: no application beats one to another employer's job. Tracking
    parameters are stripped."""
    text = _unescape(html or "")
    best: list[str] = []
    top = 0
    for m in _PAGE_URL.finditer(text):
        url = m.group(0).rstrip(_TRAILING)
        if not employer_url_ok(url):
            continue
        score = _apply_score(url)
        if score < max(min_score, 1) or score < top:
            continue
        if score > top:
            top, best = score, []
        best.append(_strip_tracking(url))
    if not best:
        return None
    sites = {registrable_domain(urlparse(u).hostname or "") for u in best}
    return best[0] if len(sites) == 1 else None


def login_wall(url: str) -> bool:
    """The board sent the browser to its sign-in page."""
    return bool(_LOGIN_URL.search(url or ""))


def challenge_page(text: str) -> bool:
    """A bot check (Cloudflare) in place of the job page."""
    return bool(_CHALLENGE_TEXT.search(text or ""))


def contact_captcha(html: str) -> bool:
    """The employer's contacts sit behind the board's captcha (the Arbeitsagentur's own offers)."""
    return bool(_CONTACT_CAPTCHA.search(html or ""))


def blocked_page(text: str) -> bool:
    """The board's block page in place of the job page."""
    return bool(_BLOCKED_TEXT.search(text or ""))
