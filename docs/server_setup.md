# Сервер: встановлення й runbook

Розраховано на Debian 13 у LXC-контейнері (4 vCPU, 8 GB RAM, 32 GB диск),
усе під root. Часовий пояс `Europe/Berlin`.

## Встановлення

Коротка англійська версія для стороннього користувача: `docs/quickstart.md`;
`scripts/server/install.sh` робить пакети, venv, Playwright, Claude Code і юніти.

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
JOBSEARCH_VNC_HINT='vnc://localhost:5901 (спершу ssh -f -N -L 5901:127.0.0.1:5900 <server>)'   # куди дивитись: re-login, hand-off картки воркера
# CLAUDE_MODEL=opus
# JOBSEARCH_TELEGRAM_TOKEN_<SLUG>=...   токен Telegram-бота клієнта (етап 2a); chat_id в accounts.yaml
# JOBSEARCH_LOCK_WAIT=900               скільки run_agent.sh чекає на лок машини, с
# JOBSEARCH_INBOX_DRY_RUN=1             тіньовий тиждень inbox watcher-а: без трекера й пошти
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
x11vnc -display :99 -rfbauth /root/.vnc/passwd -localhost -forever &   # пароль: x11vnc -storepasswd
cd /opt/jobsearch-autopilot
DISPLAY=:99 JOBSEARCH_CLIENT=<slug> bin/js scripts/relogin.py
# з Mac: ssh -f -N -L 5901:127.0.0.1:5900 <server>; open vnc://localhost:5901, залогінитись у вікні браузера,
# потім Enter у терміналі (з --wait-for <domain> Enter не потрібен: скрипт сам дочекається входу)
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

## Telegram-бот і inbox watcher (етап 2a)

Що робить: погодинний прохід по пошті контрактного клієнта (відповіді на подачі,
листи платформ) оновлює трекер, шле оператору картки з чернетками в Telegram і
відправляє follow-ups; бот приймає кнопки (надіслати чернетку, пропустити, пауза).
Спека: `docs/superpowers/specs/2026-10-22-contract-phase2-server-automation-design.md`.

Разові кроки оператора (нічого з цього не автоматизовано):

1. BotFather → новий бот → токен у `/root/.claude-env`:
   `export JOBSEARCH_TELEGRAM_TOKEN_<SLUG>=...` (SLUG великими; інше ім'я
   змінної можна задати в `accounts.yaml → telegram.token_env`).
2. Юніти й сервіс бота:
   ```bash
   cp /opt/jobsearch-autopilot/scripts/server/systemd/*.service /opt/jobsearch-autopilot/scripts/server/systemd/*.timer /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable --now jobsearch-telegram@<slug>
   ```
3. Написати боту `/start`: без `chat_id` він відповідає лише своїм id. Вписати
   його в `accounts.yaml → telegram.chat_id`, запушити дані, на сервері
   `git -C /opt/jobsearch-clients pull` і `systemctl restart jobsearch-telegram@<slug>`.
   Інші чати бот ігнорує мовчки. Зупиняти бота лише через `systemctl stop`:
   `Restart=always` піднімає його знову за 30 с.
4. Тіньовий тиждень (спека §12): у `/root/.claude-env` рядок
   `export JOBSEARCH_INBOX_DRY_RUN_<SLUG>=1` (без суфікса гасить усіх клієнтів машини), потім `systemctl enable --now jobsearch-inbox@<slug>.timer`.
   У цьому режимі трекер і пошта не чіпаються, картки лягають у
   `<clients>/<slug>/var/state/cards_dry.jsonl`. Через тиждень прибрати рядок:
   наступний прохід живий. Юніт не редагувати: у oneshot-юніті дописаний
   `ExecStart=` з drop-in не замінює перший, а додає другий запуск.
