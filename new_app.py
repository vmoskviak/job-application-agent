#!/usr/bin/env python3
"""
new_app.py — автоматична генерація заявки з URL або тексту вакансії.

Режими:
  # Повний (з API ключем):
  python new_app.py https://example.com/junior-ba
  python new_app.py --text < job_description.txt
  python new_app.py --file jd.txt

  # Без API ключа (Claude Code робить tailoring):
  python new_app.py --intake-only https://example.com/junior-ba   → виводить JSON
  python new_app.py --intake-only --text < jd.txt                 → виводить JSON
  python new_app.py --render data.json                            → рендерить з готових даних
"""

import sys
import re
import json
import argparse
from datetime import date
from pathlib import Path
from typing import Optional
import os

PROJECT_ROOT = Path(__file__).parent

sys.path.insert(0, str(PROJECT_ROOT))

from bot import client as _client

CLIENT = _client.active()
APPLICATIONS_DIR = CLIENT.applications
TRACKER_PATH = CLIENT.tracker

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / "bot" / ".env")
except ImportError:
    pass

from bot.generators.intake import intake, JobData
from bot.generators.discover import main as discover_main
from bot.generators.batch import render_all as batch_render_all
from bot.generators.responses import (
    parse_tracker_sent, generate_report, build_gmail_search_query
)
from bot.generators.cv_engine import (
    CV_FILE_STEM, letter_salutation, render_application, zeugnis_paths,
)
from bot.apply.candidate import FULL_NAME, PHONE

try:
    from bot.generators.tailor import tailor, TailoredData
except ImportError:
    tailor = None  # type: ignore
    from dataclasses import dataclass
    from typing import List, Tuple

    @dataclass
    class TailoredData:  # type: ignore
        cv_title: str
        profile: str
        kernkompetenzen: List[Tuple[str, str]]
        experience_variant: str
        skills_order: str
        fit_score: int
        fit_reason: str
        salary: str
        anschreiben: str


# ── Serialization ─────────────────────────────────────────────────────────────

def job_to_dict(job: JobData) -> dict:
    return {
        "title": job.title, "company": job.company, "location": job.location,
        "description": job.description, "email": job.email,
        "application_url": job.application_url, "source_url": job.source_url,
        "is_portal": job.is_portal,
    }


def job_from_dict(d: dict) -> JobData:
    return JobData(
        title=d["title"], company=d["company"], location=d["location"],
        description=d["description"], email=d.get("email"),
        application_url=d.get("application_url"), source_url=d.get("source_url", ""),
        is_portal=d.get("is_portal", False),
    )


