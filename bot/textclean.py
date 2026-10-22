"""Невидимі символи в тексті вакансій.

Портали (Arbeitsagentur, Interamt) розставляють у довгих німецьких словах
м'які переноси U+00AD: «Arbeit­nehmer­über­lassung» на екрані виглядає цілим,
а regex його не бачить. Так 08.10.2026 повз виключення пройшла вакансія з
Arbeitnehmerüberlassung. Те саме роблять zero-width пробіли.
"""

from __future__ import annotations

import re

_INVISIBLE_RE = re.compile("[­​‌‍⁠﻿]")


def strip_invisible(text: str | None) -> str:
    return _INVISIBLE_RE.sub("", text or "")
