"""scripts/employer_scan.py: елемент звіту несе тіло листа (потрібно inbox watcher-у для чернетки)."""
from __future__ import annotations

import base64
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE / "scripts"))


def test_item_carries_body(monkeypatch):
    import employer_scan as es  # читає клієнта (erika) під час імпорту, як у test_render_run

    payload = {"headers": [{"name": "From", "value": "a@example.com"}, {"name": "Subject", "value": "Re"},
                           {"name": "Date", "value": "Mon, 26 Oct 2026 10:00:00 +0000"}],
               "mimeType": "text/plain",
               "body": {"data": base64.urlsafe_b64encode(b"Thank you for applying to Example Co").decode()}}
    monkeypatch.setattr(es, "_get", lambda account, path, **p: {"payload": payload, "threadId": "t1"})
    item = es._item("jobs", "m1", ["Example Co"])
    assert item["body"] == "Thank you for applying to Example Co"
    assert item["classification"] == "confirmed" and item["thread_id"] == "t1" and item["company"] == "Example Co"


def test_item_body_is_capped(monkeypatch):
    import employer_scan as es

    payload = {"headers": [{"name": "From", "value": "a@example.com"}], "mimeType": "text/plain",
               "body": {"data": base64.urlsafe_b64encode(b"x" * 5000).decode()}}
    monkeypatch.setattr(es, "_get", lambda account, path, **p: {"payload": payload, "threadId": "t1"})
    assert len(es._item("jobs", "m1", [])["body"]) == 3000
