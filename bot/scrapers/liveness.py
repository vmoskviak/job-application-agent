from __future__ import annotations
import re
from typing import Tuple

from bot.scrapers.models import LivenessStatus

CLOSED_PATTERNS = [
    r"bereits besetzt",
    r"nicht veröffentlicht",
    r"nicht veroeffentlicht",
    r"nicht mehr verfügbar",
    r"nicht mehr verfugbar",
    r"stelle wurde geschlossen",
    r"no longer accepting applications",
    r"(?:stellenausschreibung|stellenanzeige) ist abgelaufen",
    r"keine bewerbungen mehr an(?:genommen|nimmt)",
    r"position has been filled",
    r"stellenangebot.*geschlossen",
]

BLOCKED_PATTERNS = [
    r"sign in",
    r"join now",
    r"email or phone",
    r"cookie-einstellungen",
    r"bitte aktivieren sie javascript",
    r"captcha",
    r"access denied",
]

LOGIN_WALL_DOMAINS = ("linkedin.com", "xing.com", "login.xing.com")


def is_login_wall(text: str, url: str) -> bool:
    lower = text.lower()
    if any(d in url for d in LOGIN_WALL_DOMAINS):
        if "sign in" in lower or "join now" in lower or "log in" in lower:
            return True
        if len(text.strip()) < 400 and "password" in lower:
            return True
    return False


def check_liveness(text: str, url: str) -> Tuple[LivenessStatus, str]:
    if not text or not text.strip():
        return "unknown", "empty or too short"

    lower = text.lower()
    stripped_len = len(text.strip())

    for pat in CLOSED_PATTERNS:
        if re.search(pat, lower):
            return "closed", f"matched: {pat}"

    # Avoid bare "404" — job pages may list ref numbers (392…404…405 on muster-partner.example)
    if re.search(
        r"seite nicht gefunden|page not found|error\s*404|404\s*error|http\s*404|status\s*404",
        lower,
    ):
        return "closed", "404 pattern"

    if is_login_wall(text, url):
        return "blocked", "login wall"

    for pat in BLOCKED_PATTERNS:
        if re.search(pat, lower):
            # StepStone listing page without job body
            if "stepstone" in url and stripped_len < 500:
                return "blocked", f"cookie/listing: {pat}"
            if "stepstone" in url:
                continue
            return "blocked", f"matched: {pat}"

    if "stepstone" in url and "stellenangebote--" in url:
        if stripped_len < 300:
            return "blocked", "stepstone: insufficient job body"

    if stripped_len < 80:
        return "unknown", "empty or too short"

    return "live", "ok"
