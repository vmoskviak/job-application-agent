"""Профілі ринків: що відрізняє контрактного клієнта від клієнта на штатну посаду.

Модуль не читає конфіг клієнта на імпорті: ринок передається параметром.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MarketProfile:
    name: str
    default_lang: str
    letter_kind: str
    required_profile: tuple[str, ...]
    subject_templates: dict[str, str] = field(default_factory=dict)


MARKETS: dict[str, MarketProfile] = {
    "de_employment": MarketProfile(
        name="de_employment",
        default_lang="de",
        letter_kind="anschreiben",
        required_profile=(
            "first_name", "last_name", "salutation", "email", "phone", "street",
            "zip_code", "city", "nationality", "birth_date", "default_salary",
        ),
        subject_templates={},
    ),
    "contract": MarketProfile(
        name="contract",
        default_lang="en",
        letter_kind="pitch",
        required_profile=(
            "first_name", "last_name", "email", "phone", "city", "country",
            "market", "rate",
        ),
        subject_templates={
            "en": "Contract: {role} · {name}",
            "de": "Anfrage: {role} · {name}",
        },
    ),
}


def get(name: str) -> MarketProfile:
    try:
        return MARKETS[name]
    except KeyError:
        raise ValueError(f"невідомий ринок '{name}', доступні: {', '.join(MARKETS)}")


# Скільки знаків опису discover і HN-скрапер передають у фільтр і в нотатки.
# Контрактний ринок потребує більше: країна («Remote anywhere from Poland»),
# місячна ставка й «Freelancers should refrain» стоять глибше 500 знаків (22.10.2026).
DESCRIPTION_LIMITS: dict[str, int] = {"de_employment": 500, "contract": 3000}


def description_limit(market: str) -> int:
    return DESCRIPTION_LIMITS.get(market, 500)
