import re

from bs4 import BeautifulSoup
from bot.generators.intake import JobData, extract_email, detect_portal
from bot.scrapers.models import PageFetch

# Logged-in SPA рендерить опис під цим заголовком; nav-сміття перед ним ріжемо,
# інакше keyword-scoring працює по меню LinkedIn замість тексту вакансії
_DESCRIPTION_MARKERS = ("About the job", "Über den Job", "Über die Stelle")

_LOCATION_LINE_RE = re.compile(r"^(.{2,60}?,.{2,60}?, (?:Germany|Deutschland))\s*(?:·|$)")
_WORKPLACE_RE = re.compile(r"^\s*(Hybrid|Remote)\s*$", re.IGNORECASE)


def _extract_location(text: str) -> str:
    """Топ-кард: "Düsseldorf, North Rhine-Westphalia, Germany · Reposted..." +
    окремий рядок Hybrid/Remote. Опис тримається від "About the job", тому
    location мусить нести і місто, і remote-маркер — location_ok дивиться сюди."""
    location = ""
    workplace = ""
    head = text.split(_DESCRIPTION_MARKERS[0])[0] if _DESCRIPTION_MARKERS[0] in text else text
    for line in head.splitlines():
        if not location:
            m = _LOCATION_LINE_RE.match(line.strip())
            if m:
                location = m.group(1)
        elif _WORKPLACE_RE.match(line):
            workplace = line.strip()
            break
    if not location:
        return "Deutschland"
    return f"{location} ({workplace})" if workplace else location


class LinkedInAdapter:
    NAME = "linkedin"

    def extract(self, page: PageFetch) -> JobData:
        soup = BeautifulSoup(page.html or page.text, "html.parser")
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else ""
        company = ""
        for sel in [".job-details-jobs-unified-top-card__company-name", "a[data-tracking-control-name='public_jobs_topcard-org-name']"]:
            el = soup.select_one(sel)
            if el:
                company = el.get_text(strip=True)
                break

        # Logged-in SPA: <h1>/top-card відсутні на domcontentloaded,
        # але <title> = "Job Title | Company | LinkedIn"
        if (not title or not company) and soup.title:
            parts = [p.strip() for p in soup.title.get_text().split("|")]
            if len(parts) >= 3 and parts[-1] == "LinkedIn":
                title = title or " | ".join(parts[:-2])
                company = company or parts[-2]

        description = page.text
        for marker in _DESCRIPTION_MARKERS:
            idx = description.find(marker)
            if idx != -1:
                description = description[idx:]
                break

        return JobData(
            title=title or "LinkedIn Job",
            company=company or "Unbekannt",
            location=_extract_location(page.text),
            description=description[:3000],
            email=extract_email(page.text, "linkedin.com"),
            application_url=detect_portal(page.text),
            source_url=page.url,
            is_portal=True,
        )
