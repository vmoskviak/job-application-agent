"""Диспетчер кнопок (bot.telegram.dispatch): відповідь у треді, захист від подвійного тапу, пауза."""
from __future__ import annotations

import sys
from pathlib import Path

from bot.state import JsonlStore, is_paused, set_paused
from bot.telegram.dispatch import Dispatcher, GmailRestSender
from bot.telegram.service import Action
from tests.inbox_fixtures import JORDAN_TRACKER_WITH_TWO_ROWS


class FakeSender:
    """Записує виклики; fail=True імітує збій Gmail."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str, str, str | None]] = []
        self.fail = fail

    def reply(self, account: str, msg_id: str, body: str, to: str | None = None) -> str:
        if self.fail:
            raise RuntimeError("gmail down")
        self.calls.append((account, msg_id, body, to))
        return f"fake-id-{len(self.calls)}"


def _disp(tmp_path, sender=None):
    cards = JsonlStore(tmp_path / "cards.jsonl")
    cards.append({"id": "re-1", "kind": "reply", "status": "open", "account": "main",
                  "message_id": "m1", "company": "Example Co", "draft": "Hallo"})
    cards.append({"id": "re-2", "kind": "reply", "status": "open", "account": "main",
                  "message_id": "m2", "company": None, "draft": "Hi"})
    tracker = tmp_path / "tracker.md"
    tracker.write_text(JORDAN_TRACKER_WITH_TWO_ROWS, encoding="utf-8")
    d = Dispatcher(sender or FakeSender(), cards, tracker, tmp_path, lambda: "2026-10-26")
    return cards, tracker, d


def test_send_replies_marks_card_and_notes_tracker(tmp_path):
    cards, tracker, d = _disp(tmp_path)
    assert d.apply(Action("t", "send", "re-1", 42)) == "OK sent fake-id-1"
    rec = cards.get("re-1")
    assert rec["status"] == "sent" and rec["sent_id"] == "fake-id-1"
    assert "· 2026-10-26 reply sent Msg fake-id-1 |" in tracker.read_text(encoding="utf-8")
    assert d.sender.calls == [("main", "m1", "Hallo", None)]


def test_send_without_company_skips_tracker(tmp_path):
    _cards, tracker, d = _disp(tmp_path)
    before = tracker.read_text(encoding="utf-8")
    assert d.apply(Action("t", "send", "re-2", 42)) == "OK sent fake-id-1"
    assert tracker.read_text(encoding="utf-8") == before


def test_second_send_tap_is_a_noop(tmp_path):
    _cards, _tracker, d = _disp(tmp_path)
    d.apply(Action("t", "send", "re-1", 42))
    assert d.apply(Action("t", "send", "re-1", 42)) == "OK already sent"
    assert len(d.sender.calls) == 1


def test_send_while_paused_is_refused(tmp_path):
    cards, _tracker, d = _disp(tmp_path)
    set_paused(tmp_path, True)
    assert d.apply(Action("t", "send", "re-1", 42)) == "ERR paused"
    assert cards.get("re-1")["status"] == "open" and d.sender.calls == []


def test_sender_failure_keeps_card_open(tmp_path):
    cards, _tracker, d = _disp(tmp_path, sender=FakeSender(fail=True))
    receipt = d.apply(Action("t", "send", "re-1", 42))
    assert receipt.startswith("ERR") and "gmail down" in receipt
    assert cards.get("re-1")["status"] == "open"


def test_skip_done_and_unknown(tmp_path):
    cards, _tracker, d = _disp(tmp_path)
    assert d.apply(Action("t", "skip", "re-1", 42)) == "OK skipped"
    assert cards.get("re-1")["status"] == "skipped"
    assert d.apply(Action("t", "send", "re-1", 42)) == "OK already skipped"
    assert d.apply(Action("t", "done", "re-2", 42)) == "OK done"
    assert d.apply(Action("t", "send", "zz", 42)) == "ERR unknown card"
    assert d.apply(Action("t", "send", None, 42)) == "ERR unknown card"


def test_pause_and_resume_actions(tmp_path):
    _cards, _tracker, d = _disp(tmp_path)
    assert d.apply(Action("t", "pause", None, 42)) == "OK paused" and is_paused(tmp_path)
    assert d.apply(Action("t", "resume", None, 42)) == "OK resumed" and not is_paused(tmp_path)


def test_gmail_rest_sender_parses_sent_id(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "gmail_rest.py").write_text(
        "import os, sys\n"
        "body = open(sys.argv[4], encoding='utf-8').read()\n"
        "assert sys.argv[1:4] == ['reply', 'main', 'm1'] and body == 'body text', sys.argv\n"
        "assert os.environ['JOBSEARCH_CLIENT'] == 'jordan'\n"
        "print('SENT id=abc123 thread=t to=x subj=s attachments=[]')\n", encoding="utf-8")
    s = GmailRestSender(engine=tmp_path, python=Path(sys.executable), client_slug="jordan")
    assert s.reply("main", "m1", "body text") == "abc123"


def test_gmail_rest_sender_raises_without_sent_line(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "gmail_rest.py").write_text("print('nothing happened')\n", encoding="utf-8")
    s = GmailRestSender(engine=tmp_path, python=Path(sys.executable), client_slug="jordan")
    try:
        s.reply("main", "m1", "body")
    except RuntimeError as e:
        assert "nothing happened" in str(e)
    else:
        raise AssertionError("очікував RuntimeError")


def test_gmail_rest_sender_passes_explicit_recipient(tmp_path):
    """Follow-up відповідає на ВЛАСНИЙ надісланий лист: без [to] gmail_rest взяв би From, тобто нас."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "gmail_rest.py").write_text(
        "import sys\n"
        "assert sys.argv[1:4] == ['reply', 'jobs', 'm1'] and sys.argv[5] == 'boss@example-co.com', sys.argv\n"
        "print('SENT id=abc123 thread=t to=boss@example-co.com subj=s attachments=[]')\n", encoding="utf-8")
    s = GmailRestSender(engine=tmp_path, python=Path(sys.executable), client_slug="jordan")
    assert s.reply("jobs", "m1", "body", to="boss@example-co.com") == "abc123"


