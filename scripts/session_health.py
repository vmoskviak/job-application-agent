#!/usr/bin/env python3
"""Read-only перевірка живості логінів у Playwright-профілі.

    .venv/bin/python scripts/session_health.py            # людський вивід
    .venv/bin/python scripts/session_health.py --json     # {"stepstone":"live",...}
    .venv/bin/python scripts/session_health.py --keepalive # + «торкнутись» живих сесій

Використовує наявний персистентний профіль (кукі вже в ньому) — НІЧОГО не мутує
(окрім навігації, що освіжає sliding-TTL кукі — у цьому й суть --keepalive).

Крім логінів перевіряє ще два німі місця (додано 25.07):
  xing_parse    — реальний /m/-лінк зі свіжого XING-листа проганяється через
                  resolve_xing_share + XingAdapter. Обидва XING-баги (заглушка
                  з company="Unbekannt" і редірект під браузерним UA) мовчали
                  тиждень саме тому, що ніхто не дивився на РЕЗУЛЬТАТ парсингу.
  alerts_label  — чи Gmail-фільтр досі навішує лейбл «Job Alerts» на всіх
                  відправників зі scripts/alert_volume.py. 25.07 у criteria
                  стояли неіснуючі адреси, і метрика обсягу показувала
                  2 листи за тиждень замість 112.

Exit code:
  0 — усі перевірки live (skip: не рахується провалом)
  2 — хоч одна expired/broken або перевірка впала

Мета: протухання логіна більше не тихе. Morning-агент читає це у Step 0,
keep-alive cron тримає кукі свіжими, а коли все ж протухло — шле алерт оператору
замість того щоб мовчки скинути StepStone/XING у manual-список.

Indeed (додано 25.07): логін живе через Google One Tap у тому ж профілі; протухання
тихо вбиває 5 job-алертів (Agile/Office/Sprache/Tier3), тому він теж під наглядом.
Re-login: docs/11_platform_alerts_setup.md → секція Indeed.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.scrapers.browser import get_context, profile_lock, shutdown_browser
from scripts.import_storage_state import _CHECKS


def check_login(ctx, name: str, url: str, markers: list[str]) -> str:
    """live | expired. Надійний сигнал протухлої сесії — редірект на login-стіну
    (authwall/uas/login), а НЕ відсутність маркерів: LinkedIn-feed це SPA, який на
    settle ще не віддав `feed-identity`/`global-nav` у HTML → маркерна перевірка
    давала хибний 'expired' навіть на живій сесії (24.07)."""
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        page.wait_for_timeout(2000)
        final_url = page.url.lower()
        content = (page.content() + " " + final_url).lower()
        redirected = any(
            x in final_url
            for x in ("authwall", "/login", "/uas/", "signup", "signin", "anmelden",
                      # Indeed не вживає /login: розлогінений редірект — на цей хост
                      "secure.indeed.com/auth",
                      # XING (25.07): редірект на https://login.xing.com/?dest_url=…
                      # не містить ані "/login", ані маркерів login-форми, тому
                      # перевірка бачила «live» на мертвій сесії. Логін-хост як
                      # окремий сигнал — покриває і майбутні login.<платформа>.
                      "//login.", "://login.")
        )
        has_markers = any(m.lower() in content for m in markers)
        login_form = any(
            m in content for m in
            ("bitte melden sie sich an", "sign in to", "log in to", "jetzt einloggen")
        ) and not has_markers
        return "expired" if (redirected or login_form) else "live"
    except Exception as e:  # noqa: BLE001 — будь-яка помилка = не можемо підтвердити live
        return f"error:{type(e).__name__}"
    finally:
        page.close()


_XING_M_RE = re.compile(r"https://(?:www\.)?xing\.com/m/[A-Za-z0-9_-]+")


def _xing_links_from_mail(days: int = 14, max_mails: int = 3) -> list[str]:
    """/m/-лінки з останніх XING-job-листів. Свіжа пошта замість хардкоду URL:
    захардкоджена вакансія за місяць закриється і перевірка почне брехати."""
    sys.path.insert(0, str(Path(__file__).parent))
    import gmail_rest as g
    from alert_volume import ALERT_SENDERS, msg_ids

    senders = " OR ".join(ALERT_SENDERS["xing"])
    ids = msg_ids(f"newer_than:{days}d from:({senders})")[:max_mails]
    links: list[str] = []
    for mid in ids:
        msg = g.api("jobs", "GET", f"messages/{mid}", params={"format": "full"})
        chunks: list[str] = []

        def walk(part):
            data = (part.get("body") or {}).get("data")
            if data:
                chunks.append(base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace"))
            for child in part.get("parts") or []:
                walk(child)

        walk(msg["payload"])
        links += _XING_M_RE.findall("\n".join(chunks))
    # dict.fromkeys — унікальні зі збереженням порядку (перші лінки листа — вакансії)
    return list(dict.fromkeys(links))


def check_xing_parse(max_tries: int = 8) -> str:
    """live | broken:<чому> | skip:<чому>.

    Ловить обидва режими відмови: резолвінг /m/ → /jobs/ і сам парсинг JSON-LD.
    company == "Unbekannt" і location == "Deutschland" — це рівно ті значення,
    які захардкоджувала стара заглушка XingAdapter, тому вони = провал.
    """
    from bot.scrapers.adapters.xing import XingAdapter
    from bot.scrapers.fetch import fetch_httpx, resolve_xing_share

    try:
        links = _xing_links_from_mail()
    except Exception as e:  # noqa: BLE001 — пошта недоступна ≠ парсинг зламаний
        return f"skip:mail:{type(e).__name__}"
    if not links:
        return "skip:немає XING-листів за 14 днів"

    checked = 0
    for link in links[:max_tries]:
        canon = resolve_xing_share(link)
        if "/jobs/" not in canon:
            continue  # інтерстишл із листа, не вакансія
        checked += 1
        try:
            job = XingAdapter().extract(fetch_httpx(canon))
        except Exception as e:  # noqa: BLE001
            return f"broken:extract:{type(e).__name__}"
        if job.company == "Unbekannt":
            return f"broken:company=Unbekannt ({canon[:60]})"
        if job.location == "Deutschland":
            return f"broken:location=Deutschland ({canon[:60]})"
        return "live"
    return f"skip:жоден із {len(links)} /m/-лінків не резолвиться у /jobs/ (checked={checked})"


def check_alerts_label() -> str:
    """live | broken:<відправники, яких нема у Gmail-фільтрі>."""
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from alert_volume import check_filter
        ok, missing = check_filter()
    except Exception as e:  # noqa: BLE001
        return f"skip:{type(e).__name__}"
    return "live" if ok else "broken:у фільтрі нема " + ", ".join(missing[:4])


def run(keepalive: bool = False) -> dict[str, str]:
    result: dict[str, str] = {}
    with profile_lock():
        ctx = get_context()
        for name, url, markers in _CHECKS:
            if name in ("xing", "stepstone"):
                # XING-сесія сесійна, StepStone теж протухає між процесами:
                # перевірка = спроба автологіну в цьому ж контексті.
                from bot.scrapers.portal_login import ensure_login
                status_login, why = ensure_login(ctx, name)
                if status_login in ("live", "logged_in"):
                    result[name] = "live"
                    continue
                result[name] = f"expired:{status_login}:{why[:80]}"
                continue
            status = check_login(ctx, name, url, markers)
            result[name] = status
            if keepalive and status == "live":
                # друге торкання логіна освіжає sliding-TTL кукі
                p = ctx.new_page()
                try:
                    p.goto(url, wait_until="domcontentloaded", timeout=30000)
                    p.wait_for_timeout(1500)
                finally:
                    p.close()
    shutdown_browser()

    # Без браузера — тому після shutdown і поза профільним локом.
    result["xing_parse"] = check_xing_parse()
    result["alerts_label"] = check_alerts_label()
    return result


def main() -> int:
    as_json = "--json" in sys.argv
    keepalive = "--keepalive" in sys.argv
    result = run(keepalive=keepalive)

    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        icon = {"live": "✅", "expired": "❌"}
        for name in ("stepstone", "xing", "linkedin", "indeed",
                     "xing_parse", "alerts_label"):
            st = result.get(name, "?")
            mark = icon.get(st) or ("⏭️ " if st.startswith("skip") else "❌")
            print(f"  {mark} {name}: {st}")
        if keepalive:
            print("keep-alive: торкнувся живих сесій")

    # skip = не змогли перевірити (немає XING-листів, пошта не відповіла) —
    # це не провал, інакше сторож почне будити оператора через тишу в пошті.
    bad = [k for k, v in result.items() if v != "live" and not v.startswith("skip")]
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(main())
