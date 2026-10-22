"""Нові сторінки подачі XING (14.10.2026): /jobs/apply/, /jobs/quick-apply/, /jobs/custom-apply/."""
from datetime import date
from pathlib import Path

from bot.apply import forms, runner
from bot.apply.xing import is_apply_page, job_id_from_url, split_attachments


def test_job_id_from_url_ignores_query_and_path_prefix():
    job = "https://www.xing.com/jobs/duesseldorf-assistenz-verwaltung-100000101"
    assert job_id_from_url(job) == "100000101"
    assert job_id_from_url(job.replace("/jobs/", "/jobs/quick-apply/") + "?") == "100000101"
    assert job_id_from_url("https://www.xing.com/jobs/custom-apply/koeln-teamassistenz-100000102?x=1#top") == "100000102"
    assert job_id_from_url("https://www.xing.com/jobs/search") == ""


def test_is_apply_page_only_for_apply_routes():
    assert is_apply_page("https://www.xing.com/jobs/apply/duesseldorf-x-100000101")
    assert is_apply_page("https://www.xing.com/jobs/quick-apply/duesseldorf-x-100000101?")
    assert is_apply_page("https://www.xing.com/jobs/custom-apply/duesseldorf-x-100000101")
    assert not is_apply_page("https://www.xing.com/jobs/duesseldorf-x-100000101")
    assert not is_apply_page("https://login.xing.com/")


def test_split_attachments_cv_and_letter():
    cv, letter = split_attachments([Path("to_send/Erika_Muster_CV.pdf"), Path("to_send/01_Anschreiben.pdf")])
    assert cv.name == "Erika_Muster_CV.pdf"
    assert letter.name == "01_Anschreiben.pdf"
    assert split_attachments([]) == (None, None)


def test_upload_names_are_unique_per_package(tmp_path):
    from bot.apply.xing import upload_copies, upload_name
    app_dir = tmp_path / "BeispielPersonal_AssistenzVerwaltung"
    (app_dir / "to_send").mkdir(parents=True)
    cv = app_dir / "to_send" / "Erika_Muster_CV.pdf"
    letter = app_dir / "to_send" / "01_Anschreiben.pdf"
    cv.write_bytes(b"cv")
    letter.write_bytes(b"letter")
    assert upload_name(app_dir, cv) == "Erika_Muster_CV_BeispielPersonal.pdf"
    assert upload_name(app_dir, letter) == "Anschreiben_BeispielPersonal.pdf"
    other = tmp_path / "Muster Agentur Nord_Assistenz"
    assert upload_name(other, letter) == "Anschreiben_MusterAgenturNord.pdf"
    out = tmp_path / "tmp"
    out.mkdir()
    copies = upload_copies(app_dir, [cv, letter, app_dir / "to_send" / "missing.pdf"], out)
    assert [c.name for c in copies] == ["Erika_Muster_CV_BeispielPersonal.pdf",
                                         "Anschreiben_BeispielPersonal.pdf"]
    assert copies[1].read_bytes() == b"letter"
    cv_copy, letter_copy = split_attachments(copies)
    assert cv_copy.name.startswith("Erika_Muster_CV") and letter_copy.name.startswith("Anschreiben")


def test_earliest_start_is_first_of_month_at_least_two_weeks_ahead():
    assert forms.earliest_start_iso(date(2026, 10, 14)) == "2026-11-01"   # 18 днів → 1.11
    assert forms.earliest_start_iso(date(2026, 10, 25)) == "2026-12-01"   # 7 днів до 1.11 → 1.12
    assert forms.earliest_start_iso(date(2026, 12, 20)) == "2027-02-01"   # через Новий рік


def test_salary_from_package_reads_generate_py(tmp_path):
    (tmp_path / "generate.py").write_text(
        "TAILORED = TailoredData(\n    cv_title='Teamassistenz',\n    salary='46000',\n)\n",
        encoding="utf-8")
    assert runner.salary_from_package(tmp_path) == "46000"
    assert runner.salary_from_package(tmp_path / "missing") is None
