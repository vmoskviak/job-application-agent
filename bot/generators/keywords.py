"""Keyword lists per job category for scoring intake results.

Категорії (треки) задає клієнт у `tracks.yaml`: ключові слова для скорингу,
варіант CV і зарплатне очікування на трек.
"""

from bot import client as _client

_tracks: dict[str, dict] = _client.active().tracks.get("tracks", {})
_default_salary = str(_client.active().profile.get("default_salary", "") or "")

CATEGORIES: dict[str, list[str]] = {
    name: list(t.get("keywords", [])) for name, t in _tracks.items()
}

# Salary defaults per category (Brutto/Jahr)
SALARY_BY_CATEGORY: dict[str, str] = {
    name: str(t.get("salary", _default_salary)) for name, t in _tracks.items()
}

# experience_variant default per category
VARIANT_BY_CATEGORY: dict[str, str] = {
    name: t["variant"] for name, t in _tracks.items()
}
