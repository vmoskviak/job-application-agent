"""Pre-flight classification of an apply page.

Чиста функція `classify_apply_page(url, html)` — тестується на фікстурах;
`detect(page, url)` — обгортка над живою сторінкою.
"""

from __future__ import annotations

import re

# Класи: stepstone_quick | xing_easy | linkedin_easy | external_ats |
#        login_required | captcha | closed | unknown

_CLOSED_MARKERS = [
    r"stellenanzeige\s+ist\s+nicht\s+mehr\s+verfügbar",
    r"stellenangebot\s+ist\s+leider\s+nicht\s+mehr\s+verfügbar",
    r"nicht\s+mehr\s+verfügbar",
    r"stelle\s+wurde\s+(?:leider\s+)?bereits\s+besetzt",
    r"job\s+is\s+no\s+longer\s+available",
    r"diese\s+stelle\s+ist\s+leider\s+schon\s+vergeben",
]

_STEPSTONE_QUICK_MARKERS = [
    r"schnelle\s+bewerbung",
    r"data-testid=\"[^\"]*harmonised-apply",
    r"data-at=\"button-apply",
    r"jetzt\s+bewerben",
]
_XING_EASY_MARKERS = [
    r"easy\s+apply",
    r"einfach\s+bewerben",
    r"data-testid=\"[^\"]*apply-button",
]
_LOGIN_MARKERS = [
    r"anmelden.{0,40}um\s+fortzufahren",
    r"bitte\s+melden\s+sie\s+sich\s+an",
    r"log\s*in\s+to\s+continue",
    r"sign\s*in\s+to\s+apply",
    r"session\s+abgelaufen",
]
_CAPTCHA_MARKERS = [
    r"recaptcha",
    r"h-?captcha",
    r"cloudflare.{0,60}(challenge|turnstile)",
    r"sicherheitsüberprüfung",
    r"are\s+you\s+a\s+human",
]


def _any(patterns: list[str], haystack: str) -> bool:
    return any(re.search(p, haystack, re.IGNORECASE) for p in patterns)


def classify_apply_page(url: str, html: str) -> str:
    u = (url or "").lower()
    if _any(_CAPTCHA_MARKERS, html):
        return "captcha"
    if _any(_CLOSED_MARKERS, html):
        return "closed"
    if _any(_LOGIN_MARKERS, html):
        return "login_required"
    if "linkedin.com" in u:
        return "linkedin_easy"
    if "stepstone.de" in u:
        return "stepstone_quick" if _any(_STEPSTONE_QUICK_MARKERS, html) else "external_ats"
    if "xing.com" in u:
        return "xing_easy" if _any(_XING_EASY_MARKERS, html) else "external_ats"
    return "unknown"


def detect(page, url: str) -> str:
    """Навігація + класифікація живої сторінки (page — playwright Page)."""
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1200)
    return classify_apply_page(page.url, page.content())
