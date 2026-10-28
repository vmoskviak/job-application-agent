#!/usr/bin/env python3
"""Готує запуск агента для активного клієнта.

    render_run.py evening|morning|onboarding [--out-dir DIR] [--headless]

Пише в `<client>/var/run/`:
    prompt_<mode>.md   промпт із docs/agents/*.md.j2 (шаблон за ринком і режимом) + конфіг клієнта
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

# Шаблон промпта за (ринок, режим). Пари, якої нема, ринок ще не підтримує.
TEMPLATES = {
    ("de_employment", "evening"): "evening_discover_agent.md.j2",
    ("de_employment", "morning"): "morning_apply_agent.md.j2",
    ("de_employment", "onboarding"): "onboarding_agent.md.j2",
    ("contract", "evening"): "evening_contract_agent.md.j2",
}
MODES = sorted({mode for _, mode in TEMPLATES})
EXIT_UNSUPPORTED = 3  # run_agent.sh: ринок цей режим не має, пропустити тихо


def supports(market: str, mode: str) -> bool:
    return (market, mode) in TEMPLATES


def mcp_server_name(alias: str) -> str:
    return "gmail" if alias == "jobs" else f"gmail-{alias}"


def _quotable_rate(rate: dict | None) -> dict:
    """Ставка без нижніх меж (hourly_min тощо): walk-away число агентові не
    показується, щоб не потрапило ні в лист, ні в дайджест."""
    return {k: v for k, v in (rate or {}).items() if "min" not in k}


def build_context(c: _client.Client) -> dict:
    groups = c.search.get("query_groups", [])
    profile = dict(c.profile)
    if "rate" in profile:
        profile["rate"] = _quotable_rate(profile["rate"])
    return {
        "slug": c.slug,
        "full_name": c.full_name,
        "profile": profile,
        "client_root": str(c.root),
        "engine": str(ENGINE),
        "gmail": c.accounts.get("gmail", {}),
        "send_address": c.send_address if c.accounts.get("gmail") else "",
        "operator_email": os.environ.get("JOBSEARCH_OPERATOR_EMAIL", ""),
        "report_email": c.report_email,
        # evening — оператор отримує один лист після вечірнього рану; always — після кожного
        "operator_mail": os.environ.get("JOBSEARCH_OPERATOR_MAIL", "evening"),
        # operator | candidate: див. Client.report_mode
        "report_mode": c.report_mode,
        "search": c.search,
        "query_count": sum(len(g.get("queries", [])) for g in groups),
        "variants": c.cv.get("variants", []),
        "year": str(date.today().year),
        "market": c.market,
        "report_lang": c.report_lang,
        "rate": _quotable_rate(c.profile.get("rate")),
        "sources": dict(c.search.get("sources") or {}),
    }


def render_prompt(mode: str, c: _client.Client) -> str:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    env = Environment(
        loader=FileSystemLoader(str(ENGINE / "docs" / "agents")),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    if not supports(c.market, mode):
        raise _client.ClientError(f"ринок {c.market}: режим {mode} ще не підтримується")
    return env.get_template(TEMPLATES[(c.market, mode)]).render(**build_context(c))


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
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--headless", action="store_true",
                        help="Playwright MCP без вікна (сервер)")
    parser.add_argument("--check", action="store_true",
                        help=f"нічого не писати: вихід 0, якщо ринок клієнта має цей режим, "
                             f"інакше {EXIT_UNSUPPORTED}")
    args = parser.parse_args(argv)

    try:
        c = _client.active()
        if not supports(c.market, args.mode):
            raise _client.ClientError(f"ринок {c.market}: режим {args.mode} ще не підтримується")
    except _client.ClientError as e:
        print(e, file=sys.stderr)
        return EXIT_UNSUPPORTED if "ще не підтримується" in str(e) else 2
    if args.check:
        return 0
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
