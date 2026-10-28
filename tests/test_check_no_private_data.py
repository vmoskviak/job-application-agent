"""scripts/check_no_private_data.py: структурна перевірка й режим --strict.

Скрипт лежить поза пакетом, тому імпортується за шляхом.
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_no_private_data.py"
_spec = importlib.util.spec_from_file_location("check_no_private_data", SCRIPT)
chk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chk)


def test_placeholder_and_platform_emails_pass():
    platforms = chk.platform_domains()
    for addr in ("jobs@test.de", "hr@firma.de", "a@b.de", "x@beispiel-agentur.de",
                 "info@kontora.example", "erika.jobs@gmail.com",
                 "jobalerts-noreply@linkedin.com", "r@mail.hays.de",
                 "no-reply@eu.greenhouse-mail.io"):
        assert chk.is_placeholder_email(addr, platforms), addr


def test_real_looking_email_is_flagged():
    platforms = chk.platform_domains()
    assert not chk.is_placeholder_email("bewerbung@irgendeine-ag.de", platforms)
    assert not chk.is_placeholder_email("info@kanzlei-xyz.com", platforms)


def test_phone_placeholders():
    assert chk.is_placeholder_phone("+49 151 00000000")
    assert chk.is_placeholder_phone("+49 30 1234567")
    assert chk.is_placeholder_phone("+49221000000")
    assert not chk.is_placeholder_phone("+49 89 123987-45")


def _files(tmp_path: Path, **named: str) -> list[Path]:
    paths = []
    for name, text in named.items():
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        paths.append(p)
    return paths


def test_structural_hits_reports_only_foreign_contacts(tmp_path, monkeypatch):
    monkeypatch.setattr(chk, "ENGINE", tmp_path)
    files = _files(
        tmp_path,
        clean_py="MAIL = 'jobs@test.de'\nPHONE = '+49 151 00000000'\n",
        leak_txt="Kontakt: personal@irgendeine-ag.de, Tel. +49 89 123987-45\n",
    )
    hits = chk.structural_hits(files)
    assert set(hits) == {"email personal@irgendeine-ag.de", "phone +49 89 123987-45"}
    assert hits["email personal@irgendeine-ag.de"] == ["leak_txt"]


@pytest.mark.parametrize("strict", [True, False])
def test_main_without_clients(tmp_path, monkeypatch, strict, capsys):
    """Без клієнтів: --strict робить структурну перевірку, без нього код 2."""
    monkeypatch.setattr(chk, "ENGINE", tmp_path)
    monkeypatch.setattr(chk._client, "list_clients", list)
    files = _files(tmp_path, LICENSE="Copyright Irgendeine Person <irgendeine@andere-ag.de>\n",
                   ok_py="X = 'hr@firma.de'\n")
    monkeypatch.setattr(chk, "repo_files", lambda: files)
    code = chk.main(["--strict"] if strict else [])
    assert code == (0 if strict else 2)


def test_main_strict_fails_on_hit_outside_license(tmp_path, monkeypatch):
    monkeypatch.setattr(chk, "ENGINE", tmp_path)
    monkeypatch.setattr(chk._client, "list_clients", list)
    files = _files(tmp_path, LICENSE="irgendeine@andere-ag.de\n",
                   leak_py="X = 'bewerbung@irgendeine-ag.de'\n")
    monkeypatch.setattr(chk, "repo_files", lambda: files)
    assert chk.main(["--strict"]) == 1