5. Перевірка: `journalctl -u jobsearch-inbox@<slug> -n 50`, `journalctl -u jobsearch-telegram@<slug> -f`,
   стан у `var/state/` (`cards.jsonl`, `inbox_seen.json`, `inbox_seen_dry.json`, `queue.jsonl`,
   `pause`, `last_watch`), рядок `INBOX OK: …` наприкінці кожного проходу. Ручний прохід перед
   увімкненням таймера (перевіряє, що `claude` є в PATH сервісу й читає `cards_dry.jsonl`):
   `/opt/jobsearch-autopilot/scripts/server/run_service.sh scripts/inbox_watch.py <slug> --days 2 --dry-run`.

Увесь прохід watcher-а йде під тим самим локом машини, що й нічні рани (зайнятий лок
понад `JOBSEARCH_LOCK_WAIT` с означає пропуск цієї години), а `run_agent.sh` тепер чекає
на лок замість пропуску рану. Чернетки пише `claude -p` без MCP-серверів і вбудованих
інструментів. Перед проходом дані підтягуються (`git pull --ff-only`), після нього
`tracker.md` комітиться й пушиться; збій будь-якого з цих кроків і збій читання пошти
стають алертом у вечірньому зведеному листі. Пауза з бота (`/pause`) зупиняє лише
відправку, картки приходять далі.

### Сповіщення для клієнтів з ранковим агентом (режим notify)

Клієнти ринку `de_employment` (трекер веде `claude -p` уночі) отримують той самий
watcher у режимі лише сповіщень: `accounts.yaml → telegram.mode: notify`. Трекер,
чернетки, follow-up і лок машини не чіпаються; про співбесіду, запит документів чи
невідомий лист від компанії з трекера оператор дізнається у ту ж годину, з уривком
листа і кнопкою в Gmail. Після кожного нічного рану `notify_run.py` окремо шле
рядки трекера, що стали `interview*`/`offer*`/`action_needed`, а `send_alert.py`
шле алерти в Telegram замість черги листа. Зведений лист (`operator_summary.py`)
при налаштованому Telegram не йде взагалі. Привід: 28.10.2026 ранковий ран о 03:11
записав телефонну співбесіду на 17:30 того ж дня, а оператор дізнався б о 20:40.

Один бот на всіх таких клієнтів, чат оператора, текст з іменем клієнта попереду:

```bash
# /root/.claude-env: той самий токен під спільним ім'ям
export JOBSEARCH_TELEGRAM_TOKEN_OPERATOR="$JOBSEARCH_TELEGRAM_TOKEN_<SLUG-з-ботом>"
```
```yaml
# accounts.yaml клієнта
telegram:
  token_env: JOBSEARCH_TELEGRAM_TOKEN_OPERATOR
  chat_id: <chat_id оператора>
  mode: notify
  lang: uk
```
```bash
systemctl enable --now jobsearch-inbox@<slug>.timer
```

`jobsearch-telegram@<slug>` для таких клієнтів НЕ вмикати: long polling на один
токен може тримати лише один процес, і це бот клієнта з `mode: cards`. Ручна
перевірка: `run_service.sh scripts/inbox_watch.py <slug> --days 2` → рядок
`INBOX OK: notified=…`. Перший прохід бачить останні `--days` днів цілком.

## Подачі без тапу: черга й диспетчер (етап 2b)

Що робить: вечірній ран більше не шле дайджест, а ставить відібрані ліди в
чергу `var/state/queue.jsonl` (`scripts/enqueue_leads.py`: канал подачі без
логіну LinkedIn, пакет `var/run/packages_<дата>/<slug>/`, перевірки перед
відправкою). Диспетчер (`scripts/dispatch_leads.py`, таймер щопівгодини вдень)
шле email-ліди сам у межах `targeting.yaml → daily_limits`, пише рядок у трекер
і квитанцію в Telegram. Заблокований лід лежить `held` з причинами і видно в
`/status`; ліди форм подає браузерний воркер (етап 2c, нижче), платформи з
логіном лишаються `queued`. Спека: та сама, §15.

Разові кроки оператора, у цьому порядку:

1. У репозиторії даних: поточні PDF резюме в `clients/<slug>/attachments/` під
   назвами з `tracks.yaml → tracks.<трек>.cv_file` і `targeting.yaml →
   agency_attachments`; `profile.yaml → report_mode: none` (без Tagesbericht,
   лише лист «Hinweis» при збоях); `targeting.yaml → send_policy: auto`,
   `daily_limits: {email: 5, form: 5}`. Закомітити, запушити, на сервері
   `git -C /opt/jobsearch-clients pull`.
