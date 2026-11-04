"""Result model for portal auto-apply."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Статуси: submitted | dry_run | skipped | closed | login_required |
#          captcha | form_unsupported | failed | handoff | restricted | quota
# restricted: акаунт платформи під обмеженням чи checkpoint (LinkedIn), подачі зупиняються.
# quota: вичерпано місячний ліміт заявок платформи.
# handoff: ATS-форму не довели до підтвердження, вкладка відкрита для оператора.
RESULT_FILENAME = "apply_result.json"


@dataclass
class ApplyResult:
    status: str
    portal: str                       # stepstone | xing | ashby | greenhouse | lever | workable | ats
    url: str
    screenshots: list[str] = field(default_factory=list)
    confirmation_text: str = ""
    reason: str = ""
    timestamp: str = ""
    # An external application (no quick apply on the portal): the employer's apply URL the module
    # found on the page, "" when none. scripts/enqueue_leads.py hands it to the form worker.
    employer_url: str = ""

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


# Reasons the portal modules give when the posting has no quick apply and sends the applicant to
# the employer's site (stepstone.py, xing.py): pre-flight external_ats, the «Hat es geklappt?»
# interstitial, StepStone's «Bewerbung fortführen» hand-off, XING's «Zur Arbeitgeber-Website».
_EXTERNAL_REASON = re.compile(r"external|сайт\w* роботодавця", re.IGNORECASE)


def external_application(result: ApplyResult | None) -> bool:
    """The runner met an external application: form_unsupported because the posting applies on the
    employer's site, not because the portal's own form failed half way (those stay manual)."""
    if result is None or result.status != "form_unsupported":
        return False
    return bool(result.employer_url) or bool(_EXTERNAL_REASON.search(result.reason or ""))


def already_submitted(app_dir: Path) -> bool:
    """Ідемпотентність: подана заявка ніколи не подається вдруге."""
    result = load_result(app_dir)
    return result is not None and result.status == "submitted"
