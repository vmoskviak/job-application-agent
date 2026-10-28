"""Перемикач шаблону промпта за (ринок, режим) у scripts/render_run.py.

Golden-файл німецького вечірнього промпта зроблено з гілки ДО правок render_run.py
(дата 2026-10-20, оператор порожній, режим пошти evening). Абсолютний шлях рушія
в промпті замінено на `<ENGINE>`, щоб файл не залежав від машини.
"""
import datetime
import sys
from pathlib import Path

import pytest

from bot import client as _client

ENGINE = Path(__file__).resolve().parent.parent
EX = ENGINE / "examples" / "clients"
FIX = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ENGINE / "scripts"))
import render_run  # noqa: E402


class _FixedDate(datetime.date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 20)


@pytest.fixture(autouse=True)
def _pinned(monkeypatch):
    """Детермінізм: фіксована дата й порожні змінні оператора."""
    monkeypatch.setattr(render_run, "date", _FixedDate)
    monkeypatch.setenv("JOBSEARCH_OPERATOR_EMAIL", "")
    monkeypatch.setenv("JOBSEARCH_OPERATOR_MAIL", "evening")


def _render(mode: str, slug: str) -> str:
    text = render_run.render_prompt(mode, _client.load(slug, base=EX))
    return text.replace(str(render_run.ENGINE), "<ENGINE>")


def test_erika_prompt_is_golden():
    prompt = _render("evening", "erika")
    golden = (FIX / "golden" / "erika_prompt_evening.md").read_text(encoding="utf-8")
    assert prompt == golden


def test_jordan_renders_contract_template():
    prompt = _render("evening", "jordan")
    assert "hn_whoishiring" in prompt and "Gehaltsvorstellung" not in prompt
    assert "EVENING OK" in prompt
    assert "EVENING OK:" in prompt.rstrip().splitlines()[-1]
    assert "label:job-alerts from:freelancermap.de newer_than:2d" in prompt
    assert "--lang-only" in prompt and "--assemble" in prompt
    assert "jordan.jobs@example.com" in prompt
    # етап 2b: дайджест-листа нема, ліди йдуть у чергу, подає лише диспетчер
    assert "mcp__gmail__send_email" not in prompt and "Contract Digest" not in prompt
    assert "js scripts/enqueue_leads.py --date YYYY-MM-DD" in prompt
    assert "scripts/dispatch_leads.py" in prompt
    assert "queued=Q held=H" in prompt
    # німецьких кроків тут нема: ні Perplexity, ні Anschreiben, ні порталів
    for banned in ("perplexity", "StepStone", "XING", "Interamt", "Anschreiben"):
        assert banned not in prompt, banned
    assert "{{" not in prompt and "{%" not in prompt
    # нижня межа ставки (walk-away) до агента не доходить ніяк
    assert "hourly_min" not in prompt
    assert not [ln for ln in prompt.splitlines() if "rate" in ln.lower() and "80" in ln]
    # Excluded групується за текстом після останньої `:`, причини названі
    for reason in ("employee_only", "onsite_only", "us_work_authorization", "us_only",
                   "rate_below_min", "outside_region", "score_below_N", "excluded_title",
                   "excluded_category", "duplicate_in_tracker", "duplicate_in_run",
                   "scrape_failed_title", "excluded_description:onsite_only"):
        assert reason in prompt, reason
    # тіло листа без тире-сполучників
    assert "no dashes as connectors" in prompt
    # відповіді на подачі: скан у звіт, без записів у трекер
    assert "js scripts/employer_scan.py --days 2 --json" in prompt
    assert "Do not edit the tracker" in prompt


def test_jordan_report_language_follows_profile():
    prompt = _render("evening", "jordan")
    assert "in English" in prompt
    assert "Мова всієї пошти німецька" not in prompt


@pytest.mark.parametrize("mode", ["morning", "onboarding"])
def test_jordan_other_modes_not_supported(mode):
    with pytest.raises(_client.ClientError, match=f"ринок contract: режим {mode} ще не підтримується"):
        render_run.render_prompt(mode, _client.load("jordan", base=EX))


def test_templates_keyed_by_market_and_mode():
    assert set(render_run.TEMPLATES) == {
        ("de_employment", "evening"), ("de_employment", "morning"),
        ("de_employment", "onboarding"), ("contract", "evening")}


def test_context_has_market_fields():
    ctx = render_run.build_context(_client.load("jordan", base=EX))
    assert ctx["market"] == "contract" and ctx["report_lang"] == "en"
    assert ctx["rate"]["hourly_target"] == 95
    assert not [k for k in ctx["rate"] if "min" in k]
    assert not [k for k in ctx["profile"].get("rate", {}) if "min" in k]
    assert ctx["sources"]["hn_whoishiring"]["enabled"] is True
    erika = render_run.build_context(_client.load("erika", base=EX))
    assert erika["market"] == "de_employment" and erika["sources"] == {}


