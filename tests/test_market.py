from pathlib import Path
from bot import client as _client, market, onboard
EX = Path(__file__).resolve().parent.parent / "examples" / "clients"


def test_market_profiles_exist():
    assert set(market.MARKETS) == {"de_employment", "contract"}
    assert market.get("contract").default_lang == "en"
    assert "rate" in market.get("contract").required_profile
    assert "birth_date" not in market.get("contract").required_profile


def test_client_market_defaults():
    erika = _client.load("erika", base=EX)
    jordan = _client.load("jordan", base=EX)
    assert erika.market == "de_employment" and erika.report_lang == "de"
    assert jordan.market == "contract" and jordan.report_lang == "en"


def test_onboard_check_jordan_passes_without_street_or_birth_date():
    errors, _ = onboard.check(_client.load("jordan", base=EX))
    assert errors == []


def test_onboard_check_erika_unchanged():
    # до зміни onboard.check(erika) уже повертав errors == []
    errors, _ = onboard.check(_client.load("erika", base=EX))
    assert errors == []


def test_required_profile_de_matches_legacy_list():
    assert onboard.required_profile("de_employment") == [
        "first_name", "last_name", "salutation", "email", "phone", "street",
        "zip_code", "city", "nationality", "birth_date", "default_salary"]
