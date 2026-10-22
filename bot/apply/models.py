"""Result model for portal auto-apply."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

# Статуси: submitted | dry_run | skipped | closed | login_required |
#          captcha | form_unsupported | failed
RESULT_FILENAME = "apply_result.json"


@dataclass
class ApplyResult:
    status: str
    portal: str                       # stepstone | xing
    url: str
    screenshots: list[str] = field(default_factory=list)
    confirmation_text: str = ""
    reason: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def save(self, app_dir: Path) -> Path:
        path = app_dir / RESULT_FILENAME
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path


def load_result(app_dir: Path) -> ApplyResult | None:
    path = app_dir / RESULT_FILENAME
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return ApplyResult(**data)


def already_submitted(app_dir: Path) -> bool:
    """Ідемпотентність: подана заявка ніколи не подається вдруге."""
    result = load_result(app_dir)
    return result is not None and result.status == "submitted"
