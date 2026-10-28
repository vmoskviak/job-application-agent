from pathlib import Path

import pytest

from bot import client as client_mod
from bot.generators import cover_note as cn

ENGINE = Path(__file__).resolve().parent.parent
RATE = {"currency_default": "EUR", "hourly_min": 80, "hourly_target": 95,
        "daily_target": 760, "usd_hourly_target": 115,
        "quote_policy": "omit_first_contact"}
AGENCIES = ["hays.de", "solcom.de"]


@pytest.fixture()
def jordan():
    c = client_mod.load("jordan", base=ENGINE / "examples" / "clients")
    return dict(c.profile), dict(c.targeting)


def _entry(**kw):
    e = {"title": "Senior Android Engineer", "company": "Acme",
         "description": "We build mobile apps and need a contractor for a long project.",
         "source_url": "https://boards.example.com/job/1", "email": None,
         "category": "android"}
    e.update(kw)
    return e


def test_agency_rule_beats_english_text():
    e = _entry(email="recruiter@hays.de")
    assert cn.note_language(e, AGENCIES) == "de"
    e = _entry(source_url="https://www.solcom.de/projekt/1")
    assert cn.note_language(e, AGENCIES) == "de"


def test_german_text_detected():
    e = _entry(title="Android-Entwickler",
               description="Wir suchen für ein Projekt in Berlin einen Entwickler und "
                           "bieten Ihnen eine Zusammenarbeit mit unserem Team an.")
    assert cn.note_language(e, AGENCIES) == "de"
    assert cn.note_language(_entry(), AGENCIES) == "en"


def test_rate_sentence_values():
    assert cn.rate_sentence({**RATE, "quote_policy": "target"}, "en", "EUR", True, False) == \
        "My rate for a full-time B2B engagement is €95/hour."
    assert cn.rate_sentence({**RATE, "quote_policy": "target"}, "en", "USD", True, False) == \
        "My rate is $115/hour, invoiced monthly as a B2B contractor."
    assert cn.rate_sentence({**RATE, "quote_policy": "target"}, "de", "EUR", True, False) == \
        "Mein Stundensatz liegt bei 95 € netto zzgl. USt."


def test_omit_first_contact_for_us_startup():
    assert cn.rate_sentence(RATE, "en", "USD", True, False) == ""
    assert cn.rate_sentence(RATE, "en", "USD", True, True) != ""
    assert cn.rate_sentence(RATE, "en", "USD", False, False) != ""


def test_currency_for():
    assert cn.currency_for(_entry(remote_scope="us_only")) == "USD"
    assert cn.currency_for(_entry(description="Pay up to $120/hour")) == "USD"
    assert cn.currency_for(_entry(company="US-based Foo")) == "USD"
    assert cn.currency_for(_entry()) == "EUR"


def test_hourly_min_never_in_output(jordan):
    profile, targeting = jordan
    profile["rate"] = {**RATE, "hourly_min": 73, "daily_target": 717,
                       "quote_policy": "target"}
    for e in (_entry(), _entry(email="x@hays.de"), _entry(remote_scope="us_only")):
        n = cn.build_note(e, "Short body.", profile, targeting)
        assert "73" not in n.text and "717" not in n.text
        assert "73" not in n.subject and "717" not in n.subject
    usd = cn.build_note(_entry(remote_scope="us_only"), "B.", profile, targeting)
    assert "$115/hour" in usd.text


def test_agency_label_boundary():
    assert cn.note_language(_entry(email="a@nothays.de"), AGENCIES) == "en"
    assert cn.note_language(_entry(email="a@mail.hays.de"), AGENCIES) == "de"
    assert cn.note_language(_entry(source_url="hays.de/x"), AGENCIES) == "de"
    assert cn.note_language(_entry(source_url="nothays.de/x"), AGENCIES) == "en"


def test_german_note_always_eur(jordan):
    profile, targeting = jordan
    profile["rate"] = {**RATE, "quote_policy": "target"}
    e = _entry(email="x@hays.de", description="Rate $120/h", remote_scope="us_only")
    n = cn.build_note(e, "Body.", profile, targeting)
    assert "Mein Stundensatz liegt bei 95 €" in n.text and "$" not in n.text


def test_dollar_needs_digit(jordan):
    assert cn.currency_for(_entry(description="use ${VAR} here")) == "EUR"
    assert cn.currency_for(_entry(description="pay $ 120/h")) == "USD"


def test_no_german_artefacts_in_english_note(jordan):
    profile, targeting = jordan
    n = cn.build_note(_entry(), "I have shipped Android apps for years.", profile, targeting)
    assert n.lang == "en"
    for w in ("Anlagen", "Sehr geehrte", "Gehaltsvorstellung"):
        assert w not in n.text


def test_no_em_dash(jordan):
    profile, targeting = jordan
    for e in (_entry(), _entry(email="x@hays.de")):
        n = cn.build_note(e, "Body.", profile, targeting, first_contact=False)
        assert "—" not in n.text and "—" not in n.subject


def test_subject_and_signoff(jordan):
    profile, targeting = jordan
    n = cn.build_note(_entry(), "Body.", profile, targeting)
    assert n.subject == "Contract: Senior Android Engineer · Jordan Example"
    assert n.text.startswith("Hello Acme team,")
    assert "Jordan Example\nLisbon" in n.text
    d = cn.build_note(_entry(email="x@hays.de"), "Body.", profile, targeting)
    assert d.subject.startswith("Anfrage: ") and d.text.startswith("Guten Tag,")


