from __future__ import annotations
import json
from bs4 import BeautifulSoup

from bot.generators.intake import JobData, extract_email, detect_portal
from bot.scrapers.models import PageFetch


def _location_from_address(address: dict) -> str:
    """«Düsseldorf, Nordrhein-Westfalen» — регіон тримаємо навмисно: NRW-фільтр
    впізнає землю навіть тоді, коли самого міста нема в NRW_LOCATIONS."""
    city = (address.get("addressLocality") or "").strip()
    region = (address.get("addressRegion") or "").strip()
    parts = [p for p in (city, region) if p]
    return ", ".join(parts) if parts else "Deutschland"


class XingAdapter:
    NAME = "xing"

    def extract(self, page: PageFetch) -> JobData:
        soup = BeautifulSoup(page.html or page.text, "html.parser")
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                    continue
                loc_obj = item.get("jobLocation") or {}
                if isinstance(loc_obj, list):
                    loc_obj = loc_obj[0] if loc_obj else {}
                desc = item.get("description")
                if isinstance(desc, str):
                    desc_clean = BeautifulSoup(desc, "html.parser").get_text("\n", strip=True)
                else:
                    desc_clean = page.text
                portal = detect_portal(page.text)
                location = _location_from_address(loc_obj.get("address") or {})
                if item.get("jobLocationType") == "TELECOMMUTE":
                    location += " (Remote)"
                return JobData(
                    title=item.get("title") or "Unbekannt",
                    company=(item.get("hiringOrganization") or {}).get("name") or "Unbekannt",
                    location=location,
                    description=desc_clean[:3000],
                    email=extract_email((page.html or "") + page.text, "xing.com"),
                    application_url=portal,
                    source_url=item.get("url") or page.url,
                    is_portal=True,
                )

        # DOM fallback: og:description має вигляд
        # "Bewirb Dich als 'X' bei <Firma> in <Місто>. Branche: ..."
        title_el = soup.find("h1")
        title = title_el.get_text(strip=True) if title_el else "XING Job"
        company, location = "Unbekannt", "Deutschland"
        og = soup.find("meta", property="og:description")
        if og and og.get("content"):
            head = og["content"].split(". Branche")[0]
            if " bei " in head:
                rest = head.split(" bei ", 1)[1]
                if " in " in rest:
                    company, location = (p.strip() for p in rest.rsplit(" in ", 1))
                else:
                    company = rest.strip()
        return JobData(
            title=title,
            company=company,
            location=location,
            description=page.text[:3000],
            email=extract_email((page.html or "") + page.text, "xing.com"),
            application_url=detect_portal(page.text),
            source_url=page.url,
            is_portal=True,
        )
