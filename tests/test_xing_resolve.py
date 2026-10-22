"""resolve_xing_share: /m/-share-лінки з job-мейлів XING."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

from bot.scrapers import fetch as fetch_mod
from bot.scrapers.fetch import resolve_xing_share

CANON = "https://www.xing.com/jobs/duesseldorf-bueroleiter-100000209"
SHARE = "https://www.xing.com/m/AbCdEfGhIjKlMnOpQrStUv"


def _stub(final_url):
    def _get(url, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", final_url))
    return _get


def test_non_xing_url_untouched(monkeypatch):
    monkeypatch.setattr(fetch_mod.httpx, "get", _stub("https://example.com/x"))
    assert resolve_xing_share("https://www.stepstone.de/stelle/1") == "https://www.stepstone.de/stelle/1"


def test_share_link_resolved_to_canonical(monkeypatch):
    monkeypatch.setattr(fetch_mod.httpx, "get", _stub(CANON + "?ijt=jb_16"))
    assert resolve_xing_share(SHARE).startswith(CANON)


def test_login_bounce_recovered_from_dest_url(monkeypatch):
    """Під браузерним UA XING збиває на login.xing.com — канонічний URL
    треба дістати з dest_url, а не здаватись."""
    bounce = (
        "https://login.xing.com/continue?level=loggedout&dest_url="
        "https%3A%2F%2Fwww.xing.com%2Fjobs%2Fduesseldorf-bueroleiter-100000209"
    )
    monkeypatch.setattr(fetch_mod.httpx, "get", _stub(bounce))
    assert resolve_xing_share(SHARE) == CANON


def test_network_failure_falls_back_to_original(monkeypatch):
    def _boom(url, **kwargs):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(fetch_mod.httpx, "get", _boom)
    assert resolve_xing_share(SHARE) == SHARE
