"""Серверна обв'язка етапу 2a: systemd-юніти вказують на наявні скрипти, run_service.sh
бере оточення з файла машини й запускає python рушія з клієнтом у JOBSEARCH_CLIENT."""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNITS_DIR = ROOT / "scripts" / "server" / "systemd"
UNITS = sorted(UNITS_DIR.glob("*.service")) if UNITS_DIR.is_dir() else []
RUN_SERVICE = ROOT / "scripts" / "server" / "run_service.sh"


def test_units_reference_existing_scripts():
    assert {u.name for u in UNITS} == {"jobsearch-telegram@.service", "jobsearch-inbox@.service",
                                       "jobsearch-dispatch@.service", "jobsearch-apply@.service"}
    for unit in UNITS:
        text = unit.read_text(encoding="utf-8")
        m = re.search(r"run_service\.sh (\S+) %i", text)
        assert m, unit.name
        assert (ROOT / m.group(1)).exists(), m.group(1)
        assert "ExecStart=/opt/jobsearch-autopilot/scripts/server/run_service.sh" in text


def test_telegram_service_restarts_and_inbox_is_oneshot():
    tg = (UNITS_DIR / "jobsearch-telegram@.service").read_text(encoding="utf-8")
    assert "Restart=always" in tg and "RestartSec=30" in tg and "WantedBy=multi-user.target" in tg
    inbox = (UNITS_DIR / "jobsearch-inbox@.service").read_text(encoding="utf-8")
    assert "Type=oneshot" in inbox and "--days 2" in inbox


def test_timer_runs_hourly_in_daytime():
    timer = (UNITS_DIR / "jobsearch-inbox@.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 07..23:10:00" in timer and "Persistent=false" in timer
    assert "RandomizedDelaySec=120" in timer and "WantedBy=timers.target" in timer


def test_run_service_sources_env_and_execs(tmp_path):
    engine = tmp_path / "engine"
    (engine / "scripts" / "server").mkdir(parents=True)
    (engine / "scripts" / "server" / "run_service.sh").write_text(RUN_SERVICE.read_text(encoding="utf-8"),
                                                                  encoding="utf-8")
    py = engine / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text('#!/bin/bash\necho "$JOBSEARCH_CLIENT $JOBSEARCH_TEST_VALUE $1 $2 $3"\n', encoding="utf-8")
    py.chmod(py.stat().st_mode | stat.S_IXUSR)
    envfile = tmp_path / "claude-env"
    envfile.write_text("export JOBSEARCH_TEST_VALUE=42\n", encoding="utf-8")

    proc = subprocess.run(["bash", str(engine / "scripts" / "server" / "run_service.sh"),
                           "scripts/x.py", "jordan", "--days", "2"],
                          env={**os.environ, "JOBSEARCH_ENV_FILE": str(envfile)},
                          capture_output=True, text=True, check=False)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"jordan 42 {engine.resolve()}/scripts/x.py --days 2"


def test_run_service_requires_script_and_slug(tmp_path):
    proc = subprocess.run(["bash", str(RUN_SERVICE)], capture_output=True, text=True, check=False,
                          env={**os.environ, "JOBSEARCH_ENV_FILE": str(tmp_path / "none")})
    assert proc.returncode != 0 and "usage" in (proc.stderr + proc.stdout).lower()


def test_dispatch_timer_daytime_only():
    """Етап 2b: диспетчер подач щопівгодини вдень, oneshot, без наздоганяння."""
    timer = (UNITS_DIR / "jobsearch-dispatch@.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 08..20:25:00" in timer and "Persistent=false" in timer
    assert "RandomizedDelaySec=120" in timer and "WantedBy=timers.target" in timer
    svc = (UNITS_DIR / "jobsearch-dispatch@.service").read_text(encoding="utf-8")
    assert "Type=oneshot" in svc and "scripts/dispatch_leads.py %i" in svc
