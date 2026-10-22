"""XING pre-screening («Diese Infos brauchen wir zuerst») перед Easy Apply."""
from bot.apply.xing import answer_prescreening, pick_level_option

OPTS = ["", "-", "A1-A2 (Grundkenntnisse)", "B1-B2 (Gute Kenntnisse)", "C1 (Fließend)",
        "C2 (Verhandlungssicher / Muttersprachlich)"]


def test_pick_level_option_by_prefix():
    assert pick_level_option(OPTS, "C2").startswith("C2")
    assert pick_level_option(OPTS, "b1-b2") == "B1-B2 (Gute Kenntnisse)"
    assert pick_level_option(OPTS, "") is None
    assert pick_level_option(OPTS, "B2") is None  # XING має лише B1-B2


def test_answer_prescreening_without_modal_is_noop():
    class Loc:
        def __init__(self, *a, **k): pass
        @property
        def first(self): return self
        def is_visible(self, timeout=0): return False
    class Page:
        def locator(self, sel): return Loc()
    assert answer_prescreening(Page()) == []


class _FakeLoc:
    """Мінімальний локатор: модал лише з питанням про роботу в ЄС, без мов."""

    def __init__(self, sel: str, log: list[str]):
        self.sel = sel
        self.log = log

    @property
    def first(self):
        return self

    def locator(self, sel):
        return _FakeLoc(sel, self.log)

    def get_by_text(self, text, exact=False):
        return _FakeLoc(f"text={text}", self.log)

    def count(self):
        # Дропдаунів мов у цьому модалі нема; радіо «Ja» є.
        return 0 if "pre-screening" in self.sel else 1

    def is_visible(self, timeout=0):
        return "Speichern und weiter" in self.sel

    def is_checked(self):
        return False

    def check(self):
        self.log.append(f"check:{self.sel}")

    def click(self, timeout=0):
        self.log.append(f"click:{self.sel}")


def test_answer_prescreening_eu_only_modal_without_language_dropdowns():
    """14.10.2026 (BeispielPersonal): модал питав лише дозвіл на роботу в ЄС, без мов.

    Детектор за дропдауном німецької пропускав модал, Easy-Apply-форма не
    відкривалась і подача падала як «форма для іншої вакансії».
    """
    log: list[str] = []

    class Page:
        def locator(self, sel):
            return _FakeLoc(sel, log)

        def wait_for_timeout(self, ms):
            pass

    assert answer_prescreening(Page()) == ["eu=yes", "saved"]
    assert log == ["check:input[value='yes']",
                   "click:button:has-text('Speichern und weiter zur Bewerbung')"]


def test_national_phone_strips_prefix_and_spaces():
    from bot.apply.xing import national_phone
    assert national_phone("+49 151 00000000", "+49") == "15100000000"
    assert national_phone("0151 00000000", "+49") == "15100000000"
    assert national_phone("+43 660 1234567", "+49") == "436601234567"


def test_pick_header_button_prefers_closest_below_h1():
    from bot.apply.xing import pick_header_button
    cands = [(120.0, "Einfach bewerben"),      # стрічка «Ähnliche Jobs» НАД заголовком
             (560.0, "Zur Arbeitgeber-Website"),  # шапка вакансії
             (2400.0, "Einfach bewerben")]     # стрічка під вакансією
    assert pick_header_button(400.0, cands) == (1, "external")
    cands[1] = (560.0, "Einfach bewerben")
    assert pick_header_button(400.0, cands) == (1, "easy")
    assert pick_header_button(3000.0, cands) == (None, "none")


def test_candidate_phone_national_from_profile():
    from bot.apply import candidate
    assert candidate.PHONE_NATIONAL == candidate.national_phone(candidate.PHONE, candidate.PHONE_PREFIX)
    assert "+" not in candidate.PHONE_NATIONAL and " " not in candidate.PHONE_NATIONAL
