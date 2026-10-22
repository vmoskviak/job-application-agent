import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.generators.cv_engine import build_cv_data, make_cv_pdf, make_cv_docx

PHOTO_PATH = Path(__file__).parent.parent / "examples" / "clients" / "erika" / "photo.jpeg"


def _sample_cv(photo_path=None):
    return build_cv_data(
        title="Test Analyst",
        profile="Test profile sentence for CV rendering.",
        kernkompetenzen=[("Tools", "Jira · Confluence")],
        experience_variant="ba",
        skills_order="ba",
        photo_path=photo_path,
    )


def test_pdf_without_photo():
    cv_data = _sample_cv(photo_path=None)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        out = Path(f.name)
    make_cv_pdf(cv_data, out)
    assert out.exists()
    assert out.stat().st_size > 10_000, f"PDF too small: {out.stat().st_size}"


def _rendered_size(make, suffix, photo_path):
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        out = Path(f.name)
    make(_sample_cv(photo_path=photo_path), out)
    return out.stat().st_size


def test_pdf_with_photo():
    assert PHOTO_PATH.exists(), f"Photo not found: {PHOTO_PATH}"
    with_photo = _rendered_size(make_cv_pdf, ".pdf", PHOTO_PATH)
    without = _rendered_size(make_cv_pdf, ".pdf", None)
    assert with_photo > without, "фото не потрапило в PDF"


def test_docx_with_photo():
    assert PHOTO_PATH.exists(), f"Photo not found: {PHOTO_PATH}"
    with_photo = _rendered_size(make_cv_docx, ".docx", PHOTO_PATH)
    without = _rendered_size(make_cv_docx, ".docx", None)
    assert with_photo > without, "фото не потрапило в DOCX"


FULL_LETTER = """Erika Mustermann
Musterstraße 1
40210 Düsseldorf

Düsseldorf, 14.07.2026

Musterkontext GmbH
z. Hd. Recruiting
Frechen

Bewerbung als Mitarbeiter Seminar- und Veranstaltungsorganisation (m/w/d)

Sehr geehrte Damen und Herren,

Sie suchen jemanden, der gerne organisiert.

Bei Beispiel Logistik habe ich Workshops organisiert.

Über ein Gespräch freue ich mich.

Mit freundlichen Grüßen

Erika Mustermann

Anlagen:
- Lebenslauf
- Arbeitszeugnisse"""


def test_letter_body_strips_full_letter_header_and_signature():
    from bot.generators.cv_engine import _letter_body
    body = _letter_body(FULL_LETTER)
    assert body == [
        "Sie suchen jemanden, der gerne organisiert.",
        "Bei Beispiel Logistik habe ich Workshops organisiert.",
        "Über ein Gespräch freue ich mich.",
    ]


def test_letter_body_strips_signature_before_closing():
    # реальний кейс: підпис і Anlagen стоять ДО "Mit freundlichen Grüßen"
    text = ("Sehr geehrte Damen und Herren,\n\nErster Absatz.\n\n"
            "Erika Mustermann\n\nAnlagen: - Lebenslauf\n\n"
            "Mit freundlichen Grüßen\n\nErika Mustermann")
    from bot.generators.cv_engine import _letter_body
    assert _letter_body(text) == ["Erster Absatz."]


def test_letter_body_keeps_body_only_text_unchanged():
    from bot.generators.cv_engine import _letter_body
    text = "Erster Absatz.\n\nZweiter Absatz mit Grüßen aus Düsseldorf."
    assert _letter_body(text) == [
        "Erster Absatz.",
        "Zweiter Absatz mit Grüßen aus Düsseldorf.",
    ]


