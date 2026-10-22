from __future__ import annotations
import contextlib
import fcntl
import random
import time
from typing import Iterator

from bot.scrapers.profile import LOCK_FILE, PROFILE_DIR

_context = None
_playwright = None
_last_nav_at: float = 0.0
MIN_DELAY_SEC = 1.5


@contextlib.contextmanager
def profile_lock(timeout_sec: float = 30.0) -> Iterator[None]:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as fh:
        deadline = time.time() + timeout_sec
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    raise RuntimeError(
                        "Playwright profile busy (MCP session open?). "
                        "Close MCP browser or wait."
                    )
                time.sleep(1.0)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _rate_limit() -> None:
    global _last_nav_at
    elapsed = time.time() - _last_nav_at
    if elapsed < MIN_DELAY_SEC:
        time.sleep(MIN_DELAY_SEC - elapsed + random.uniform(0, 0.5))
    _last_nav_at = time.time()


def get_context():
    global _context, _playwright
    if _context is not None:
        return _context
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "Install playwright: pip install playwright && playwright install chromium"
        ) from e

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    _playwright = sync_playwright().start()
    _context = _playwright.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=True,
        viewport={"width": 1280, "height": 900},
        locale="de-DE",
    )
    return _context


# Нижче цієї межі body — ще навігаційна шапка SPA, а не вакансія: XING віддав
# рівно 400 символів меню й футера (27.07), і цикл зупинявся на них, бо шапка
# вже не росла. Стабільність без тексту — не стабільність.
MIN_BODY_CHARS = 1200


def settle_body(page, settle_ms: int, text: str) -> str:
    """Чекає (до settle_ms), поки body-текст перестане рости і набере обсяг.

    SPA (logged-in LinkedIn, XING) рендерять опис вакансії асинхронно, і зріз
    на domcontentloaded ловить саму навігацію.
    """
    deadline = time.time() + settle_ms / 1000
    while settle_ms and time.time() < deadline:
        page.wait_for_timeout(1000)
        grown = page.inner_text("body")
        if len(grown) == len(text) and len(grown) >= MIN_BODY_CHARS:
            return grown
        text = grown
    return text


def fetch_with_browser(
    url: str, timeout_ms: int = 25000, settle_ms: int = 0
) -> tuple[str, str]:
    """Returns (text, html)."""
    with profile_lock():
        _rate_limit()
        ctx = get_context()
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(800)
            text = settle_body(page, settle_ms, page.inner_text("body"))

            html = page.content()
            return text, html
        finally:
            page.close()


def shutdown_browser() -> None:
    global _context, _playwright
    if _context:
        _context.close()
        _context = None
    if _playwright:
        _playwright.stop()
        _playwright = None