2. Юніти й тіньовий тиждень:
   ```bash
   cp /opt/jobsearch-autopilot/scripts/server/systemd/jobsearch-dispatch@.* /etc/systemd/system/
   systemctl daemon-reload
   echo 'export JOBSEARCH_DISPATCH_DRY_RUN=1' >> /root/.claude-env
   systemctl enable --now jobsearch-dispatch@<slug>.timer
   systemctl restart jobsearch-telegram@<slug>
   ```
3. Після першого вечірнього рану: `cat var/state/queue.jsonl` і
   `cat var/run/packages_<дата>/*/meta.json`; після першого проходу таймера:
   `cat var/state/queue_dry.jsonl` (що пішло б) і `journalctl -u jobsearch-dispatch@<slug> -n 30`.
   Ручний прохід: `/opt/jobsearch-autopilot/scripts/server/run_service.sh scripts/dispatch_leads.py <slug> --dry-run`.
4. Кінець тіньового тижня: прибрати рядок `JOBSEARCH_DISPATCH_DRY_RUN` з
   `/root/.claude-env`; наступний прохід таймера шле.

Диспетчер не бере лок машини (ні LLM, ні дисплея). Перед проходом `git pull
--ff-only` даних, після живої подачі `tracker.md` комітиться й пушиться.
`/pause` з бота зупиняє і його.

## Браузерний воркер: ATS-форми без логіну (етап 2c)

Що робить: `scripts/apply_leads.py` (таймер щогодини вдень о :40, останній
старт 19:40) бере з `var/state/queue.jsonl` queued ліди каналу `form` (Ashby,
Greenhouse, Lever, Workable без логіну) у межах `daily_limits.form` і подає їх
у видимому Chromium на дисплеї сервера `:99`, у профілі Playwright клієнта.
Поля заповнюються з `profile.yaml` (`links`, `form_answers`), вільний текст
пише модель, резюме береться з пакета (файл на `CV…`, інакше перше вкладення).
Подано: рядок у трекері й квитанція в Telegram. Вакансія закрита: `held`.
Все, що воркер не довів до підтвердження (обов'язкове поле без відповіді,
reCAPTCHA, помилки валідації, тиша після Submit), стає hand-off: вкладка
лишається відкритою, в Telegram приходить картка. Платформи з логіном і Easy
Apply лишаються `queued` до наступного етапу. Воркер бере лок машини лише на
час заповнення форми; поки чекає оператора, лок вільний, а профіль браузера
зайнятий. Спека: та сама, §7 (крок 2c) і §15.

Разові кроки оператора, у цьому порядку:

1. Xvfb `:99` і x11vnc уже працюють (див. «Re-login у портали»). x11vnc
   слухає лише localhost і з паролем (`-localhost -rfbauth /root/.vnc/passwd`),
   тому ззовні до дисплея лише через SSH-тунель. Підказка для hand-off картки в
   `/root/.claude-env`:
   ```bash
   export JOBSEARCH_VNC_HINT='vnc://localhost:5901 (спершу ssh -f -N -L 5901:127.0.0.1:5900 <server>)'
   ```
   Без неї картка не каже, куди дивитись. У `profile.yaml` клієнта заповнені
   `links` і `form_answers`, бо без них більшість форм іде в hand-off.
2. Юніти й тіньовий прохід:
   ```bash
   cp /opt/jobsearch-autopilot/scripts/server/systemd/jobsearch-apply@.* /etc/systemd/system/
   systemctl daemon-reload
   echo 'export JOBSEARCH_APPLY_DRY_RUN=1' >> /root/.claude-env
   systemctl enable --now jobsearch-apply@<slug>.timer
   systemctl restart jobsearch-telegram@<slug>
   ```
   Рестарт бота потрібен, щоб кнопки hand-off картки закривали й лід у черзі.
