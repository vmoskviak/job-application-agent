"""bot.client: вибір клієнта, шляхи, статуси."""
import pytest

from bot import client


def test_active_client_is_fixture():
    c = client.active()
    assert c.slug == "erika"
    assert c.full_name == "Erika Mustermann"
    assert c.file_stem == "Erika_Mustermann"
    assert c.tracker == c.root / "tracker.md"
    assert c.photo == c.root / "photo.jpeg"
    assert c.send_address == "erika.mustermann.jobs@example.com"
    assert c.playwright_profile == "erika"


def test_bootstrap_argv_moves_flag_to_env(monkeypatch):
    monkeypatch.delenv(client.ENV_CLIENT, raising=False)
    argv = ["new_app.py", "--client", "someone", "--discover", "urls.txt"]
    client.bootstrap_argv(argv)
    assert argv == ["new_app.py", "--discover", "urls.txt"]
    import os
    assert os.environ[client.ENV_CLIENT] == "someone"

    argv = ["x", "--client=other"]
    client.bootstrap_argv(argv)
    assert argv == ["x"] and os.environ[client.ENV_CLIENT] == "other"


def test_bootstrap_argv_without_flag_keeps_env(monkeypatch):
    monkeypatch.setenv(client.ENV_CLIENT, "erika")
    argv = ["x", "--dry-run"]
    client.bootstrap_argv(argv)
    assert argv == ["x", "--dry-run"]


def test_unknown_client_fails_loudly(tmp_path):
    with pytest.raises(client.ClientError):
        client.load("nobody", tmp_path)


