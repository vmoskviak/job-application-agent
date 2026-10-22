"""Рендер не має claim-ити відправку і не має губити Gehaltsvorstellung.

27.07: `--batch-render` друкував «✅ Email відправлено» і писав у трекер
«✅ Email sent» ще до того, як щось пішло (відправка — окремий крок Step 4).
Це той самий клас помилки, що інцидент 24.07 з фантомним `confirmed`.

Там же: власний текст `anschreiben` у батчі обходив рядок про Gehaltsvorstellung,
який у шаблонному шляху додає промпт tailor.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.generators.batch import batch_summary_report
from bot.generators.cv_engine import ensure_salary_sentence

LETTER = (
    "Sehr geehrte Damen und Herren,\n\n"
    "auf die Position bewerbe ich mich gern.\n\n"
    "Ich bin sofort verfügbar und freue mich auf ein Gespräch."
)


def test_report_does_not_claim_a_send():
    report = batch_summary_report([], ready=["A GmbH"], portal=["B AG"])
    low = report.lower()
    assert "відправлено" not in low
    assert "sent" not in low
    assert "готов" in low


def test_report_still_names_both_buckets():
    report = batch_summary_report([], ready=["A GmbH"], portal=["B AG"])
    assert "A GmbH" in report
    assert "B AG" in report


def test_salary_sentence_appended_when_missing():
    out = ensure_salary_sentence(LETTER, "42000")
    assert "Gehaltsvorstellung" in out
    assert "42.000" in out
    # додається в кінець тіла, а не перед привітанням
    assert out.index("Gehaltsvorstellung") > out.index("bewerbe ich mich")


def test_salary_sentence_not_duplicated():
    already = LETTER + "\n\nMeine Gehaltsvorstellung liegt bei 42.000 € brutto jährlich."
    assert ensure_salary_sentence(already, "42000").count("Gehaltsvorstellung") == 1


def test_salary_sentence_skipped_without_salary():
    assert ensure_salary_sentence(LETTER, "") == LETTER
    assert ensure_salary_sentence(LETTER, None) == LETTER


def test_salary_formatted_with_thousands_separator():
    assert "36.000 €" in ensure_salary_sentence(LETTER, "36000")


def test_garbage_salary_is_ignored():
    assert ensure_salary_sentence(LETTER, "nach Vereinbarung") == LETTER


# ── Anlagen vs канал подачі ──────────────────────────────────────────────────
# Портал шле лише CV + Anschreiben, а блок «Anlagen:» обіцяв
# 7 позицій — виглядало, ніби кандидатка забула вкладення. Портальний пакет
# (job.email == None) мусить друкувати чесний список: лише Lebenslauf.

def _render_capture_anlagen(monkeypatch, tmp_path, email):
    import bot.generators.cv_engine as ce
    from bot.generators.intake import JobData
    from bot.generators.tailor import TailoredData

    captured = {}
    monkeypatch.setattr(ce, "make_cv_docx", lambda *a, **k: None)
    monkeypatch.setattr(ce, "make_cv_pdf", lambda *a, **k: None)
    monkeypatch.setattr(
        ce, "make_letter_pdf",
        lambda text, job, path, anlagen=None: captured.setdefault("pdf", anlagen))
    monkeypatch.setattr(
        ce, "make_letter_docx",
        lambda text, job, path, anlagen=None: captured.setdefault("docx", anlagen))

    job = JobData(title="Assistenz", company="Test GmbH", location="Köln",
                  description="x", email=email, application_url=None,
                  source_url="https://stepstone.de/x", is_portal=email is None)
    tailored = TailoredData(
        cv_title="Assistenz", profile="p", kernkompetenzen=[["a", "b"]],
        experience_variant="buero", skills_order="buero",
        fit_score=70, fit_reason="r", salary="42000", anschreiben=LETTER)
    ce.render_application(job, tailored, tmp_path, photo_path=None)
    return captured


def test_portal_package_promises_only_lebenslauf(monkeypatch, tmp_path):
    captured = _render_capture_anlagen(monkeypatch, tmp_path, email=None)
    assert captured["pdf"] == ["Lebenslauf"]
    assert captured["docx"] == ["Lebenslauf"]


def test_email_package_keeps_full_anlagen_list(monkeypatch, tmp_path):
    captured = _render_capture_anlagen(monkeypatch, tmp_path, email="jobs@test.de")
    assert captured["pdf"][0] == "Lebenslauf"
    assert len(captured["pdf"]) > 1  # Zeugnisse обіцяні, бо email їх реально шле
