"""Тести ганяються на вигаданій кандидатці з examples/clients/erika.

Змінні мають стояти до першого імпорту `bot.*`: модулі читають конфіг
клієнта під час імпорту.
"""
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
os.environ["JOBSEARCH_CLIENTS_DIR"] = str(_ROOT / "examples" / "clients")
os.environ["JOBSEARCH_CLIENT"] = "erika"
