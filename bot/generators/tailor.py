"""Claude Haiku API — tailors CV data for a specific job description."""

import json
from dataclasses import dataclass
from typing import Optional, List, Tuple

from bot.generators.intake import JobData

from bot import client as _client

# Бриф кандидата й правила tailoring — вільний текст із candidate_brief.md
# клієнта: досвід, освіта, мови, заборонені фрази, що можна й не можна
# стверджувати, які варіанти CV під які ролі, структура Anschreiben.
CANDIDATE_PROFILE = _client.active().brief
TAILORING_RULES = ""

_DEFAULT_SALARY = str(_client.active().profile["default_salary"])
_VARIANTS = "|".join(_client.active().cv["variants"])
_DEFAULT_VARIANT = _client.active().cv.get(
    "default_variant", _client.active().cv["variants"][0])


@dataclass
class TailoredData:
    cv_title: str
    profile: str
    kernkompetenzen: List[Tuple[str, str]]
    experience_variant: str
    skills_order: str
    fit_score: int
    fit_reason: str
    salary: str
    anschreiben: str


def determine_salary(variant: str) -> str:
    return _DEFAULT_SALARY


def parse_tailor_response(raw: str) -> Optional[TailoredData]:
    try:
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:])
        if raw.endswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[:-1])
        data = json.loads(raw.strip())
        kk = data.get("kernkompetenzen", [])
        kernkompetenzen = [
            (k[0], k[1]) if isinstance(k, list) else (k, "")
            for k in kk
        ]
        return TailoredData(
            cv_title=data["cv_title"],
            profile=data["profile"],
            kernkompetenzen=kernkompetenzen,
            experience_variant=data.get("experience_variant", _DEFAULT_VARIANT),
            skills_order=data.get("skills_order", _DEFAULT_VARIANT),
            fit_score=int(data.get("fit_score", 50)),
            fit_reason=data.get("fit_reason", ""),
            salary=data.get("salary", _DEFAULT_SALARY),
            anschreiben=data["anschreiben"],
        )
    except Exception:
        return None


def tailor(job: JobData, api_key: str) -> Optional[TailoredData]:
    """Calls Claude Haiku to generate tailored CV data for a job."""
    import anthropic  # optional dependency — only this direct-API path needs it

    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""Du bist ein professioneller CV-Spezialist für deutsche Bewerbungen.

KANDIDATENPROFIL:
{CANDIDATE_PROFILE}

STELLENANZEIGE:
Titel: {job.title}
Firma: {job.company}
Ort: {job.location}
Beschreibung: {job.description[:2500]}

Erstelle tailored CV-Daten. Antworte NUR mit validem JSON (kein Markdown, kein Text davor/danach):
{{
  "cv_title": "...",
  "profile": "3-4 Sätze, konkret, kein AI-Speak",
  "kernkompetenzen": [["Label", "Wert · Wert · Wert"], ...],
  "experience_variant": "{_VARIANTS}",
  "skills_order": "{_VARIANTS}",
  "fit_score": 0-100,
  "fit_reason": "1-2 Sätze warum passend oder nicht",
  "salary": "{_DEFAULT_SALARY}",
  "anschreiben": "Vollständiger Anschreiben-Text (4 Absätze, ohne Anrede und Grußformel)"
}}"""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        return parse_tailor_response(message.content[0].text)
    except Exception as e:
        print(f"[tailor] Claude API error: {e}")
        return None
