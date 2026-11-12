"""scripts/gmail_rest.py access_token: a 5xx from Google's token endpoint is retried, a 4xx is not.

29.10.2026: one 500 from oauth2.googleapis.com made the inbox watcher send an
«OAuth oder Gmail prüfen» alert although both tokens were fine.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE / "scripts"))

import gmail_rest as g


@pytest.fixture
def token_files(monkeypatch, tmp_path):
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps({"installed": {"client_id": "id", "client_secret": "s"}}), encoding="utf-8")
    creds = tmp_path / "creds.json"
    creds.write_text(json.dumps({"refresh_token": "r"}), encoding="utf-8")
    monkeypatch.setitem(g.OAUTH_KEYS, "acc", keys)
    monkeypatch.setitem(g.CREDS, "acc", creds)
    monkeypatch.setattr(g.time, "sleep", lambda s: None)
    monkeypatch.setattr(g, "_TOKENS", {})


def _responses(monkeypatch, codes):
    calls = []
    req = httpx.Request("POST", "https://oauth2.googleapis.com/token")

    def post(*a, **k):
        code = codes[len(calls)]
        calls.append(code)
        body = {"access_token": "tok"} if code == 200 else {"error": "x"}
        return httpx.Response(code, json=body, request=req)

    monkeypatch.setattr(g.httpx, "post", post)
    return calls


def test_retries_server_error_then_succeeds(monkeypatch, token_files):
    calls = _responses(monkeypatch, [500, 503, 200])
    assert g.access_token("acc") == "tok"
    assert calls == [500, 503, 200]


def test_gives_up_after_three_server_errors(monkeypatch, token_files):
    calls = _responses(monkeypatch, [500, 500, 500])
    with pytest.raises(httpx.HTTPStatusError):
        g.access_token("acc")
    assert len(calls) == 3


def test_client_error_is_not_retried(monkeypatch, token_files):
    calls = _responses(monkeypatch, [400, 200])
    with pytest.raises(httpx.HTTPStatusError):
        g.access_token("acc")
    assert calls == [400]


def test_token_is_cached_for_the_pass_and_refreshed_near_expiry(monkeypatch, token_files):
    """04.11.2026: every Gmail call refreshed the token, one more request per call to time out."""
    calls = _responses(monkeypatch, [200, 200])
    assert g.access_token("acc") == "tok" and g.access_token("acc") == "tok"
    assert calls == [200]
    now = g.time.time()
    monkeypatch.setattr(g.time, "time", lambda: now + 3600 - g.TOKEN_MARGIN_SECONDS + 1)
    assert g.access_token("acc") == "tok"
    assert calls == [200, 200]


def _api_responses(monkeypatch, outcomes):
    """outcomes: status codes, or "timeout" for a read timeout; one per attempt."""
    calls = []
    req = httpx.Request("GET", g.API_URL + "x")

    def request(method, url, **k):
        out = outcomes[len(calls)]
        calls.append(method)
        if out == "timeout":
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(out, json={"ok": out}, request=req)

    monkeypatch.setattr(g.httpx, "request", request)
    monkeypatch.setattr(g, "access_token", lambda account: "tok")
    return calls


def test_get_is_retried_on_timeout_and_5xx(monkeypatch, token_files):
    calls = _api_responses(monkeypatch, ["timeout", 503, 200])
    assert g.api("acc", "GET", "x") == {"ok": 200}
    assert calls == ["GET", "GET", "GET"]


def test_get_gives_up_after_three_attempts(monkeypatch, token_files):
    _api_responses(monkeypatch, ["timeout", "timeout", "timeout"])
    with pytest.raises(httpx.ReadTimeout):
        g.request("acc", "GET", "x")
    _api_responses(monkeypatch, [503, 503, 503])
    assert g.request("acc", "GET", "x").status_code == 503


def test_post_is_never_retried(monkeypatch, token_files):
    calls = _api_responses(monkeypatch, ["timeout", 200])
    with pytest.raises(httpx.ReadTimeout):
        g.request("acc", "POST", "messages/send")
    assert calls == ["POST"]
    calls = _api_responses(monkeypatch, [502, 200])
    assert g.request("acc", "POST", "messages/send").status_code == 502
    assert calls == ["POST"]
