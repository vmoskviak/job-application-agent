"""Contact and form-fill data for applications.

Єдине джерело правди для контактів кандидата — форми порталів, CV,
Anschreiben і follow-up-підписи беруть значення звідси. Самі значення
живуть у `profile.yaml` активного клієнта (див. `bot/client.py`); міняти
контакти ТІЛЬКИ там.
"""

from bot import client as _client

_p = _client.active().profile

FIRST_NAME = _p["first_name"]
LAST_NAME = _p["last_name"]
FULL_NAME = f"{FIRST_NAME} {LAST_NAME}"
EMAIL = _p["email"]
PHONE = _p["phone"]
PHONE_PREFIX = _p.get("phone_prefix", "+49")


def national_phone(phone: str = None, prefix: str = None) -> str:
    """«+49 151 00000000» → «15100000000» для форм з окремим select коду країни
    (StepStone і XING відхиляють «+49» у полі номера, 08.10.2026)."""
    phone = PHONE if phone is None else phone
    prefix = PHONE_PREFIX if prefix is None else prefix
    digits = "".join(ch for ch in phone if ch.isdigit())
    pre = "".join(ch for ch in prefix if ch.isdigit())
    if pre and digits.startswith(pre):
        digits = digits[len(pre):]
    return digits.lstrip("0")


PHONE_NATIONAL = national_phone()
STREET = _p["street"]
ZIP_CODE = str(_p["zip_code"])
CITY = _p["city"]
COUNTRY = _p.get("country", "Deutschland")
NATIONALITY = _p["nationality"]
AVAILABILITY = _p.get("availability", "ab sofort")
SALUTATION = _p["salutation"]
BIRTH_DATE_ISO = str(_p["birth_date"])  # input[type=date] приймає ISO

DEFAULT_SALARY = str(_p["default_salary"])
# Рівні мов для select-ів «Deutschkenntnisse / Englischkenntnisse»
# (profile.yaml → language_levels: {Deutsch: C2, Englisch: B1-B2}).
LANGUAGE_LEVELS: dict = dict(_p.get("language_levels") or {})
# Відповіді на типові додаткові питання форм (profile.yaml → form_answers):
#   {pattern: 'regex по тексту питання', answer: 'текст'}   для текстових полів
#   {pattern: '...', choice: 'no' | 'yes'}                  для radio Ja/Nein
FORM_ANSWERS: list[dict] = list(_p.get("form_answers") or [])

# Мапа "натяк у назві поля" → значення. Ключі порівнюються lowercase
# проти name/id/label/placeholder інпута.
FIELD_HINTS: dict[str, str] = {
    "vorname": FIRST_NAME,
    "first": FIRST_NAME,
    "nachname": LAST_NAME,
    "last": LAST_NAME,
    "surname": LAST_NAME,
    "e-mail": EMAIL,
    "email": EMAIL,
    "telefon": PHONE,
    "phone": PHONE,
    "mobil": PHONE,
    "straße": STREET,
    "strasse": STREET,
    "street": STREET,
    "plz": ZIP_CODE,
    "postleitzahl": ZIP_CODE,
    "zip": ZIP_CODE,
    "ort": CITY,
    "stadt": CITY,
    "city": CITY,
    "wohnort": CITY,
    "land": COUNTRY,
    "country": COUNTRY,
    "staatsangehörigkeit": NATIONALITY,
    "nationalität": NATIONALITY,
    "nationality": NATIONALITY,
    "verfügbar": AVAILABILITY,
    "eintrittstermin": AVAILABILITY,
    "starttermin": AVAILABILITY,
}


def salary_for(entry: dict | None) -> str:
    """Зарплатне очікування з дайджест-запису (salary_hint) або дефолт."""
    if entry and entry.get("salary_hint"):
        return str(entry["salary_hint"])
    return DEFAULT_SALARY
