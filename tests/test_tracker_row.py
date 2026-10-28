"""Новий рядок подачі в головній таблиці трекера (етап 2b, append_row)."""
from __future__ import annotations

from bot.generators.inbox import append_note, append_row

HEADER = ("# Application Tracker — Jordan Example\n\n"
          "| # | Date | Company | Position | Source | Salary range | Status | Last update | Next action | Notes |\n"
          "|---|---|---|---|---|---|---|---|---|---|\n")


def test_append_row_numbers_and_escapes(tmp_path):
    t = tmp_path / "tracker.md"
    t.write_text(HEADER, encoding="utf-8")
    n = append_row(t, date="2026-10-26", company="Contract Co", position="Senior Android | Kotlin",
                   source="LinkedIn", salary="", status="sent", next_action="wait 7 days", notes="Msg m1")
    assert n == 1
    assert append_row(t, date="2026-10-26", company="B", position="P", source="HN", salary="",
                      status="sent", next_action="", notes="") == 2
    text = t.read_text(encoding="utf-8")
    assert "| 1 | 2026-10-26 | Contract Co | Senior Android / Kotlin | LinkedIn |  | sent | 2026-10-26 | wait 7 days | Msg m1 |\n" in text
    assert "| 2 | 2026-10-26 | B | P | HN |  | sent | 2026-10-26 |  |  |\n" in text
    assert append_note(t, "Contract Co", "reply sent Msg m2") is True


def test_append_row_after_existing_rows_and_trailing_text(tmp_path):
    t = tmp_path / "tracker.md"
    t.write_text(HEADER + "| 7 | 2026-10-20 | Old Co | Role | HN |  | sent | 2026-10-20 | wait | x |\n\n## Notes\n\ntext\n",
                 encoding="utf-8")
    assert append_row(t, date="2026-10-26", company="New", position="P", source="S", salary="",
                      status="sent", next_action="", notes="") == 8
    lines = t.read_text(encoding="utf-8").splitlines()
    assert lines.index("| 8 | 2026-10-26 | New | P | S |  | sent | 2026-10-26 |  |  |") == lines.index("| 7 | 2026-10-20 | Old Co | Role | HN |  | sent | 2026-10-20 | wait | x |") + 1
    assert lines[-1] == "text"
