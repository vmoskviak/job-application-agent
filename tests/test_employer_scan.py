"""Детермінований пошук відповідей роботодавців у обох скриньках.

Інцидент 25.07.2026: запрошення на співбесіду від Falk & Falk (softgarden)
пролежало 29 годин у скриньці jobs. Ранковий ран звітував
`responses=2 interviews=0`, бо дивився лише main, а канал відповідей тримався на
двох крихких припущеннях: (1) домен відповіді = домен, куди подавались;
(2) лейбл `Employer` існує в обох скриньках (у jobs його не було взагалі).
"""
import pytest

from bot.generators.employer_scan import (
    ATS_DOMAINS,
    build_employer_queries,
    exit_code,
    needs_action,
    summarize,
)


class TestQueries:
    def test_every_tracker_domain_survives(self):
        domains = [f"firma{i}.de" for i in range(47)]
        qs = build_employer_queries("jobs", domains, exclude_senders=[], days=7)
        joined = " ".join(qs)
        for d in domains:
            assert f"from:{d}" in joined

    def test_queries_are_chunked(self):
        """Gmail ріже надто довгий запит — розбиваємо на партії."""
        domains = [f"firma{i}.de" for i in range(47)]
        qs = build_employer_queries("jobs", domains, exclude_senders=[], days=7, chunk=20)
        assert len(qs) >= 3
        assert all(q.count("from:") <= 20 for q in qs if "label:" not in q or "from:" in q)

    def test_ats_domains_always_present(self):
        qs = build_employer_queries("jobs", [], exclude_senders=[], days=7)
        joined = " ".join(qs)
        for d in ("softgarden.io", "successfactors.com", "rexx-hr.com", "jobs2web.com"):
            assert d in joined
        assert "softgarden.io" in " ".join(ATS_DOMAINS) or "softgarden.io" in ATS_DOMAINS

    def test_jobs_account_scans_inbox_and_label(self):
        qs = build_employer_queries("jobs", ["firma.de"], exclude_senders=[], days=7)
        joined = " ".join(qs)
        assert "in:inbox" in joined
        assert "label:Employer" in joined

    def test_main_account_includes_trash(self):
        """Відповіді можуть бути видалені з main — trash обов'язковий."""
        qs = build_employer_queries("main", ["firma.de"], exclude_senders=[], days=7)
        joined = " ".join(qs)
        assert "in:trash" in joined

    def test_alert_senders_excluded(self):
        qs = build_employer_queries(
            "jobs", ["firma.de"], exclude_senders=["info@jobagent.stepstone.de"], days=7
        )
        assert all("-from:info@jobagent.stepstone.de" in q for q in qs)

    def test_days_window_applied(self):
        qs = build_employer_queries("jobs", ["firma.de"], exclude_senders=[], days=3)
        assert all("newer_than:3d" in q for q in qs)

    def test_unknown_account_rejected(self):
        with pytest.raises(ValueError):
            build_employer_queries("gmail", [], exclude_senders=[], days=7)


class TestNeedsAction:
    @pytest.mark.parametrize("cls", ["interview", "info_request"])
    def test_action_classes(self, cls):
        assert needs_action(cls) is True

    @pytest.mark.parametrize("cls", ["rejected", "confirmed", "unknown"])
    def test_passive_classes(self, cls):
        assert needs_action(cls) is False


class TestSummarize:
    def _item(self, cls, **kw):
        base = {"classification": cls, "message_id": "m1", "account": "jobs",
                "subject": "s", "from": "a@b.de", "company": "Firma"}
        base.update(kw)
        return base

    def test_counts_by_classification(self):
        s = summarize([self._item("rejected"), self._item("rejected"), self._item("interview")])
        assert s["counts"]["rejected"] == 2
        assert s["counts"]["interview"] == 1

    def test_action_needed_collects_interviews(self):
        s = summarize([self._item("interview", message_id="x"), self._item("rejected")])
        assert [i["message_id"] for i in s["action_needed"]] == ["x"]

    def test_already_handled_interview_is_not_action(self):
        """Якщо у трекері вже стоїть interview і є наша відповідь — не будити."""
        s = summarize([self._item("interview", message_id="x", answered=True)])
        assert s["action_needed"] == []

    def test_empty_input(self):
        s = summarize([])
        assert s["counts"] == {} and s["action_needed"] == []


class TestExitCode:
    def test_zero_when_nothing_to_do(self):
        assert exit_code({"action_needed": []}) == 0

    def test_two_when_action_needed(self):
        assert exit_code({"action_needed": [{"message_id": "x"}]}) == 2


class TestClosure:
    """Хибний позитив не має тримати вартового у exit=2 щодня.

    Eingangsbestätigung Falk & Falk лежить в окремому треді й теж класифікується
    як interview (у тексті є «Vorstellungsgespräch»). Якщо в трекері вже стоїть
    interview — дія закрита, будити нікого не треба.
    """

    def test_closed_when_thread_answered(self):
        from bot.generators.employer_scan import is_closed

        item = {"classification": "interview", "company": "Firma", "thread_answered": True}
        assert is_closed(item, {}) is True

    def test_closed_when_tracker_already_interview(self):
        from bot.generators.employer_scan import is_closed

        item = {"classification": "interview", "company": "Falk & Falk LLP",
                "thread_answered": False}
        assert is_closed(item, {"Falk & Falk LLP": "interview"}) is True

    def test_open_when_tracker_only_sent(self):
        from bot.generators.employer_scan import is_closed

        item = {"classification": "interview", "company": "Firma", "thread_answered": False}
        assert is_closed(item, {"Firma": "sent"}) is False

    def test_open_when_company_unknown(self):
        from bot.generators.employer_scan import is_closed

        item = {"classification": "interview", "company": None, "thread_answered": False}
        assert is_closed(item, {}) is False

    def test_tracker_status_by_company_reads_both_tables(self, tmp_path):
        from bot.generators.employer_scan import tracker_status_by_company

        p = tmp_path / "tracker.md"
        p.write_text(
            "| # | Date | Company | Position | Source | Salary | Status | Last | Next | Notes |\n"
            "|---|---|---|---|---|---|---|---|---|---|\n"
            "| 247 | 2026-07-22 | Falk & Falk LLP | Assistenz | Email | 42K | interview | 2026-07-25 | — | нотатка |\n"
            "| 246 | 2026-07-24 | Weststrom GmbH | IT-BA | LinkedIn | 48K | ready | 2026-07-24 | — | нотатка |\n"
            "\n"
            "| 2026-07-22 | Scrum Master | Chemhandel AG, Mönchengladbach | rejected 24.07 |\n",
            encoding="utf-8",
        )
        st = tracker_status_by_company(p)
        assert st["Falk & Falk LLP"] == "interview"
        assert st["Weststrom GmbH"] == "ready"
        assert "rejected" in st["Chemhandel AG"]


class TestScanAccounts:
    def test_all_uses_only_configured_mailboxes(self):
        from bot.generators.employer_scan import scan_accounts
        assert scan_accounts(["jobs", "main"]) == ["jobs", "main"]
        assert scan_accounts(["jobs"]) == ["jobs"]

    def test_explicit_missing_mailbox_is_an_error(self):
        from bot.generators.employer_scan import scan_accounts
        with pytest.raises(ValueError):
            scan_accounts(["jobs"], "main")
        with pytest.raises(ValueError):
            scan_accounts([], "all")

    def test_legacy_both_and_single(self):
        from bot.generators.employer_scan import scan_accounts
        assert scan_accounts(["jobs"], "both") == ["jobs"]
        assert scan_accounts(["jobs", "main"], "jobs") == ["jobs"]