def test_gmail_rest_sender_send_parses_id(tmp_path):
    """Новий лист із вкладеннями: gmail_rest send <account> <draft.json>."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "gmail_rest.py").write_text(
        "import json, os, sys\n"
        "draft = json.load(open(sys.argv[3], encoding='utf-8'))\n"
        "assert sys.argv[1:3] == ['send', 'jobs'], sys.argv\n"
        "assert draft == {'to': 'a@example.com', 'subject': 'S', 'body': 'Body',\n"
        "                 'attachments': [os.path.join(os.environ['CV_DIR'], 'cv.pdf')]}, draft\n"
        "assert os.environ['JOBSEARCH_CLIENT'] == 'jordan'\n"
        "print('SENT id=m1 to=a@example.com subj=S attachments=[\\'cv.pdf\\']')\n", encoding="utf-8")
    import os
    os.environ["CV_DIR"] = str(tmp_path)
    try:
        s = GmailRestSender(engine=tmp_path, python=Path(sys.executable), client_slug="jordan")
        assert s.send("jobs", "a@example.com", "S", "Body", [tmp_path / "cv.pdf"]) == "m1"
    finally:
        del os.environ["CV_DIR"]


def test_gmail_rest_sender_send_raises_without_sent(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "gmail_rest.py").write_text("print('send дозволено лише з jobs')\n", encoding="utf-8")
    s = GmailRestSender(engine=tmp_path, python=Path(sys.executable), client_slug="jordan")
    try:
        s.send("main", "a@example.com", "S", "B", [])
    except RuntimeError as e:
        assert "дозволено" in str(e)
    else:
        raise AssertionError("очікував RuntimeError")


def _handoff_disp(tmp_path, lead: dict, card: dict):
    queue = JsonlStore(tmp_path / "queue.jsonl")
    queue.append(lead)
    cards = JsonlStore(tmp_path / "cards.jsonl")
    cards.append(card)
    tracker = tmp_path / "tracker.md"
    tracker.write_text(JORDAN_TRACKER_WITH_TWO_ROWS, encoding="utf-8")
    return queue, tracker, Dispatcher(FakeSender(), cards, tracker, tmp_path, lambda: "2026-10-28", queue=queue)


def test_done_on_platform_lead_writes_platform_source(tmp_path):
    """Hand-off на платформі: sent_id і джерело в трекері з платформи запису черги."""
    queue, tracker, d = _handoff_disp(
        tmp_path,
        {"id": "l1", "status": "handoff", "channel": "linkedin_easy", "platform": "linkedin",
         "company": "Contract Co", "title": "Android Engineer"},
        {"id": "handoff-l1", "kind": "handoff", "status": "open", "lead_id": "l1", "company": "Contract Co",
         "title": "Android Engineer", "url": "https://www.linkedin.com/jobs/view/1/"})
    assert d.apply(Action("2026-10-28 10:00", "done", "handoff-l1", 1)) == "OK done"
    assert queue.get("l1")["sent_id"] == "linkedin:operator"
    assert "| contract: linkedin |" in tracker.read_text(encoding="utf-8")


def test_done_takes_platform_from_card_first(tmp_path):
    queue, tracker, d = _handoff_disp(
        tmp_path,
        {"id": "l2", "status": "handoff", "channel": "platform_login"},
        {"id": "handoff-l2", "kind": "handoff", "status": "open", "lead_id": "l2", "company": "Agency",
         "title": "Kotlin", "url": "https://www.gulp.de/gulp2/g/projekte/agentur/C1", "ats": None,
         "platform": "gulp"})
    assert d.apply(Action("t", "done", "handoff-l2", 1)) == "OK done"
    assert queue.get("l2")["sent_id"] == "gulp:operator"
    assert "| contract: gulp |" in tracker.read_text(encoding="utf-8")
