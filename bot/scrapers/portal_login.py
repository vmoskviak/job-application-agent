"""Автологін у портал усередині вже відкритого браузерного контексту.

XING (з 07.2026) видає кук `login` лише сесійним, навіть із «Eingeloggt
bleiben»: він гине з процесом браузера, тож кожен новий контекст бачить XING
розлогіненим. Логін окремим процесом нічого не дає; він має відбутись у тому
самому контексті, який далі подає заявки чи перевіряє сесії. Звідси
`ensure_login(ctx, "xing")`, яку кличуть bot.apply.xing і session_health.

Два способи, у цьому порядку:

1. Google-OAuth («Weiter mit Google»): акаунт порталу прив'язаний до
   jobs-Gmail, а Google-сесія в тому ж профілі жива (вона персистентна).
   Перехід на OAuth-URL порталу логінить без пароля. Якщо Google показує
   вибір акаунта, обирається адреса jobs-скриньки (client.send_address).
2. Форма з паролем: облікові дані ПОЗА git, лише на машині запуску,
   accounts.yaml → portal_credentials: /root/.<slug>-portal-credentials.json
   (дефолт ~/.<slug>-portal-credentials.json), chmod 600:
   {"xing": {"username": "name@example.com", "password": "..."}}
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from bot import client as _client

PORTALS: dict[str, dict] = {
    "xing": {
        "login_url": "https://login.xing.com/?locale=de",
        "probe_url": "https://www.xing.com/jobs/find",
        "user": "input[name='username']",
        "password": "input[name='password']",
        "remember": "input[name='perm']",
        "submit": "button[type='submit']",
        "session_cookie": ("login", "xing.com"),
        # «Weiter mit Google» на login.xing.com веде сюди; з живою Google-сесією
        # в профілі XING логінить одразу, без вибору акаунта (07.10.2026).
        "google_oauth_url": "https://xing.com/signup/oauth?caller=login_page",
    },
    "stepstone": {
        "login_url": "https://www.stepstone.de/de-DE/candidate/login",
        # Під анонімом редіректить на /candidate/login; головна сторінка показує
        # слово «Profil» і анонімам, тому за нею сесію не перевіряти (07.10.2026).
        "probe_url": "https://www.stepstone.de/candidate/notification-settings",
        "user": "input[name='email'], input[type='email']",
        "password": "input[name='password'], input[type='password']",
        "remember": "input[name='rememberMe'], input[type='checkbox']",
        "submit": "button[type='submit']",
        "session_cookie": None,
        # «Mit Google einloggen» на /candidate/login (href кнопки, 07.10.2026).
        "google_oauth_url": (
            "https://www.stepstone.de/rlac/login-registration-service/oauth/Google/register"
            "?returnUrl=%2Fde-DE%2Fcandidate%2Fgoogle%2Fsuccess%3FformType%3Dlogin"
            "%26googleAuthLoginSource%3DLoginPage%26checkboxState%3Dtrue"
            "%26googleAuthRedirection%3Dhttps%253A%252F%252Fwww.stepstone.de%252F"
            "&loginSource=LoginPage&lang=de"),
    },
}

# Один логін на контекст: повторні виклики з того самого ctx не ходять у мережу.
_DONE_ATTR = "_jobsearch_portal_login"


def credentials_path(c: _client.Client) -> Path:
    raw = c.accounts.get("portal_credentials")
    return Path(raw).expanduser() if raw else Path.home() / f".{c.slug}-portal-credentials.json"


def load_credentials(path: Path, portal: str) -> dict | None:
    """{"username", "password"} або None, якщо файла чи запису нема."""
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    entry = data.get(portal)
    if not entry or not entry.get("username") or not entry.get("password"):
        return None
    return {"username": str(entry["username"]), "password": str(entry["password"])}


def on_login_host(url: str) -> bool:
    host = url.split("//")[-1].split("/")[0]
    return host.startswith("login.") or "/login" in url


def has_session_cookie(ctx, spec: dict) -> bool:
    """True, якщо сесійний кук порталу є; портали без відомого кука (None) не перевіряються."""
    if not spec.get("session_cookie"):
        return True
    name, domain = spec["session_cookie"]
    return any(c["name"] == name and domain in c.get("domain", "") for c in ctx.cookies())


def is_live(ctx, spec: dict) -> bool:
    page = ctx.new_page()
    try:
        page.goto(spec["probe_url"], wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        return not on_login_host(page.url)
    finally:
        page.close()


def _dismiss_consent(page) -> None:
    """Найприватніший варіант банера згоди; якщо його нема, нічого не чіпаємо."""
    for label in ("Nur erforderliche", "Nur notwendige", "Ablehnen", "Alle ablehnen"):
        try:
            btn = page.get_by_role("button", name=label, exact=False).first
            if btn.is_visible(timeout=1500):
                btn.click()
                page.wait_for_timeout(800)
                return
        except Exception:  # noqa: BLE001
            continue


def login_google(ctx, spec: dict, google_email: str) -> tuple[bool, str]:
    url = spec.get("google_oauth_url")
    if not url:
        return False, "портал без Google-OAuth"
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)
        if "accounts.google.com" in page.url:
            # Вибір акаунта: розмітка в Google різна, тому два селектори. У
            # профілі з кількома Google-акаунтами перший збіг буває прихованим
            # (14.10.2026, профіль із трьома акаунтами в чузері) — шукаємо видимий.
            # Два окремі локатори: «css, text=…» через кому Playwright читає як
            # один CSS-селектор і не знаходить нічого.
            acc = None
            page.wait_for_timeout(2000)
            for candidates in (page.locator(f'[data-identifier="{google_email}"]'),
                               page.get_by_text(google_email, exact=False)):
                try:
                    for i in range(min(candidates.count(), 6)):
                        if candidates.nth(i).is_visible(timeout=1500):
                            acc = candidates.nth(i)
                            break
                except Exception:  # noqa: BLE001
                    acc = None
                if acc is not None:
                    break
            if acc is None:
                return False, f"Google просить увійти (сесії в профілі нема): {page.url[:70]}"
            acc.click()
            deadline = time.time() + 40
            while time.time() < deadline and "accounts.google.com" in page.url:
                page.wait_for_timeout(1500)
        # XING після Google ще крутить редиректи через login.xing.com
        # (auth_token, security-check); перевірка в цей момент бачила «сесії
        # нема», хоча за 8 с профіль уже на xing.com/jobs/find (14.10.2026).
        deadline = time.time() + 25
        while time.time() < deadline and on_login_host(page.url):
            page.wait_for_timeout(1500)
        if has_session_cookie(ctx, spec) and not on_login_host(page.url):
            return True, f"залогінено через Google, url={page.url[:70]}"
        return False, f"після Google-OAuth url={page.url[:70]}, сесії нема"
    finally:
        page.close()


def login_form(ctx, spec: dict, creds: dict) -> tuple[bool, str]:
    page = ctx.new_page()
    try:
        page.goto(spec["login_url"], wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
        _dismiss_consent(page)
        page.fill(spec["user"], creds["username"])
        page.fill(spec["password"], creds["password"])
        try:
            if not page.is_checked(spec["remember"]):
                page.check(spec["remember"])
        except Exception:  # noqa: BLE001 — чекбокс міг зникнути, логін усе одно пробуємо
            pass
        page.click(spec["submit"])
        deadline = time.time() + 40
        while time.time() < deadline:
            page.wait_for_timeout(1500)
            if not on_login_host(page.url):
                break
        if has_session_cookie(ctx, spec) and not on_login_host(page.url):
            return True, f"залогінено формою, url={page.url[:70]}"
        body = ""
        try:
            body = page.inner_text("body")[:200].replace("\n", " ")
        except Exception:  # noqa: BLE001
            pass
        return False, f"після сабміту url={page.url[:70]} · {body}"
    finally:
        page.close()


def ensure_login(ctx, portal: str, force: bool = False) -> tuple[str, str]:
    """(status, detail). status: live | logged_in | failed | no_credentials.

    Перевіряє сесію, за потреби логінить; результат запам'ятовує на контексті,
    щоб apply на десяти XING-вакансіях не ходив десять разів на login.
    """
    spec = PORTALS[portal]
    done: dict = getattr(ctx, _DONE_ATTR, None) or {}
    if not force and portal in done:
        return done[portal]

    if is_live(ctx, spec):
        result = ("live", "сесія жива")
    else:
        c = _client.active()
        attempts: list[str] = []
        ok, why = login_google(ctx, spec, c.send_address)
        if ok:
            result = ("logged_in", why)
        else:
            attempts.append(f"google: {why}")
            creds = load_credentials(credentials_path(c), portal)
            if creds:
                ok, why = login_form(ctx, spec, creds)
                attempts.append(f"форма: {why}")
                result = ("logged_in", why) if ok else ("failed", " · ".join(attempts))
            else:
                attempts.append(f"форма: немає {credentials_path(c)} або запису '{portal}'")
                result = ("no_credentials", " · ".join(attempts))

    done[portal] = result
    try:
        setattr(ctx, _DONE_ATTR, done)
    except Exception:  # noqa: BLE001 — контекст без __dict__: просто без кешу
        pass
    return result
