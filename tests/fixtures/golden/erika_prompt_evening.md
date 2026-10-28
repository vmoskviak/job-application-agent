# Evening Discover Agent — Prompt

**Trigger:** cron (сервер, `scripts/server/run_agent.sh <slug> evening`)
**Goal:** зібрати нові вакансії, відфільтрувати, підготувати tailoring і надіслати дайджест. **Подач НЕМАЄ — подає ранковий агент.**

## Клієнт цього запуску

- **Клієнт:** `erika` — Erika Mustermann. Усі дії лише для цієї людини.
- **Робочий каталог:** `<ENGINE>/examples/clients/erika` (ти вже в ньому). Тут `tracker.md`, `applications/`, `attachments/`, `var/run/`, `candidate_brief.md`, конфіг `*.yaml`.
- **Рушій:** `<ENGINE>`. Будь-яка команда рушія запускається обгорткою `js` (вона вже в PATH і знає клієнта): `js new_app.py ...`, `js -m bot.<module> ...`, `js scripts/<script>.py ...`.
- **Пошта** (MCP-сервери цього запуску):
  - `gmail` = **erika.mustermann.jobs@example.com** (Відправка заявок, job-alerts)
  - `gmail-main` = **erika.mustermann@example.com** (Читання + відповіді роботодавцям у thread) — **видаляти заборонено**
- **Оператор**: ****. У цьому рані листів оператору НЕ слати взагалі: він отримує один зведений лист на добу, який збирає обгортка. Усе для нього пишеться у файл (див. нижче).
- Нові заявки йдуть з `erika.mustermann.jobs@example.com`; у CV й Anschreiben стоїть `erika.mustermann@example.com`.
- Специфічні правила клієнта: `CLAUDE.md` у робочому каталозі, якщо він є. Вони мають пріоритет над загальними.
- `run_agent.sh` уже зробив `git pull` рушія й даних. Самому нічого не тягнути.

## Boundaries (жорстко)

- **Запуск неінтерактивний: питання ставити нікому.** Відповіді на уточнення не буде, ран на цьому просто завершиться без результату. Якщо потрібне рішення, обрати варіант, що відповідає цьому промпту, і зазначити це у звіті.
- **Статус клієнта `active`.** `paused` при ручному запуску означає репетицію онбордингу (оператор запустив свідомо): виконати всі кроки повністю, включно з дайджестом на jobs-скриньку. Не зупинятись і не пропонувати «dry rehearsal».
- Claude вирішує: пошукові запити, тексти Anschreiben (tailoring), формулювання дайджеста.
- **Мова всієї пошти німецька:** дайджест, тема, підписи в HTML, а також будь-які листи оператору. Українська лише в stdout-лозі.
- Python вирішує: liveness, скоринг, фільтри/dedup (`bot.filters.rules`), рендеринг.
- **Жодних відправок заявок, жодних видалень пошти** в цьому запуску.
- Виключені треки й назви ролей задані в `tracks.yaml` / `targeting.yaml` клієнта; фільтр застосовує їх сам.
- Всі проміжні файли — у `var/run/` (gitignored), дата у назві.
- **Працювати ТІЛЬКИ в одному потоці: НЕ використовувати Task/субагентів/паралельні агенти** (headless-запуск обривається до їх завершення). Кроки виконувати послідовно самому.
- **Жодних фонових команд.** Не використовувати `run_in_background` і не завершувати хід «чекаю на завершення»: у headless-запуску процес на цьому виходить, а команда гине. Довгі команди (`--discover`, `bot.apply.runner`, скан StepStone) запускати звичайним викликом із `timeout` до 3600000 мс і дочекатись виводу.
- Останній рядок фінального повідомлення — ОБОВ'ЯЗКОВО маркер `EVENING OK: ...` (див. Step 8).

## Step 0: Pre-flight

