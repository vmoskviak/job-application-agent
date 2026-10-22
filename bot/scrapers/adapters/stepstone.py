from __future__ import annotations
import json
from bs4 import BeautifulSoup

from bot.generators.intake import JobData, extract_email, detect_portal
from bot.scrapers.models import PageFetch


class StepstoneAdapter:
    NAME = "stepstone"

    def extract(self, page: PageFetch) -> JobData:
        soup = BeautifulSoup(page.html or page.text, "html.parser")
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == "JobPosting":
                    title = item.get("title") or "Unbekannt"
                    company = (item.get("hiringOrganization") or {}).get("name") or "Unbekannt"
                    loc_obj = item.get("jobLocation") or {}
                    if isinstance(loc_obj, list):
                        loc_obj = loc_obj[0] if loc_obj else {}
                    address = loc_obj.get("address") or {}
                    location = address.get("addressLocality") or "Deutschland"
                    # schema.org: TELECOMMUTE = повністю віддалена вакансія;
                    # location_ok із remote_policy: full дивиться лише сюди
                    if item.get("jobLocationType") == "TELECOMMUTE":
                        location += " (Remote)"
                    desc = item.get("description") or page.text
                    if isinstance(desc, str):
                        desc_clean = BeautifulSoup(desc, "html.parser").get_text("\n", strip=True)
                    else:
                        desc_clean = page.text
                    email = extract_email(page.html + page.text, "stepstone.de")
                    portal = detect_portal(page.text)
                    return JobData(
                        title=title,
                        company=company,
                        location=location,
                        description=desc_clean[:3000],
                        email=email,
                        application_url=portal,
                        source_url=page.url,
                        is_portal=portal is not None,
                    )
        # DOM fallback
        title_el = soup.select_one('[data-at="header-job-title"]') or soup.find("h1")
        title = title_el.get_text(strip=True) if title_el else page.text.split("\n")[0][:120]
        company_el = soup.select_one('[data-at="metadata-company-name"]')
        company = company_el.get_text(strip=True) if company_el else "Unbekannt"
        return JobData(
            title=title,
            company=company,
            location="Deutschland",
            description=page.text[:3000],
            email=extract_email(page.html + page.text, "stepstone.de"),
            application_url=detect_portal(page.text),
            source_url=page.url,
            is_portal=detect_portal(page.text) is not None,
        )
