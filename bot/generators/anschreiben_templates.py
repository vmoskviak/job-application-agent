"""
Anschreiben paragraph blocks per track.

Used by Claude in chat for faster batch tailoring.
Not called from Python pipelines directly — reference module only.

Блоки живуть у `tracks.yaml` клієнта, ключ `letter` треку. Each template
dict has four keys:
  anlass    — opening paragraph (why this company / role)
  fit       — core competence paragraph
  transfer  — transferable-skills / motivation closing paragraph
  salary_hint — Brutto/Jahr target as string
"""

from typing import Optional

from bot import client as _client

_TEMPLATES: dict[str, dict] = {
    name: dict(t["letter"])
    for name, t in _client.active().tracks.get("tracks", {}).items()
    if t.get("letter")
}

CATEGORIES_WITH_TEMPLATES: set[str] = set(_TEMPLATES)

# Alias for convenience
ALL_CATEGORIES = CATEGORIES_WITH_TEMPLATES

# ── Public API ────────────────────────────────────────────────────────────────


def get_template(category: str) -> Optional[dict]:
    """Return the Anschreiben template dict for a Büro category, or None.

    Returns None for IT categories (T1–T4) and unknown category names.
    """
    return _TEMPLATES.get(category)
