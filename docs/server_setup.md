# Сервер: встановлення й runbook

Розраховано на Debian 13 у LXC-контейнері (4 vCPU, 8 GB RAM, 32 GB диск),
усе під root. Часовий пояс `Europe/Berlin`.

## Встановлення

```bash
apt install git curl gnupg jq locales cron rsync openssh-server \
  fonts-dejavu fonts-liberation2 libpango-1.0-0 libpangocairo-1.0-0 libcairo2 \
  libgdk-pixbuf-2.0-0 libffi-dev python3 python3-venv python3-pip xvfb x11vnc
# Node 22 (NodeSource), потім:
npm install -g @anthropic-ai/claude-code

git clone git@github.com:<user>/jobsearch-autopilot.git /opt/jobsearch-autopilot
git clone git@github.com:<user>/jobsearch-clients.git  /opt/jobsearch-clients
cd /opt/jobsearch-autopilot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/playwright install --with-deps chromium
```

Репозиторій даних сервер і читає, і пише (трекер після кожного ранку), тому
deploy key для нього потрібен з правом запису. Для рушія досить читання.

## Конфіг машини: `/root/.claude-env` (chmod 600, поза git)

```bash
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
JOBSEARCH_CLIENTS_DIR=/opt/jobsearch-clients/clients
JOBSEARCH_OPERATOR_EMAIL=operator@example.com   # звіти, співбесіди
JOBSEARCH_ALERT_TO=operator@example.com         # алерти про збої
JOBSEARCH_OPERATOR_MAIL=evening                 # evening: один зведений лист після вечірнього рану, вночі нічого; always: лист після кожного рану
PERPLEXITY_API_KEY=...
FIRECRAWL_API_KEY=...
JOBSEARCH_VNC_HINT=vnc://<ip-сервера>:5900   # підказка в повідомленнях про re-login
# CLAUDE_MODEL=opus
```

`run_agent.sh` читає цей файл сам. Без `FIRECRAWL_API_KEY` запасний шлях
скрапінгу мовчки вимикається, решта працює.

## Claude auth

`claude setup-token` друкує довгоживучий токен у термінал, файла не створює.
Запускати в широкій tmux-панелі, інакше токен обріжеться переносом рядка:

```bash
tmux new-session -d -s token -x 400 -y 50 "claude setup-token; sleep 600"
tmux capture-pane -t token -p -J        # URL для авторизації в браузері
tmux send-keys -t token "<код>" Enter
tmux capture-pane -t token -p -J | grep -o "sk-ant-oat01[a-zA-Z0-9_-]*" | head -1
tmux kill-session -t token
# перевірка:
set -a; source /root/.claude-env; set +a; claude -p "reply with exactly: AUTH-OK"
```

Модель за замовчуванням Opus: на слабшій моделі агент вигадував статуси
заявок і Msg-ID.

## Gmail

Для кожної скриньки клієнта окремий каталог `~/.gmail-mcp/<creds_dir>`
(назва з `accounts.yaml → creds_dir`) з токеном `credentials.json` і ключем
OAuth-клієнта `gcp-oauth.keys.json`.

Ключ OAuth-клієнта краще заводити під власним Google-акаунтом клієнта: свій
проєкт Google Cloud, своя згода, сама себе додає в тестові користувачі. Тоді
клієнти не залежать один від одного і від акаунта оператора. Якщо в каталозі
клієнта ключа нема, рушій бере спільний `~/.gmail-mcp/gcp-oauth.keys.json`
(стара схема, один проєкт на машину: кожну нову скриньку треба додавати в
Test users того проєкту, інакше Google дає `403 access_denied`).

Створити ключ під акаунтом клієнта (у браузері, залогіненим як її jobs-пошта):

1. console.cloud.google.com → новий проєкт, напр. `jobsearch-<slug>`.
2. APIs & Services → Library → Gmail API → Enable.
3. Google Auth Platform → Branding: назва застосунку, support email і
   contact email = її jobs-адреса; Audience: External; Test users: її
   jobs-адреса (і main, якщо є).
4. Clients → Create client → Desktop app → Download JSON →
   `~/.gmail-mcp/<creds_dir>/gcp-oauth.keys.json` на сервері.

Токен:

```bash
mkdir -p ~/.gmail-mcp/<creds_dir>
GMAIL_OAUTH_PATH=~/.gmail-mcp/<creds_dir>/gcp-oauth.keys.json \
GMAIL_CREDENTIALS_PATH=~/.gmail-mcp/<creds_dir>/credentials.json \
  npx -y @gongrzhe/server-gmail-autoauth-mcp auth
# відкрити надрукований URL, увійти в ПОТРІБНУ скриньку
JOBSEARCH_CLIENT=<slug> /opt/jobsearch-autopilot/bin/js scripts/gmail_rest.py check jobs
JOBSEARCH_CLIENT=<slug> /opt/jobsearch-autopilot/bin/js scripts/gmail_rest.py check main
```