def test_invalid_status_rejected(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    (d / "profile.yaml").write_text("status: sleeping\nfirst_name: A\nlast_name: B\n")
    with pytest.raises(client.ClientError):
        client.load("x", tmp_path)


def test_list_clients_filters_dirs_without_profile(tmp_path):
    (tmp_path / "notes").mkdir()
    for slug, status in (("a", "active"), ("b", "placed")):
        d = tmp_path / slug
        d.mkdir()
        (d / "profile.yaml").write_text(
            f"status: {status}\nfirst_name: {slug}\nlast_name: Test\n")
        for f in ("cv.yaml", "tracks.yaml", "targeting.yaml"):
            (d / f).write_text("{}\n")
    found = client.list_clients(tmp_path)
    assert [(c.slug, c.status) for c in found] == [("a", "active"), ("b", "placed")]


def test_render_run_produces_prompt_and_mcp(tmp_path, monkeypatch):
    import json
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import render_run

    monkeypatch.setenv("JOBSEARCH_OPERATOR_EMAIL", "operator@example.com")
    for mode in ("evening", "morning"):
        assert render_run.main([mode, "--out-dir", str(tmp_path), "--headless"]) == 0
        text = (tmp_path / f"prompt_{mode}.md").read_text(encoding="utf-8")
        assert "{{" not in text and "{%" not in text
        assert "erika.mustermann.jobs@example.com" in text
        assert "operator@example.com" in text
        assert f"{mode.upper()} OK" in text
    mcp = json.loads((tmp_path / "mcp.json").read_text())
    assert {"playwright", "gmail", "gmail-main"} <= set(mcp["mcpServers"])
    assert "--headless" in mcp["mcpServers"]["playwright"]["args"]


def test_gmail_aliases_and_reply_account_with_main():
    c = client.active()
    assert c.gmail_aliases == ["jobs", "main"]
    assert c.reply_account == "main"
    assert c.account_for_address("erika.mustermann.jobs@example.com") == "jobs"
    assert c.account_for_address("Erika.Mustermann@example.com") == "main"
    assert c.account_for_address("nobody@example.com") == "main"


def test_client_without_main_replies_land_on_jobs():
    """У клієнта без особистої Gmail-скриньки блок main відсутній."""
    c = client.active()
    only_jobs = {"gmail": {"jobs": c.accounts["gmail"]["jobs"]}, "send_from": "jobs"}
    solo = client.Client(slug=c.slug, root=c.root, profile=c.profile, cv=c.cv,
                         tracks=c.tracks, targeting=c.targeting, accounts=only_jobs)
    assert solo.gmail_aliases == ["jobs"]
    assert solo.reply_account == "jobs"
    assert solo.account_for_address("whatever@example.com") == "jobs"
    assert solo.send_address == c.send_address


def test_oauth_keys_prefer_client_own_file(tmp_path):
    """Свій ключ OAuth-клієнта в каталозі скриньки має пріоритет над спільним."""
    c = client.active()
    gm = tmp_path / ".gmail-mcp"
    (gm / "erika-jobs").mkdir(parents=True)
    (gm / "gcp-oauth.keys.json").write_text("{}")
    assert c.credentials_path("jobs", tmp_path) == gm / "erika-jobs" / "credentials.json"
    assert c.oauth_keys_path("jobs", tmp_path) == gm / "gcp-oauth.keys.json"
    (gm / "erika-jobs" / "gcp-oauth.keys.json").write_text("{}")
    assert c.oauth_keys_path("jobs", tmp_path) == gm / "erika-jobs" / "gcp-oauth.keys.json"
    # main без власного ключа далі бере спільний
    assert c.oauth_keys_path("main", tmp_path) == gm / "gcp-oauth.keys.json"


def test_report_email_from_profile():
    c = client.active()
    assert c.report_email == "erika.privat@example.com"
    bare = client.Client(slug=c.slug, root=c.root, cv=c.cv, tracks=c.tracks,
                         targeting=c.targeting, accounts=c.accounts,
                         profile={k: v for k, v in c.profile.items() if k != "report_email"})
    assert bare.report_email == ""


def test_morning_prompt_splits_candidate_and_operator_reports(monkeypatch):
    """Кандидатка отримує лише подані заявки; manual-список іде тільки оператору."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import render_run

    monkeypatch.setenv("JOBSEARCH_OPERATOR_EMAIL", "operator@example.com")
    monkeypatch.setenv("JOBSEARCH_OPERATOR_MAIL", "always")
    c = client.active()
    text = render_run.render_prompt("morning", c)
    cand = text[text.index("### 9a. Bericht an die Kandidatin"):text.index("### 9b. Bericht an den Operator")]
    assert "erika.privat@example.com" in cand and "operator@example.com" in cand
    assert "Deine Bewerbungen vom" in cand
    # запрошення на співбесіду кандидатка бачить першим блоком свого листа
    assert "Einladung von [Firma]" in cand and "Запрошення стоять першими" in cand
    assert cand.index("Einladung: Firma") < cand.index("heute sind N Bewerbungen")
    oper = text[text.index("### 9b. Bericht an den Operator"):]
    assert "manual-список" in oper and "erika.privat@example.com" not in oper

    bare = client.Client(slug=c.slug, root=c.root, cv=c.cv, tracks=c.tracks,
                         targeting=c.targeting, accounts=c.accounts,
                         profile={k: v for k, v in c.profile.items() if k != "report_email"})
    text = render_run.render_prompt("morning", bare)
    assert "Bericht an die Kandidatin" not in text
    assert "### Bericht an den Operator" in text and "{{" not in text


def test_alert_subject_is_german_and_not_always_failure():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "scripts" / "server" / "send_alert.py"
    spec = importlib.util.spec_from_file_location("send_alert", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    subj = mod.alert_subject("erika", "morning", "Offene Antwort eines Arbeitgebers")
    assert subj == "[jobsearch ALERT] erika Morgenlauf: Offene Antwort eines Arbeitgebers"
    assert "failed" not in subj


def test_evening_mail_mode_sends_operator_nothing_at_night(monkeypatch):
    """Дефолт: оператор отримує один лист увечері, нічний ран пише звіт у файл."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import render_run

    monkeypatch.setenv("JOBSEARCH_OPERATOR_EMAIL", "operator@example.com")
    monkeypatch.delenv("JOBSEARCH_OPERATOR_MAIL", raising=False)
    c = client.active()
    morning = render_run.render_prompt("morning", c)
    cand = morning[morning.index("### 9a. Bericht an die Kandidatin"):morning.index("### 9b. Bericht an den Operator")]
    assert "bcc`: **operator@example.com" not in cand and "Без `cc` і `bcc`" in cand
    oper = morning[morning.index("### 9b. Bericht an den Operator"):]
    assert "var/run/operator_report_YYYY-MM-DD.html" in oper
    assert "mcp__gmail__send_email` → **operator@example.com**" not in morning
    assert "листів оператору НЕ слати" in morning

    evening = render_run.render_prompt("evening", c)
    assert "var/run/operator_evening_YYYY-MM-DD.html" in evening
    assert "Playwright-Login nötig`, текст німецькою" not in evening
    assert "{{" not in evening and "{%" not in evening



def test_telegram_config_defaults():
    """accounts.yaml → telegram: token лише через env, chat_id порожній = ніхто не допущений."""
    from pathlib import Path as _P
    ex = _P(__file__).resolve().parent.parent / "examples" / "clients"
    from dataclasses import replace
    c = client.load("jordan", base=ex)
    assert c.telegram == {"token_env": "JOBSEARCH_TELEGRAM_TOKEN_JORDAN", "chat_id": 424242,
                          "mode": "cards", "lang": c.report_lang}
    bare = replace(c, accounts={**c.accounts, "telegram": {}})
    assert bare.telegram["token_env"] == "JOBSEARCH_TELEGRAM_TOKEN_JORDAN" and bare.telegram["chat_id"] is None
    zero = replace(c, accounts={**c.accounts, "telegram": {"chat_id": 0}})
    assert zero.telegram["chat_id"] is None
    # спільний бот оператора на кількох клієнтів: режим notify і мова сповіщень окремо від report_lang
    shared = replace(c, accounts={**c.accounts, "telegram": {
        "token_env": "JOBSEARCH_TELEGRAM_TOKEN_OPERATOR", "chat_id": 7, "mode": "notify", "lang": "uk"}})
    assert shared.telegram == {"token_env": "JOBSEARCH_TELEGRAM_TOKEN_OPERATOR", "chat_id": 7,
                               "mode": "notify", "lang": "uk"}
    import pytest
    with pytest.raises(ValueError):
        _ = replace(c, accounts={**c.accounts, "telegram": {"mode": "loud"}}).telegram
