"""scripts/apply_leads.py: конфіг з клієнта й оточення, контекст відповідей, прохід без дисплея
чи з зайнятим профілем, алерт про збій лока, юніти systemd. PlaywrightDriver тут не створюється."""
from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(ENGINE / "scripts"))

import apply_leads as al
from apply_leads import build_config, ctx_for

from bot import client as _client
from bot.apply.models import ApplyResult
from bot.apply_leads import ApplyPassResult
from bot.packages import Package
from bot.sendqueue import QUEUE_FILE
from bot.state import JsonlStore, state_dir

EX = ENGINE / "examples" / "clients"
SYSTEMD = ENGINE / "scripts" / "server" / "systemd"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("JOBSEARCH_APPLY_DRY_RUN", "JOBSEARCH_HANDOFF_UNTIL", "JOBSEARCH_VNC_HINT"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def client(tmp_path):
    """Контрактний приклад (send_policy: auto) з коренем у tmp."""
    c = dataclasses.replace(_client.load("jordan", base=EX), root=tmp_path / "jordan")
    (c.root / "var").mkdir(parents=True, exist_ok=True)
    c.tracker.write_text("# tracker\n", encoding="utf-8")
    return c


@pytest.fixture
def client_with_policy(client):
    client.targeting["send_policy"] = "approve"
    return client


@pytest.fixture
def pkg_with_two_attachments(tmp_path):
    att = tmp_path / "attachments"
    return Package("acme-dev", tmp_path / "pkg", {"company": "Acme", "title": "Dev"}, "form",
                   "https://jobs.ashbyhq.com/acme/1", "ashby", "en", "Subject", "note body",
                   [att / "Projektliste.pdf", att / "CV_Erika.pdf"])


def _args(dry_run: bool = False) -> argparse.Namespace:
    return argparse.Namespace(dry_run=dry_run)


def test_build_config_dry_run_from_env(monkeypatch, client):
    assert build_config(client, _args()).dry_run is False
    monkeypatch.setenv("JOBSEARCH_APPLY_DRY_RUN", "1")
    assert build_config(client, _args()).dry_run is True
    monkeypatch.setenv("JOBSEARCH_APPLY_DRY_RUN", "0")
    assert build_config(client, _args()).dry_run is False
    assert build_config(client, _args(dry_run=True)).dry_run is True


def test_build_config_policy_not_auto_forces_dry_run(client_with_policy):
    assert build_config(client_with_policy, _args()).dry_run is True
    del client_with_policy.targeting["send_policy"]
    assert build_config(client_with_policy, _args()).dry_run is True


def test_build_config_handoff_until_from_env(monkeypatch, client):
    assert build_config(client, _args()).handoff_until == "19:55"
    monkeypatch.setenv("JOBSEARCH_HANDOFF_UNTIL", "18:30")
    assert build_config(client, _args()).handoff_until == "18:30"
    monkeypatch.setenv("JOBSEARCH_HANDOFF_UNTIL", "half past six")   # кривий формат: дефолт, не падіння
    assert build_config(client, _args()).handoff_until == "19:55"


def test_build_config_from_client(monkeypatch, client):
    monkeypatch.setenv("JOBSEARCH_VNC_HINT", "ssh tunnel, then vnc://localhost:5901")
    monkeypatch.setenv("JOBSEARCH_LOCK_DIR", "/tmp/locks-test")
    cfg = build_config(client, _args())
    assert cfg.limits == {"email": 5, "form": 5} and cfg.lang == client.report_lang
    assert cfg.chat_id == 424242 and cfg.followup_days == 7 and cfg.source_label == "contract"
    assert cfg.vnc_hint == "ssh tunnel, then vnc://localhost:5901"
    assert cfg.lock_path == Path("/tmp/locks-test/jobsearch-agent.lock")


def test_build_config_limits_default_without_daily_limits(client):
    """Фінальне рев'ю, I8: клієнт без daily_limits дістає дефолти спеки §6, не form 0."""
    client.targeting.pop("daily_limits", None)
    assert build_config(client, _args()).limits == {"email": 5, "form": 5}
    client.targeting["daily_limits"] = {"form": 2}
    assert build_config(client, _args()).limits == {"email": 5, "form": 2}


def test_ctx_for_picks_cv_attachment(client, pkg_with_two_attachments):
    ctx = ctx_for(client, {}, pkg_with_two_attachments)
    assert ctx.resume.name == "CV_Erika.pdf"
    assert ctx.profile is client.profile and ctx.lead == pkg_with_two_attachments.lead
    assert ctx.note == "note body" and ctx.lang == "en"


def test_ctx_for_falls_back_to_first_attachment(client, pkg_with_two_attachments):
    pkg = dataclasses.replace(pkg_with_two_attachments, attachments=[Path("/x/Resume.pdf"), Path("/x/B.pdf")])
    assert ctx_for(client, {}, pkg).resume == Path("/x/Resume.pdf")


def test_ctx_for_cv_prefix_is_case_insensitive(client, pkg_with_two_attachments):
    pkg = dataclasses.replace(pkg_with_two_attachments, attachments=[Path("/x/Projekte.pdf"), Path("/x/cv_de.pdf")])
    assert ctx_for(client, {}, pkg).resume == Path("/x/cv_de.pdf")


def test_ctx_for_without_attachments_raises(client, pkg_with_two_attachments):
    """Рев'ю 6, minor: без вкладень не Path("."), а ValueError; run_apply кладе лід у held."""
    pkg = dataclasses.replace(pkg_with_two_attachments, attachments=[])
    with pytest.raises(ValueError, match="no attachments"):
        ctx_for(client, {}, pkg)


class _FakeDriver:
    """Замість PlaywrightDriver: фіксує, чи брали профіль і чи гасили браузер."""

    def __init__(self, client, busy: bool = False):
        self.busy = busy
        self.held = False
        self.shut = False
        self.shots_dir = None

    def hold_profile(self):
        if self.busy:
            raise RuntimeError("Playwright profile busy")
        self.held = True

    def shutdown(self):
        self.shut = True


@pytest.fixture
def wired(monkeypatch, client):
    """run_pass без git, дисплея, браузера й пошти; calls рахує виклики."""
    calls = {"pull": 0, "push": 0, "apply": 0, "alerts": [], "drivers": []}
    monkeypatch.setattr(al, "pull_data", lambda root, alert: calls.__setitem__("pull", calls["pull"] + 1) or True)
    monkeypatch.setattr(al, "push_tracker",
                        lambda root, msg, alert: calls.__setitem__("push", calls["push"] + 1) or True)
    monkeypatch.setattr(al, "queue_alert", lambda msg, tail="": calls["alerts"].append(msg))
    monkeypatch.setattr(al, "_display_available", lambda: True)

    def driver_factory(c, busy=False):
        d = _FakeDriver(c, busy)
        calls["drivers"].append(d)
        return d

    monkeypatch.setattr(al, "PlaywrightDriver", driver_factory)
    calls["result"] = ApplyPassResult()

    def fake_run_apply(*a, **k):
        calls["apply"] += 1
        return calls["result"]

    monkeypatch.setattr(al, "run_apply", fake_run_apply)
    _queue(client).append({"id": "lead-form", "status": "queued", "channel": "form"})
    return calls


def _queue(c) -> JsonlStore:
    return JsonlStore(state_dir(c.var) / QUEUE_FILE)


def test_pass_without_display_does_not_touch_queue(monkeypatch, client, wired, capsys):
    monkeypatch.setattr(al, "_display_available", lambda: False)
    assert al.run_pass(client, build_config(client, _args()), transport=None) == 0
    assert wired["apply"] == 0 and wired["drivers"] == []
    assert "[apply] без DISPLAY воркер не запускається" in capsys.readouterr().out


def test_pass_with_busy_profile_exits_quietly(monkeypatch, client, wired, capsys):
    monkeypatch.setattr(al, "PlaywrightDriver", lambda c: _FakeDriver(c, busy=True))
    assert al.run_pass(client, build_config(client, _args()), transport=None) == 0
    assert wired["apply"] == 0 and wired["alerts"] == []
    assert "[apply] профіль зайнятий (hand-off триває)" in capsys.readouterr().out


def test_pass_pushes_tracker_only_after_live_send(client, wired, capsys):
    cfg = build_config(client, _args())
    wired["result"] = ApplyPassResult(sent=["lead-x"])
    assert al.run_pass(client, cfg, transport=None) == 0
    assert (wired["pull"], wired["push"]) == (1, 1)
    assert wired["drivers"][-1].held and wired["drivers"][-1].shut
    out = capsys.readouterr().out.strip().splitlines()[-1]
    assert out.startswith("APPLY OK: ") and '"sent": ["lead-x"]' in out
    wired["result"] = ApplyPassResult()
    al.run_pass(client, cfg, transport=None)
    assert (wired["pull"], wired["push"]) == (2, 1)
    cfg.dry_run = True
    wired["result"] = ApplyPassResult(sent=["lead-y"])
    al.run_pass(client, cfg, transport=None)
    assert (wired["pull"], wired["push"]) == (3, 1)


def test_pass_alerts_on_lock_error(client, wired):
    wired["result"] = ApplyPassResult(lock_busy=True, error="lock: Permission denied")
    assert al.run_pass(client, build_config(client, _args()), transport=None) == 0
    assert wired["alerts"] == ["Browser-Worker: lock: Permission denied"]


def test_pass_after_deadline_is_one_stderr_line(client, wired, capsys):
    wired["result"] = ApplyPassResult(after_deadline=True)
    assert al.run_pass(client, build_config(client, _args()), transport=None) == 0
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "19:55" in err[0]
    assert wired["alerts"] == [] and wired["push"] == 0


def test_pass_stopped_by_margin_still_pushes_what_it_sent(client, wired, capsys):
    """Рев'ю 6, Important 2: прохід зупинився за запасом до дедлайну, але подане до того комітиться."""
    wired["result"] = ApplyPassResult(sent=["lead-x"], after_deadline=True)
    assert al.run_pass(client, build_config(client, _args()), transport=None) == 0
    assert wired["push"] == 1
    cap = capsys.readouterr()
    assert len(cap.err.strip().splitlines()) == 1 and "APPLY OK: " in cap.out


def test_idle_pass_does_not_hold_profile(client, wired):
    """Рев'ю 6, minor: без queued form-лідів профіль не береться (relogin, session_health не блокуються),
    але run_apply однаково йде: протухлі hand-off і sending розбираються."""
    _queue(client).update("lead-form", status="sent")
    assert al.run_pass(client, build_config(client, _args()), transport=None) == 0
    assert wired["apply"] == 1 and not wired["drivers"][-1].held


def test_idle_pass_ignores_busy_profile(monkeypatch, client, wired, capsys):
    _queue(client).update("lead-form", status="held")
    monkeypatch.setattr(al, "PlaywrightDriver", lambda c: _FakeDriver(c, busy=True))
    assert al.run_pass(client, build_config(client, _args()), transport=None) == 0
    assert wired["apply"] == 1 and "профіль зайнятий" not in capsys.readouterr().out


@pytest.fixture
def main_wired(monkeypatch, client):
    """main() без справжнього клієнта, сигналів і проходу: ловить cfg і встановлений обробник SIGTERM."""
    seen = {"cfg": None, "transport": "unset", "signals": {}}
    monkeypatch.setattr(al._client, "active", lambda: client)
    monkeypatch.setattr(sys, "argv", ["apply_leads.py"])
    monkeypatch.setattr(al.signal, "signal", lambda sig, h: seen["signals"].__setitem__(sig, h))

    def fake_pass(c, cfg, transport):
        seen["cfg"], seen["transport"] = cfg, transport
        return 0

    monkeypatch.setattr(al, "run_pass", fake_pass)
    return seen


def test_main_without_transport_forces_dry_run(monkeypatch, client, main_wired, capsys):
    """Рев'ю 6, Important 1: без Telegram hand-off картка не дійде, а вкладка чекала б до 19:55."""
    monkeypatch.delenv(client.telegram["token_env"], raising=False)
    assert al.main() == 0
    assert main_wired["transport"] is None and main_wired["cfg"].dry_run is True
    err = capsys.readouterr().err
    assert "лише тіньовий режим" in err and client.telegram["token_env"] in err


def test_main_with_transport_stays_live(monkeypatch, client, main_wired):
    monkeypatch.setenv(client.telegram["token_env"], "123:abc")
    assert al.main() == 0
    assert main_wired["transport"] is not None and main_wired["cfg"].dry_run is False


def test_main_installs_sigterm_exit(monkeypatch, client, main_wired):
    """Рев'ю 6, minor: systemctl stop шле SIGTERM; вихід через sys.exit, щоб finally закрив браузер."""
    import signal

    monkeypatch.delenv(client.telegram["token_env"], raising=False)
    al.main()
    handler = main_wired["signals"][signal.SIGTERM]
    with pytest.raises(SystemExit) as e:
        handler(signal.SIGTERM, None)
    assert e.value.code == 143


def test_units_exist():
    service = (SYSTEMD / "jobsearch-apply@.service").read_text(encoding="utf-8")
    timer = (SYSTEMD / "jobsearch-apply@.timer").read_text(encoding="utf-8")
    assert "DISPLAY=:99" in service and "Type=oneshot" in service and "TimeoutStartSec=12h" in service
    assert "run_service.sh scripts/apply_leads.py %i" in service
    assert "08..19:40" in timer and "RandomizedDelaySec=120" in timer and "Persistent=false" in timer


# ── етап 2d: драйвер за каналом, конфіг платформ ─────────────────────────────

class _Page:
    def __init__(self, url: str = "about:blank") -> None:
        self.url = url


def _result(url: str = "u") -> ApplyResult:
    return ApplyResult("submitted", "x", url)


@pytest.fixture
def driver_calls(monkeypatch, client, pkg_with_two_attachments):
    """PlaywrightDriver з підміненими адаптерами: calls фіксує, хто і з чим викликаний."""
    calls = []
    monkeypatch.setattr("bot.apply.linkedin.easy_apply",
                        lambda page, url, ctx, llm, brief, shots, dry: calls.append(("linkedin", llm, dry)) or _result())
    monkeypatch.setattr("bot.apply.freelancermap.apply",
                        lambda page, url, note, att, shots, dry: calls.append(("freelancermap", note, att)) or _result())
    monkeypatch.setattr("bot.apply.gulp.apply",
                        lambda page, url, note, rate, cur, cv, shots, dry: calls.append(("gulp", rate, cur, cv.name))
                        or _result())
    monkeypatch.setattr("bot.apply.ats_form.apply",
                        lambda page, url, ctx, llm, brief, shots_dir, dry_run: calls.append(("form", llm)) or _result())
    d = al.PlaywrightDriver(client)
    d._llm = llm = object()
    return calls, d, ctx_for(client, {}, pkg_with_two_attachments), llm


def test_driver_dispatches_by_channel(driver_calls):
    calls, d, ctx, llm = driver_calls
    d.apply(_Page(), "https://www.linkedin.com/jobs/view/1/", ctx, True, "linkedin_easy")
    d.apply(_Page(), "https://www.freelancermap.de/nproj/1.html", ctx, False, "platform_login")
    d.apply(_Page(), "https://www.gulp.de/gulp2/g/projekte/agentur/C1", ctx, False, "platform_login")
    d.apply(_Page(), "https://jobs.ashbyhq.com/acme/1", ctx, False, "form")
    d.apply(_Page(), "https://jobs.ashbyhq.com/acme/1", ctx, False)
    assert calls == [("linkedin", llm, True), ("freelancermap", "note body", ctx.attachments),
                     ("gulp", "95", "EUR", "CV_Erika.pdf"), ("form", llm), ("form", llm)]


def test_driver_platform_without_adapter_or_rate_fails(driver_calls, client):
    calls, d, ctx, _ = driver_calls
    r = d.apply(_Page(), "https://www.malt.de/project/1", ctx, False, "platform_login")
    assert (r.status, r.portal, r.reason) == ("failed", "malt", "no adapter")
    r = d.apply(_Page(), "https://example.org/x", ctx, False, "platform_login")
    assert (r.status, r.portal, r.reason) == ("failed", "platform", "no adapter")
    ctx.profile = {**client.profile, "rate": {"currency_default": "EUR"}}
    r = d.apply(_Page(), "https://www.gulp.de/gulp2/g/projekte/agentur/C1", ctx, False, "platform_login")
    assert (r.status, r.portal, r.reason) == ("failed", "gulp", "profile.rate.hourly_target missing")
    assert calls == []


def test_driver_gulp_rate_accepts_numbers_and_rejects_text(driver_calls, client):
    calls, d, ctx, _ = driver_calls
    gulp_url = "https://www.gulp.de/gulp2/g/projekte/agentur/C1"
    for value, shown in ((95, "95"), (95.6, "96"), ("97.2", "97")):
        ctx.profile = {**client.profile, "rate": {"hourly_target": value}}
        d.apply(_Page(), gulp_url, ctx, False, "platform_login")
        assert calls[-1] == ("gulp", shown, "EUR", "CV_Erika.pdf")
    ctx.profile = {**client.profile, "rate": {"hourly_target": "ninety"}}
    r = d.apply(_Page(), gulp_url, ctx, False, "platform_login")
    assert (r.status, r.portal, r.reason) == ("failed", "gulp", "profile.rate.hourly_target not numeric")
    assert len(calls) == 3


def test_driver_resolve_offsite_and_probe_pick_linkedin(monkeypatch, driver_calls):
    _, d, _, _ = driver_calls
    monkeypatch.setattr("bot.apply.linkedin.resolve_offsite", lambda page, url: ("resolved", "https://ext/1"))
    monkeypatch.setattr("bot.apply.linkedin.probe_submitted", lambda page: "application was sent")
    monkeypatch.setattr("bot.apply.ats_form.probe", lambda page: (_ for _ in ()).throw(AssertionError("ats probe")))
    assert d.resolve_offsite(_Page(), "https://www.linkedin.com/jobs/view/1/") == ("resolved", "https://ext/1")
    assert d.probe_submitted(_Page("https://www.linkedin.com/jobs/view/1/")) == "application was sent"
    assert d.probe_submitted(_Page("https://jobs.ashbyhq.com/acme/1")) == ""   # ATS-проба впала: чекаємо далі


def test_build_config_carries_targeting_and_platforms_path(client):
    cfg = build_config(client, _args())
    assert cfg.targeting is client.targeting
    assert cfg.platforms_path == state_dir(client.var) / "platforms.json"


def test_ctx_for_fills_attachments(client, pkg_with_two_attachments):
    ctx = ctx_for(client, {}, pkg_with_two_attachments)
    assert ctx.attachments == list(pkg_with_two_attachments.attachments)
    assert ctx.resume.name == "CV_Erika.pdf"


def test_pass_holds_profile_for_queued_platform_lead(client, wired):
    _queue(client).update("lead-form", status="sent")
    _queue(client).append({"id": "lead-li", "status": "queued", "channel": "linkedin_easy"})
    assert al.run_pass(client, build_config(client, _args()), transport=None) == 0
    assert wired["drivers"][-1].held
