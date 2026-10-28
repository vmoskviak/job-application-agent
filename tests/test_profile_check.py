"""Pre-flight bot.scrapers.profile: контрактний ринок не логіниться в портали,
тож відсутній браузерний профіль не має зупиняти ран."""
import importlib
import os

import pytest

from bot import client as _client
from bot.scrapers import profile as _profile


@pytest.fixture
def reload_profile(monkeypatch):
    def _reload(slug: str):
        monkeypatch.setenv("JOBSEARCH_CLIENT", slug)
        _client.active.cache_clear()  # active() кешує клієнта на процес
        return importlib.reload(_profile)
    yield _reload
    os.environ["JOBSEARCH_CLIENT"] = "erika"
    _client.active.cache_clear()
    importlib.reload(_profile)


def test_contract_client_passes_without_profile(reload_profile, tmp_path, monkeypatch, capsys):
    mod = reload_profile("jordan")
    monkeypatch.setattr(mod, "PROFILE_DIR", tmp_path / "missing")
    with pytest.raises(SystemExit) as e:
        mod.main()
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "LOGIN REQUIRED" not in out and "contract" in out


def test_employment_client_still_needs_profile(reload_profile, tmp_path, monkeypatch, capsys):
    mod = reload_profile("erika")
    monkeypatch.setattr(mod, "PROFILE_DIR", tmp_path / "missing")
    with pytest.raises(SystemExit) as e:
        mod.main()
    assert e.value.code == 1
    assert "LOGIN REQUIRED" in capsys.readouterr().out
