#!/usr/bin/env python3
"""Готує запуск агента для активного клієнта.

    render_run.py evening|morning|onboarding [--out-dir DIR] [--headless]

Пише в `<client>/var/run/`:
    prompt_<mode>.md   промпт із docs/agents/<mode>_*.md.j2 + конфіг клієнта
    mcp.json           MCP-сервери під його пошту й браузерний профіль
    settings.json      хуки Claude Code (заборона небезпечних видалень пошти)

і друкує шлях до промпта. Оператор (кому йдуть звіти) — env JOBSEARCH_OPERATOR_EMAIL.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from bot import client as _client  # noqa: E402

TEMPLATES = {
    "evening": "evening_discover_agent.md.j2",
    "morning": "morning_apply_agent.md.j2",
    "onboarding": "onboarding_agent.md.j2",
}


def mcp_server_name(alias: str) -> str:
    return "gmail" if alias == "jobs" else f"gmail-{alias}"


def build_context(c: _client.Client) -> dict:
    groups = c.search.get("query_groups", [])
    return {
        "slug": c.slug,
        "full_name": c.full_name,
        "profile": c.profile,
        "client_root": str(c.root),
        "engine": str(ENGINE),
        "gmail": c.accounts.get("gmail", {}),
        "send_address": c.send_address if c.accounts.get("gmail") else "",
        "operator_email": os.environ.get("JOBSEARCH_OPERATOR_EMAIL", ""),
        "report_email": c.report_email,
        # evening — оператор отримує один лист після вечірнього рану; always — після кожного
        "operator_mail": os.environ.get("JOBSEARCH_OPERATOR_MAIL", "evening"),
        "search": c.search,
        "query_count": sum(len(g.get("queries", [])) for g in groups),
        "variants": c.cv.get("variants", []),
        "year": str(date.today().year),
    }


def render_prompt(mode: str, c: _client.Client) -> str:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    env = Environment(
        loader=FileSystemLoader(str(ENGINE / "docs" / "agents")),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    return env.get_template(TEMPLATES[mode]).render(**build_context(c))


def build_mcp(c: _client.Client, headless: bool) -> dict:
    home = Path.home()
    pw_args = ["-y", "@playwright/mcp@latest", "--user-data-dir",
               str(home / ".playwright-mcp" / c.playwright_profile)]
    if headless:
        pw_args.append("--headless")
    servers: dict = {"playwright": {"command": "npx", "args": pw_args}}
    for alias in c.gmail_aliases:
        servers[mcp_server_name(alias)] = {
            "command": "npx",
            "args": ["-y", "@gongrzhe/server-gmail-autoauth-mcp"],
            "env": {
                "GMAIL_CREDENTIALS_PATH": str(c.credentials_path(alias, home)),
                "GMAIL_OAUTH_PATH": str(c.oauth_keys_path(alias, home)),
            },
        }
    # Ключі беруться з оточення запуску (на сервері — /root/.claude-env)
    servers["perplexity"] = {
        "command": "npx", "args": ["-y", "@perplexity-ai/mcp-server"],
        "env": {"PERPLEXITY_API_KEY": "${PERPLEXITY_API_KEY}"},
    }
    servers["firecrawl"] = {
        "command": "npx", "args": ["-y", "firecrawl-mcp"],
        "env": {"FIRECRAWL_API_KEY": "${FIRECRAWL_API_KEY}"},
    }
    return {"mcpServers": servers}


def build_settings() -> dict:
    hook = ENGINE / "scripts" / "hooks" / "guard_gmail_delete.py"
    return {"hooks": {"PreToolUse": [{
        "matcher": "mcp__gmail.*delete.*",
        "hooks": [{"type": "command", "command": f'python3 "{hook}"', "timeout": 10}],
    }]}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=sorted(TEMPLATES))
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--headless", action="store_true",
                        help="Playwright MCP без вікна (сервер)")
    args = parser.parse_args(argv)

    c = _client.active()
    out = args.out_dir or c.run_dir
    out.mkdir(parents=True, exist_ok=True)

    prompt = out / f"prompt_{args.mode}.md"
    prompt.write_text(render_prompt(args.mode, c), encoding="utf-8")
    (out / "mcp.json").write_text(
        json.dumps(build_mcp(c, args.headless), ensure_ascii=False, indent=2),
        encoding="utf-8")
    (out / "settings.json").write_text(
        json.dumps(build_settings(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(prompt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