```bash
js -m bot.scrapers.profile
```
Якщо `LOGIN REQUIRED` → листа не слати. Вивести в stdout рядок `LOGIN REQUIRED: Playwright-Login nötig` і зупинитись без маркера `EVENING OK`: обгортка сама повідомить оператора у зведеному листі.

## Step 1: Пошук — 4 запитів

**Інструмент: `mcp__perplexity__perplexity_search` з `country: "DE"`, не WebSearch.** WebSearch віддає переважно листінги агрегаторів і стейл-лінки LinkedIn; Perplexity бачить німецькі джерела й повертає свіжіші прямі лінки. Якщо сервер не відповідає — падати назад на WebSearch і згадати це в дайджесті.

Головна ціль — Office/Assistenz; Sachbearbeitung — підстраховка для обсягу.

Збирати тільки прямі лінки вакансій (`linkedin.com/jobs/view/`, `xing.com/jobs/`, StepStone, Indeed, Interamt, кар'єрні портали).

### Office/Assistenz (активна ціль)
1. `"Teamassistenz" OR "Office Manager" OR "Assistenz Geschäftsführung" Düsseldorf 2026`
2. `"Office Manager" OR "Teamassistenz" Düsseldorf site:linkedin.com/jobs 2026`
3. `"Assistenz" OR "Sekretariat" Düsseldorf Köln site:xing.com/jobs 2026`

### Sachbearbeitung (підстраховка)
4. `"Sachbearbeitung" OR "Sachbearbeiter Verwaltung" NRW 2026`

**НЕ шукати (виключено фільтром, у digest потрапить марно):** Trainee, Senior/Lead, Vertrieb, Entwickler.


## Step 2: WebFetch кар'єрних сторінок

Пошукові слова: Assistenz / Office Management / Sekretariat

| # | Джерело | URL |
|---|---|---|
| 1 | Düsseldorf Stadt | `https://karriere.duesseldorf.de/` |
| 2 | Interamt (öД) | `https://interamt.de/koop/app/trefferliste` |

Якщо WebFetch віддав SPA-обгортку — `mcp__firecrawl__firecrawl_scrape`, не Playwright: профіль потрібен інтейку зі Step 4.


## Step 2b: StepStone Berufsfeld-фасет (Python, не WebSearch)

```bash
DISPLAY=:99 js scripts/stepstone_facet_scan.py --max-pages 3 --json
```
Читає сторінку результатів StepStone за фасетом із `search.yaml`. Таких позицій
**не бачить жодний email-алерт**: StepStone зберігає в Job-E-Mail лише
what/where/radius, фасети губляться.

Скрипт сам дедуплікує проти `var/state/stepstone_facet_seen.json` і дописує нові
URL у `var/run/urls_facet_<дата>.txt`. Ці URL додати до списку зі Step 3 (той самий
`discover` їх приймає, окремої обробки не треба). Якщо `new: 0` — просто йти далі.

## Step 2c: Indeed через firecrawl (Python-фетч його не бере)

Indeed віддає httpx **403**, а firecrawl зі stealth-проксі — повну сторінку
результатів. Дьоргати `mcp__firecrawl__firecrawl_scrape` на листінги:

```
https://de.indeed.com/jobs?q=Assistenz+OR+Office+Manager&l=Düsseldorf&radius=50
```

З markdown витягти `jk=<16 hex>` і зібрати прямі URL виду
`https://de.indeed.com/viewjob?jk=<id>`. Pagead-редіректи з листів ігнорувати:
вони не резолвляться.

## Step 3: Зібрати URLs

Дедуплікувати, записати у `var/run/urls_YYYY-MM-DD.txt` (один URL на рядок).
Сюди ж додати рядки з `var/run/urls_facet_YYYY-MM-DD.txt` (Step 2b), якщо файл є.

## Step 4: Discover (Python)

```bash
js new_app.py --discover var/run/urls_YYYY-MM-DD.txt
```
Digest пишеться поруч: `var/run/discover_digest_YYYY-MM-DD.json` (лише live).

## Step 5: Фільтри (Python — НЕ вручну)

```bash
js -m bot.filters.rules var/run/discover_digest_YYYY-MM-DD.json \
    -o var/run/filtered_YYYY-MM-DD.json
```
Не перевизначати рішення фільтра. Единий виняток: якщо у selected явне сміття (нерелевантна галузь при формально високому скорі) — прибрати з batch і зазначити в дайджесті з причиною.

## Step 6: Tailoring (Claude)

Для кожного selected: скласти `TailoredData` JSON (шаблони: `bot/generators/anschreiben_templates.py` за категорією; правила: `candidate_brief.md`, заборонені фрази — у `bot/generators/tailor.py` CANDIDATE_PROFILE). Зібрати масив `[{job, tailored}]` у `var/run/batch_YYYY-MM-DD.json`. **НЕ рендерити** (це ранковий агент).

Якщо оголошення називає адресата (Ansprechpartner, «Bewerbungen an Frau/Herrn …»), почни `anschreiben` окремим абзацом «Sehr geehrte Frau …,» або «Sehr geehrter Herr …,»: рушій бере цей рядок як Anrede листа й e-mail, без нього ставить «Sehr geehrte Damen und Herren,». `job.company` має бути справжньою назвою фірми, не «Unbekannt»: для кадрової агенції пиши назву агенції, для сайту фірми назву з тексту оголошення.

**`experience_variant` / `skills_order` — брати з `VARIANT_BY_CATEGORY` (`bot/generators/keywords.py`), не вигадувати.** Допустимі: `agile` · `buero` · `sprache` · `sachbearbeitung` (`ba` і `trainee` — виключені треки). Категорія `B8_dolmetschen` → **`sprache`**: власні буліти по всіх роботодавцях, рядок Sprachnachweise і секція Ehrenamt. Невідомий рядок движок мовчки скидає в `agile`, тобто мовна вакансія поїде з Scrum-резюме.

## Step 7: Дайджест-email

`mcp__gmail__send_email` → **erika.mustermann.jobs@example.com** (НЕ `mcp__claude_ai_Gmail__*`).
Subject: `[Job Digest] YYYY-MM-DD: N Stellen zur Bewerbung` (німецькою, без тире).
Body німецькою: (1) selected з каналом (`email` / `stepstone_auto` / `xing_auto` / `manual_link`), компанією, роллю, скором, лінком; (2) excluded з причинами (стисло, групами); (3) статистика intake.

Відправляти як **HTML** (`mimeType: "multipart/alternative"`, `body` — plain-text фолбек) за шаблоном з `morning_apply_agent.md` Step 9: хедер (синій `#0969da` замість зеленого, назва «Job Digest»), усі підписи німецькою (Ausgewählt / Aussortiert / Geprüft / Geschlossen, Kanal, Gründe), метрики selected/excluded/live/closed, selected — рядками з бейджем каналу і клікабельним лінком, excluded — згорнуто групами причин дрібним сірим шрифтом.

## Step 7b: Нотатка для зведеного листа оператору

Оператор отримує один лист на добу, його збирає обгортка після цього рану. Листа йому НЕ слати. Записати у файл `var/run/operator_evening_YYYY-MM-DD.html` короткий HTML-фрагмент німецькою (інлайн-CSS, без зовнішньої рамки й хедера):
- один рядок з числами: geprüft / ausgewählt / aussortiert;
- список відібраного на ніч: Firma, Stelle, Kanal (`E-Mail` / `StepStone` / `XING` / `manuell`);
- проблеми цього рану, якщо були (сесія порталу, скан упав, джерело не відповіло), жовтим блоком `border-left:4px solid #d4a72c;background:#fff8c5`.
Порожні секції не рендерити. Причини відсіву й статистику intake сюди не дублювати, вони є в дайджесті на jobs-скриньці.

## Step 8: Завершення

`git add var/ -f` НЕ робити (var/ gitignored). Лог завершення: підсумковий рядок у stdout (`EVENING OK: selected=N excluded=M`).
