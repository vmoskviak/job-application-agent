#!/usr/bin/env python3
"""Керована браузер-сесія (узагальнений REPL) — один довгоживучий браузер.

Те саме, що scripts/interamt_repl.py, але без прив'язки до Interamt:
стартовий URL і файли команд задаються аргументами.

  python3 web_repl.py <start_url> <tag>
  /tmp/<tag>_cmd.json  {"seq": 1, "actions": [...]}
  /tmp/<tag>_out.json  результат останньої партії
  /tmp/<tag>_quit      файл-сигнал → чистий вихід

ops: goto|dump|click|fill|press|shot|wait|eval|state|scroll
"""
from __future__ import annotations
import json, sys, pathlib, time, traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from bot.scrapers.browser import profile_lock
from bot.scrapers.profile import PROFILE_DIR
from playwright.sync_api import sync_playwright

START = sys.argv[1] if len(sys.argv) > 1 else "https://www.linkedin.com/jobs/"
TAG = sys.argv[2] if len(sys.argv) > 2 else "web"
CMD = pathlib.Path(f"/tmp/{TAG}_cmd.json")
OUTF = pathlib.Path(f"/tmp/{TAG}_out.json")
QUIT = pathlib.Path(f"/tmp/{TAG}_quit")
SHOTS = pathlib.Path("/tmp/alerts_shots")
SHOTS.mkdir(exist_ok=True)
MAX_MIN = 90

JS_STATE = """
() => {
  const b = document.body.innerText || '';
  return {
    url: location.href.slice(0,140),
    title: document.title.slice(0,90),
    anon: /Sign in|Anmelden|Join now|Jetzt Mitglied/i.test(b) && !/Abmelden|Sign out/i.test(b),
    logged: !!document.querySelector('img.global-nav__me-photo, .global-nav__me, [data-test-global-nav-me]'),
  };
}
"""

JS_DUMP = """
() => ({
  url: location.href.slice(0,140),
  title: document.title.slice(0,90),
  text: (document.body.innerText||'').replace(/\\n{2,}/g,'\\n').slice(0,3500),
  links: [...document.querySelectorAll('a')].filter(e=>!!e.offsetParent)
    .map(e=>`${(e.innerText||'').trim().slice(0,45)} -> ${(e.getAttribute('href')||'').slice(0,80)}`)
    .filter(s=>s.length>4).slice(0,70),
  buttons: [...document.querySelectorAll('button,input[type=submit]')].filter(e=>!!e.offsetParent)
    .map(e=>`${(e.textContent||e.value||'').trim().slice(0,45)}|aria=${(e.getAttribute('aria-label')||'').slice(0,45)}|id=${(e.id||'').slice(0,30)}`)
    .filter(s=>s.length>10).slice(0,60),
})
"""


def safe_shot(page, path, full: bool = False, timeout: int = 8000):
    """Скрін, який не кидає. Повертає None або текст помилки.

    На LinkedIn Page.screenshot зависає на "waiting for fonts to load" і з
    дефолтним таймаутом 30 с валить сесію разом із локом профілю.
    """
    try:
        page.screenshot(path=str(path), full_page=full, timeout=timeout,
                        animations="disabled")
        return None
    except Exception as e:  # noqa: BLE001
        return f"screenshot failed: {type(e).__name__}: {str(e)[:80]}"


