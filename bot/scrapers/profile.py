from __future__ import annotations
import sys
from pathlib import Path

from bot import client as _client

# Окремий браузерний профіль на клієнта: у ньому живуть логіни порталів.
_name = _client.active().playwright_profile
PROFILE_DIR = Path.home() / ".playwright-mcp" / _name
LOCK_FILE = Path(f"/tmp/jobsearch-playwright-{_name}.lock")


def profile_exists() -> bool:
    return PROFILE_DIR.exists() and any(PROFILE_DIR.iterdir())


def check_platform_sessions() -> dict[str, str]:
    """Lightweight check — full check needs browser (Task 4)."""
    if not profile_exists():
        return {"profile": "missing"}
    return {"profile": "ok"}


def main() -> None:
    info = check_platform_sessions()
    if info.get("profile") == "missing":
        print("LOGIN REQUIRED: profile dir missing:", PROFILE_DIR)
        print("See docs/onboarding.md (браузерний профіль)")
        sys.exit(1)
    print("OK: profile dir exists:", PROFILE_DIR)
    sys.exit(0)


if __name__ == "__main__":
    main()
