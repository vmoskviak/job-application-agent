#!/usr/bin/env python3
"""PreToolUse-хук Claude Code: блокує небезпечні видалення через Gmail MCP.

stdin: JSON з tool_name виду mcp__<server>__<tool>. MCP-сервер `gmail` —
акаунт `jobs` клієнта, `gmail-<alias>` — решта (напр. `gmail-main`).

Правила:
- batch_delete_emails заборонено завжди: спершу read_email на кожен лист,
  потім delete_email по одному.
- delete на акаунті з `may_delete: false` (accounts.yaml) заборонено.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, ensure_ascii=False))
    sys.exit(0)


def protected_servers() -> dict[str, str]:
    """MCP-сервер → адреса для акаунтів, де видаляти не можна."""
    if not os.environ.get("JOBSEARCH_CLIENT"):
        return {}
    try:
        from bot import client
        accounts = client.active().accounts.get("gmail", {})
    except Exception:
        return {}
    out = {}
    for alias, acc in accounts.items():
        if not acc.get("may_delete", False):
            out["gmail" if alias == "jobs" else f"gmail-{alias}"] = acc.get("address", alias)
    return out


def main() -> None:
    try:
        tool = json.load(sys.stdin).get("tool_name", "")
    except Exception:
        sys.exit(0)
    parts = tool.split("__")
    if len(parts) < 3 or parts[0] != "mcp" or "delete" not in parts[-1]:
        sys.exit(0)
    server, action = parts[1], parts[-1]
    guarded = protected_servers()
    if server in guarded:
        deny(f"На {guarded[server]} ({server}) видалення ЗАБОРОНЕНЕ. "
             "Онови трекер і залиш лист в inbox.")
    if "batch_delete" in action:
        deny("batch_delete_emails заборонено: спочатку read_email на КОЖЕН лист, "
             "звіт, потім delete_email по одному (макс. 5 за раз).")
    sys.exit(0)


if __name__ == "__main__":
    main()