def test_length_bounds(jordan):
    profile, targeting = jordan
    body = " ".join(["word"] * 150)
    n = cn.build_note(_entry(), body, profile, targeting)
    assert 100 <= len(n.text.split()) <= 220


def test_write_notes_paths(tmp_path, jordan):
    profile, targeting = jordan
    n = cn.build_note(_entry(), "Body.", profile, targeting)
    paths = cn.write_notes([("acme-1", n)], tmp_path, "2026-10-20")
    assert paths == [tmp_path / "notes_2026-10-20" / "acme-1.txt"]
    content = paths[0].read_text(encoding="utf-8")
    assert content.splitlines()[0] == n.subject
    assert content.splitlines()[1] == ""
    assert content.endswith(n.text) or content.rstrip("\n").endswith(n.text)


def test_entry_slug():
    e = _entry(company="Acme GmbH & Co.", title="Senior Android Engineer (Kotlin)")
    assert cn.entry_slug(e) == "acme-gmbh-co-senior-android-engineer-kotlin"
    long = cn.entry_slug(_entry(company="A" * 50, title="B" * 50))
    assert len(long) <= 60 and not long.endswith("-")
    assert cn.entry_slug({}) == "lead"


def test_cover_note_cli(tmp_path):
    import json
    import os
    import subprocess
    import sys

    agency = _entry(company="Projekt Partner", title="Android-Entwickler",
                    email="projekte@hays.de")
    plain = _entry()
    filtered = tmp_path / "filtered.json"
    filtered.write_text(json.dumps({"selected": [plain, agency], "excluded": []}))
    env = {**os.environ, "JOBSEARCH_CLIENTS_DIR": str(ENGINE / "examples" / "clients"),
           "JOBSEARCH_CLIENT": "jordan", "PYTHONPATH": str(ENGINE)}
    cmd = [sys.executable, "-m", "bot.generators.cover_note"]

    out = subprocess.run([*cmd, "--lang-only", str(filtered)], env=env, cwd=tmp_path,
                         capture_output=True, text=True, check=True)
    langs = json.loads(out.stdout)
    s_plain, s_agency = cn.entry_slug(plain), cn.entry_slug(agency)
    assert langs == {s_plain: "en", s_agency: "de"}

    bodies = tmp_path / "bodies.json"
    bodies.write_text(json.dumps({s_plain: "I build Android apps.",
                                  s_agency: "Ich entwickle Android-Apps."}))
    out = subprocess.run([*cmd, "--assemble", str(filtered), str(bodies),
                          "--out-dir", str(tmp_path / "run"), "--date", "2026-10-20"],
                         env=env, cwd=tmp_path, capture_output=True, text=True, check=True)
    paths = [Path(p) for p in out.stdout.split()]
    assert sorted(p.name for p in paths) == sorted([f"{s_plain}.txt", f"{s_agency}.txt"])
    assert all(p.parent == tmp_path / "run" / "notes_2026-10-20" for p in paths)
    en = (tmp_path / "run" / "notes_2026-10-20" / f"{s_plain}.txt").read_text()
    assert en.startswith("Contract: Senior Android Engineer · Jordan Example")
    assert "I build Android apps." in en and "Hello Acme team," in en
    de = (tmp_path / "run" / "notes_2026-10-20" / f"{s_agency}.txt").read_text()
    assert de.startswith("Anfrage: Android-Entwickler") and "Guten Tag," in de

    # тіло без пари в bodies.json: лід пропускається з попередженням, а не падає
    bodies.write_text(json.dumps({s_plain: "Only one."}))
    out = subprocess.run([*cmd, "--assemble", str(filtered), str(bodies),
                          "--out-dir", str(tmp_path / "run2"), "--date", "2026-10-20"],
                         env=env, cwd=tmp_path, capture_output=True, text=True, check=True)
    assert len(out.stdout.split()) == 1 and s_agency in out.stderr


def test_dashes_in_body_and_title_normalised(jordan):
    """Агент може повернути тіло з тире-сполучниками: збірка замінює їх комою."""
    profile, targeting = jordan
    body = "I shipped Android apps — mostly Kotlin — for eight years.\n" \
           "Payments, — offline sync – and tests  too, in 2019–2023."
    e = _entry(title="Android Engineer – Kotlin Multiplatform")
    n = cn.build_note(e, body, profile, targeting)
    for dash in ("—", "–"):
        assert dash not in n.text and dash not in n.subject
    assert "I shipped Android apps, mostly Kotlin, for eight years." in n.text
    assert "Payments, offline sync, and tests too, in 2019-2023." in n.text
    assert ", ," not in n.text and "  " not in n.text
    assert n.subject == "Contract: Android Engineer, Kotlin Multiplatform · Jordan Example"


def test_spaced_numeric_ranges_keep_hyphen(jordan):
    """«€90 – 100/h» і «€90–€100» — діапазони, а не сполучники: дефіс, не кома."""
    profile, targeting = jordan
    body = "Rates of €90 – 100/h or €90–€100 are usual, 2019–2021 too – as agreed."
    n = cn.build_note(_entry(), body, profile, targeting)
    assert "€90-100/h" in n.text
    assert "€90-€100" in n.text
    assert "2019-2021 too, as agreed" in n.text
