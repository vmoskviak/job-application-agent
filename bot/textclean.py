"""Invisible characters in job texts.

Portals (Arbeitsagentur, Interamt) put soft hyphens U+00AD into long German words:
"Arbeit\u00adnehmer\u00adüber\u00adlassung" looks whole on screen, but a regex does not see it.
That is how a posting with Arbeitnehmerüberlassung slipped past the exclusions on 08.10.2026.
Zero-width spaces do the same, and Upwork alert mails pad their preheader with
U+034F (combining grapheme joiner) between soft hyphens.
"""

from __future__ import annotations

import re

_INVISIBLE_RE = re.compile("[\u00ad\u034f\u200b\u200c\u200d\u2060\ufeff]")


def strip_invisible(text: str | None) -> str:
    return _INVISIBLE_RE.sub("", text or "")
