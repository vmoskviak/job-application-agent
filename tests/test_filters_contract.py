"""Гілка контрактного ринку в bot.filters.rules.

rules читає конфіг клієнта під час імпорту, а conftest закріплює erika,
тому повний прогін на jordan іде окремим процесом.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_rules(tmp_path):
    env = {**os.environ, "JOBSEARCH_CLIENTS_DIR": str(ROOT / "examples" / "clients"),
           "JOBSEARCH_CLIENT": "jordan"}
    out = tmp_path / "filtered.json"
    subprocess.run([sys.executable, "-m", "bot.filters.rules",
                    str(ROOT / "tests" / "fixtures" / "contract_digest.json"),
                    "-o", str(out), "--tracker", str(tmp_path / "tracker.md")],
                   cwd=ROOT, env=env, check=True, capture_output=True)
    return json.loads(out.read_text())


def test_contract_reasons_and_tags(tmp_path):
    res = run_rules(tmp_path)
    reasons = {e["company"]: e["reason"] for e in res["excluded"]}
    assert reasons["Employ Co"] == "excluded_description:employee_only"
    assert reasons["Office Co"] == "excluded_description:onsite_only"
    assert reasons["USA Co"] == "us_only"
    # 26.10.2026: «San Francisco, CA» без слова remote в описі проходив як unknown
    assert reasons["Bay Co"] == "us_only"
    # R6: US work authorization з пайплайна, заперечення не виключає
    assert reasons["Auth Co"] == "us_work_authorization"
    # R4: engagement=employment без contract-сигналу виключається з пайплайна
    assert reasons["Equity Co"] == "employee_only"
    assert reasons["Perm Co"] == "employee_only"
    sel = {e["company"]: e for e in res["selected"]}
    assert sel["Hacker Co"]["engagement"] == "unknown"
    assert sel["Contract Co"]["engagement"] == "contract"
    assert sel["Contract Co"]["priority_rank"] < sel["Hacker Co"]["priority_rank"]
    assert sel["Hybrid Co"]["remote_scope"] == "hybrid"
    assert sel["NegAuth Co"]["remote_scope"] == "global"
    assert set(sel) == {"Contract Co", "Hacker Co", "Hybrid Co", "NegAuth Co"}


def test_contract_rate_and_region(tmp_path, monkeypatch):
    """rate_below_min і outside_region без підпроцесу: підміняємо ринок,
    ставки й локації в уже імпортованому модулі."""
    from bot.filters import rules
    monkeypatch.setattr(rules, "_MARKET", "contract")
    monkeypatch.setattr(rules, "_RATE", {"hourly_min": 80, "daily_target": 760})
    monkeypatch.setattr(rules, "LOCATIONS", ["lisbon", "portugal"])
    monkeypatch.setattr(rules, "EXCLUDED_DESCRIPTION_PATTERNS", [])
    base = {"title": "Android Engineer", "location": "Remote", "score": 60, "category": "x"}
    digest = [
        {**base, "company": "Cheap Hour", "description": "Contract, remote Europe, $60/hour."},
        {**base, "company": "Cheap Day", "description": "Contract, remote Europe, €500/day."},
        {**base, "company": "Fair Day", "description": "Contract, remote Europe, €650/day."},
        {**base, "company": "Far Hybrid", "location": "Berlin",
         "description": "Contract, hybrid, 3 days in office."},
    ]
    res = rules.apply_rules(digest, tmp_path / "tracker.md", min_score=25)
    reasons = {e["company"]: e["reason"] for e in res["excluded"]}
    assert reasons == {"Cheap Hour": "rate_below_min", "Cheap Day": "rate_below_min",
                       "Far Hybrid": "outside_region"}
    sel = {e["company"]: e for e in res["selected"]}
    assert sel["Fair Day"]["stated_rate"] == {"low": 650, "high": 650, "unit": "day", "currency": "EUR"}


def _contract_rules(monkeypatch, **over):
    from bot.filters import rules
    monkeypatch.setattr(rules, "_MARKET", "contract")
    monkeypatch.setattr(rules, "_RATE", {"hourly_min": 80, "daily_target": 760})
    monkeypatch.setattr(rules, "LOCATIONS", ["lisbon", "portugal"])
    monkeypatch.setattr(rules, "EXCLUDED_DESCRIPTION_PATTERNS", [])
    monkeypatch.setattr(rules, "LANGUAGES_OK", {"english", "portuguese"})
    monkeypatch.setattr(rules, "DESCRIPTION_BONUS", [])
    for k, v in over.items():
        monkeypatch.setattr(rules, k, v)
    return rules


def test_contract_country_restriction_outside_region(tmp_path, monkeypatch):
    rules = _contract_rules(monkeypatch)
    base = {"title": "Android Engineer", "score": 60, "category": "android"}
    digest = [
        {**base, "company": "Poland Only", "location": "Warsaw, Poland",
         "description": "Contract. Location: Remote anywhere from Poland."},
        {**base, "company": "Home Country", "location": "Remote",
         "description": "Contract. Remote (Portugal)."},
        {**base, "company": "Bare Location", "location": "Poland",
         "description": "Contract, Kotlin, Compose."},
    ]
    res = rules.apply_rules(digest, tmp_path / "tracker.md", min_score=25)
    reasons = {e["company"]: e["reason"] for e in res["excluded"]}
    assert reasons == {"Poland Only": "outside_region", "Bare Location": "outside_region"}
    sel = {e["company"]: e for e in res["selected"]}
    assert sel["Home Country"]["remote_scope"] == "country:portugal"


def test_contract_monthly_and_foreign_currency_rates(tmp_path, monkeypatch):
    rules = _contract_rules(monkeypatch)
    base = {"title": "Android Engineer", "location": "Remote", "score": 60, "category": "android"}
    digest = [
        {**base, "company": "Cheap Month",
         "description": "Contract, remote Europe. Salary range: B2B 6500 - 7500 Euro/month netto."},
        {**base, "company": "Fair Month",
         "description": "Contract, remote Europe. €15,000/month on B2B."},
        {**base, "company": "Cheap Zloty",
         "description": "Contract, remote Europe. 126 - 158 PLN/h + vat."},
    ]
    res = rules.apply_rules(digest, tmp_path / "tracker.md", min_score=25)
    reasons = {e["company"]: e["reason"] for e in res["excluded"]}
    assert reasons == {"Cheap Month": "rate_below_min", "Cheap Zloty": "rate_below_min"}
    assert [e["company"] for e in res["selected"]] == ["Fair Month"]


def test_contract_language_mismatch(tmp_path, monkeypatch):
    rules = _contract_rules(monkeypatch)
    base = {"title": "Android Engineer", "location": "Remote", "score": 60, "category": "android"}
    digest = [
        {**base, "company": "Polish Co",
         "description": "Contract, remote Europe. Biegła znajomość języka polskiego oraz angielskiego."},
        {**base, "company": "English Co",
         "description": "Contract, remote Europe. Fluent English required."},
    ]
    res = rules.apply_rules(digest, tmp_path / "tracker.md", min_score=25)
    reasons = {e["company"]: e["reason"] for e in res["excluded"]}
    assert reasons == {"Polish Co": "language_mismatch:polish"}
    assert [e["company"] for e in res["selected"]] == ["English Co"]


def test_contract_unpaid_role(tmp_path, monkeypatch):
    rules = _contract_rules(monkeypatch)
    base = {"title": "Founding AI Engineer", "location": "Remote", "score": 60, "category": "ai"}
    digest = [
        {**base, "company": "Equity Co", "description": "Contract, remote worldwide. Equity-only role."},
        {**base, "company": "Paid Co", "description": "Contract, remote worldwide. €100/hour."},
    ]
    res = rules.apply_rules(digest, tmp_path / "tracker.md", min_score=25)
    reasons = {e["company"]: e["reason"] for e in res["excluded"]}
    assert reasons == {"Equity Co": "unpaid"}
    assert [e["company"] for e in res["selected"]] == ["Paid Co"]


def test_contract_closed_posting(tmp_path, monkeypatch):
    rules = _contract_rules(monkeypatch)
    base = {"title": "Product Engineer", "location": "Remote", "score": 60, "category": "ai"}
    digest = [
        {**base, "company": "Closed Co", "description": "Freelance, remote Europe. CLOSED NOW."},
        {**base, "company": "Open Co", "description": "Freelance, remote Europe."},
    ]
    res = rules.apply_rules(digest, tmp_path / "tracker.md", min_score=25)
    assert {e["company"]: e["reason"] for e in res["excluded"]} == {"Closed Co": "closed"}
