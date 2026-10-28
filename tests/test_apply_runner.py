import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.apply.models import ApplyResult, already_submitted, load_result
from bot.apply.runner import apply_one, main as runner_main, portal_from_url


def test_portal_from_url():
    assert portal_from_url("https://www.stepstone.de/stelle/x") == "stepstone"
    assert portal_from_url("https://www.xing.com/jobs/x") == "xing"
    assert portal_from_url("https://www.linkedin.com/jobs/view/1") is None
    assert portal_from_url("https://karriere.foo.de/1") is None
    assert portal_from_url("https://jobs.beispielwerk.example/de/sites/BEISPIELWERK/job/100001/") == "oracle_cx"
    assert portal_from_url("https://foo.example.com/en/sites/CX_1/job/123") == "oracle_cx"


def test_apply_result_roundtrip(tmp_path):
    result = ApplyResult("submitted", "stepstone", "https://x", ["a.png"],
                         confirmation_text="vielen dank für ihre bewerbung")
    result.save(tmp_path)
    loaded = load_result(tmp_path)
    assert loaded.status == "submitted"
    assert already_submitted(tmp_path)


def test_apply_one_skips_already_submitted(tmp_path):
    ApplyResult("submitted", "stepstone", "https://x").save(tmp_path)
    result = apply_one(
        {"app_dir": str(tmp_path), "url": "https://www.stepstone.de/stelle/x"},
        dry_run=True,
    )
    assert result.status == "skipped"
    assert "already submitted" in result.reason


def test_apply_one_unsupported_portal(tmp_path):
    result = apply_one(
        {"app_dir": str(tmp_path), "url": "https://www.linkedin.com/jobs/view/1"},
        dry_run=True,
    )
    assert result.status == "skipped"
    assert "not supported" in result.reason


def test_apply_one_missing_app_dir(tmp_path):
    result = apply_one(
        {"app_dir": str(tmp_path / "nope"), "url": "https://www.stepstone.de/stelle/x"},
        dry_run=True,
    )
    assert result.status == "failed"


def test_runaway_guard_aborts_whole_run(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("APPLY_GUARD_LIMIT", raising=False)
    queue = [
        {"app_dir": str(tmp_path / f"a{i}"), "url": "https://www.stepstone.de/stelle/x"}
        for i in range(26)
    ]
    batch = tmp_path / "queue.json"
    batch.write_text(json.dumps(queue), encoding="utf-8")

    exit_code = runner_main(["--batch", str(batch), "--dry-run"])
    assert exit_code == 3
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["guard_triggered"] is True
    assert summary["results"] == []
    # нічого не створено — жодна заявка не чіпалась
    assert not any((tmp_path / f"a{i}").exists() for i in range(26))


def test_guard_limit_env_override(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("APPLY_GUARD_LIMIT", "2")
    queue = [
        {"app_dir": str(tmp_path / f"a{i}"), "url": "https://www.stepstone.de/stelle/x"}
        for i in range(3)
    ]
    batch = tmp_path / "queue.json"
    batch.write_text(json.dumps(queue), encoding="utf-8")
    assert runner_main(["--batch", str(batch), "--dry-run"]) == 3


def test_batch_accepts_filtered_json_shape(tmp_path, capsys, monkeypatch):
    """{selected: [...]} від bot.filters.rules теж приймається."""
    monkeypatch.delenv("APPLY_GUARD_LIMIT", raising=False)
    batch = tmp_path / "filtered.json"
    batch.write_text(json.dumps({"selected": [
        {"app_dir": str(tmp_path / "missing"), "url": "https://karriere.foo.de/1"},
    ]}), encoding="utf-8")
    exit_code = runner_main(["--batch", str(batch), "--dry-run"])
    assert exit_code == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["counts"] == {"skipped": 1}
