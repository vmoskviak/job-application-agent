from bot.scrapers.adapters.generic import GenericAdapter
from bot.scrapers.adapters.stepstone import StepstoneAdapter
from bot.scrapers.adapters.linkedin import LinkedInAdapter
from bot.scrapers.adapters.xing import XingAdapter
from bot.scrapers.adapters.gulp import GulpAdapter
from bot.scrapers.adapters.indeed import IndeedAdapter

_ADAPTERS = [
    ("stepstone.de", StepstoneAdapter),
    ("linkedin.com", LinkedInAdapter),
    ("xing.com", XingAdapter),
    ("gulp.de", GulpAdapter),
    ("indeed.com", IndeedAdapter),
    ("indeed.de", IndeedAdapter),
]


def get_adapter(url: str):
    for domain, cls in _ADAPTERS:
        if domain in url:
            return cls()
    return GenericAdapter()
