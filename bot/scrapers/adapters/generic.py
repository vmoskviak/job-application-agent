from bot.generators.intake import parse_text, JobData
from bot.scrapers.models import PageFetch


class GenericAdapter:
    NAME = "generic"

    def extract(self, page: PageFetch) -> JobData:
        return parse_text(page.text, source_url=page.url)
