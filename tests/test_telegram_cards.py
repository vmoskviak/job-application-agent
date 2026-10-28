"""Картки Telegram (bot.telegram.cards): текст HTML з екрануванням, кнопки, ліміти."""
from __future__ import annotations

from bot.telegram.cards import (
    card_id,
    followup_card,
    gmail_url,
    help_text,
    rejection_card,
    reply_card,
    status_text,
)


def _item(**over):
    base = {"from": "A <a@example.com>", "subject": "<Re: Android>", "date": "Mon, 26 Oct 2026",
            "company": "Example Co", "message_id": "1a2b3c4d5e6f7a8b"}
    return {**base, **over}


def test_reply_card_escapes_and_has_buttons():
    c = reply_card(_item(), "interview", "Danke, gern.", "re-1a2b3c4d5e6f", "https://mail.google.com/x", "uk")
    assert c.kind == "reply"
    assert "&lt;Re: Android&gt;" in c.text and "Співбесіда" in c.text and "<pre>Danke, gern.</pre>" in c.text
    assert c.text.startswith("<b>Example Co</b> · Співбесіда")
    assert [b.data or b.url for row in c.buttons for b in row] == [
        "send:re-1a2b3c4d5e6f", "https://mail.google.com/x", "skip:re-1a2b3c4d5e6f"]
    assert [b.text for row in c.buttons for b in row] == ["Надіслати", "Відкрити в Gmail", "Пропустити"]


def test_reply_card_without_draft_has_no_send_button():
    c = reply_card({"from": "a@example.com", "subject": "s", "date": "", "company": None, "message_id": "m"},
                   "outreach", None, "ou-m", "u", "en")
    assert "draft unavailable" in c.text.lower()
    assert all(b.data != "send:ou-m" for row in c.buttons for b in row)
    assert c.text.startswith("<b>example.com</b> · Outreach")     # без компанії: домен відправника


def test_draft_in_pre_is_escaped():
    c = reply_card(_item(), "info_request", "Rate: <95 & 105>", "re-x", "u", "en")
    assert "<pre>Rate: &lt;95 &amp; 105&gt;</pre>" in c.text


def test_card_id_and_gmail_url():
    assert card_id("reply", "1a2b3c4d5e6f7a8b") == "re-1a2b3c4d5e6f"
    assert card_id("rejected", "m") == "re-m"
    assert gmail_url("jordan@example.com", "1a2b") == \
        "https://mail.google.com/mail/u/0/?authuser=jordan@example.com#all/1a2b"


def test_rejection_and_followup_cards_have_no_buttons():
    r = rejection_card(_item(), "rj-m", "https://mail.google.com/x", "uk")
    assert r.kind == "rejected" and r.buttons == [] and "Відмова" in r.text and "Example Co" in r.text
    f = followup_card("Example Co", "Android Engineer", "abc123", "en")
    assert f.kind == "followup" and f.buttons == [] and "Example Co" in f.text and "Android Engineer" in f.text


def test_long_text_is_cut():
    c = rejection_card(_item(subject="x" * 5000), "rj-m", "u", "en")
    assert len(c.text) <= 4096 and c.text.endswith("…")


def test_status_and_help_text():
    s = status_text({"open_cards": 2, "sent_today": 1, "followups_today": 0, "paused": True, "last_watch": "10:05"}, "uk")
    assert "2" in s and "10:05" in s and "пауз" in s.lower()
    assert "/status" in help_text("en") and "/pause" in help_text("uk")


def test_long_draft_is_cut_inside_pre():
    c = reply_card(_item(), "interview", "d" * 6000, "re-x", "u", "en")
    assert len(c.text) <= 4096 and c.text.count("<pre>") == 1 and "</pre>" in c.text
    assert c.text.index("</pre>") > c.text.index("<pre>")


def test_lead_cards():
    from bot.telegram.cards import lead_failed_card, lead_sent_card

    c = lead_sent_card("Contract Co", "Android", "email", "m1", "uk")
    assert c.kind == "lead_sent" and c.buttons == [] and "Contract Co" in c.text and "m1" in c.text
    assert c.text.startswith("Подано")
    f = lead_failed_card("Contract <Co>", "Android", "email", "<boom>" * 200, "en")
    assert f.kind == "lead_failed" and f.buttons == []
    assert "&lt;boom&gt;" in f.text and "Contract &lt;Co&gt;" in f.text and f.text.count("boom") == 50   # 300 символів до екранування
    assert f.text.startswith("Failed")


