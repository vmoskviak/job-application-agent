"""scripts/gmail_rest.py send: без вкладення, якого нема на диску, лист не йде (рев'ю 2b, Critical 1)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE / "scripts"))

import gmail_rest as g


def test_send_exits_on_missing_attachment(monkeypatch, tmp_path):
    sent: list = []
    monkeypatch.setattr(g, "api", lambda *a, **k: sent.append(k) or {"id": "x"})
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps({"to": "a@example.com", "subject": "S", "body": "B",
                                 "attachments": [str(tmp_path / "missing.pdf")]}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["gmail_rest.py", "send", g.SEND_FROM, str(draft)])
    with pytest.raises(SystemExit) as e:
        g.main()
    assert "missing.pdf" in str(e.value) and sent == []