`check` друкує адресу скриньки: звірити, що токен від тієї, що треба.

## Re-login у портали (новий клієнт або протухла сесія)

Логіни StepStone, XING, LinkedIn живуть у браузерному профілі
`~/.playwright-mcp/<playwright_profile>`. Вхід робить людина через VNC:

```bash
Xvfb :99 -screen 0 1280x900x24 &
x11vnc -display :99 -passwd <пароль> -listen 0.0.0.0 -forever &
cd /opt/jobsearch-autopilot
DISPLAY=:99 JOBSEARCH_CLIENT=<slug> bin/js scripts/relogin.py
# з робочого комп'ютера: vnc://<ip-сервера>:5900, залогінитись у вікні браузера,
# потім Enter у терміналі
JOBSEARCH_CLIENT=<slug> bin/js scripts/session_health.py --json
```

**StepStone і XING.** Обидва логіняться автоматично в тому ж браузері, що
працює далі, через «Mit Google einloggen», якщо акаунт порталу зв'язаний із
jobs-Gmail (перший раз Google питає згоду, її дає людина по VNC або
оператор у Chrome-профілі клієнта). Деталі для XING:

**XING** (з 07.2026) не дає персистентної сесії навіть із «Eingeloggt
bleiben»: кук `login` сесійний і зникає з процесом браузера. Тому для XING
ручний логін по VNC не допомагає; замість нього `session_health` і
`bot.apply` логінять XING самі, у тому ж браузері, що працює далі
(`bot/scrapers/portal_login.py`). Найпростіше:
XING-акаунт клієнта прив'язаний до jobs-Gmail («Weiter mit Google»), а в
профіль по VNC один раз зроблено вхід у Google. Google-сесія персистентна,
і скрипт логінить XING через неї без жодного пароля. Запасний шлях: файл
з обліковими даними, лише на сервері, chmod 600, шлях в
`accounts.yaml → portal_credentials`:

```json
{"xing": {"username": "<xing-email>", "password": "<пароль>"}}
```

Перевірка: `JOBSEARCH_CLIENT=<slug> DISPLAY=:99 bin/js scripts/portal_login.py xing` (окремий процес, лише діагностика).

reCAPTCHA проходить лише людина. Сесію з іншої машини можна перенести через
`scripts/export_storage_state.py` і `import_storage_state.py`; файл стану
після імпорту видалити на обох машинах, у ньому живі cookies.

## Cron

Вмикати після репетиції хоча б одного клієнта (`docs/onboarding.md` §4):

```bash
crontab /opt/jobsearch-autopilot/scripts/server/crontab.txt
```

Клієнти обробляються по черзі під одним локом. Кого брати, вирішує
`status: active` у `profile.yaml`: щоб поставити людину на паузу, cron чіпати
не треба.

Ручний запуск одного клієнта:

```bash
scripts/server/run_agent.sh <slug> evening
APPLY_MODE=dry-run scripts/server/run_agent.sh <slug> morning
```

## Де дивитись, коли щось не так

| Що | Де |
|---|---|
| Лог рану | `<clients>/<slug>/var/logs/<mode>_<дата>.log` |
| Проміжні файли | `<clients>/<slug>/var/run/` |
| Повний транскрипт сесії Claude | `/root/.claude/projects/` (лог містить лише підсумок) |
| Звіт cleanup | `var/run/cleanup_<дата>.json`; `unmatched: not_found` це норма |

Пошта оператору: один лист на добу, `[<Ім'я>: Tagesbericht]`, після вечірнього
рану. Його збирає `scripts/server/operator_summary.py` без Claude: лічильники з
трекера, звіт нічного рану з `var/run/operator_report_<дата>.html`, нотатка
вечірнього рану і алерти з черги `var/run/alerts_pending.jsonl`. Уночі оператор
пошти не отримує. Стан у будь-який момент, без листа:

```bash
js scripts/server/operator_summary.py
```

Алерт «немає маркера `MORNING OK`» означає, що агент не дійшов до кінця:
дивитись хвіст лога. «Claude limit reached»: ран вперся в ліміт підписки,
наступний піде за розкладом.

## Бекап

Дані клієнтів у приватному репозиторії, сервер пушить після кожного ранку.
На сервері унікальні лише `/root/.claude-env`, `~/.gmail-mcp` і
`~/.playwright-mcp`. Втрата сервера означає повторні OAuth і логіни в портали
по кожному клієнту, заявки й трекер при цьому цілі.
