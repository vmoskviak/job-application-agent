from bot.scrapers.adapters.generic import GenericAdapter
from bot.scrapers.adapters.stepstone import StepstoneAdapter
from bot.scrapers.adapters.linkedin import LinkedInAdapter
from bot.scrapers.adapters.xing import XingAdapter

_ADAPTERS = [
    ("stepstone.de", StepstoneAdapter),
    ("linkedin.com", LinkedInAdapter),
    ("xing.com", XingAdapter),
]


def get_adapter(url: str):
    for domain, cls in _ADAPTERS:
        if domain in url:
            return cls()
    return GenericAdapter()