def test_sprache_variant_uses_own_bullets_everywhere():
    """Варіант CV мусить бути наскрізним: буліти й скіли з одного варіанта."""
    cv = build_cv_data(
        title="Sprachmittlerin", profile="p", kernkompetenzen=[],
        experience_variant="sprache", skills_order="sprache",
    )
    def bullets_of(needle):
        return [b for j in cv["experience"] if needle in j["company"] for b in j["bullets"]]
    assert any("Aufbereitung" in b for b in bullets_of("Beispiel Logistik"))
    # запис без власних sprache-булітів бере нейтральні, а не чужий варіант
    assert bullets_of("Muster Versicherung") == ["Bearbeitung von Anträgen und Schadenmeldungen"]
    labels = [label for label, _ in cv["skills"]]
    assert "Vermittlung" in labels


def test_ehrenamt_only_for_people_facing_variants():
    for variant in ("sprache", "buero"):
        cv = build_cv_data(title="t", profile="p", kernkompetenzen=[],
                           experience_variant=variant, skills_order=variant)
        assert cv["ehrenamt"], f"{variant}: Ehrenamt має бути"
        assert "Musterverein" in cv["ehrenamt"][0]["org"]
    for variant in ("agile", "ba", "trainee", "sachbearbeitung"):
        cv = build_cv_data(title="t", profile="p", kernkompetenzen=[],
                           experience_variant=variant, skills_order=variant)
        assert cv["ehrenamt"] == [], f"{variant}: Ehrenamt має бути прихований"


def test_ehrenamt_reaches_rendered_documents():
    cv = build_cv_data(title="Dolmetscherin", profile="p", kernkompetenzen=[],
                       experience_variant="sprache", skills_order="sprache")
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "cv.pdf"
        make_cv_pdf(cv, pdf)
        assert pdf.stat().st_size > 10_000
        docx_path = Path(tmp) / "cv.docx"
        make_cv_docx(cv, docx_path)
        import zipfile
        xml = zipfile.ZipFile(docx_path).read("word/document.xml").decode()
        assert "EHRENAMTLICHES ENGAGEMENT" in xml  # sec() пише заголовки капсом
        assert "Musterverein" in xml


def test_variant_bound_experience_and_education(monkeypatch):
    """Запис досвіду чи освіти з `variants` рендериться лише в цих варіантах:
    сертифікат Scrum Master у CV на Empfang лише з'їдає місце на третій сторінці."""
    from bot.generators import cv_engine
    job = {"dates": "2023", "company": "Nur Agile GmbH", "title": "t", "bullets": ["b"]}
    monkeypatch.setitem(cv_engine._cv, "experience",
                        [*cv_engine._cv["experience"], {**job, "variants": ["agile"]}])
    monkeypatch.setattr(cv_engine, "EDUCATION", [
        *cv_engine.EDUCATION,
        {"dates": "2026", "inst": "Zertifikat-Institut", "deg": "Scrum Master", "variants": ["agile"]},
    ])
    agile = build_cv_data(title="t", profile="p", kernkompetenzen=[],
                          experience_variant="agile", skills_order="agile")
    buero = build_cv_data(title="t", profile="p", kernkompetenzen=[],
                          experience_variant="buero", skills_order="buero")
    assert any(j["company"] == "Nur Agile GmbH" for j in agile["experience"])
    assert not any(j["company"] == "Nur Agile GmbH" for j in buero["experience"])
    assert any(e["inst"] == "Zertifikat-Institut" for e in agile["education"])
    assert not any(e["inst"] == "Zertifikat-Institut" for e in buero["education"])
    # записи без `variants` лишаються скрізь
    assert len(buero["education"]) == len(agile["education"]) - 1


def _cv_html(cv_data: dict) -> str:
    """HTML, з якого WeasyPrint робить PDF: текст перевіряємо тут, бо ні
    poppler, ні pypdf на сервері немає."""
    from jinja2 import Environment, FileSystemLoader
    from bot import client as _client
    env = Environment(loader=FileSystemLoader(str(_client.ENGINE_ROOT / "cv_templates")), autoescape=True)
    return env.get_template("cv_template.html").render(**cv_data)


