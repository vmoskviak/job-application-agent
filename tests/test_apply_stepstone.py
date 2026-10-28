"""Регресії StepStone-модуля з ручної черги 28.07.

1. Chemhandel: профільний quick-apply засабмітив сам одразу після заповнення,
   модуль шукав submit-кнопку, не знаходив і писав form_unsupported — хоча на
   екрані вже стояло «deine Bewerbung ist raus». False negative → ручна черга.
2. Pax-Bank: DRAWER_OVERLAY перехоплював pointer events, звичайний клік падав
   таймаутом → failed. JS-клік іде повз hit-testing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import bot.apply.stepstone as stepstone
from bot.apply import detect, forms

URL = "https://www.stepstone.de/stellenangebote--Test--1-inline.html"


class FakeLocator:
    def __init__(self, page, sel):
        self.page = page
        self.sel = sel
        self.first = self

    def is_visible(self, timeout=None):
        return True

    def is_enabled(self, timeout=None):
        return True

    def click(self, timeout=None):
        if "submit" in self.sel.lower() or "abschicken" in self.sel.lower():
            self.page.submit_click_attempts += 1
            if self.page.submit_click_raises:
                raise Exception(
                    "DRAWER_OVERLAY subtree intercepts pointer events")
        # apply-кнопка або submit без оверлею — ок

    def evaluate(self, script):
        self.page.js_clicks.append(self.sel)


class FakePage:
    url = URL

    def __init__(self, submit_click_raises=False):
        self.submit_click_raises = submit_click_raises
        self.submit_click_attempts = 0
        self.js_clicks = []

    def locator(self, sel):
        return FakeLocator(self, sel)

    def wait_for_timeout(self, ms):
        pass

    def content(self):
        return ""

    def inner_text(self, sel):
        return ""


def _patch_flow(monkeypatch, confirmations):
    """confirmations — черга відповідей find_confirmation (список, mutable)."""
    monkeypatch.setattr(detect, "detect", lambda page, url: "stepstone_quick")
    monkeypatch.setattr(detect, "classify_apply_page", lambda url, html: "stepstone_quick")
    monkeypatch.setattr(forms, "accept_cookies", lambda page: None)
    monkeypatch.setattr(forms, "fill_known_fields", lambda page: [])
    monkeypatch.setattr(forms, "fill_extended_fields", lambda page, salary, source=None: [])
    monkeypatch.setattr(forms, "upload_documents_mapped",
                        lambda page, cv, anschreiben, zeugnisse: [])
    monkeypatch.setattr(forms, "screenshot", lambda page, app_dir, name: name)
    monkeypatch.setattr(forms, "find_confirmation",
                        lambda page: confirmations.pop(0) if confirmations else "")


def test_auto_submitted_quick_apply_is_submitted_not_unsupported(monkeypatch, tmp_path):
    # підтвердження вже на екрані ДО пошуку submit-кнопки
    _patch_flow(monkeypatch, ["bewerbung ist raus"])
    page = FakePage()
    result = stepstone.apply(page, URL, tmp_path, [], dry_run=False)
    assert result.status == "submitted"
    assert result.confirmation_text == "bewerbung ist raus"
    assert "auto-submitted" in result.reason
    assert page.submit_click_attempts == 0  # кнопки нема і не треба


def test_auto_submit_during_dry_run_reported_honestly(monkeypatch, tmp_path):
    _patch_flow(monkeypatch, ["bewerbung ist raus"])
    result = stepstone.apply(FakePage(), URL, tmp_path, [], dry_run=True)
    assert result.status == "submitted"  # факт подачі, не намір
    assert "dry-run intended" in result.reason


def test_overlay_intercepted_click_falls_back_to_js(monkeypatch, tmp_path):
    # перший find_confirmation порожній (форма ще не подана),
    # другий — після JS-кліку — підтвердження
    _patch_flow(monkeypatch, ["", "bewerbung ist raus"])
    page = FakePage(submit_click_raises=True)
    result = stepstone.apply(page, URL, tmp_path, [], dry_run=False)
    assert result.status == "submitted"
    assert page.submit_click_attempts == 1
    assert len(page.js_clicks) == 1  # fallback спрацював


def test_normal_submit_click_still_works(monkeypatch, tmp_path):
    _patch_flow(monkeypatch, ["", "bewerbung ist raus"])
    page = FakePage(submit_click_raises=False)
    result = stepstone.apply(page, URL, tmp_path, [], dry_run=False)
    assert result.status == "submitted"
    assert page.js_clicks == []  # JS-fallback не чіпався

def test_confirmation_patterns_stepstone_2026_final_page_only():
    """Інтерстишл «Bewerbung wird …» не є підтвердженням: після нього StepStone
    може передати заявку на сайт роботодавця."""
    from bot.apply import forms
    class P:
        def __init__(self, body): self._b = body
        def inner_text(self, sel): return self._b
    assert forms.find_confirmation(P("Deine Bewerbung ist auf dem Weg. Bewerbungsschluss 17.10.2026"))
    assert forms.find_confirmation(P("Geschafft, deine Bewerbung ist raus!"))
    assert not forms.find_confirmation(P("Bewerbung wird vorbereitet. Das sollte nur wenige Sekunden dauern"))
    assert not forms.find_confirmation(P("Lass uns deine Bewerbung abschließen"))


def test_external_handoff_page_is_recognised():
    from bot.apply.stepstone import is_external_handoff
    assert is_external_handoff("Hoppla, da ist was schiefgelaufen. Du musst diese Bewerbung auf der "
                               "Unternehmensseite weiterführen. Bewerbung fortführen")
    assert not is_external_handoff("Deine Bewerbung ist auf dem Weg")


def test_bewerbung_versendet_is_confirmation():
    """Фінальна сторінка 08.10.2026: «Bewerbung versendet! Du bist jetzt offiziell im Rennen»."""
    from bot.apply import forms
    class P:
        def inner_text(self, sel): return "Bewerbung versendet! Du bist jetzt offiziell im Rennen um diese Stelle"
    assert forms.find_confirmation(P())


def test_shows_already_applied_reads_apply_button():
    from bot.apply.stepstone import shows_already_applied
    class Btn:
        def __init__(self, t): self.t = t
        def inner_text(self): return self.t
    class Loc:
        def __init__(self, texts): self.texts = texts
        def count(self): return len(self.texts)
        def nth(self, i): return Btn(self.texts[i])
    class Page:
        def __init__(self, texts): self.texts = texts
        def locator(self, sel): return Loc(self.texts)
    assert shows_already_applied(Page(["", "Schon beworben"]))
    assert not shows_already_applied(Page(["Ich bin interessiert"]))


def test_plain_select_choice_currency_and_source():
    """Кастомні питання ATS у StepStone-формі (23.10.2026): обов'язкові «Währung»
    і «Wie haben Sie von der Position erfahren» лишались порожніми, submit
    неактивний, заявка йшла в manual."""
    from bot.apply.forms import plain_select_choice

    cur = [("1", "USD"), ("2", "EUR"), ("3", "CHF")]
    assert plain_select_choice("währung *", cur) == ("2", "währung")
    src = [("a", "Zeitung"), ("b", "StepStone"), ("c", "Sonstiges")]
    assert plain_select_choice("wie haben sie von der position erfahren?", src,
                               source="StepStone") == ("b", "quelle:StepStone")
    # без порталу або без такої опції — не вгадуємо
    assert plain_select_choice("wie haben sie von der position erfahren?", src) == (None, None)
    assert plain_select_choice("wie haben sie von der position erfahren?",
                               [("a", "Zeitung")], source="StepStone") == (None, None)
    assert plain_select_choice("haben sie einen führerschein?", [("j", "Ja"), ("n", "Nein")]) == (None, None)


def test_generic_value_label_needs_group_question():
    from bot.apply.forms import is_generic_label

    assert is_generic_label("value *") and is_generic_label("") and is_generic_label("Wert")
    assert is_generic_label("question-7f3a-value value *")
    assert not is_generic_label("gehaltsvorstellung value")
