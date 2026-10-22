"""Усі модулі bot/ мають компілюватись (07.10.2026: битий f-рядок у bot/apply/xing.py
дійшов до main, бо збірка тестів упала, а `pytest | tail` сховав код виходу)."""
import py_compile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULES = sorted(p for p in (ROOT / "bot").rglob("*.py") if "__pycache__" not in p.parts)


@pytest.mark.parametrize("path", MODULES, ids=lambda p: str(p.relative_to(ROOT)))
def test_module_compiles(path, tmp_path):
    py_compile.compile(str(path), cfile=str(tmp_path / "out.pyc"), doraise=True)
