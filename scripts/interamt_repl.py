#!/usr/bin/env python3
"""Керована сесія Interamt: один довгоживучий браузер, команди через файл.

Причина: сесія Interamt = серверна, на non-persistent JSESSIONID. Кожен
перезапуск браузера ризикує її вбити, а логін робить людина. Тож тримаємо
ОДИН інстанс і ганяємо в нього команди, замість серії окремих скриптів.

Протокол:
  /tmp/ia_cmd.json  {"seq": 1, "actions": [{"op": "...", ...}, ...]}
  /tmp/ia_out.json  результат останньої партії
  /tmp/ia_quit      файл-сигнал → чистий вихід (ctx.close)

ops: goto|dump|click|fill|press|shot|wait|eval|state
"""
from __future__ import annotations
import json, sys, pathlib, time, traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from bot.scrapers.browser import profile_lock
from bot.scrapers.profile import PROFILE_DIR
from playwright.sync_api import sync_playwright

# Як дістатись до дисплея сервера: env JOBSEARCH_VNC_HINT (напр. vnc://host:5900)
VNC_HINT = __import__("os").environ.get("JOBSEARCH_VNC_HINT", "VNC сервера, див. docs/server_setup.md")

CMD = pathlib.Path("/tmp/ia_cmd.json")
OUTF = pathlib.Path("/tmp/ia_out.json")
QUIT = pathlib.Path("/tmp/ia_quit")
SHOTS = pathlib.Path("/tmp/alerts_shots")
SHOTS.mkdir(exist_ok=True)
MAX_MIN = 90

JS_STATE = """
() => {
  const b = document.body.innerText || '';
  return {
    url: location.href.slice(0,110),
    title: document.title,
    anon: b.includes('Registrieren') && b.includes('Login'),
    logged: /Abmelden|Mein Interamt|Meine Suchprofile|Meine Jobagenten|Mein Profil/i.test(b),
  };
}
"""

JS_DUMP = """
() => ({
  url: location.href.slice(0,140),
  title: document.title,
  text: (document.body.innerText||'').replace(/\\n{2,}/g,'\\n').slice(0,3000),
  links: [...document.querySelectorAll('a')].filter(e=>!!e.offsetParent)
    .map(e=>`${(e.innerText||'').trim().slice(0,45)} -> ${(e.getAttribute('href')||'').slice(0,70)}`)
    .filter(s=>s.length>4).slice(0,60),
  buttons: [...document.querySelectorAll('button,input[type=submit]')].filter(e=>!!e.offsetParent)
    .map(e=>`${(e.innerText||e.value||'').trim().slice(0,40)}|id=${(e.id||'').slice(0,35)}`)
    .filter(s=>s.length>2).slice(0,50),
  inputs: [...document.querySelectorAll('input:not([type=hidden]),select,textarea')]
    .filter(e=>!!e.offsetParent)
    .map(e=>`${e.tagName}:${e.type||''}|id=${e.id}|name=${e.name}|val=${(e.value||'').slice(0,35)}`)
    .slice(0,45),
})
"""


def run_action(page, a: dict) -> dict:
    op = a.get("op")
    if op == "goto":
        page.goto(a["url"], wait_until="domcontentloaded", timeout=a.get("timeout", 45000))
        page.wait_for_timeout(a.get("settle", 3500))
        return {"op": op, "url": page.url[:120]}
    if op == "wait":
        page.wait_for_timeout(a.get("ms", 2000))
        return {"op": op}
    if op == "state":
        return {"op": op, **page.evaluate(JS_STATE)}
    if op == "dump":
        return {"op": op, **page.evaluate(JS_DUMP)}
    if op == "eval":
        return {"op": op, "result": page.evaluate(a["js"])}
    if op == "shot":
        p = SHOTS / f"{a.get('name','shot')}.png"
        page.screenshot(path=str(p), full_page=a.get("full", False))
        return {"op": op, "file": str(p)}
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
                "url": page.url[:120]}
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
    if op == "upload":
        loc = page.locator(a["sel"]).first
        loc.set_input_files(a["path"])
        page.wait_for_timeout(a.get("settle", 2500))
        return {"op": op, "sel": a["sel"], "file": a["path"]}
    if op == "press":
        page.keyboard.press(a["key"])
        page.wait_for_timeout(a.get("settle", 3000))
        return {"op": op, "key": a["key"], "url": page.url[:120]}
    return {"op": op, "error": "unknown op"}


def main():
    for f in (CMD, OUTF, QUIT):
        if f.exists():
            f.unlink()
    with profile_lock(timeout_sec=150):
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR), headless=False,
                viewport={"width": 1380, "height": 1000}, locale="de-DE",
                args=["--disable-blink-features=AutomationControlled",
                      "--window-position=0,0", "--window-size=1400,1050"],
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.interamt.de/koop/app/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            for label in ("Cookies akzeptieren", "Alle akzeptieren", "Akzeptieren"):
                try:
                    b = page.get_by_role("button", name=label, exact=True)
                    if b.count() and b.first.is_visible():
                        b.first.click(timeout=3000)
                        page.wait_for_timeout(1500)
                        break
                except Exception:
                    pass

            st = page.evaluate(JS_STATE)
            print(f"стартовий стан: logged={st['logged']} anon={st['anon']}", flush=True)

            if not st["logged"]:
                print("!! сесія НЕ пережила перезапуск — відкриваю форму логіна", flush=True)
                for sel in ('a:has-text("Login")', 'text="Login"'):
                    try:
                        loc = page.locator(sel).first
                        if loc.count() and loc.is_visible():
                            loc.click(timeout=6000)
                            page.wait_for_timeout(2500)
                            break
                    except Exception:
                        pass
                page.screenshot(path=str(SHOTS / "repl_login_needed.png"))
                print("=" * 58, flush=True)
                print(f"ПОТРІБЕН ЛОГІН — {VNC_HINT}", flush=True)
                print("Форма відкрита. Введи username+password — я продовжу САМ.", flush=True)
                print("=" * 58, flush=True)
                for i in range(240):          # до 20 хв
                    time.sleep(5)
                    try:
                        st = page.evaluate(JS_STATE)
                        if st["logged"]:
                            print(f"✅ логін пройшов (через {5*(i+1)}s)", flush=True)
                            break
                    except Exception:
                        continue
            if st.get("logged"):
                print("✅ Interamt залогінений — приймаю команди", flush=True)
            else:
                print("⚠️  логіна нема, але команди все одно приймаю", flush=True)

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
                            print(f"\n--- партія seq={last_seq}: {len(cmd.get('actions',[]))} дій", flush=True)
                            results = []
                            for a in cmd.get("actions", []):
                                try:
                                    r = run_action(page, a)
                                except Exception as e:
                                    r = {"op": a.get("op"), "error": f"{type(e).__name__}: {str(e)[:200]}"}
                                results.append(r)
                                print("   ", json.dumps(r, ensure_ascii=False)[:600], flush=True)
                            OUTF.write_text(json.dumps(
                                {"seq": last_seq, "results": results}, ensure_ascii=False, indent=1))
                            print(f"--- готово seq={last_seq}", flush=True)
                except Exception:
                    print("cmd-loop err:\n" + traceback.format_exc()[:600], flush=True)
                time.sleep(2)
            else:
                print(">>> таймаут", flush=True)
            ctx.close()
            print("браузер закрито", flush=True)


if __name__ == "__main__":
    main()