def test_certificates_section_rendered_per_variant(monkeypatch):
    """Окрема секція «Weiterbildung & Zertifikate»: ATS і рекрутери шукають
    сертифікати саме там, усередині Ausbildung їх не видно. Записи з `variants`
    показуються лише в цих варіантах; порожня секція не рендериться."""
    import zipfile
    from bot.generators import cv_engine
    monkeypatch.setattr(cv_engine, "CERTIFICATES", [
        {"dates": "2026", "inst": "Muster-Institut", "deg": "Scrum Master", "note": "", "variants": ["agile"]},
        {"dates": "2025", "inst": "Sprachschule", "deg": "telc B2", "note": ""},
    ])
    agile = build_cv_data(title="t", profile="p", kernkompetenzen=[],
                          experience_variant="agile", skills_order="agile")
    buero = build_cv_data(title="t", profile="p", kernkompetenzen=[],
                          experience_variant="buero", skills_order="buero")
    assert [c["deg"] for c in agile["certificates"]] == ["Scrum Master", "telc B2"]
    assert [c["deg"] for c in buero["certificates"]] == ["telc B2"]
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "cv.pdf"
        make_cv_pdf(agile, pdf)
        assert pdf.stat().st_size > 10_000
        html = _cv_html(agile)
        assert "Weiterbildung &amp; Zertifikate" in html and "Muster-Institut" in html
        docx_path = Path(tmp) / "cv.docx"
        make_cv_docx(agile, docx_path)
        xml = zipfile.ZipFile(docx_path).read("word/document.xml").decode()
        assert "WEITERBILDUNG &amp; ZERTIFIKATE" in xml and "Muster-Institut" in xml
    monkeypatch.setattr(cv_engine, "CERTIFICATES", [])
    empty = build_cv_data(title="t", profile="p", kernkompetenzen=[],
                          experience_variant="agile", skills_order="agile")
    assert empty["certificates"] == []
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "cv.pdf"
        make_cv_pdf(empty, pdf)
        assert "Zertifikate" not in _cv_html(empty)


def test_non_string_bullet_is_rejected(monkeypatch):
    """Регресія 14.10: буліт «Betreuung: Sprechstunden, …» без лапок YAML читає як
    словник, і в PDF стояло `{'Betreuung': 'Sprechstunden, …'}`. Рушій має
    впасти з назвою роботодавця, а не рендерити сміття."""
    import pytest
    from bot.generators import cv_engine
    job = {"dates": "2023", "company": "Kaputt GmbH", "title": "t",
           "bullets": {"default": [{"Betreuung": "Sprechstunden, Ein- und Auszug"}]}}
    monkeypatch.setitem(cv_engine._cv, "experience", [*cv_engine._cv["experience"], job])
    with pytest.raises(ValueError, match="Kaputt GmbH"):
        build_cv_data(title="t", profile="p", kernkompetenzen=[],
                      experience_variant="agile", skills_order="agile")


def test_unknown_variant_falls_back_without_crashing():
    cv = build_cv_data(title="t", profile="p", kernkompetenzen=[],
                       experience_variant="does-not-exist", skills_order="nope")
    assert cv["ehrenamt"] == []
    assert all(j["bullets"] for j in cv["experience"])


def test_normalize_cv_title_strips_letter_subject_line():
    from bot.generators.cv_engine import normalize_cv_title
    assert normalize_cv_title("Bewerbung als Teamassistenz (m/w/d)") == "Teamassistenz"
    assert normalize_cv_title("Bewerbung als PMO Manager (all genders)") == "PMO Manager"
    assert normalize_cv_title("Junior Scrum Master") == "Junior Scrum Master"


def test_contact_block_has_no_english_country_suffix():
    """Німецьке CV не пише «Düsseldorf, Germany»."""
    from bot.generators.cv_engine import CONTACT
    assert CONTACT[0] == "Düsseldorf"


def test_anlagen_list_matches_actually_sent_files():
    """Список Anlagen у листі мусить збігатися з тим, що реально йде вкладенням."""
    from bot.generators.cv_engine import ANLAGEN_LABELS, zeugnis_paths
    assert ANLAGEN_LABELS[0] == "Lebenslauf"
    assert len(ANLAGEN_LABELS) == len(zeugnis_paths()) + 1