def run_action(page, a: dict) -> dict:
    op = a.get("op")
    if op == "goto":
        page.goto(a["url"], wait_until="domcontentloaded", timeout=a.get("timeout", 45000))
        page.wait_for_timeout(a.get("settle", 4000))
        return {"op": op, "url": page.url[:140]}
    if op == "wait":
        page.wait_for_timeout(a.get("ms", 2000))
        return {"op": op}
    if op == "scroll":
        page.mouse.wheel(0, a.get("dy", 900))
        page.wait_for_timeout(a.get("settle", 1500))
        return {"op": op}
    if op == "state":
        return {"op": op, **page.evaluate(JS_STATE)}
    if op == "dump":
        return {"op": op, **page.evaluate(JS_DUMP)}
    if op == "eval":
        return {"op": op, "result": page.evaluate(a["js"])}
    if op == "shot":
        p = SHOTS / f"{a.get('name','shot')}.png"
        # LinkedIn вішає screenshot на "waiting for fonts to load" → скрін не критичний,
        # ніколи не валимо через нього всю сесію (гочі перевірено 25.07).
        err = safe_shot(page, p, full=a.get("full", False), timeout=a.get("timeout", 8000))
        return {"op": op, "file": str(p), **({"warn": err} if err else {})}
    if op == "click":
        if a.get("sel"):
            loc = page.locator(a["sel"])
        else:
            loc = page.get_by_text(a["text"], exact=a.get("exact", True))
        n = loc.count()
        if not n:
            return {"op": op, "error": "not found", "target": a.get("sel") or a.get("text")}
        loc.nth(a.get("nth", 0)).click(timeout=a.get("timeout", 10000),
                                       force=a.get("force", False))
        page.wait_for_timeout(a.get("settle", 3000))
        return {"op": op, "clicked": a.get("sel") or a.get("text"), "matches": n,
                "url": page.url[:140]}
    if op == "fill":
        loc = page.locator(a["sel"]).first
        loc.click(timeout=10000, force=a.get("force", True))
        loc.fill("")
        if a.get("type"):
            loc.type(a["value"], delay=a.get("delay", 80))
        else:
            loc.fill(a["value"])
        page.wait_for_timeout(a.get("settle", 1200))
        return {"op": op, "sel": a["sel"], "now": loc.input_value()[:60]}
    if op == "press":
        page.keyboard.press(a["key"])
        page.wait_for_timeout(a.get("settle", 3000))
        return {"op": op, "key": a["key"], "url": page.url[:140]}
    return {"op": op, "error": "unknown op"}


def main():
    for f in (CMD, OUTF, QUIT):
        if f.exists():
            f.unlink()
    with profile_lock(timeout_sec=180):
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR), headless=False,
                viewport={"width": 1380, "height": 1000}, locale="de-DE",
                args=["--disable-blink-features=AutomationControlled",
                      "--window-position=0,0", "--window-size=1400,1050"],
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(START, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)
            for label in ("Accept", "Akzeptieren", "Alle akzeptieren", "Accept cookies"):
                try:
                    b = page.get_by_role("button", name=label, exact=False)
                    if b.count() and b.first.is_visible():
                        b.first.click(timeout=3000)
                        page.wait_for_timeout(1500)
                        break
                except Exception:
                    pass
            st = page.evaluate(JS_STATE)
            print(f"стартовий стан: {json.dumps(st, ensure_ascii=False)}", flush=True)
            safe_shot(page, SHOTS / f"{TAG}_start.png")
            print(f"REPL готовий. {CMD} / {OUTF} / {QUIT}", flush=True)

            last_seq = None
            deadline = time.time() + MAX_MIN * 60
            while time.time() < deadline:
                if QUIT.exists():
                    print(">>> quit-сигнал", flush=True)
                    break
                try:
                    if CMD.exists():
                        cmd = json.loads(CMD.read_text())
                        if cmd.get("seq") != last_seq:
                            last_seq = cmd.get("seq")
                            print(f"\n--- партія seq={last_seq}", flush=True)
                            results = []
                            for a in cmd.get("actions", []):
                                try:
                                    r = run_action(page, a)
                                except Exception as e:
                                    r = {"op": a.get("op"), "error": f"{type(e).__name__}: {str(e)[:220]}"}
                                results.append(r)
                                print("   ", json.dumps(r, ensure_ascii=False)[:500], flush=True)
                            OUTF.write_text(json.dumps(
                                {"seq": last_seq, "results": results}, ensure_ascii=False, indent=1))
                            print(f"--- готово seq={last_seq}", flush=True)
                except Exception:
                    print("cmd-loop err:\n" + traceback.format_exc()[:600], flush=True)
                time.sleep(2)
            ctx.close()
            print("браузер закрито", flush=True)


if __name__ == "__main__":
    main()
