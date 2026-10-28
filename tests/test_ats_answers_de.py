"""Німецькі форми роботодавців (ринок найму): класи полів і відповіді з профілю Erika."""
from datetime import date
from pathlib import Path

import pytest

from bot import client
from bot.apply.ats_answers import Answer, AnswerContext, answer_for
from bot.apply.ats_fields import Field, classify


def F(label, type="text", tag="input", required=True, options=None, group="", combobox=False, name=""):
    return Field(i=0, tag=tag, type=type, name=name, id="", required=required, label=label,
                 placeholder="", visible=True, value="", options=options, group=group, combobox=combobox)


@pytest.fixture
def ctx(tmp_path):
    return AnswerContext(profile=dict(client.active().profile), lead={"company": "Beispiel GmbH"},
                         note="Anschreiben-Text", resume=tmp_path / "Erika_Mustermann_CV.pdf",
                         today=date(2026, 10, 28), lang="de", market="de_employment",
                         cover_letter=tmp_path / "01_Anschreiben.pdf")


@pytest.mark.parametrize("label,kind", [
    ("Straße und Hausnummer", "street"), ("PLZ", "zip"), ("Ort", "city"), ("Wohnort", "city"),
    ("Land", "country"), ("Geburtsdatum", "birth_date"), ("Staatsangehörigkeit", "nationality"),
    ("Gehaltsvorstellung (brutto/Jahr)", "salary"), ("Frühestmöglicher Eintrittstermin", "date"),
    ("Wie sind Sie auf uns aufmerksam geworden?", "source"), ("Vorname", "first_name"),
    ("Telefon", "phone"), ("Standort der Stelle", "unknown"), ("Landesvorwahl", "unknown"),
])
def test_classify_german_labels(label, kind):
    assert classify(F(label)) == kind


def test_anrede_radio_and_select():
    assert classify(F("Anrede", type="radio", options=["Frau", "Herr"], group="Anrede")) == "salutation"
    assert classify(F("Anrede", tag="select", type="select", options=["Bitte wählen", "Frau", "Herr", "Divers"])) == "salutation"


def test_address_and_identity_answers(ctx):
    p = ctx.profile
    assert answer_for(F("Straße"), ctx) == Answer("text", p["street"])
    assert answer_for(F("PLZ"), ctx) == Answer("text", p["zip_code"])
    assert answer_for(F("Ort"), ctx) == Answer("text", p["city"])
    assert answer_for(F("Geburtsdatum"), ctx) == Answer("text", "15.01.1990")
    assert answer_for(F("Geburtsdatum", type="date"), ctx) == Answer("date", "1990-01-15")
    assert answer_for(F("Anrede", tag="select", type="select", options=["Bitte wählen", "Frau", "Herr"]), ctx) == Answer("option", "Frau")
    assert answer_for(F("Staatsangehörigkeit", tag="select", type="select", options=["Bitte wählen", "Deutsch", "Österreichisch"]), ctx) == Answer("option", "Deutsch")
    assert answer_for(F("Staatsangehörigkeit"), ctx) == Answer("text", "Deutsch")


def test_salary_is_annual_on_employment_market(ctx):
    assert answer_for(F("Gehaltsvorstellung"), ctx) == Answer("text", "48000 EUR")
    assert answer_for(F("Jahresgehalt", type="number"), ctx) == Answer("text", "48000")
    contract = AnswerContext(**{**ctx.__dict__, "market": "contract"})
    assert "per hour" in answer_for(F("Gehaltsvorstellung"), contract).value


def test_start_date_follows_employment_rule(ctx):
    # 1-ше наступного місяця, якщо до нього ≥ 14 днів, інакше через місяць: 28.10 → 01.12
    assert answer_for(F("Frühestmöglicher Eintrittstermin"), ctx) == Answer("text", "01.12.2026")
    assert answer_for(F("Eintrittsdatum", type="date"), ctx) == Answer("date", "2026-12-01")


def test_cover_letter_file_is_uploaded_when_package_has_one(ctx):
    assert answer_for(F("Anschreiben", type="file", required=False), ctx) == Answer("file", str(ctx.cover_letter))
    bare = AnswerContext(**{**ctx.__dict__, "cover_letter": None})
    assert answer_for(F("Anschreiben", type="file", required=False), bare) == Answer("skip")


def test_german_select_defaults(ctx):
    sel = lambda label, opts: F(label, tag="select", type="select", options=opts)  # noqa: E731
    assert answer_for(sel("Kündigungsfrist", ["Bitte wählen", "sofort verfügbar", "1 Monat", "3 Monate"]), ctx) == Answer("option", "sofort verfügbar")
    assert answer_for(sel("Umzugsbereitschaft", ["Ja", "Nein"]), ctx) == Answer("option", "Nein")
    assert answer_for(sel("Land", ["Österreich", "Deutschland", "Schweiz"]), ctx) == Answer("option", "Deutschland")
    assert answer_for(sel("Wie sind Sie auf uns aufmerksam geworden?", ["Empfehlung", "StepStone", "Sonstiges"]), ctx) == Answer("option", "StepStone")
    contract = AnswerContext(**{**ctx.__dict__, "market": "contract"})
    assert answer_for(sel("Kündigungsfrist", ["sofort", "1 Monat"]), contract) is None
