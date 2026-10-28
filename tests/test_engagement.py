import pytest
from bot.generators import engagement as e

@pytest.mark.parametrize("title,desc,expected", [
    ("Senior Android Engineer (Contract)", "6-month contract, remote", "contract"),
    ("Android Engineer", "Full-time employee, 401k, equity package", "employment"),
    ("Android Engineer", "Festanstellung, unbefristet, 30 Urlaubstage", "employment"),
    ("Android-Entwickler", "Projektlaufzeit 12 Monate, Stundensatz nach Vereinbarung", "contract"),
    ("Android Engineer", "We build apps. Email us.", "unknown"),
    ("Android Engineer", "Full-time, open to contractors too", "contract"),
    ("Android Engineer", "permanent contract, 30 days holiday", "employment"),
    ("Android-Entwickler", "Festanstellung, kein Freelancer", "employment"),
])
def test_classify_engagement(title, desc, expected):
    assert e.classify_engagement(title, desc) == expected

@pytest.mark.parametrize("desc,loc,expected", [
    ("Remote worldwide", "", "global"),
    ("Remote within Europe", "Berlin", "europe"),
    ("Remote, must reside in the US", "", "us_only"),
    ("On-site only, no remote", "Munich", "onsite"),
    ("Hybrid, 2 days in office", "Düsseldorf", "hybrid"),
    ("Great team", "", "unknown"),
    ("Global company, on-site", "Berlin", "unknown"),
])
def test_remote_scope(desc, loc, expected):
    assert e.remote_scope("Android Engineer", desc, loc) == expected

# 26.10.2026: п'ять лідів за чотири вечори («San Francisco, CA», «Seattle, WA»,
# «Pittsburgh, PA») проходили фільтр як unknown: у LinkedIn-локації США нема назви країни.
@pytest.mark.parametrize("loc", [
    "San Francisco, CA", "Pittsburgh, PA", "Seattle, WA", "New York, NY",
    "San Francisco Bay Area", "Austin, Texas Metropolitan Area",
    "Washington DC-Baltimore Area",
])
def test_remote_scope_us_location_without_signal(loc):
    assert e.remote_scope("Android Engineer", "Great team, Kotlin and Compose.", loc) == "us_only"

@pytest.mark.parametrize("desc,loc,expected", [
    ("Great team.", "Berlin, DE", "unknown"),                 # DE тут Німеччина, не Delaware
    ("Great team.", "Köln, Nordrhein-Westfalen", "unknown"),
    ("Great team.", "Warsaw, Mazowieckie, Poland", "country:poland"),
    ("Remote worldwide.", "San Francisco, CA", "global"),     # опис важливіший за локацію
])
def test_remote_scope_location_fallback_limits(desc, loc, expected):
    assert e.remote_scope("Android Engineer", desc, loc) == expected

def test_timezone_tags():
    assert e.timezone_tag("4 h overlap with US East") == "et_overlap"
    assert e.timezone_tag("must work Pacific hours") == "pacific_hours"
    assert e.timezone_tag("async team") == ""

def test_us_auth_negation_passes():
    assert e.us_work_authorization_required("US work authorization required") is True
    assert e.us_work_authorization_required("no US work authorization required") is False
    assert e.us_work_authorization_required("open to contractors worldwide") is False
    assert e.us_work_authorization_required("security clearance needed") is True
    assert e.us_work_authorization_required("No agencies please, US work authorization required") is True

@pytest.mark.parametrize("text,expected", [
    ("$90-110/hr", e.Rate(90, 110, "hour", "USD")),
    ("€700/Tag", e.Rate(700, 700, "day", "EUR")),
    ("95 €/h", e.Rate(95, 95, "hour", "EUR")),
    ("£500 per day", e.Rate(500, 500, "day", "GBP")),
])
def test_parse_rate(text, expected):
    assert e.parse_rate(text) == expected

def test_parse_rate_salary_is_not_rate():
    assert e.parse_rate("80k-100k") is None
    assert e.parse_rate("€60.000/Jahr") is None

