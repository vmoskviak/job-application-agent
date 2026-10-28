"""Сторож німецьких текстів пакета: ae/oe/ue замість ä/ö/ü.

22.10.2026 вечірній батч одного клієнта видав усі шість Anschreiben і профілі
CV транслітом («fuer», «Duesseldorf», «Gespraech», «Geschaeftsfuehrung»), і
ранок їх зрендерив і поставив у чергу. Такий лист роботодавець читає як
написаний без німецької клавіатури, тож пакет краще не рендерити зовсім.

Два сигнали:
1. Слово з короткого списку, яке німецькою існує лише з умлаутом («fuer»,
   «koennen», «Gespraech», «…fuehr…»): одного вистачає.
2. Кілька слів із ae/oe/ue після приголосної в тексті, де нема жодного
   ä/ö/ü/ß. Справжній німецький лист без умлаутів не буває, а поодинокі
   «Coesfeld», «Soest», «aktuell», «Queue» у звичайному тексті цей поріг не
   беруть.
Слова з назви фірми, ролі, міста й імені кандидата не рахуються: їх дає джерело
(«Buero Beispiel GmbH» так і називається).
"""

from __future__ import annotations

import re
from typing import Iterable

# Основи, які німецькою пишуться лише з умлаутом
_STRONG_RE = re.compile(
    r"\b(?:fuer|ueber\w*|\w*koenn\w*|moecht\w*|waer\w*|wuerd\w*|haett\w*|\w*gruess\w*"
    r"|\w*gespraech\w*|\w*persoenlich\w*|koeln\w*|duesseldorf\w*|muenchen\w*"
    r"|\w*fuehr\w*|\w*stuetz\w*|\w*taetig\w*|\w*faehig\w*|\w*verfueg\w*"
    r"|\w*zuverlaessig\w*|\w*staendig\w*|\w*kuendig\w*|\w*schaeft\w*|\w*pruef\w*"
    r"|\w*buero\w*|\w*moeglich\w*|\w*groess\w*|\w*erklaer\w*|\w*traeg\w*"
    r"|\w*fuell\w*|\w*waehr\w*|\w*hoer\w*|\w*koerper\w*|\w*aender\w*|\w*loes\w*)\b",
    re.IGNORECASE,
)
# ae/oe/ue після приголосної (після голосної це «neue», «Bauer», після q — «Quelle»)
_WEAK_RE = re.compile(r"\b\w*?(?<![aeiouyäöüq])(?:ae|oe|ue)\w*\b", re.IGNORECASE)
# Слова, де сполучення справжнє: німецькі на -uell/-uenz, власні назви, англійські
_ALLOW_RE = re.compile(
    r"^(?:\w*uell\w*|\w*uenz\w*|zuerst|israel\w*|michael\w*|raphael\w*|\w*poesie\w*"
    r"|goethe\w*|boeing|phoenix|aerob\w*|\w*koeffizient\w*|duell\w*|manuel\w*"
    r"|\w*(?:value|values|queue|issue|issues|true|blue|due|venue|revenue|continue"
    r"|tuesday|fluent\w*|confluence|influence\w*|sequence\w*|statue|genuine|pursue"
    r"|argue|vogue|rogue|league|tongue|clue|glue|cue|sue|hue))$",
    re.IGNORECASE,
)
_UMLAUT_RE = re.compile(r"[äöüÄÖÜß]")
_WEAK_THRESHOLD = 3


def _tokens(texts: Iterable[str]) -> set[str]:
    return {w.lower() for t in texts for w in re.findall(r"\w+", t or "")}


def transliterations(text: str, known: Iterable[str] = ()) -> list[str]:
    """Слова-транслітерації в тексті (порожньо = текст у порядку).
    known — назва фірми, роль, місто, ім'я: їхні слова не рахуються."""
    skip = _tokens(known)
    strong = [m.group(0) for m in _STRONG_RE.finditer(text or "")
              if m.group(0).lower() not in skip]
    if strong:
        return list(dict.fromkeys(strong))
    if _UMLAUT_RE.search(text or ""):
        return []
    weak = [m.group(0) for m in _WEAK_RE.finditer(text or "")
            if m.group(0).lower() not in skip and not _ALLOW_RE.match(m.group(0))]
    weak = list(dict.fromkeys(weak))
    return weak if len(weak) >= _WEAK_THRESHOLD else []


def check_tailored(tailored, known: Iterable[str] = ()) -> list[str]:
    """Транслітерації в текстах пакета: Anschreiben, профіль і заголовок CV,
    Kernkompetenzen. Службові поля (variant, fit_reason) не перевіряються."""
    parts = [tailored.anschreiben, tailored.profile, tailored.cv_title]
    parts += [f"{a} {b}" for a, b in (tailored.kernkompetenzen or [])]
    return transliterations("\n".join(p for p in parts if p), known)
