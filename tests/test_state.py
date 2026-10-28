"""Сховища стану етапу 2a: JSONL-записи з id, множина бачених листів, прапорець паузи."""
from bot.state import JsonlStore, SeenSet, is_paused, set_paused, state_dir


def test_jsonl_store_roundtrip_and_update(tmp_path):
    s = JsonlStore(tmp_path / "cards.jsonl")
    s.append({"id": "re-1", "status": "open"})
    s.append({"id": "re-2", "status": "open"})
    assert s.update("re-1", status="sent", tg_message_id=7) is True
    assert s.get("re-1") == {"id": "re-1", "status": "sent", "tg_message_id": 7}
    assert [r["id"] for r in s.read()] == ["re-1", "re-2"]
    assert s.update("nope", status="x") is False


def test_jsonl_store_survives_broken_line(tmp_path):
    p = tmp_path / "q.jsonl"
    p.write_text('{"id": "a"}\nnot json\n{"id": "b"}\n', encoding="utf-8")
    assert [r["id"] for r in JsonlStore(p).read()] == ["a", "b"]


def test_jsonl_store_missing_file_reads_empty(tmp_path):
    assert JsonlStore(tmp_path / "none.jsonl").read() == []
    assert JsonlStore(tmp_path / "none.jsonl").get("x") is None


def test_seen_set_persists(tmp_path):
    p = tmp_path / "seen.json"
    s = SeenSet(p)
    s.add("m1")
    assert "m1" in SeenSet(p) and "m2" not in SeenSet(p)


def test_pause_flag(tmp_path):
    assert is_paused(tmp_path) is False
    set_paused(tmp_path, True)
    assert is_paused(tmp_path) is True
    set_paused(tmp_path, False)
    assert is_paused(tmp_path) is False


def test_state_dir_is_created(tmp_path):
    d = state_dir(tmp_path / "var")
    assert d == tmp_path / "var" / "state" and d.is_dir()


def test_update_is_atomic(tmp_path, monkeypatch):
    """Рев'ю 2b, Important 9: збій під час перезапису не лишає обрізаного файла."""
    import os

    from bot.state import JsonlStore
    s = JsonlStore(tmp_path / "q.jsonl")
    s.append({"id": "a", "status": "sent"})
    s.append({"id": "b", "status": "queued"})

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    try:
        s.update("b", status="sent")
    except OSError:
        pass
    assert [r["id"] for r in JsonlStore(tmp_path / "q.jsonl").read()] == ["a", "b"]
    assert JsonlStore(tmp_path / "q.jsonl").get("b")["status"] == "queued"
    assert [p.name for p in tmp_path.iterdir()] == ["q.jsonl"] or all(p.name == "q.jsonl" or not p.name.endswith(".jsonl") for p in tmp_path.iterdir())
    monkeypatch.undo()
    assert s.update("b", status="sent") and s.get("b")["status"] == "sent"
    assert [p.name for p in tmp_path.iterdir()] == ["q.jsonl"]