def test_detect_lang():
    assert e.detect_lang("Wir suchen einen Android-Entwickler für ein Projekt mit Kotlin") == "de"
    assert e.detect_lang("We are looking for an Android engineer for a contract") == "en"


# --- 22.10.2026: дірки, знайдені на дайджестах 21–22.10 ---

@pytest.mark.parametrize("desc", [
    "For this project, we are only accepting applications from employed staff. "
    "Freelancers should refrain from applying. Projektlaufzeit 6 Monate.",
    "Unfortunately, we cannot present freelancers to the client. Stundensatz nach Vereinbarung.",
    "Projekt für 12 Monate, keine Freiberufler, nur Festangestellte von Systemhäusern.",
    # GULP-агентури, дослівний шаблон 22.10.2026
    "Projektbörse GULP (freiberuflich). Für dieses Projekt werden ausschließlich Bewerbungen "
    "von angestellten Mitarbeiterinnen (von Systemhäusern, IT - Dienstleistern, etc. ) akzeptiert. "
    "Freiberuflerinnen können beim Kunden leider nicht vorgestellt werden. "
    "Bitte sehen Sie als Freiberufler*innen von Bewerbungen ab.",
])
def test_explicit_freelancer_rejection_wins_over_contract_signals(desc):
    assert e.classify_engagement("Senior Go Developer", desc) == "employment"


@pytest.mark.parametrize("desc,loc,expected", [
    ("Location: Remote anywhere from Poland. Type of cooperation: B2B contract", "Warsaw, Poland", "country:poland"),
    ("Senior Android Engineer. Fully Remote - Poland. 6 Month Contract", "Poland", "country:poland"),
    ("Full Stack Developer | Remote (Germany) | Full-time", "", "country:germany"),
    ("Contract, remote from India", "", "country:india"),
    ("Small team, ping us", "Poland", "country:poland"),
    ("Remote within Europe, Poland preferred", "", "europe"),
    ("REMOTE (EU & UK)", "", "europe"),
    ("Remote (US)", "", "us_only"),
    ("REMOTE (US or Canada)", "", "us_only"),
    ("REMOTE (Americas: US or LatAm)", "", "us_only"),
    ("REMOTE (US/Canada) | $180K", "", "us_only"),
    ("Full Time, Remote India", "", "country:india"),
    # обмеження країною перемагає слово Europe деінде в тексті
    ("Fully Remote - Poland. We work across Europe, the UK and North America.", "", "country:poland"),
    # не обмеження: Європа або весь світ у тому самому уточненні
    ("REMOTE (preferred CET ±2: Spain, UK, Poland, Romania)", "", "europe"),
    ("REMOTE – US / Canada / Brazil / Europe (CET-compatible)", "", "europe"),
    ("REMOTE (Worldwide: US / Canada / Europe / UK / LATAM)", "", "europe"),
    ("Berlin, Germany / REMOTE (CET ±3 preferred)", "", "europe"),
    ("REMOTE(Worldwide) or ONSITE", "", "global"),
    ("We're remote, reach us at jobs@example.org", "", "unknown"),
    # HN: окреме поле ONSITE без слова remote
    ("San Francisco | ONSITE | Full-time | $150K", "", "onsite"),
    ("ONSITE 5 days/week, New York City", "", "onsite"),
    ("Austin, TX | ONSITE or FULLY REMOTE", "", "unknown"),
])
def test_remote_scope_country_restriction(desc, loc, expected):
    assert e.remote_scope("Android Engineer", desc, loc) == expected


def test_parse_rate_monthly_and_pln():
    r = e.parse_rate("Salary range: B2B 6500 - 7500 Euro/month- neto")
    assert (r.low, r.high, r.unit, r.currency) == (6500, 7500, "month", "EUR")
    r = e.parse_rate("126 - 158 PLN/h + vat na B2B")
    assert (r.low, r.high, r.unit, r.currency) == (126, 158, "hour", "PLN")
    r = e.parse_rate("$3,500-$5,000 USD/month")
    assert (r.low, r.high, r.unit, r.currency) == (3500, 5000, "month", "USD")
    assert e.parse_rate("€90k/year, equity") is None


