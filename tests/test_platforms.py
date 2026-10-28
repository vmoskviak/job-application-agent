from datetime import datetime, timedelta, timezone

from bot.platforms import (
    clear,
    declared,
    effective,
    end_of_day,
    first_of_next_month,
    platform_of,
    read_state,
    set_off,
    set_shadow,
    status_lines,
)


def test_platform_of_by_channel_and_host():
    assert platform_of("linkedin_easy", "https://www.linkedin.com/jobs/view/1/") == "linkedin"
    assert platform_of("linkedin_offsite", "https://www.linkedin.com/jobs/view/1/") == "linkedin"
    assert platform_of("platform_login", "https://www.freelancermap.de/nproj/1.html") == "freelancermap"
    assert platform_of("platform_login", "https://www.gulp.de/gulp2/g/projekte/agentur/C1") == "gulp"
    assert platform_of("platform_login", "https://www.freelance.de/projekte/projekt-1-x") == "freelance_de"
    assert platform_of("platform_login", "https://notfreelancermap.de/x") is None
    assert platform_of("form", "https://jobs.ashbyhq.com/x/1") is None


def test_declared_normalises_values():
    t = {"platforms": {"linkedin": "on", "gulp": "shadow", "freelancermap": True, "xing": "maybe"}}
    assert declared(t) == {"linkedin": "on", "gulp": "shadow", "freelancermap": "on", "xing": "off"}
    assert declared({}) == {}


def test_effective_temporary_off_wins_until_expiry(tmp_path):
    now = datetime(2026, 10, 28, 10, 0)
    path = tmp_path / "platforms.json"
    set_off(path, "linkedin", "restricted", end_of_day(now), now)
    t = {"platforms": {"linkedin": "on", "gulp": "on"}}
    assert effective(t, path, now)["linkedin"] == "off"
    assert effective(t, path, datetime(2026, 10, 29, 8, 0))["linkedin"] == "on"
    assert effective(t, path, now)["gulp"] == "on"
    assert "linkedin" in read_state(path)   # прострочене не видаляється


def test_shadow_entry_does_not_override_declared_off(tmp_path):
    now = datetime(2026, 10, 28, 10, 0)
    path = tmp_path / "platforms.json"
    set_shadow(path, "freelance_de", end_of_day(now), now)
    assert effective({"platforms": {"freelance_de": "off"}}, path, now)["freelance_de"] == "off"


def test_clear_removes_entry_and_unreadable_file_is_empty(tmp_path):
    path = tmp_path / "platforms.json"
    path.write_text("{not json")
    assert read_state(path) == {}
    now = datetime(2026, 10, 28, 10, 0)
    set_off(path, "gulp", "login_required", end_of_day(now), now)
    clear(path, "gulp")
    assert read_state(path) == {}


def test_first_of_next_month_and_end_of_day():
    assert first_of_next_month(datetime(2026, 12, 28, 10, 0)) == datetime(2027, 1, 1, 0, 0)
    assert end_of_day(datetime(2026, 10, 28, 10, 0)) == datetime(2026, 10, 28, 23, 59)


def test_status_lines(tmp_path):
    now = datetime(2026, 10, 28, 10, 0)
    path = tmp_path / "platforms.json"
    set_off(path, "linkedin", "restricted", end_of_day(now), now)
    lines = status_lines({"platforms": {"linkedin": "on", "gulp": "shadow"}}, path, now,
                         {"linkedin": 2}, {"linkedin": 5, "gulp": 3})
    assert lines == ["linkedin: on → off (restricted) 2/5", "gulp: shadow 0/3"]


def test_effective_with_aware_now_from_cli(tmp_path):
    """CLI передає datetime.now(tz=utc).astimezone(): порівняння з наївними позначками не падає."""
    now = datetime(2026, 10, 28, 10, 0).astimezone()   # місцевий час із зоною
    path = tmp_path / "platforms.json"
    set_off(path, "gulp", "login_required", end_of_day(now), now)
    t = {"platforms": {"gulp": "on", "linkedin": "on"}}
    assert effective(t, path, now) == {"gulp": "off", "linkedin": "on"}
    assert effective(t, path, datetime(2026, 10, 29, 8, 0).astimezone())["gulp"] == "on"
    set_shadow(path, "linkedin", end_of_day(now), now)
    assert effective(t, path, now)["linkedin"] == "shadow"
    assert status_lines(t, path, now, {}, {"gulp": 3})[0] == "gulp: on → off (login_required) 0/3"


def test_set_off_with_aware_times_writes_naive_local_format(tmp_path):
    path = tmp_path / "platforms.json"
    local = datetime(2026, 10, 28, 10, 0).astimezone()
    set_off(path, "gulp", "operator", end_of_day(local), local)
    assert read_state(path)["gulp"] == {"off_until": "2026-10-28 23:59", "reason": "operator", "at": "2026-10-28 10:00"}
    utc = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)   # інша зона: пишеться місцевий настінний час
    set_shadow(path, "linkedin", utc, utc)
    expected = utc.astimezone().strftime("%Y-%m-%d %H:%M")
    assert read_state(path)["linkedin"] == {"shadow_until": expected, "at": expected}
    assert effective({"platforms": {"linkedin": "on"}}, path, utc - timedelta(minutes=1))["linkedin"] == "shadow"


def test_write_is_atomic_and_leaves_no_tmp(tmp_path, monkeypatch):
    import json

    import pytest

    from bot import platforms

    path = tmp_path / "state" / "platforms.json"
    now = datetime(2026, 10, 28, 10, 0).astimezone()   # місцевий час із зоною, як із CLI
    set_off(path, "gulp", "login_required", end_of_day(now), now)
    set_shadow(path, "linkedin", end_of_day(now), now)
    assert sorted(p.name for p in path.parent.iterdir()) == ["platforms.json"]
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "gulp": {"off_until": "2026-10-28 23:59", "reason": "login_required", "at": "2026-10-28 10:00"},
        "linkedin": {"shadow_until": "2026-10-28 23:59", "at": "2026-10-28 10:00"}}

    before = path.read_text(encoding="utf-8")

    def broken_replace(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(platforms.os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        clear(path, "gulp")
    assert path.read_text(encoding="utf-8") == before   # старий файл цілий, тимчасовий прибрано
    assert sorted(p.name for p in path.parent.iterdir()) == ["platforms.json"]
