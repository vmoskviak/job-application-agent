"""Скоринг реальних заголовків, які пайплайн проґавив 27.07.2026.

Обидва ліди Musterfirma (Düsseldorf, Office/Verwaltung) стояли в ручному
списку ранкового звіту як Tier-1, але в digest мали score 0 (порожній скрейп)
і score 12 (нема синоніма в жодній категорії). MIN_SCORE = 25.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.filters.rules import MIN_SCORE, PRIORITY_ORDER
from bot.generators.anschreiben_templates import get_template
from bot.generators.discover import score_job
from bot.generators.keywords import CATEGORIES, SALARY_BY_CATEGORY, VARIANT_BY_CATEGORY

# Скорочені, але дослівні шматки описів з живого скрейпу.
AVIVAR_OFFICE = (
    "Assistenz / Office Management (m/w/d) - Kziff. CL31AV",
    "Sie sind organisiert, kommunikationsstark und möchten Ihre kaufmännischen "
    "Fähigkeiten in einem modernen Arbeitsumfeld einbringen? Für unseren Kunden in "
    "Düsseldorf suchen wir eine Assistenz im Office Management. Sie übernehmen die "
    "Büroorganisation, Terminplanung und Korrespondenz.",
)
AVIVAR_EMPFANG = (
    "Mitarbeiter Verwaltung / Empfang / Organisation (m/w/d) - Kziff. CL38AV",
    "Sie sind ein Organisationstalent, arbeiten serviceorientiert und behalten auch "
    "in einem abwechslungsreichen Arbeitsalltag den Überblick? Zu Ihren Aufgaben "
    "gehören der Empfang von Gästen, die Büroorganisation sowie allgemeine "
    "Verwaltungsaufgaben und Teamorganisation.",
)


def test_office_management_lead_passes_min_score():
    score, cat = score_job(*AVIVAR_OFFICE)
    assert score >= MIN_SCORE, f"{score} < {MIN_SCORE}, category={cat}"
    assert cat == "B4_assistenz"


def test_empfang_lead_passes_min_score():
    score, cat = score_job(*AVIVAR_EMPFANG)
    assert score >= MIN_SCORE, f"{score} < {MIN_SCORE}, category={cat}"
    assert cat == "B9_empfang"


def test_empfang_ranks_below_assistenz():
    """Tier-3 не має обганяти Tier-1 у черзі подач."""
    assert PRIORITY_ORDER["B9_empfang"] > PRIORITY_ORDER["B4_assistenz"]


def test_every_category_has_salary_variant_and_template():
    for cat in CATEGORIES:
        assert cat in SALARY_BY_CATEGORY, f"{cat}: нема вилки"
        assert cat in VARIANT_BY_CATEGORY, f"{cat}: нема CV-варіанта"
        if cat.startswith("B"):
            assert get_template(cat) is not None, f"{cat}: нема шаблону Anschreiben"


def test_salary_defined_for_every_category():
    assert set(SALARY_BY_CATEGORY) == set(CATEGORIES)
    assert set(SALARY_BY_CATEGORY.values()) == {"48000"}
