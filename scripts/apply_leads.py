#!/usr/bin/env python3
"""Прохід браузерного воркера контрактного клієнта (таймер jobsearch-apply@<slug>).

    DISPLAY=:99 js scripts/apply_leads.py [--dry-run]

Подає queued ліди з var/state/queue.jsonl у межах денних лімітів у видимому
браузері на дисплеї сервера, щоб оператор міг доробити hand-off по VNC у тій
самій вкладці. Канали: form (ATS-форми без логіну), linkedin_easy і
linkedin_offsite (Easy Apply, зовнішня форма), platform_login (freelancermap,
GULP); вимикачі платформ у targeting.yaml і var/state/platforms.json. Подача:
рядок у трекері, квитанція в Telegram; hand-off: картка з VNC-підказкою
(JOBSEARCH_VNC_HINT) і кнопками «Подано» / «Пропустити», прохід чекає до
JOBSEARCH_HANDOFF_UNTIL (за замовчуванням 19:55). Без DISPLAY чи з профілем браузера, який тримає
інший процес, прохід нічого не чіпає і виходить з 0. Тіньовий тиждень:
--dry-run або JOBSEARCH_APPLY_DRY_RUN=1 в оточенні машини, тоді форма
заповнюється без Submit, результат і скріншот лягають у var/state/queue_dry.jsonl
і working/ пакета, трекер і Telegram не чіпаються. Перед проходом дані
підтягуються (ff-only), після живої подачі трекер комітиться й пушиться
(помічники scripts/inbox_watch.py).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import signal
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(ENGINE / "scripts"))

from inbox_watch import pull_data, push_tracker, queue_alert

from bot import client as _client
from bot.apply.ats_answers import AnswerContext
from bot.apply.models import ApplyResult
from bot.apply_leads import CHANNELS, ApplyConfig, run_apply
from bot.llm import machine_lock_path
from bot.packages import Package
from bot.platforms import STATE_FILE, platform_of
from bot.sendqueue import QUEUE_FILE, due_any, limits_from
from bot.state import JsonlStore, state_dir
from bot.telegram.api import HttpTransport, Transport

DRY_RUN_ENV = "JOBSEARCH_APPLY_DRY_RUN"
HANDOFF_ENV = "JOBSEARCH_HANDOFF_UNTIL"
VNC_ENV = "JOBSEARCH_VNC_HINT"
HANDOFF_DEFAULT = "19:55"
CARDS_FILE = "cards.jsonl"
PROFILE_WAIT_SEC = 5
LLM_TIMEOUT = 240


def _now():
    return datetime.now(tz=timezone.utc).astimezone()


def _display_available() -> bool:
    """Той самий тест, що в scripts/portal_login.py: DISPLAY задано і сокет X-сервера є."""
    d = os.environ.get("DISPLAY", "")
    return d.startswith(":") and Path(f"/tmp/.X11-unix/X{d[1:].split('.')[0]}").exists()


def _handoff_until() -> str:
    raw = str(os.environ.get(HANDOFF_ENV, "")).strip()
    if not raw:
        return HANDOFF_DEFAULT
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return raw
    print(f"[apply] {HANDOFF_ENV}={raw!r} не HH:MM, беру {HANDOFF_DEFAULT}", file=sys.stderr)
    return HANDOFF_DEFAULT


def build_config(c: _client.Client, args: argparse.Namespace) -> ApplyConfig:
    dry = bool(args.dry_run) or str(os.environ.get(DRY_RUN_ENV, "")).strip().lower() in ("1", "true", "yes")
    policy = str(c.targeting.get("send_policy") or "").strip().lower()
    if policy != "auto":   # усе, крім явного auto, означає тіньовий режим (спека §6: send_policy)
        print(f"[apply] targeting.send_policy={policy or 'відсутній'}: лише тіньовий режим, нічого не подаю",
              file=sys.stderr)
        dry = True
    # limits_from: дефолти спеки §6 поверх daily_limits, інакше клієнт без daily_limits має form 0 і мовчки не подає
    return ApplyConfig(limits=limits_from(c.targeting), lang=c.report_lang,
                       chat_id=c.telegram["chat_id"], dry_run=dry,
                       vnc_hint=str(os.environ.get(VNC_ENV, "")).strip(), handoff_until=_handoff_until(),
                       lock_path=machine_lock_path(),
                       source_label="contract" if c.market == "contract" else "portal",
                       followup_days=int(c.targeting.get("followup_days") or 7),
                       targeting=c.targeting, platforms_path=state_dir(c.var) / STATE_FILE,
                       market=c.market, handoff_buttons=c.telegram["mode"] == "cards")


def ctx_for(c: _client.Client, rec: dict, pkg: Package) -> AnswerContext:
    """Контекст відповідей форми: профіль клієнта, лід і нотатка з пакета, CV (файл на «CV…» без
    урахування регістру, інакше перше вкладення) і всі вкладення пакета. rec не використовується: run_apply передає сюди
    лід пакета. Пакет без вкладень: ValueError, run_apply кладе лід у held (attachment_none)."""
    if not pkg.attachments:
        raise ValueError("package has no attachments")
    names = [(p, Path(p).name.lower()) for p in pkg.attachments]
    # ринок найму: пакет із applications/<дата>/ має «<Ім'я>_<Прізвище>_CV.pdf» і «01_Anschreiben.pdf»
    cv = next((p for p, n in names if n.startswith("cv") or n.endswith("_cv.pdf")), pkg.attachments[0])
    letter = next((p for p, n in names if "anschreiben" in n or "cover" in n), None)
    return AnswerContext(profile=c.profile, lead=pkg.lead, note=pkg.note, resume=Path(cv),
                         today=_now().date(), lang=pkg.lang, attachments=[Path(p) for p in pkg.attachments],
                         market=c.market, cover_letter=Path(letter) if letter else None)


class PlaywrightDriver:
    """Driver для run_apply: видимий Chromium на дисплеї сервера, профіль клієнта.

    Профіль браузера (profile_lock) тримається до кінця процесу: hand-off чекає оператора
    у відкритій вкладці без лока машини, і інший процес не має відкрити той самий профіль.
    Модулі браузера імпортуються ліниво: bot.scrapers.profile читає активного клієнта на імпорті.
    """

    def __init__(self, c: _client.Client) -> None:
        self.c = c
        self.brief = c.brief
        self.shots_dir: Path | None = None   # папка пакета поточного ліда (ставить run_pass через ctx_for)
        self._held = contextlib.ExitStack()
        self._profile = False
        self._llm = None

    def hold_profile(self) -> None:
        """Лок профілю на весь процес; RuntimeError, якщо за PROFILE_WAIT_SEC його не віддали."""
        if self._profile:
            return
        from bot.scrapers.browser import profile_lock

        self._held.enter_context(profile_lock(timeout_sec=PROFILE_WAIT_SEC))
        self._profile = True

    def open(self, url: str):
        from bot.scrapers.browser import get_context

        self.hold_profile()
        return get_context(headed=True).new_page()

    def _runner(self):
        from bot.llm import ClaudeRunner

        if self._llm is None:
            # без лока: прохід уже тримає лок машини навколо driver.apply
            self._llm = ClaudeRunner(None, model=os.environ.get("CLAUDE_MODEL", "opus"), timeout=LLM_TIMEOUT)
        return self._llm

    def apply(self, page, url: str, ctx: AnswerContext, dry_run: bool, channel: str = "form") -> ApplyResult:
        """Адаптер за каналом: linkedin_easy → Easy Apply, platform_login → адаптер платформи за хостом,
        решта → ATS-форма. LLM потрібна лише формам (ATS і LinkedIn), платформи йдуть без неї."""
        shots = Path(self.shots_dir or self.c.run_dir)
        if channel == "linkedin_easy":
            from bot.apply import linkedin

            return linkedin.easy_apply(page, url, ctx, self._runner(), self.brief, shots, dry_run)
        if channel == "platform_login":
            platform = platform_of(channel, url)
            if platform == "freelancermap":
                from bot.apply import freelancermap

                return freelancermap.apply(page, url, ctx.note, ctx.attachments, shots, dry_run)
            if platform == "gulp":
                from bot.apply import gulp

                rate = ctx.profile.get("rate") or {}
                if rate.get("hourly_target") in (None, ""):
                    return ApplyResult("failed", "gulp", url, reason="profile.rate.hourly_target missing")
                try:   # 95, 95.5 чи "95" з YAML; поле GULP приймає ціле
                    hourly = str(round(float(rate["hourly_target"])))
                except (TypeError, ValueError):
                    return ApplyResult("failed", "gulp", url, reason="profile.rate.hourly_target not numeric")
                return gulp.apply(page, url, ctx.note, hourly, rate.get("currency_default") or "EUR", ctx.resume,
                                  shots, dry_run)
            return ApplyResult("failed", platform or "platform", url, reason="no adapter")
        from bot.apply import ats_form

        return ats_form.apply(page, url, ctx, self._runner(), self.brief, shots_dir=shots, dry_run=dry_run)

    def resolve_offsite(self, page, url: str) -> tuple[str, str | None]:
        """Зовнішня форма вакансії LinkedIn: (стан, URL форми або None)."""
        from bot.apply import linkedin

        return linkedin.resolve_offsite(page, url)

    def is_closed(self, page) -> bool:
        try:
            return bool(page.is_closed())
        except Exception:  # noqa: BLE001 — браузер уже впав: вкладки теж нема
            return True

    def probe_submitted(self, page) -> str:
        """Фраза підтвердження, коли кнопки Submit уже не видно; "" інакше або якщо вкладку закрили.
        На linkedin.com своя проба (діалог Easy Apply), деінде проба ATS-форми."""
        from bot.apply.ats_detect import confirmation_in
        from bot.apply.ats_form import probe

        try:
            if "linkedin.com" in (page.url or ""):
                from bot.apply import linkedin

                return linkedin.probe_submitted(page)
            p = probe(page)
        except Exception:  # noqa: BLE001 — оператор закрив вкладку чи сторінка навігує: чекаємо далі
            return ""
        return "" if p.submit_visible else confirmation_in(p.text)

    def close(self, page) -> None:
        with contextlib.suppress(Exception):
            page.close()

    def shutdown(self) -> None:
        """Браузер закривається до того, як профіль віддається іншим."""
        from bot.scrapers.browser import shutdown_browser

        try:
            with contextlib.suppress(Exception):
                shutdown_browser()
        finally:
            self._held.close()
            self._profile = False


def run_pass(c: _client.Client, cfg: ApplyConfig, transport: Transport | None) -> int:
    if not _display_available():
        print("[apply] без DISPLAY воркер не запускається")
        return 0
    pull_data(c.root, queue_alert)
    state = state_dir(c.var)
    driver = PlaywrightDriver(c)
    if due_any(JsonlStore(state / QUEUE_FILE), CHANNELS):
        # профіль лише коли є що подавати: порожній прохід не блокує relogin.py і session_health
        try:
            driver.hold_profile()
        except RuntimeError:
            # інший прохід чекає оператора у відкритій вкладці, або профіль відкрив relogin
            print("[apply] профіль зайнятий (hand-off триває)")
            return 0
    try:

        def ctx(lead: dict, pkg: Package) -> AnswerContext:
            driver.shots_dir = Path(pkg.dir)   # скріншоти поточного ліда в working/ його пакета
            return ctx_for(c, lead, pkg)

        res = run_apply(cfg, JsonlStore(state / QUEUE_FILE), JsonlStore(state / CARDS_FILE), c.tracker, state,
                        driver, ctx, transport, _now, time.sleep)
    finally:
        driver.shutdown()
    if res.after_deadline:   # те, що прохід устиг до запасу, нижче однаково пушиться
        print(f"[apply] за {cfg.deadline_margin_minutes} хв до {cfg.handoff_until} і пізніше нових живих подач "
              "нема: hand-off уже ніхто не дочекається, решта черги чекає наступного дня", file=sys.stderr)
    if res.error:
        queue_alert(f"Browser-Worker: {res.error}")
    if res.sent and not cfg.dry_run:
        push_tracker(c.root, f"{c.slug}: apply {_now().date().isoformat()}: {len(res.sent)} sent", queue_alert)
    print("APPLY OK: " + json.dumps(asdict(res), ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="тіньовий режим: заповнити без Submit, записати у queue_dry.jsonl")
    args = ap.parse_args()
    # systemctl stop шле SIGTERM: вихід через sys.exit, щоб finally у run_pass закрив браузер і віддав профіль
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    c = _client.active()
    cfg = build_config(c, args)
    tg = c.telegram
    token = os.environ.get(tg["token_env"])
    transport = HttpTransport(token) if token and tg["chat_id"] else None
    if transport is None:
        print(f"[apply] без {tg['token_env']} або telegram.chat_id картки лише в лог", file=sys.stderr)
        if not cfg.dry_run:
            # hand-off картка не дійде, а вкладка чекала б до дедлайну: живих подач без Telegram нема
            print(f"[apply] без {tg['token_env']} або telegram.chat_id лише тіньовий режим, нічого не подаю",
                  file=sys.stderr)
            cfg.dry_run = True
    if not cfg.vnc_hint:
        print(f"[apply] {VNC_ENV} не задано: hand-off картка без підказки, як зайти на дисплей", file=sys.stderr)
    return run_pass(c, cfg, transport)


if __name__ == "__main__":
    sys.exit(main())
