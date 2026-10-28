"""Тести джерела HN Who is hiring: офлайн, синтетичні фікстури."""
import json
from pathlib import Path

import pytest

from bot.scrapers import hn_whoishiring as hn

FIX = Path(__file__).parent / "fixtures" / "hn"


def _search():
    return json.loads((FIX / "search_by_date.json").read_text())


def _thread():
    return json.loads((FIX / "item_thread.json").read_text())


def _comment(cid):
    return next(c for c in _thread()["children"] if c["id"] == cid)


def test_picks_who_is_hiring_not_who_wants():
    t = hn.find_current_thread(lambda url: _search())
    assert t.story_id == 900001
    assert t.month == "October 2026"
    assert "wants" not in t.title


def test_only_direct_children(tmp_path):
    jobs, _ = hn.collect(lambda u: _search(), lambda u: _thread(), tmp_path / "s.json")
    assert len(jobs) == 6
    assert all("1003" not in j.source_url for j in jobs)


def test_parse_header_with_pipes():
    j = hn.parse_comment(_comment(1001))
    assert j.company == "Acme Robotics"
    assert j.title == "Senior Python Contractor"
    assert j.location == "Berlin (Remote)"
    assert j.source_url == "https://news.ycombinator.com/item?id=1001"
    assert j.is_portal is False


def test_parse_comment_without_pipes():
    j = hn.parse_comment(_comment(1004))
    assert j.company == "Tiny startup building tools for fictional"
    assert j.title == ""


def test_html_entities_and_paragraphs():
    j = hn.parse_comment(_comment(1005))
    assert j.company == "R&D Labs"
    assert "Rust & Go" in j.description
    assert "<p>" not in j.description
    assert "\n" in j.description


def test_email_and_url_extraction():
    j = hn.parse_comment(_comment(1001))
    assert j.email == "jobs@example.com"
    assert j.application_url == "https://example.com/careers/python"


def test_seen_state_dedup(tmp_path):
    state = tmp_path / "state" / "seen.json"
    first, ids = hn.collect(lambda u: _search(), lambda u: _thread(), state)
    assert not state.exists()  # collect() сам стан не пише
    hn.commit_seen(state, ids)
    second, _ = hn.collect(lambda u: _search(), lambda u: _thread(), state)
    assert len(first) == 6 and second == []
    assert 1003 not in json.loads(state.read_text())
    assert not list(state.parent.glob("*.tmp"))


def test_api_error_sets_flag(tmp_path):
    def boom(url):
        raise RuntimeError("down")
    state = tmp_path / "seen.json"
    assert hn.collect(boom, boom, state) == ([], set())
    assert "down" in hn.last_error
    assert not state.exists()
    hn.collect(lambda u: _search(), lambda u: _thread(), state)
    assert hn.last_error == ""


def test_remote_not_duplicated_or_negated():
    assert hn.parse_comment(_comment(1005)).location == "Remote"
    assert hn.parse_comment(_comment(1007)).location == "Berlin"
    neg = {"id": 1, "text": "Co | Role | Onsite (no remote)"}
    assert hn.parse_comment(neg).location == "Onsite (no remote)"
    neg2 = {"id": 2, "text": "Co | Role | Berlin | remote not possible"}
    assert "(Remote)" not in hn.parse_comment(neg2).location


def test_href_preferred_over_truncated_text():
    j = hn.parse_comment(_comment(1006))
    assert j.application_url == "https://jobs.example.com/apply/123"


def test_cli_error_exit_code_and_no_commit(tmp_path, monkeypatch):
    def boom(url):
        raise RuntimeError("down")
    monkeypatch.setattr(hn, "_get_json", boom)
    out, state = tmp_path / "o.json", tmp_path / "seen.json"
    assert hn.main(["--out", str(out), "--state", str(state)]) == 2
    assert json.loads(out.read_text()) == []
    assert not state.exists()


def test_cli_writes_digest(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hn, "_get_json",
                        lambda url: _search() if "search_by_date" in url else _thread())
    out = tmp_path / "hn.json"
    rc = hn.main(["--out", str(out), "--state", str(tmp_path / "seen.json")])
    assert rc == 0
    rows = json.loads(out.read_text())
    assert len(rows) == 6
    keys = {"score", "category", "title", "company", "location", "email",
            "application_url", "source_url", "description", "liveness",
            "intake_method", "source", "is_portal"}
    assert keys <= set(rows[0])
    assert rows[0]["source"] == "hn_whoishiring" and rows[0]["liveness"] == "live"
    assert "900001" in capsys.readouterr().err
    assert (tmp_path / "seen.json").exists()


def test_cli_fallback_title_for_free_prose(tmp_path, monkeypatch):
    """Коментар без `|`: у рядку дайджесту title = перший рядок (до 80
    символів), інакше фільтр відкидає його як scrape_failed_title."""
    long_line = "Fictional Widgets GmbH hires a senior Kotlin contractor for a remote " \
                "project lasting six months"
    thread = _thread()
    thread["children"].append({"id": 1099, "text": long_line + "<p>Details follow."})
    monkeypatch.setattr(hn, "_get_json",
                        lambda url: _search() if "search_by_date" in url else thread)
    out = tmp_path / "hn.json"
    assert hn.main(["--out", str(out), "--state", str(tmp_path / "seen.json")]) == 0
    rows = {r["source_url"].rsplit("=", 1)[1]: r for r in json.loads(out.read_text())}
    assert rows["1004"]["title"] == ("Tiny startup building tools for fictional "
                                     "bakeries and more things here")
    assert rows["1099"]["title"] == long_line[:80].strip()
    assert rows["1001"]["title"] == "Senior Python Contractor"
    assert hn.parse_comment(_comment(1004)).title == ""


def test_cli_no_thread_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr(hn, "_get_json",
                        lambda url: {"hits": []} if "search_by_date" in url else _thread())
    out, state = tmp_path / "o.json", tmp_path / "seen.json"
    assert hn.main(["--out", str(out), "--state", str(state)]) == 2
    assert json.loads(out.read_text()) == []
    assert not state.exists()
    assert "не знайдено" in hn.last_error