def _erika(**profile):
    c = _client.load("erika", base=EX)
    c.profile.update(profile)
    return c


def test_default_report_mode_is_operator():
    assert _client.load("erika", base=EX).report_mode == "operator"
    morning = render_run.render_prompt("morning", _client.load("erika", base=EX))
    assert "Bericht an die Kandidatin" in morning and "candidate_report.py" not in morning


def test_candidate_report_mode_morning():
    p = render_run.render_prompt("morning", _erika(report_mode="candidate"))
    assert "Vollbericht an die Kandidatin" in p and "js scripts/candidate_report.py --json" in p
    assert "--mark-reported" in p and "«Wichtig» an Kandidatin und Operator" in p
    assert "operator_todo_YYYY-MM-DD.html" in p
    assert "Morgenbericht" not in p and "operator_report_YYYY-MM-DD.html" not in p
    assert "{{" not in p and "{%" not in p
    assert "MORNING OK:" in p
    ev = render_run.render_prompt("evening", _erika(report_mode="candidate"))
    assert "operator_todo_YYYY-MM-DD_evening.html" in ev and "operator_evening_" not in ev


def test_report_mode_none_accepted():
    """Етап 2b: без Tagesbericht і без report_email; лише Hinweis-лист при збоях."""
    assert _erika(report_mode="none", report_email="").report_mode == "none"
    assert render_run.build_context(_erika(report_mode="none"))["report_mode"] == "none"


def test_report_mode_validation():
    with pytest.raises(_client.ClientError, match="report_mode 'boss'"):
        _ = _erika(report_mode="boss").report_mode
    with pytest.raises(_client.ClientError, match="без report_email"):
        _ = _erika(report_mode="candidate", report_email="").report_mode


def test_contract_alert_senders_come_from_search_yaml():
    """Email-алерти: відправники з search.yaml → один Gmail-запит; вимкнені джерела не потрапляють."""
    c = _client.load("jordan", base=EX)
    c.search["sources"] = {
        "hn_whoishiring": {"enabled": True},
        "freelancermap_alerts": {"enabled": True, "channel": "email", "sender": "freelancermap.de"},
        "gulp_alerts": {"enabled": True, "channel": "email", "sender": "alerts@gulp.example"},
        "linkedin_contract_alerts": {"enabled": False, "channel": "email",
                                     "sender": "jobalerts-noreply@linkedin.com"},
    }
    prompt = render_run.render_prompt("evening", c)
    assert "label:job-alerts from:(freelancermap.de OR alerts@gulp.example) newer_than:2d" in prompt
    assert "jobalerts-noreply@linkedin.com" not in prompt
    assert "urls_alerts_YYYY-MM-DD.txt" in prompt
    assert "{{" not in prompt and "{%" not in prompt


def test_contract_email_source_without_sender_is_skipped():
    """Увімкнене email-джерело без `sender` не дає запиту: крок пропускається з поясненням."""
    c = _client.load("jordan", base=EX)
    c.search["sources"] = {"hn_whoishiring": {"enabled": True},
                           "freelancermap_alerts": {"enabled": True, "channel": "email"}}
    prompt = render_run.render_prompt("evening", c)
    assert "label:job-alerts" not in prompt
    assert "sender" in prompt


def test_supports_follows_templates():
    assert render_run.supports("contract", "evening")
    assert not render_run.supports("contract", "morning")
    assert render_run.supports("de_employment", "morning")


@pytest.mark.parametrize("mode,rc", [("evening", 0), ("morning", 3), ("onboarding", 3)])
def test_check_flag_exits_3_when_mode_unsupported(mode, rc, monkeypatch, tmp_path, capsys):
    """run_agent.sh питає `--check` до рендеру: 3 = ринок цей режим не має, пропустити тихо."""
    monkeypatch.setenv("JOBSEARCH_CLIENT", "jordan")
    monkeypatch.setenv("JOBSEARCH_CLIENTS_DIR", str(EX))
    _client.active.cache_clear()
    assert render_run.main([mode, "--check", "--out-dir", str(tmp_path)]) == rc
    assert not list(tmp_path.iterdir()), "--check нічого не пише"
    out = capsys.readouterr()
    assert out.out == ""
    if rc:
        assert "ще не підтримується" in out.err


def test_main_reports_client_error_without_traceback(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("JOBSEARCH_CLIENT", "jordan")
    monkeypatch.setenv("JOBSEARCH_CLIENTS_DIR", str(EX))
    _client.active.cache_clear()
    assert render_run.main(["morning", "--out-dir", str(tmp_path)]) == 3
    assert "ще не підтримується" in capsys.readouterr().err
