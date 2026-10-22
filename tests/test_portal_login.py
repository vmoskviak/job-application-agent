"""bot/scrapers/portal_login: облікові дані поза git, детекція login-хоста, кеш на контексті."""
import json

from bot.scrapers import portal_login as pl


def test_load_credentials(tmp_path):
    f = tmp_path / "creds.json"
    assert pl.load_credentials(f, "xing") is None
    f.write_text(json.dumps({"xing": {"username": "u@example.com", "password": "pw"}}))
    assert pl.load_credentials(f, "xing") == {"username": "u@example.com", "password": "pw"}
    assert pl.load_credentials(f, "stepstone") is None
    f.write_text(json.dumps({"xing": {"username": "u@example.com"}}))
    assert pl.load_credentials(f, "xing") is None


def test_credentials_path_default_and_override():
    from bot import client
    c = client.active()
    assert pl.credentials_path(c).name == f".{c.slug}-portal-credentials.json"
    c2 = client.Client(slug=c.slug, root=c.root, profile=c.profile, cv=c.cv, tracks=c.tracks,
                       targeting=c.targeting,
                       accounts={**c.accounts, "portal_credentials": "/root/.x.json"})
    assert str(pl.credentials_path(c2)) == "/root/.x.json"


def test_login_host_detection():
    assert pl.on_login_host("https://login.xing.com/?locale=de&dest_url=x")
    assert pl.on_login_host("https://www.stepstone.de/login")
    assert not pl.on_login_host("https://www.xing.com/jobs/find")


def test_ensure_login_is_cached_per_context(monkeypatch):
    """Десять XING-подач в одному процесі = одна перевірка сесії."""
    calls = []

    class Ctx:  # мінімальний об'єкт із __dict__, як Playwright-контекст
        pass

    monkeypatch.setattr(pl, "is_live", lambda ctx, spec: calls.append("probe") or True)
    ctx = Ctx()
    assert pl.ensure_login(ctx, "xing") == ("live", "сесія жива")
    assert pl.ensure_login(ctx, "xing") == ("live", "сесія жива")
    assert calls == ["probe"]
    assert pl.ensure_login(ctx, "xing", force=True)[0] == "live"
    assert calls == ["probe", "probe"]


def test_portals_have_probe_and_google_oauth():
    for name in ("xing", "stepstone"):
        spec = pl.PORTALS[name]
        assert spec["probe_url"].startswith("https://")
        assert spec["google_oauth_url"].startswith("https://")


def test_session_cookie_optional():
    class Ctx:
        def cookies(self):
            return []
    assert pl.has_session_cookie(Ctx(), {"session_cookie": None})
    assert not pl.has_session_cookie(Ctx(), {"session_cookie": ("login", "xing.com")})
