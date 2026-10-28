from __future__ import annotations

from bot.scrapers.adapters import get_adapter
from bot.scrapers.fetch import fetch_page
from bot.scrapers.liveness import check_liveness
from bot.scrapers.models import IntakeResult
from bot.job_title import is_junk_title, recover_title


def intake_url(url: str, use_playwright: bool = True) -> IntakeResult:
    try:
        page = fetch_page(url, use_playwright=use_playwright)
    except RuntimeError as e:
        return IntakeResult(job=None, liveness="failed", intake_method="none", reason=str(e))
    except Exception as e:
        return IntakeResult(job=None, liveness="failed", intake_method="none", reason=str(e))

    if not page.text:
        return IntakeResult(job=None, liveness="failed", intake_method=page.method, reason="empty page")

    # XING-лист несе десятки /m/-лінків, і лише частина з них вакансії: решта —
    # «дивись, що знайшов пошук» та інші інтерстишли. resolve_xing_share() віддає
    # /jobs/-URL лише для справжніх вакансій, тому /m/, що залишився /m/, — не лід.
    # Без цього XingAdapter парсив інтерстишл через og:description і робив лід із
    # title «schau, was unsere KI-gestützte Suche für Dich gefunden hat».
    if "xing.com/m/" in page.url:
        return IntakeResult(job=None, liveness="failed", intake_method=page.method,
                            reason="xing /m/ не резолвиться у /jobs/ — не вакансія")

    liveness, reason = check_liveness(page.text, url)
    if liveness != "live":
        return IntakeResult(job=None, liveness=liveness, intake_method=page.method, reason=reason)

    adapter = get_adapter(url)
    job = adapter.extract(page)
    # Адаптер узяв шапку сторінки («Suchen», «XING Job») замість назви, а опис
    # цілий: назву відновлюємо з опису, інакше фільтр викине живу вакансію.
    if is_junk_title(job.title):
        job.title = recover_title(job.description) or job.title
    if len(job.description.strip()) < 100:
        return IntakeResult(job=None, liveness="unknown", intake_method=page.method, reason="description too short")

    return IntakeResult(job=job, liveness="live", intake_method=page.method, reason="ok")
