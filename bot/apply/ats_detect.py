"""Виявлення результату після Submit на ATS-формах (Ashby, Greenhouse, Lever, Workable).

Чиста логіка без браузера: `PROBE_JS` виконується через `page.evaluate`,
викликач додає `text` з `page.inner_text("body")` і передає `PageProbe`
в `outcome_after_submit`.

Увага: невидимий reCAPTCHA Enterprise (iframe `anchor`) є на кожній сторінці
Ashby/Greenhouse навіть без виклику, тому слово «recaptcha» НЕ ознака капчі.
Виклик — лише видимий iframe `bframe` (або hCaptcha `challenge`, Cloudflare).
Тому `detect.classify_apply_page` тут не використовується.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from bot.apply.detect import _CLOSED_MARKERS

CONFIRMATION_EN = (
    "thank you for applying",
    "thanks for applying",
    "application has been submitted",
    "application submitted",
    "application was submitted",
    "application has been received",
    "we have received your application",
    "application received",
    "thank you for your application",
    "we've received your application",
)

SUBMIT_TEXTS = (
    "submit application",
    "submit",
    "apply",
    "bewerbung absenden",
    "bewerben",
    "send application",
)

_CLOSED_EXTRA = [
    r"no\s+longer\s+accepting\s+applications",
    r"this\s+job\s+is\s+no\s+longer\s+available",
    r"position\s+has\s+been\s+filled",
    r"job\s+not\s+found",
]

# JS для page.evaluate: один об'єкт з полями PageProbe, крім `text`.
# Видимість: getBoundingClientRect() width і height > 0 та не display:none.
PROBE_JS = r"""
(() => {
  const SUBMIT_TEXTS = %SUBMIT_TEXTS%;
  // cssVisible: CSS-видимість (display, visibility, opacity через checkVisibility
  // з запасним варіантом) для елемента й усіх предків до body, плюс власний rect
  // з width і height > 0. Rect предків і viewport НЕ перевіряються: довга форма
  // має Submit і помилки нижче за екран, і це не «форми немає». Інакше
  // submit_visible=false дав би хибне «submitted», а помилки валідації пропали б.
  // onScreen: cssVisible і власний rect перетинає viewport; лише для викликів
  // (bframe reCAPTCHA лежить у контейнері visibility:hidden; top:-10000px; opacity:0).
  const cssOk = (el) => {
    const css = window.getComputedStyle(el);
    return el.checkVisibility
      ? el.checkVisibility({checkVisibilityCSS: true, checkOpacity: true})
      : (css.display !== 'none' && css.visibility !== 'hidden' && parseFloat(css.opacity) > 0);
  };
  const cssVisible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return false;
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
      if (!cssOk(n)) return false;
    }
    return true;
  };
  const onScreen = (el) => {
    if (!cssVisible(el)) return false;
    const r = el.getBoundingClientRect();
    return r.bottom > 0 && r.right > 0 && r.top < window.innerHeight && r.left < window.innerWidth;
  };
  const all = (sel) => Array.from(document.querySelectorAll(sel));

  const submitVisible = all('button[type=submit], button, input[type=submit]').some((el) => {
    if (!cssVisible(el)) return false;
    if (el.matches('button[type=submit], input[type=submit]')) return true;
    const t = (el.innerText || el.value || '').trim().toLowerCase();
    // короткі слова лише точним збігом («Apply for another role» не кнопка Submit)
    return SUBMIT_TEXTS.some((s) => (s.includes(' ') ? t.includes(s) : t === s));
  });

  const challengeSrc = /recaptcha\/.*bframe|hcaptcha\.com\/.*challenge|challenges\.cloudflare\.com/i;
  const iframeChallenge = all('iframe').some((f) => challengeSrc.test(f.src || '') && onScreen(f));
  const textChallenge = /select all images|verify you are human/i;
  const elChallenge = all('[aria-label], h1, h2, h3, p, div, span, label').some((el) => {
    if (!onScreen(el)) return false;
    const label = el.getAttribute('aria-label') || '';
    if (textChallenge.test(label)) return true;
    return el.children.length === 0 && textChallenge.test(el.textContent || '');
  });

  const invalidCount = all('[aria-invalid=true]').filter(cssVisible).length;

  const errorTexts = all('[role=alert], .error, [class*=error-message], [id*=error]')
    .filter(cssVisible)
    .map((el) => (el.innerText || '').trim())
    .filter((t) => t.length > 0)
    .slice(0, 10);

  return {
    html: document.documentElement.outerHTML,
    submit_visible: submitVisible,
    challenge_visible: iframeChallenge || elChallenge,
    invalid_count: invalidCount,
    error_texts: errorTexts,
  };
})()
""".replace("%SUBMIT_TEXTS%", repr(list(SUBMIT_TEXTS)))


@dataclass
class PageProbe:
    text: str
    html: str
    submit_visible: bool
    challenge_visible: bool
    invalid_count: int
    error_texts: list[str] = field(default_factory=list)


def confirmation_in(text: str) -> str:
    """Знайдена фраза підтвердження (нижній регістр) або ''."""
    low = (text or "").lower()
    for phrase in CONFIRMATION_EN:
        if phrase in low:
            return phrase
    # Ледачий імпорт: forms тягне candidate.py, який читає конфіг клієнта при імпорті.
    from bot.apply.forms import CONFIRMATION_PATTERNS

    for phrase in CONFIRMATION_PATTERNS:
        if phrase in low:
            return phrase
    return ""


def outcome_after_submit(probe: PageProbe) -> Literal["submitted", "challenge", "validation", "pending"]:
    """Результат після кліку Submit за знімком сторінки."""
    if probe.challenge_visible:
        return "challenge"
    if not probe.submit_visible and confirmation_in(probe.text):
        return "submitted"
    if probe.invalid_count > 0 or probe.error_texts:
        return "validation"
    return "pending"


def closed_in(text: str) -> bool:
    """Вакансія закрита: німецькі й англійські маркери."""
    return any(re.search(p, text or "", re.IGNORECASE) for p in [*_CLOSED_MARKERS, *_CLOSED_EXTRA])