3. Перший тіньовий прохід руками, коли в черзі є form-лід:
   ```bash
   DISPLAY=:99 /opt/jobsearch-autopilot/scripts/server/run_service.sh scripts/apply_leads.py <slug> --dry-run
   ```
   Форма заповнюється без Submit. Подивитись скріншот
   `var/run/packages_<дата>/<slug>/working/apply_form_filled.png` (тут `<slug>`
   це папка ліда в пакетах) і рядок у `var/state/queue_dry.jsonl` (`result`:
   статус, причина hand-off, заповнені поля). Тіньовий режим не заповнює той
   самий лід двічі за день. Лог таймера: `journalctl -u jobsearch-apply@<slug> -n 30`.
4. Живий режим: прибрати рядок `JOBSEARCH_APPLY_DRY_RUN` з `/root/.claude-env`
   (на деплої 2d змінна прибирається в будь-якому разі, крок 2 розділу про
   етап 2d; відтоді тіньовий режим лише за платформою в `targeting.yaml`);
   наступний прохід таймера подає. `send_policy`, відмінний від `auto`, і далі
   тримає воркер у тіньовому режимі.
5. Hand-off. Картка називає компанію, роль, причину, URL і VNC-підказку. З Mac:
   ```bash
   ssh -f -N -L 5901:127.0.0.1:5900 <server>
   open vnc://localhost:5901
   ```
   У вікні Chromium доробити форму й натиснути Submit. Вкладка чекає до 19:55
   (`JOBSEARCH_HANDOFF_UNTIL=HH:MM` у `/root/.claude-env` змінює дедлайн). Якщо
   сторінка показала підтвердження, воркер сам закриває лід як поданий.
   Кнопка «Подано» на картці закриває лід як `sent` з рядком у трекері,
   «Пропустити» кладе його в `held` (`operator_skipped`). Без відповіді до
   дедлайну лід стає `failed`, картка `expired`. Один hand-off на прохід: поки
   він триває, решта форм чекає, а наступний тік таймера не стартує.

За 10 хв до дедлайну (з 19:45) воркер не починає нової живої подачі, решта
черги лишається `queued` до наступного дня (у журналі один рядок про це);
hand-off, що почався раніше, однаково чекає до 19:55.
Перед проходом `git pull --ff-only` даних, після живої подачі `tracker.md`
комітиться й пушиться. Без `DISPLAY` чи з профілем браузера, який тримає інший
процес (`scripts/relogin.py`, MCP), прохід виходить з 0 і чергу не чіпає;
профіль береться лише тоді, коли в черзі є form-ліди. Без токена бота чи
`chat_id` воркер працює лише в тіньовому режимі: hand-off картка не дійшла б.
Пакет без вкладень лягає в `held` (`attachment_none`). Збій лока машини йде
алертом «Browser-Worker: …» у зведений лист. `/pause` з бота зупиняє і воркер,
`systemctl stop` закриває браузер і віддає профіль.

## Платформи з логіном: LinkedIn, freelancermap, GULP (етап 2d)

Що працює: той самий `jobsearch-apply@<slug>.timer` (щогодини вдень о :40, останній
старт 19:40), нових юнітів нема. Крім каналу `form` прохід бере `linkedin_offsite`,
`linkedin_easy` і `platform_login` (freelancermap, GULP). Порядок у проході:
спершу `linkedin_offsite` (дешева резолюція зовнішнього посилання, лід стає `form`
з ATS за хостом і того ж проходу подається як form), далі `form`, `linkedin_easy`,
`platform_login`. Резолюція нічого не подає і не рахується в ліміти.

Вимикачі й ліміти в `targeting.yaml` клієнта:
```yaml
daily_limits: {email: 5, form: 5, linkedin: 5, freelancermap: 2, gulp: 3}
platforms: {linkedin: on, freelancermap: shadow, gulp: shadow, freelance_de: off}
```
`on` подає, `shadow` заповнює без Submit (рядок у `var/state/queue_dry.jsonl`,
скріншот у пакеті), `off` пропускає лід не чіпаючи. Платформа без ключа вимкнена.
Окрім денного ліміту: не більше 2 подач LinkedIn за прохід і пауза 180 с після
кожної живої спроби на платформі. `freelance_de` вимкнено, бо Basic-акаунт не
може відгукуватись.

