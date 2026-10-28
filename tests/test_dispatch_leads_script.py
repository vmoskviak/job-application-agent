"""scripts/dispatch_leads.py: конфіг з клієнта й оточення, тіньовий режим, push трекера лише після живої подачі."""
from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(ENGINE / "scripts"))

import dispatch_leads as dl
from bot import client as _client
from bot.dispatch_leads import DispatchResult

EX = ENGINE / "examples" / "clients"


def _jordan(tmp_path):
    c = dataclasses.replace(_client.load("jordan", base=EX), root=tmp_path / "jordan")
    (c.root / "var").mkdir(parents=True, exist_ok=True)
    c.tracker.write_text("# tracker\n", encoding="utf-8")
    return c


def test_build_config_from_client(monkeypatch, tmp_path):
    monkeypatch.delenv("JOBSEARCH_DISPATCH_DRY_RUN", raising=False)
    c = _jordan(tmp_path)
    cfg = dl.build_config(c, argparse.Namespace(dry_run=False))
    assert cfg.account == c.send_from and cfg.limits == {"email": 5, "form": 5}
    assert cfg.lang == c.report_lang and cfg.chat_id == 424242 and cfg.followup_days == 7
    assert cfg.dry_run is False


def test_build_config_dry_run_env(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBSEARCH_DISPATCH_DRY_RUN", "1")
    assert dl.build_config(_jordan(tmp_path), argparse.Namespace(dry_run=False)).dry_run is True
    monkeypatch.setenv("JOBSEARCH_DISPATCH_DRY_RUN", "0")
    assert dl.build_config(_jordan(tmp_path), argparse.Namespace(dry_run=False)).dry_run is False
    assert dl.build_config(_jordan(tmp_path), argparse.Namespace(dry_run=True)).dry_run is True


def test_pass_pushes_tracker_only_after_live_send(monkeypatch, tmp_path):
    c = _jordan(tmp_path)
    calls = {"pull": 0, "push": 0}
    monkeypatch.setattr(dl, "pull_data", lambda root, alert: calls.__setitem__("pull", calls["pull"] + 1) or True)
    monkeypatch.setattr(dl, "push_tracker", lambda root, msg, alert: calls.__setitem__("push", calls["push"] + 1) or True)
    monkeypatch.setattr(dl, "run_dispatch", lambda *a, **k: DispatchResult(sent=["lead-x"]))
    cfg = dl.build_config(c, argparse.Namespace(dry_run=False))
    assert dl.run_pass(c, cfg, transport=None) == 0
    assert calls == {"pull": 1, "push": 1}
    monkeypatch.setattr(dl, "run_dispatch", lambda *a, **k: DispatchResult())
    assert dl.run_pass(c, cfg, transport=None) == 0
    assert calls == {"pull": 2, "push": 1}
    cfg.dry_run = True
    monkeypatch.setattr(dl, "run_dispatch", lambda *a, **k: DispatchResult(sent=["lead-y"]))
    assert dl.run_pass(c, cfg, transport=None) == 0
    assert calls == {"pull": 3, "push": 1}


def test_pass_prints_result_line(monkeypatch, tmp_path, capsys):
    c = _jordan(tmp_path)
    monkeypatch.setattr(dl, "pull_data", lambda root, alert: True)
    monkeypatch.setattr(dl, "run_dispatch", lambda *a, **k: DispatchResult(failed=["lead-x"], skipped_limit=2))
    dl.run_pass(c, dl.build_config(c, argparse.Namespace(dry_run=True)), transport=None)
    out = capsys.readouterr().out.strip().splitlines()[-1]
    assert out.startswith("DISPATCH OK: ") and '"failed": ["lead-x"]' in out and '"skipped_limit": 2' in out


def test_build_config_forces_dry_run_unless_send_policy_auto(monkeypatch, tmp_path):
    """Рев'ю 2b, Important 3: send_policy читається; усе, крім auto, означає тіньовий режим."""
    monkeypatch.delenv("JOBSEARCH_DISPATCH_DRY_RUN", raising=False)
    c = _jordan(tmp_path)
    assert c.targeting["send_policy"] == "auto"
    assert dl.build_config(c, argparse.Namespace(dry_run=False)).dry_run is False
    c.targeting["send_policy"] = "approve"
    assert dl.build_config(c, argparse.Namespace(dry_run=False)).dry_run is True
    del c.targeting["send_policy"]
    assert dl.build_config(c, argparse.Namespace(dry_run=False)).dry_run is True