@pytest.mark.parametrize("desc,expected", [
    ("Biegła znajomość języka polskiego oraz angielskiego.", {"polish", "english"}),
    ("Fluent Polish and English required.", {"polish", "english"}),
    ("German at B2 or better.", {"german"}),
    ("Verhandlungssichere Deutschkenntnisse (C1).", {"german"}),
    ("Native Dutch speaker is a must.", {"dutch"}),
    ("We ship fast. Kotlin, Compose.", set()),
    ("German is a plus.", set()),
    ("Mile widziane: znajomość języka niemieckiego.", set()),
    ("Our German clients need fluent English.", {"english"}),
    ("We are a Dutch fintech based in Amsterdam.", set()),
    ("Strong attention to UI polish is a must.", set()),
])
def test_language_required(desc, expected):
    assert e.languages_required(desc) == expected



@pytest.mark.parametrize("desc,expected", [
    ("This is an equity-only role initially (10+ hrs/week).", True),
    ("Business Development Manager (Freelance, Commission-only)", True),
    ("We help small businesses collect unpaid invoices. Contract, €90/h.", False),
    ("Contract, remote Europe, €95/hour.", False),
])
def test_unpaid_role(desc, expected):
    assert e.unpaid_role(desc) is expected


def test_vacation_benefits_mean_employment_without_contract_signal():
    desc = "Remote (Germany) | Full-time. We offer 32 vacation days and paid parental leave."
    assert e.classify_engagement("Full Stack Developer", desc) == "employment"
    # contract-сигнал однаково перемагає бенефіти
    desc2 = "Contract, full-time. Perks: five weeks' vacation, paid parental leave."
    assert e.classify_engagement("Product Engineer", desc2) == "contract"


@pytest.mark.parametrize("title,desc,expected", [
    # B2B як бізнес-модель компанії не є B2B-контрактом
    ("AI Engineer", "Acme runs a live B2B marketplace. Full-time.", "unknown"),
    ("Backend Engineer", "We build B2B SaaS for insurers.", "unknown"),
    ("Kotlin Developer", "Type of cooperation: B2B contract", "contract"),
    ("Android Developer", "126 - 158 PLN/h + vat na B2B", "contract"),
])
def test_b2b_business_model_is_not_contract(title, desc, expected):
    assert e.classify_engagement(title, desc) == expected


@pytest.mark.parametrize("desc,expected", [
    ("Product Engineer | Freelance / employment | CLOSED NOW, thanks to everyone who applied.", True),
    ("[CLOSED] Senior Android Engineer, remote", True),
    ("Update: this position has been filled, thanks everyone.", True),
    ("We are no longer accepting applications.", True),
    ("We are no longer accepting applications from agencies.", False),
    ("Closed-source SDK, contract role.", False),
])
def test_posting_closed(desc, expected):
    assert e.posting_closed(desc) is expected


def test_bare_global_word_yields_to_a_non_eu_location():
    # «global fandom» в описі плюс «remote» у назві: голе слово global не робить
    # вакансію з location «United States» доступною з Європи
    desc = "We are a dream team pushing the boundaries of storytelling, global fandom and more."
    assert e.remote_scope("Software Engineer 4 - Android (Remote)", desc, "United States") == "us_only"
    assert e.remote_scope("Senior Android Engineer, remote", desc, "Bangalore, India") == "country:india"
    assert e.remote_scope("Senior Android Engineer, remote", desc, "Berlin, Germany") == "country:germany"
    assert e.remote_scope("Senior Android Engineer, remote", desc, "") == "global"


def test_explicit_worldwide_keeps_global_despite_location():
    desc = "Fully remote, work from anywhere in the world."
    assert e.remote_scope("Android Engineer", desc, "United States") == "global"
