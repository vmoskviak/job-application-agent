"""Тести ганяються на вигаданій кандидатці з examples/clients/erika.

Змінні мають стояти до першого імпорту `bot.*`: модулі читають конфіг
клієнта під час імпорту.
"""
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
os.environ["JOBSEARCH_CLIENTS_DIR"] = str(_ROOT / "examples" / "clients")
os.environ["JOBSEARCH_CLIENT"] = "erika"

import pytest


@pytest.fixture(autouse=True)
def _reset_active_client():
    """Тести, що перемикають клієнта (test_render_run: jordan + active.cache_clear),
    лишали його в lru_cache для всіх наступних тестів; перший лінивий імпорт
    candidate після них падав на KeyError 'street'. Кеш скидається після кожного тесту."""
    yield
    from bot import client as _client
    _client.active.cache_clear()
