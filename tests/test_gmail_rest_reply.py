"""scripts/gmail_rest.py reply: адресат. Відповідь на чужий лист іде відправнику (Reply-To, далі From),
відповідь на НАШ власний лист (follow-up на подачу) іде його адресату, а не нам самим."""
from __future__ import annotations

import base64
import email
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE / "scripts"))

import gmail_rest as g


def _fake_api(sent: list, original_headers: dict):
    def api(account, method, path, **kw):
        if method == "GET":
            return {"threadId": "t1", "payload": {"headers": [{"name": k, "value": v} for k, v in original_headers.items()]}}
        raw = kw["json"]["raw"]
        sent.append(email.message_from_bytes(base64.urlsafe_b64decode(raw)))
        return {"id": "new1", "threadId": "t1"}
    return api


@pytest.mark.parametrize("headers,expected_to", [
    # вхідний лист роботодавця: відповідь відправнику
    ({"From": "HR <hr@firma.example>", "To": "erika.mustermann.jobs@example.com", "Subject": "Re: Bewerbung", "Message-ID": "<a@x>"},
     "HR <hr@firma.example>"),
    ({"From": "HR <hr@firma.example>", "Reply-To": "bewerbung@firma.example", "To": "erika.mustermann.jobs@example.com",
      "Subject": "Re: Bewerbung", "Message-ID": "<a@x>"}, "bewerbung@firma.example"),
    # наш власний надісланий лист (follow-up на подачу): відповідь його адресату
    ({"From": "Erika Mustermann <erika.mustermann.jobs@example.com>", "To": "bewerbung@firma.example",
      "Subject": "Bewerbung als Assistenz", "Message-ID": "<b@x>"}, "bewerbung@firma.example"),
])
def test_reply_recipient(monkeypatch, tmp_path, headers, expected_to):
    sent: list = []
    monkeypatch.setattr(g, "api", _fake_api(sent, headers))
    body = tmp_path / "body.txt"
    body.write_text("Hallo", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["gmail_rest.py", "reply", "jobs", "m1", str(body)])
    g.main()
    assert sent[0]["To"] == expected_to
    assert sent[0]["In-Reply-To"] == headers["Message-ID"]


def test_reply_explicit_recipient_wins(monkeypatch, tmp_path):
    sent: list = []
    headers = {"From": "Erika Mustermann <erika.mustermann.jobs@example.com>", "To": "bewerbung@firma.example",
               "Subject": "Bewerbung", "Message-ID": "<b@x>"}
    monkeypatch.setattr(g, "api", _fake_api(sent, headers))
    body = tmp_path / "body.txt"
    body.write_text("Hallo", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["gmail_rest.py", "reply", "jobs", "m1", str(body), "chef@firma.example"])
    g.main()
    assert sent[0]["To"] == "chef@firma.example"
