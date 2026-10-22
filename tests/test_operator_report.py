"""Зведений вечірній лист оператору: трекер, черга алертів, складання листа."""
from bot import operator_report as rep

TRACKER = """# Application Tracker — erika

| # | Date | Company | Position | Source | Salary range | Status | Last update | Next action | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-08 | Empfang (m/w/d) | Alpha GmbH, Köln | sent 2026-10-08 (Msg 1a11ae48cb6c8d84) |
| 2026-10-08 | Teamassistenz (m/w/d) | RP Stellenmarkt | Beta AG, Köln | confirmed 2026-10-09 — Eingangsbestätigung; portal_submitted (StepStone) 2026-10-08 |
| 2026-10-08 | HR Assistant | Gamma SE, Köln | manual 2026-10-08 — nur mit Konto: https://jobs.example.com/apply/1 · Paket bereit |
| 2026-10-09 | Empfangskraft | Delta KG, Köln | ready 2026-10-09 — form_unsupported, manuell: https://www.example.org/j/2 |
| 2026-10-09 | Verkäuferin | Epsilon GmbH, Köln | 🔄 Файли готові — подати вручну |
| 2026-10-09 | Rezeption | Zeta GmbH, Köln | skipped 2026-10-09 — Vollzeit |
| 2026-10-08 | Assistenz Finance | Kappa GmbH, Köln | rejected 2026-10-12 — Absage (Msg 1a12f9845b03676f); portal_submitted (Firmen-ATS) 2026-10-08 |
| 2026-10-08 | Empfangsmitarbeiter | Eta Group, Köln | interview_invited 2026-10-08 — Einladung (Msg 1a11bec1417698b7) |
| 2026-10-09 | Assistenz | Theta GmbH, Köln | sent 2026-10-09 (Msg 1a11e31421e44dca) |
| 7 | 2026-10-09 | Iota AG | Office Manager | StepStone | — | sent | 2026-10-09 | — | Msg 1a11e31286fbc23b |
"""


def _rows(tmp_path):
    f = tmp_path / "tracker.md"
    f.write_text(TRACKER, encoding="utf-8")
    return rep.parse_rows(f)


def test_parse_rows_both_formats_and_kinds(tmp_path):
    rows = _rows(tmp_path)
    kinds = {r.company: r.kind for r in rows}
    assert kinds == {"Alpha GmbH": "submitted", "Beta AG": "submitted", "Gamma SE": "manual",
                     "Delta KG": "manual", "Epsilon GmbH": "manual", "Zeta GmbH": "closed",
                     "Eta Group": "interview", "Theta GmbH": "submitted", "Iota AG": "submitted",
                     "Kappa GmbH": "rejected"}
    beta = next(r for r in rows if r.company == "Beta AG")
    assert beta.title == "Teamassistenz (m/w/d)" and beta.submitted_on == "2026-10-08"
    assert next(r for r in rows if r.company == "Gamma SE").url == "https://jobs.example.com/apply/1"


def test_summarize_counts_today_and_open_manual(tmp_path):
    s = rep.summarize(_rows(tmp_path), "2026-10-09")
    assert s["submitted_total"] == 6  # відмова теж подана заявка
    assert [r.company for r in s["rejected"]] == ["Kappa GmbH"]
    assert sorted(r.company for r in s["submitted_today"]) == ["Iota AG", "Theta GmbH"]
    assert [r.company for r in s["interviews"]] == ["Eta Group"]
    assert len(s["manual_open"]) == 3


def test_alert_queue_keeps_one_per_reason(tmp_path):
    rep.queue_alert(tmp_path, "morning", "Offene Antwort eines Arbeitgebers", "tail-1")
    rep.queue_alert(tmp_path, "evening", "Offene Antwort eines Arbeitgebers", "tail-2")
    rep.queue_alert(tmp_path, "morning", "Timeout nach 3 Stunden")
    alerts = rep.read_alerts(tmp_path)
    assert [a["run"] for a in alerts] == ["evening", "morning"]
    rep.clear_alerts(tmp_path)
    assert rep.read_alerts(tmp_path) == []


def test_mail_contains_fragments_alerts_and_manual_links(tmp_path):
    (tmp_path / "operator_report_2026-10-09.html").write_text("<p>NACHT-A</p>", encoding="utf-8")
    (tmp_path / "operator_report_2026-10-09_1430.html").write_text("<p>NACHT-B</p>", encoding="utf-8")
    (tmp_path / "operator_report_2026-10-08.html").write_text("<p>GESTERN</p>", encoding="utf-8")
    s = rep.summarize(_rows(tmp_path), "2026-10-09")
    alerts = [{"message": "Lauf <fehlgeschlagen>", "run": "evening", "at": "2026-10-09 20:45"}]
    page = rep.build_html("Erika Mustermann", "2026-10-09", s, alerts,
                          rep.run_fragments(tmp_path, "operator_report", "2026-10-09"), [])
    assert "NACHT-A" in page and "NACHT-B" in page and "GESTERN" not in page
    assert "Lauf &lt;fehlgeschlagen&gt;" in page
    assert 'href="https://jobs.example.com/apply/1"' in page
    subj = rep.subject("Erika", "2026-10-09", s, alerts)
    assert subj == "[Erika: Tagesbericht] 09.10.2026: 2 beworben, 3 manuell offen, 1 Hinweis"
    text = rep.build_text("2026-10-09", s, [])
    assert "manuell offen:   3" in text and "Hinweise" not in text


def test_reply_with_msgid_in_tracker_is_closed(tmp_path):
    """Personio шле з власного домену: компанію не зіставити, але Msg-ID уже в трекері."""
    from bot.generators.employer_scan import is_closed, tracker_known_msgids
    f = tmp_path / "tracker.md"
    f.write_text(TRACKER, encoding="utf-8")
    known = tracker_known_msgids(f)
    assert "1a11bec1417698b7" in known
    assert is_closed({"classification": "interview", "company": None, "message_id": "1a11bec1417698b7"}, {}, known)
    assert not is_closed({"classification": "interview", "company": None, "message_id": "ffffffffffffffff"}, {}, known)


def test_kind_ignores_leading_emoji_and_bold():
    # Рядки 16.10.2026 мали статус «✅ **confirmed 16.10** …» і падали в other,
    # тож у звіті «heute beworben» їх не було.
    from bot.operator_report import Row
    def kind(status):
        return Row("2026-10-16", "Teamassistenz", "Beispiel GmbH", status).kind
    assert kind("✅ **confirmed 16.10** — Eingangsbestätigung") == "submitted"
    assert kind("✅ **sent (email) 16.10** — jobs Msg") == "submitted"
    assert kind("🔴 rejected 15.10 (main Msg)") == "rejected"
    assert kind("⛔ **skipped 16.10** — 12–15 Std.") == "closed"
    assert kind("🔄 Файли готові — подати вручну") == "manual"
    assert kind("🔄 **форма заповнена 16.10**") == "manual"
