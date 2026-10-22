"""Interamt-модуль: кодифікація живої подачі Musterbehörde 28.07.2026."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot.apply.interamt as interamt
from bot.apply import forms
from bot.apply.runner import portal_from_url

URL = "https://interamt.de/koop/app/stelle?id=1234567"

GENERATE_PY = '''
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
from bot.generators.cv_engine import render_application
from bot.generators.intake import JobData
from bot.generators.tailor import TailoredData

job = JobData(title="Sachbearbeitung", company="Musterbehörde", location="Köln",
              description="x", email=None, application_url=None,
              source_url="u", is_portal=True)
tailored = TailoredData(
    cv_title="t", profile="p", kernkompetenzen=[["a", "b"]],
    experience_variant="sachbearbeitung", skills_order="sachbearbeitung",
    fit_score=80, fit_reason="r", salary="42000",
    anschreiben='die Mittel zu prüfen ist meine Arbeit.\\n\\nIch bin sofort verfügbar.',
)
'''


def test_portal_from_url_maps_interamt():
    assert portal_from_url("https://interamt.de/koop/app/stelle?id=1") == "interamt"
    assert portal_from_url("https://www.interamt.de/koop/app/trefferliste") == "interamt"


def test_read_anschreiben_text_via_ast(tmp_path):
    (tmp_path / "generate.py").write_text(GENERATE_PY, encoding="utf-8")
    text = interamt.read_anschreiben_text(tmp_path)
    assert text.startswith("die Mittel zu prüfen")
    assert "\n\n" in text  # ast розгортає екрановані \n у справжні


def test_read_anschreiben_missing_or_broken(tmp_path):
    assert interamt.read_anschreiben_text(tmp_path) is None
    (tmp_path / "generate.py").write_text("def broken(:", encoding="utf-8")
    assert interamt.read_anschreiben_text(tmp_path) is None


def test_letter_html_adds_anrede_and_gruss():
    html = interamt.letter_html(
        "Sehr geehrte Damen und Herren,\n\nText hier.\n\n"
        "Mit freundlichen Grüßen\n\nErika Mustermann\n\nAnlagen: CV"
    )
    # Anrede один раз, хвости (Gruß/підпис/Anlagen вихідного тексту) зрізані
    assert html.count("Sehr geehrte") == 1
    assert html.count("Mit freundlichen Grüßen") == 1
    assert "Anlagen" not in html
    assert "<p>Text hier.</p>" in html


def test_slot_file_mapping(tmp_path):
    f = interamt.slot_file
    assert f("Anschreiben / Motivationsschreiben", tmp_path).name == "01_Anschreiben.pdf"
    assert f("Lebenslauf", tmp_path).name == "Erika_Mustermann_CV.pdf"
    assert f("Arbeitszeugnis(se) / Beurteilung(en)", tmp_path).name == "Arbeitszeugnisse.pdf"
    assert f("Abschlusszeugnis Studium bzw. Zeugnis 2. Staatsexamen", tmp_path).name == \
        "Studienabschluesse.pdf"
    assert f("Abschlusszeugnis Schule", tmp_path).name == \
        "Abschlusszeugnis_Schule.pdf"
    assert f("Nachweis Sprachkenntnisse", tmp_path).name == "Sprachnachweis.pdf"
    # свідомо ігноровані та незнайомі слоти
    assert f("Lichtbild (nicht verpflichtend)", tmp_path) is None
    assert f("Wehr-/Zivildienstbescheinigung", tmp_path) is None
    assert f("Führungszeugnis", tmp_path) is None


# ── fake-page флоу ────────────────────────────────────────────────────────────

class FakeLocator:
    def __init__(self, page, sel):
        self.page = page
        self.sel = sel
        self.first = self

    def count(self):
        return 1 if self.page._matches(self.sel) else 0

    def is_visible(self, timeout=None):
        return self.page._matches(self.sel)

    def click(self, timeout=None):
        self.page.clicked.append(self.sel)
        self.page._on_click(self.sel)

    def set_input_files(self, path):
        self.page.uploads.append((self.sel, path))


class FakePage:
    """Мінімальна модель Wicket-візарда: title перемикається по кліках."""

    def __init__(self, logged=True, feedback_on=None):
        self.step = "Stellenangebot"
        self.logged = logged
        self.feedback_on = feedback_on or {}
        self.clicked = []
        self.uploads = []
        self.ck_html = None
        self.submitted = False
        self.save_attempted = set()
        self._steps = ["Anschreiben", "Bewerbungsbogen", "Lebenslauf",
                       "Anlagen", "Abschluss"]

    _KNOWN = ("speichern und weiter", "Anschreiben neu erstellen",
              "Bewerbungsprofil übernehmen", "Bewerbungs-Profil übernehmen",
              "input[type=file]")

    def _matches(self, sel):
        if "Online Bewerben" in sel:
            return self.step == "Stellenangebot"
        if "Online absenden" in sel:
            return self.step == "Abschluss"
        if "value='ja'" in sel or ":has-text('ja')" in sel:
            return self.step == "confirm"
        return any(k in sel for k in self._KNOWN)

    def _on_click(self, sel):
        if "Online Bewerben" in sel:
            self.step = "Anschreiben"
        elif "speichern und weiter" in sel:
            self.save_attempted.add(self.step)
            if self.step not in self.feedback_on:
                i = self._steps.index(self.step)
                self.step = self._steps[i + 1]
        elif "Online absenden" in sel:
            self.step = "confirm"
        elif "value='ja'" in sel or ":has-text('ja')" in sel:
            self.step = "liste"
            self.submitted = True

    # playwright API
    def goto(self, url, wait_until=None, timeout=None):
        pass

    def wait_for_timeout(self, ms):
        pass

    def title(self):
        return f"{self.step} | Mein INTERAMT"

    def locator(self, sel):
        return FakeLocator(self, sel)

    def evaluate(self, js, arg=None):
        if "CKEDITOR" in js:
            self.ck_html = arg
            return True
        if "input[type=file]" in js:
            return [
                {"i": 0, "name": "obl:0:file", "label": "Anschreiben / Motivationsschreiben", "done": False},
                {"i": 1, "name": "obl:1:file", "label": "Lebenslauf", "done": False},
                {"i": 2, "name": "obl:2:file", "label": "Lichtbild (nicht verpflichtend)", "done": False},
            ]
        return None

    def inner_text(self, sel):
        if self.step == "confirm":
            return "Logout\nMöchten Sie Ihre Bewerbung jetzt absenden?\nnein ja"
        if self.step == "liste":
            return "Logout\nBewerbungen\n1234567\nMusterbehörde\n28.07.2026"
        base = "Logout\nMein INTERAMT" if self.logged else "Registrieren\nLogin\nMein INTERAMT"
        if self.step in self.feedback_on and self.step in self.save_attempted:
            return base + "\nBitte überprüfen Sie die Eingabe in den folgenden Feldern:\n" \
                + self.feedback_on[self.step]
        if self.step == "Abschluss":
            return base + "\nFolgende Pflichtanlagen fehlen noch: Lichtbild (nicht verpflichtend)"
        return base

    def screenshot(self, path=None, full_page=False):
        Path(path).write_bytes(b"png")


def _package(tmp_path):
    (tmp_path / "generate.py").write_text(GENERATE_PY, encoding="utf-8")
    to_send = tmp_path / "to_send"
    to_send.mkdir()
    (to_send / "01_Anschreiben.pdf").write_bytes(b"x")
    (to_send / "Erika_Mustermann_CV.pdf").write_bytes(b"x")
    return tmp_path


def test_login_required_without_session(tmp_path):
    page = FakePage(logged=False)
    result = interamt.apply(page, URL, _package(tmp_path), [], dry_run=False)
    assert result.status == "login_required"
    assert "interamt_repl" in result.reason


def test_happy_path_submits_and_confirms(tmp_path):
    page = FakePage()
    result = interamt.apply(page, URL, _package(tmp_path), [], dry_run=False)
    assert result.status == "submitted"
    assert "1234567" in result.confirmation_text
    assert page.submitted
    assert page.ck_html and "Sehr geehrte" in page.ck_html
    # Anschreiben і CV залиті, Lichtbild пропущено
    assert len(page.uploads) == 2


def test_dry_run_stops_before_absenden(tmp_path):
    page = FakePage()
    result = interamt.apply(page, URL, _package(tmp_path), [], dry_run=True)
    assert result.status == "dry_run"
    assert not page.submitted
    assert not any("Online absenden" in c for c in page.clicked)
    assert "Pflichtanlagen fehlen" in result.reason


def test_behoerde_specific_bogen_fields_fail_safe(tmp_path):
    page = FakePage(feedback_on={"Bewerbungsbogen": "Amtsbezeichnung: Pflichtfeld."})
    result = interamt.apply(page, URL, _package(tmp_path), [], dry_run=False)
    assert result.status == "form_unsupported"
    assert "Bewerbungsbogen" in result.reason
    assert "Amtsbezeichnung" in result.reason
    assert not page.submitted


def test_missing_anschreiben_text_fails_early(tmp_path):
    to_send = tmp_path / "to_send"
    to_send.mkdir()
    page = FakePage()
    result = interamt.apply(page, URL, tmp_path, [], dry_run=False)
    assert result.status == "form_unsupported"
    assert "generate.py" in result.reason
    assert page.clicked == []  # нікуди не клікали, пакет неповний
