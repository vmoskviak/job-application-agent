"""Ліміт опису, який discover і HN-скрапер передають у фільтр.

500 знаків для de_employment (розмір дайджесту), 3000 для contract: країна,
ставка й «Freelancers should refrain» стоять глибше в тексті (22.10.2026)."""
from bot.generators import discover
from bot.scrapers import hn_whoishiring


def test_description_limit_per_market():
    assert discover.description_limit("de_employment") == 500
    assert discover.description_limit("contract") == 3000


def test_hn_rows_use_same_limit():
    assert hn_whoishiring.description_limit is discover.description_limit