Тимчасові записи лежать у `var/state/platforms.json`. Правило пріоритету:
`targeting.yaml` це оголошений постійний стан. `/platform <name> off|shadow`
пише запис до кінця дня; `/platform <name> on` лише стирає запис дня і не
піднімає оголошені `off` чи `shadow` (бот дописує «declared … in targeting.yaml»).
`/platform` без аргументів показує рядок на платформу: стан і подано сьогодні /
ліміт. Постійно перемикати лише правкою `targeting.yaml` у data-репо.

Картки без кнопок:
- `login_required`: сесія злетіла. Лід лишається `queued`, платформа вимкнена до
  кінця дня, одна картка на платформу за прохід. Зайти по VNC (тунель і
  `x11vnc` як у «Re-login у портали»), у вікні браузера залогінитись (з
  `--wait-for` Enter у терміналі не потрібен: скрипт сам бачить вхід і
  виходить), далі `/platform <platform> on`:
  ```bash
  cd /opt/jobsearch-autopilot
  DISPLAY=:99 JOBSEARCH_CLIENT=<slug> bin/js scripts/relogin.py <login-url> --wait-for <domain>
  ```
  Адреси входу: LinkedIn `https://www.linkedin.com/login` (`--wait-for linkedin.com`),
  freelancermap `https://www.freelancermap.de/login` (`--wait-for freelancermap.de`),
  GULP `https://www.gulp.de/` і там «Anmelden» (`--wait-for gulp.de`).
- `restricted`: платформа обмежила акаунт; вимкнено до кінця дня, перевірити
  акаунт руками, далі `/platform <platform> on`.
- `platform_quota`: місячна квота вичерпана (freelancermap), лід `held`,
  платформа вимкнена до 1-го числа.

**Порядок деплою не можна міняти: спершу data-репо, потім рушій.** Якщо рушій
оновився, а data-репо ще не має ключа `platforms`, кожен LinkedIn-лід і лід
іншої платформи ляже в `held` з причиною `platform_off`. Відновлення: додати
`platforms` у data-репо й для кожної дати прогнати `--rechannel` (крок 4).
Перед деплоєм переконатись, що data-репо з `platforms` запушене з Mac.

Кроки оператора, у цьому порядку (на сервері не міняється нічого без його слова;
слово на прибирання `JOBSEARCH_APPLY_DRY_RUN` і на LinkedIn першим уже дане):

1. Підтягнути репозиторії, спершу дані:
   ```bash
   cd /opt/jobsearch-clients && git pull
   cd /opt/jobsearch-autopilot && git pull
   ```
2. Прибрати тіньовий прапорець 2c (з цього моменту тіньовий режим задають лише
   платформи в `targeting.yaml`):
   ```bash
   cp /root/.claude-env /root/.claude-env.bak-2026-10-28
   sed -i '/JOBSEARCH_APPLY_DRY_RUN/d' /root/.claude-env
   grep -c JOBSEARCH_APPLY_DRY_RUN /root/.claude-env   # має бути 0
   ```
3. Рестарт бота (нова команда `/platform`):
   ```bash
   systemctl restart jobsearch-telegram@<slug>
   ```
4. Перекласти утримані LinkedIn-ліди в нові канали, для кожної дати, коли були
   такі ліди (тут 26 і 27 жовтня):
   ```bash
   cd /opt/jobsearch-autopilot
   scripts/server/run_service.sh scripts/enqueue_leads.py <slug> --date 2026-10-26 --rechannel
   scripts/server/run_service.sh scripts/enqueue_leads.py <slug> --date 2026-10-27 --rechannel
   ```
   Записи `failed` `--rechannel` не чіпає (такий лід міг уже піти: процес упав
   посеред форми чи hand-off протух); їхні id у зведенні під `skipped_failed`,
   розібрати руками.