def tailored_from_dict(d: dict) -> TailoredData:
    kk = d.get("kernkompetenzen", [])
    kernkompetenzen = [(k[0], k[1]) if isinstance(k, list) else tuple(k) for k in kk]
    return TailoredData(
        cv_title=d["cv_title"], profile=d["profile"],
        kernkompetenzen=kernkompetenzen,
        experience_variant=d.get("experience_variant", "agile"),
        skills_order=d.get("skills_order", "agile"),
        fit_score=int(d.get("fit_score", 50)),
        fit_reason=d.get("fit_reason", ""),
        salary=d.get("salary", "48000"),
        anschreiben=d["anschreiben"],
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_folder_name(company: str, title: str) -> str:
    def clean(s: str) -> str:
        s = re.sub(r'\s*\(m/w/d\)\s*', '', s, flags=re.IGNORECASE)
        s = re.sub(r'[^a-zA-Z0-9äöüÄÖÜß]', '', s)
        return s[:25].strip()
    return f"{clean(company)}_{clean(title)}"


def write_data_only_generate_py(job: JobData, tailored: TailoredData, app_dir: Path) -> None:
    """Writes a minimal data-only generate.py that imports cv_engine."""
    kk_repr = repr(tailored.kernkompetenzen)
    desc_short = repr(job.description[:200])
    content = f'''#!/usr/bin/env python3
"""Auto-generated — {job.company} — {job.title}"""

import os
import sys
from pathlib import Path

# Пакет лежить у даних клієнта, рушій окремо: шлях до нього з env або
# той, з якого пакет згенеровано.
sys.path.insert(0, os.environ.get("JOBSEARCH_ENGINE", {str(PROJECT_ROOT)!r}))
os.environ.setdefault("JOBSEARCH_CLIENT", {CLIENT.slug!r})

from bot.generators.cv_engine import render_application
from bot.generators.intake import JobData
from bot.generators.tailor import TailoredData

JOB = JobData(
    title={job.title!r},
    company={job.company!r},
    location={job.location!r},
    description={desc_short},
    email={job.email!r},
    application_url={job.application_url!r},
    source_url={job.source_url!r},
    is_portal={job.is_portal!r},
)

TAILORED = TailoredData(
    cv_title={tailored.cv_title!r},
    profile={tailored.profile!r},
    kernkompetenzen={kk_repr},
    experience_variant={tailored.experience_variant!r},
    skills_order={tailored.skills_order!r},
    fit_score={tailored.fit_score!r},
    fit_reason={tailored.fit_reason!r},
    salary={tailored.salary!r},
    anschreiben={tailored.anschreiben!r},
)

if __name__ == "__main__":
    out = Path(__file__).parent / "to_send"
    render_application(JOB, TAILORED, out)
    print("Done:", out)
'''
    (app_dir / "generate.py").write_text(content, encoding="utf-8")


def write_email_draft(job: JobData, tailored: TailoredData, app_dir: Path) -> None:
    """Writes email_draft.json for Claude Code to send via Gmail MCP."""
    if not job.email:
        return

    subject = f"Bewerbung als {job.title} – {FULL_NAME}"
    body = (
        f"{letter_salutation(getattr(tailored, 'anschreiben', ''))}\n\n"
        f"anbei erhalten Sie meine Bewerbungsunterlagen für die Stelle als "
        f"{job.title} bei {job.company}.\n\n"
        f"Mit freundlichen Grüßen\n"
        f"{FULL_NAME}\n"
        f"{PHONE}"
    )

    to_send = app_dir / "to_send"
    attachments = [
        str(to_send / f"{CV_FILE_STEM}.pdf"),
        str(to_send / "01_Anschreiben.pdf"),
    ] + [str(p) for p in zeugnis_paths(tailored.experience_variant)]

    draft = {
        "from": CLIENT.send_address,
        "to": job.email,
        "subject": subject,
        "body": body,
        "attachments": attachments,
        "fit_score": tailored.fit_score,
        "fit_reason": tailored.fit_reason,
        "portal_url": job.application_url,
        "portal_note": "Подати також через портал!" if job.application_url else None,
    }
    (app_dir / "email_draft.json").write_text(
        json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def update_tracker(job: JobData, tailored: TailoredData, app_dir: Path) -> None:
    """Appends a new row to the client's tracker.md."""
    if not TRACKER_PATH.exists():
        return
    today = date.today().strftime("%Y-%m-%d")
    # Рендер не відправляє: статус мусить лишатись "готово", інакше трекер
    # бреше до Step 4 і follow-up-логіка бачить неіснуючу подачу (27.07).
    if job.email and job.application_url:
        status = "📧 Пакет готовий (email ще не відправлено) · 🌐 Portal pending"
    elif job.email:
        status = "📧 Пакет готовий (email ще не відправлено)"
    elif job.application_url:
        status = f"🌐 Portal: {job.application_url}"
    else:
        status = "🔄 Файли готові — подати вручну"
    # URL вакансії в рядку: за ним filters.rules відсіює повторний відбір тієї
    # самої вакансії, коли назва фірми не розпізналась («Unbekannt»).
    source = getattr(job, "source_url", None)
    if source and source not in status:
        status = f"{status} · {source}"

    # «|» у назві з LinkedIn («Assistenz | Office Manager») розвалював рядок
    # таблиці на зайву колонку, і парсери брали посаду за компанію (16.10).
    def cell(text: str) -> str:
        return re.sub(r"\s*\|\s*", " / ", text or "").strip()

    row = (f"| {today} | {cell(job.title)} | {cell(job.company)}, "
           f"{cell(job.location)} | {status} |\n")
    with open(TRACKER_PATH, "a", encoding="utf-8") as f:
        f.write(row)


# ── Pipelines ─────────────────────────────────────────────────────────────────

def intake_only(url: Optional[str] = None, text: Optional[str] = None) -> Optional[dict]:
    """Step 1 only: scrape/parse job → return JSON dict (no AI).
    Used when Claude Code does the tailoring via subscription."""
    print("[1/1] Збираю дані вакансії...", file=sys.stderr)
    job = intake(url=url, text=text)
    if job is None:
        print("[new_app] Не вдалось отримати дані вакансії", file=sys.stderr)
        return None
    data = job_to_dict(job)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return data


def render_from_data(job_dict: dict, tailored_dict: dict) -> Optional[Path]:
    """Steps 3-5: create folder, render PDFs, write email_draft, update tracker.
    Used when Claude Code provides pre-computed tailored data (no AI call here)."""
    job = job_from_dict(job_dict)
    tailored = tailored_from_dict(tailored_dict)

    if job.email:
        from bot.filters.rules import email_channel_blocked
        if email_channel_blocked(job.company):
            print(f"⛔ {job.company}: email-канал мертвий (портал-only, DSGVO) — "
                  f"{job.email} ігнорується, пакет іде як портальний/ручний")
            job.email = None

    print(f"\n{'='*50}")
    print(f"  Firma:  {job.company}")
    print(f"  Rolle:  {job.title}")
    print(f"  Fit:    {tailored.fit_score}/100 — {tailored.fit_reason}")
    print(f"  Email:  {job.email or 'не знайдено'}")
    if job.application_url:
        print(f"  Portal: {job.application_url}  ← подати ТАКОЖ вручну")
    print(f"{'='*50}\n")

    today_str = date.today().strftime("%Y-%m-%d")
    folder_name = make_folder_name(job.company, job.title)
    app_dir = APPLICATIONS_DIR / today_str / folder_name
    counter = 1
    while app_dir.exists():
        app_dir = APPLICATIONS_DIR / today_str / f"{folder_name}_v{counter + 1}"
        counter += 1
    (app_dir / "to_send").mkdir(parents=True, exist_ok=True)
    (app_dir / "working").mkdir(exist_ok=True)
    (app_dir / "working" / "job_description.txt").write_text(
        f"Source: {job.source_url}\n\n{job.description}", encoding="utf-8"
    )

    print("[1/3] Генерую CV та Anschreiben...")
    render_application(job, tailored, app_dir / "to_send")

    print("[2/3] Записую generate.py та email_draft.json...")
    write_data_only_generate_py(job, tailored, app_dir)
    write_email_draft(job, tailored, app_dir)

    print("[3/3] Оновлюю трекер...")
    update_tracker(job, tailored, app_dir)

    print(f"\nГотово: {app_dir}")
    if job.email:
        print("📧 email_draft.json готовий — відправити окремим кроком "
              "(gmail send / Step 4), рендер листів не шле.")
    else:
        print("⚠️  Email не знайдено — подати вручну.")
    if job.application_url:
        print(f"🌐 Портал (подати також): {job.application_url}")

    return app_dir


def run(url: Optional[str] = None, text: Optional[str] = None) -> Optional[Path]:
    """Full pipeline with direct API call (потребує ANTHROPIC_API_KEY)."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("[new_app] ANTHROPIC_API_KEY не знайдено — використай --intake-only + Claude Code")
        return None
    if tailor is None:
        print("[new_app] tailor модуль недоступний")
        return None

    print("[1/5] Збираю дані вакансії...")
    job = intake(url=url, text=text)
    if job is None:
        print("[new_app] Не вдалось отримати дані вакансії")
        return None

    print(f"[2/5] Tailoring для: {job.title} @ {job.company}...")
    tailored = tailor(job, api_key)
    if tailored is None:
        print("[new_app] Claude API не повернув результат")
        return None

    return render_from_data(job_to_dict(job), {
        "cv_title": tailored.cv_title, "profile": tailored.profile,
        "kernkompetenzen": list(tailored.kernkompetenzen),
        "experience_variant": tailored.experience_variant,
        "skills_order": tailored.skills_order, "fit_score": tailored.fit_score,
        "fit_reason": tailored.fit_reason, "salary": tailored.salary,
        "anschreiben": tailored.anschreiben,
    })


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Автоматична генерація заявки")
    parser.add_argument("url", nargs="?", help="URL вакансії")
    parser.add_argument("--text", action="store_true", help="Читати текст з stdin")
    parser.add_argument("--file", type=Path, help="Читати текст з файлу")
    parser.add_argument("--intake-only", action="store_true",
                        help="Тільки скрейп → JSON (Claude Code робить tailoring)")
    parser.add_argument("--render", type=Path, metavar="DATA_JSON",
                        help="Рендерити з готових даних data.json {job:..., tailored:...}")
    parser.add_argument("--discover", type=Path, metavar="URLS_FILE",
                        help="Batch intake з urls.txt → scored digest JSON")
    parser.add_argument(
        "--playwright",
        dest="use_playwright",
        action="store_true",
        default=True,
        help="Use Playwright fallback for discover (default: on)",
    )
    parser.add_argument(
        "--no-playwright",
        dest="use_playwright",
        action="store_false",
        help="httpx only (fast but StepStone/LinkedIn will fail)",
    )
    parser.add_argument("--batch-render", type=Path, metavar="BATCH_JSON",
                        help="Batch render з [{job, tailored}] JSON → всі заявки")
    parser.add_argument("--check-responses", action="store_true",
                        help="Аналіз відповідей роботодавців з трекеру")
    parser.add_argument("--process-responses", type=Path, metavar="RESPONSES_JSON",
                        help="Класифікувати відповіді + оновити трекер → JSON-звіт")
    parser.add_argument("--filter", type=Path, metavar="DIGEST_JSON", dest="filter_digest",
                        help="Застосувати авто-фільтри до digest → {selected, excluded}")
    parser.add_argument("--apply", type=Path, metavar="APP_DIR",
                        help="Авто-подача одного пакета на портал (потребує --url)")
    parser.add_argument("--url", dest="apply_url", help="URL вакансії для --apply")
    parser.add_argument("--apply-batch", type=Path, metavar="QUEUE_JSON",
                        help="Авто-подача черги заявок (StepStone/XING)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Для --apply*: заповнити форму + скріншот, НЕ сабмітити")
    parser.add_argument("--followups", action="store_true",
                        help="Прострочені follow-ups із трекера → JSON (DUE + skipped)")
    parser.add_argument("--send", action="store_true",
                        help="Для --followups: сигнал, що агент має слати reply")
    parser.add_argument("--out", type=Path, metavar="JSON",
                        help="Для --followups: куди писати список DUE")
    parser.add_argument("--cleanup", action="store_true",
                        help="Чистка: auto-ghost 45д, видалення rejected/ghosted папок, "
                             "дедуп Zeugnisse, ротація var/ (JSON-звіт у stdout)")
    parser.add_argument("--max-delete", type=int, default=None, metavar="N",
                        help="Для --cleanup: ліміт видалень папок за запуск")
    args = parser.parse_args()

    if args.followups:
        from bot.followups import main as followups_main
        argv = ["--tracker", str(TRACKER_PATH)]
        if args.send:
            argv.append("--send")
        if args.out:
            argv += ["--out", str(args.out)]
        sys.exit(followups_main(argv))

    if args.cleanup:
        from bot.cleanup import main as cleanup_main
        argv = []
        if args.dry_run:
            argv.append("--dry-run")
        if args.max_delete is not None:
            argv += ["--max-delete", str(args.max_delete)]
        sys.exit(cleanup_main(argv))

    if args.process_responses:
        from bot.generators.responses import process_responses
        responses = json.loads(args.process_responses.read_text(encoding="utf-8"))
        report = process_responses(responses, TRACKER_PATH)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.filter_digest:
        from bot.filters.rules import main as filters_main
        sys.exit(filters_main([str(args.filter_digest)]))

    if args.apply or args.apply_batch:
        from bot.apply.runner import main as apply_main
        argv = []
        if args.apply:
            if not args.apply_url:
                print("[apply] --apply потребує --url")
                sys.exit(2)
            argv += ["--app-dir", str(args.apply), "--url", args.apply_url]
        if args.apply_batch:
            argv += ["--batch", str(args.apply_batch)]
        if args.dry_run:
            argv.append("--dry-run")
        sys.exit(apply_main(argv))

    # --render mode: приймає JSON з job + tailored
    if args.render:
        data = json.loads(args.render.read_text(encoding="utf-8"))
        result = render_from_data(data["job"], data["tailored"])
        sys.exit(0 if result else 1)

    if args.discover:
        discover_main(args.discover, use_playwright=args.use_playwright)
        sys.exit(0)

    if args.batch_render:
        batch_render_all(args.batch_render, APPLICATIONS_DIR)
        sys.exit(0)

    if args.check_responses:
        if not TRACKER_PATH.exists():
            print("[check-responses] Трекер не знайдено:", TRACKER_PATH)
            sys.exit(1)
        entries = parse_tracker_sent(TRACKER_PATH)
        if not entries:
            print("[check-responses] Немає 'Email sent' заявок у трекері.")
            sys.exit(0)
        query = build_gmail_search_query(entries)
        print(f"\n[check-responses] Знайдено {len(entries)} відправлених заявок.")
        print(f"\nGmail пошуковий запит для Claude:")
        print(f"  {query}")
        print(f"\nПідказка: попросіть Claude виконати Gmail MCP search з цим запитом,")
        print(f"збережіть результати в responses.json, потім запустіть:")
        print(f"  python3 new_app.py --process-responses responses.json")
        sys.exit(0)

    # Resolve text input
    text = None
    if args.text:
        text = sys.stdin.read()
    elif args.file:
        text = args.file.read_text(encoding="utf-8")

    if not args.url and not text:
        parser.print_help()
        sys.exit(1)

    # --intake-only mode: тільки скрейп, без AI
    if args.intake_only:
        result = intake_only(url=args.url, text=text)
        sys.exit(0 if result else 1)

    # Full mode: потребує API ключ
    result = run(url=args.url, text=text)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
