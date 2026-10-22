"""Рушій автоматизованого пошуку роботи.

Будь-яка точка входу (`python new_app.py`, `python -m bot.*`, scripts/*.py)
приймає `--client <slug>`: прапорець знімається з argv тут, до імпорту
модулів, що читають конфіг клієнта під час імпорту.
"""

from bot import client as _client

_client.bootstrap_argv()
