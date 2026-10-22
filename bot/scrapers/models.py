from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Optional

from bot.generators.intake import JobData

LivenessStatus = Literal["live", "closed", "blocked", "unknown", "failed"]


@dataclass
class PageFetch:
    url: str
    text: str
    html: str
    method: Literal["httpx", "playwright", "none"]
    status_code: Optional[int] = None


@dataclass
class IntakeResult:
    job: Optional[JobData]
    liveness: LivenessStatus
    intake_method: Literal["httpx", "playwright", "none"]
    reason: str = ""