5. Перший прохід руками в денному вікні, коли профіль браузера вільний (не йде
   `relogin.py`, MCP, чужий hand-off):
   ```bash
   DISPLAY=:99 /opt/jobsearch-autopilot/scripts/server/run_service.sh scripts/apply_leads.py <slug>
   ```
   Прочитати рядок `APPLY OK: {...}`. Очікування: `resolved` для offsite-лідів,
   `sent` для LinkedIn, якщо лід чистий, рядки в `queue_dry.jsonl` для
   freelancermap і GULP (вони в `shadow`).
6. Перевірити тіньові скріншоти
   `var/run/packages_<дата>/<папка-ліда>/working/apply_form_filled.png` за
   чеклістом нижче. Коли все правильно, перевести платформу в `on`: `/platform
   freelancermap on` на оголошений `shadow` не діє, тож змінити в data-репо
   `platforms: {..., freelancermap: on, ...}`, закомітити й запушити з Mac;
   наступний прохід підхопить через `pull_data`. Те саме для GULP, окремо.
7. Де дивитись:
   ```bash
   journalctl -u jobsearch-apply@<slug> -n 50
   cat /opt/jobsearch-clients/clients/<slug>/var/state/platforms.json
   ```
   і `/status`, `/platform` у Telegram.

### Чеклист першого живого проходу

Живцем не перевірено, дивитись очима, не довіряти зеленому статусу:

- LinkedIn: у модалці Easy Apply вибраний саме щойно завантажений файл резюме
  (radio з `aria-label` = ім'я файлу, а не збережене резюме; інакше
  `resume_not_selected`); прапорець Follow компанії вимкнений (інакше
  `follow_still_checked`); текст підтвердження після Submit збігся, а не впав у
  `handoff` через тишу. У черзі: `sent` лише з рядком у трекері й квитанцією.
  Відповіді на питання про роки досвіду беруться з `experience_years` у
  `profile.yaml`; оператор підтверджує числа. Перша подача краща на лід, де
  Follow видно на скріншоті.
- freelancermap: після `set_input_files` код перевіряє лише `files.length`;
  відрендерений список вкладень (DOM не захоплено) оцінити очима на
  скріншоті: CV і Projektliste на місці. Лічильник «Restliche Bewerbungen für
  diesen Monat: n/total» до і після першої живої подачі зменшився на одиницю.
  Текст підтвердження живцем не бачено: невідомий текст буде `handoff`, не `sent`.
- GULP: dry run файл CV не ставить, тож на тіньовому скріншоті поле CV порожнє,
  і це норма. Ім'я файлу CV в iframe з'являється лише при живому проході; без
  нього Submit не натискається (`handoff cv_upload_unconfirmed`). Текст
  підтвердження не бачено ніколи (кнопку не натискали), тому перша жива подача
  майже напевно піде в `handoff`: оператор доробляє й тисне «Подано».
- LinkedIn: якщо перший результат `handoff no_easy_apply` або `held no_apply_link`,
  підозрюй очікування сторінки, не лід: SPA під логіном могла не встигнути
  відрендерити кнопку Apply за 15 с. Відкрити вакансію по VNC і подивитись.
- Усі платформи: рядок у `queue.jsonl` має ключ `platform`, `/platform` показує
  лічильник «подано сьогодні / ліміт», вкладки hand-off не зависли на профілі
  (наступний прохід не стартує, поки вона відкрита).

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
наступний піде за розкладом. «Engine-Pull fehlgeschlagen» або «Datenpull
fehlgeschlagen»: репозиторій не підтягнувся, ран пройшов на локальному стані
(трекери й конфіги могли бути застарілі); у листі `git status --short` того
репозиторію. Типова причина: той самий файл створено і на сервері, і на Mac.
Звірити `cmp` з `git show origin/main:<шлях>`, однакові файли прибрати в stash,
далі `git pull --rebase`. Серверні зміни, що відрізняються, не викидати.

## Бекап

Дані клієнтів у приватному репозиторії, сервер пушить після кожного ранку.
На сервері унікальні лише `/root/.claude-env`, `~/.gmail-mcp` і
`~/.playwright-mcp`. Втрата сервера означає повторні OAuth і логіни в портали
по кожному клієнту, заявки й трекер при цьому цілі.
