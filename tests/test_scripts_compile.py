"""Усі скрипти мають хоча б компілюватись.

Інцидент 07.10.2026: подвійна кома в імпорті scripts/employer_scan.py дійшла
до сервера, бо тести скрипти не імпортують (вони читають клієнта під час
імпорту). py_compile ловить це без імпорту.
"""
import py_compile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = sorted(
    p for d in (ROOT / "scripts", ROOT / "scripts" / "server", ROOT / "scripts" / "hooks")
    if d.is_dir() for p in d.glob("*.py")
) + [ROOT / "new_app.py"]


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: str(p.relative_to(ROOT)))
def test_script_compiles(path, tmp_path):
    py_compile.compile(str(path), cfile=str(tmp_path / "out.pyc"), doraise=True)