def test_anlagen_list_matches_sent_files_for_every_variant():
    """Розбіжність списку і вкладень = кандидатка «забула» додати файл ."""
    from bot.generators.cv_engine import anlagen_labels, zeugnis_paths
    for variant in ("agile", "buero", "sprache", "sachbearbeitung", "ba", "trainee"):
        labels = anlagen_labels(variant)
        assert len(labels) == len(zeugnis_paths(variant)) + 1, variant


def test_variant_bound_attachment_only_for_its_variants():
    """Вкладення з `variants` у cv.yaml чіпляється лише до цих варіантів CV."""
    from bot.generators.cv_engine import zeugnis_paths
    for variant in ("sachbearbeitung", "agile"):
        names = [p.name for p in zeugnis_paths(variant)]
        assert sum("Sprachzertifikat" in n for n in names) == 1, variant
    for variant in ("buero", "sprache", None):
        names = [p.name for p in zeugnis_paths(variant)]
        assert not any("Sprachzertifikat" in n for n in names), variant


def test_portal_zeugnisse_is_flagged_subset():
    from bot.generators.cv_engine import portal_zeugnisse, zeugnis_paths
    portal = portal_zeugnisse()
    assert [p.name for p in portal] == [
        "01_Zeugnis_BeispielLogistik_2025.pdf",
        "02_Zeugnis_MusterVersicherung_2021.pdf",
    ]
    assert set(portal) <= set(zeugnis_paths())


def test_zeugnis_paths_returns_shared_attachments():
    from bot.generators.cv_engine import zeugnis_paths, ATTACHMENTS_DIR, ZEUGNIS_FILES
    paths = zeugnis_paths()
    assert len(paths) == len(ZEUGNIS_FILES)
    for p in paths:
        assert p.parent == ATTACHMENTS_DIR
        assert p.exists()


def test_zeugnis_paths_raises_on_missing_file(monkeypatch, tmp_path):
    import bot.generators.cv_engine as cv_engine
    monkeypatch.setattr(cv_engine, "ATTACHMENTS_DIR", tmp_path)
    import pytest
    with pytest.raises(FileNotFoundError):
        cv_engine.zeugnis_paths()


def test_letter_salutation_uses_named_contact_from_text():
    # Оголошення називає адресата: звертання на ім'я замість «Damen und Herren».
    from bot.generators.cv_engine import _letter_body, letter_salutation
    text = "Sehr geehrter Herr Muster,\n\nErster Absatz.\n\nZweiter Absatz."
    assert letter_salutation(text) == "Sehr geehrter Herr Muster,"
    assert _letter_body(text) == ["Erster Absatz.", "Zweiter Absatz."]


def test_letter_salutation_defaults_without_anrede():
    from bot.generators.cv_engine import DEFAULT_SALUTATION, letter_salutation
    assert letter_salutation("Erster Absatz.\n\nZweiter.") == DEFAULT_SALUTATION
    assert letter_salutation("") == DEFAULT_SALUTATION
    assert letter_salutation("Sehr geehrte Frau Beispiel\n\nText") == "Sehr geehrte Frau Beispiel,"


def test_letter_location_strips_linkedin_region_and_mode():
    # LinkedIn-локація потрапляла в шапку листа як «адреса» (16.10.2026).
    from bot.generators.cv_engine import letter_location
    assert letter_location("Düsseldorf, North Rhine-Westphalia, Germany (Hybrid)") == "Düsseldorf"
    assert letter_location("Düsseldorf, Nordrhein-Westfalen, Deutschland") == "Düsseldorf"
    assert letter_location("Köln (Remote)") == "Köln"
    assert letter_location("40213 Düsseldorf") == "40213 Düsseldorf"
    assert letter_location("Deutschland") == "Deutschland"
    assert letter_location("") == ""