def test_status_text_with_queue():
    s = {"open_cards": 0, "sent_today": 0, "followups_today": 0, "paused": False, "last_watch": "-",
         "queue": {"queued": {"email": 1, "form": 3}, "held": 2, "sent_today": {"email": 1}, "failed": 0}}
    text = status_text(s, "en")
    assert "Queue: email 1, form 3, held 2" in text and "Applied today: email 1" in text
    assert "Черга: email 1, form 3, held 2" in status_text(s, "uk")
    assert "Queue" not in status_text({k: v for k, v in s.items() if k != "queue"}, "en")


def test_platform_card_copy():
    from bot.telegram.cards import platform_card

    c = platform_card("linkedin", "login_required", "vnc://localhost:5901", "uk")
    assert c.text.startswith("Сесія на linkedin злетіла")
    assert c.text == ("Сесія на linkedin злетіла. Зайди по VNC (vnc://localhost:5901): DISPLAY=:99 "
                      "JOBSEARCH_CLIENT=&lt;slug&gt; bin/js scripts/relogin.py &lt;login-url&gt; "
                      "--wait-for &lt;domain&gt;, або в браузері на сервері; "
                      "воркер тримає профіль лише під час проходу (кілька хвилин о :40). "
                      "Після входу: /platform linkedin on")
    assert c.text.endswith("/platform linkedin on") and c.buttons == []
    en = platform_card("linkedin", "login_required", "vnc://h", "en")
    assert en.text.startswith("Session on linkedin expired. Log in over VNC (vnc://h): DISPLAY=:99 "
                              "JOBSEARCH_CLIENT=&lt;slug&gt; bin/js scripts/relogin.py &lt;login-url&gt; "
                              "--wait-for &lt;domain&gt;, or in the browser")
    assert en.text.endswith("/platform linkedin on")
    assert platform_card("gulp", "restricted", "", "uk").text.startswith("gulp обмежив акаунт")
    assert "restricted" in platform_card("linkedin", "restricted", "", "en").text.lower()
    assert platform_card("gulp", "platform_quota", "", "uk").text == "Місячна квота gulp вичерпана: вимкнено до 1-го числа."
    assert platform_card("gulp", "platform_quota", "", "en").text.startswith("Monthly quota on gulp")
    assert platform_card("x<y>", "weird", "", "en").text == "x&lt;y&gt;: weird"
    assert "&lt;h&gt;" in platform_card("li", "login_required", "<h>", "uk").text


def test_platform_card_copy_has_no_dashes():
    from bot.telegram.cards import platform_card

    for lang in ("uk", "en"):
        for status in ("login_required", "restricted", "platform_quota"):
            text = platform_card("gulp", status, "vnc", lang).text
            assert "\u2014" not in text and "\u2013" not in text


def test_status_text_lists_platforms():
    base = {"open_cards": 0, "sent_today": 0, "followups_today": 0, "paused": False, "last_watch": "-"}
    text = status_text({**base, "platforms": ["linkedin: on 2/5", "gulp: shadow 0/3"]}, "en")
    assert text.endswith("\nlinkedin: on 2/5\ngulp: shadow 0/3")
    assert status_text({**base, "platforms": []}, "en") == status_text(base, "en")


def test_help_mentions_platform():
    assert "/platform" in help_text("uk") and "/platform" in help_text("en")


_TELEGRAM_TAGS = {"b", "i", "u", "s", "a", "code", "pre", "tg-spoiler", "blockquote"}


def _bad_tags(text: str) -> list[str]:
    """Теги, яких parse_mode HTML у Telegram не знає: з ними sendMessage падає і нічого не шле."""
    import re

    return [t for t in re.findall(r"</?([A-Za-z][\w-]*)", text) if t.lower() not in _TELEGRAM_TAGS]


def test_copy_has_only_telegram_html_tags():
    from bot.telegram import cards
    from bot.telegram.service import _TEXTS, PLATFORM_HELP

    strings = [PLATFORM_HELP, help_text("uk"), help_text("en")]
    for table in (cards._UI, cards._PLATFORM, _TEXTS):
        for lang in ("uk", "en"):
            strings += [v for v in table[lang].values() if isinstance(v, str)]
    assert [s for s in strings if _bad_tags(s)] == []
