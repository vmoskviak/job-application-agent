#!/usr/bin/env bash
# Синхронізація рушія в публічну портфоліо-копію (github.com/vmoskviak/job-application-agent).
#
#     scripts/sync_public.sh [шлях до публічного клону] [--dry-run]
#
# Копіює файли rsync-ом, нічого не комітить і не пушить. Після копіювання
# ганяє check_no_private_data.py --strict у цільовій папці й падає, якщо
# там лишились чужі контакти. Далі руками: git status, git diff, commit, push.
#
# Що в публічну копію не йде і чому:
#   .git .venv __pycache__ .pytest_cache   службове
#   .claude/                               локальні налаштування сесій; у публічному клоні свій settings.json
#   .superpowers/ docs/superpowers/        робочі плани й спеки з історією реальних клієнтів
#   bot/apply/check24.py bot/apply/eismann.py  модулі під сайти конкретних роботодавців (реальні заявки)
#   tests/test_apply_employer_portals.py   їхні тести (URL із тими самими хостами)
# У цілі не чіпаються (protect): LICENSE, docs/index.md, docs/privacy.md.
set -euo pipefail

ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${HOME}/Projects/public/jobsearch-autopilot"
DRY=()
for arg in "$@"; do
    case "$arg" in
        --dry-run|-n) DRY=(--dry-run) ;;
        *) TARGET="$arg" ;;
    esac
done

if [[ ! -d "$TARGET/.git" ]]; then
    echo "ціль не git-клон: $TARGET" >&2
    exit 1
fi

EXCLUDES=(
    # /.git без слеша: у worktree це файл, а в цілі каталог, і той не можна чіпати
    --exclude=/.git --exclude=/.venv --exclude=__pycache__/ --exclude=.pytest_cache/
    --exclude=/.claude/ --exclude=/.superpowers/ --exclude=/docs/superpowers/
    --exclude=/bot/apply/check24.py --exclude=/bot/apply/eismann.py
    --exclude=/tests/test_apply_employer_portals.py
    --exclude=.DS_Store --exclude=/var/ --exclude=/tmp/ --exclude=/logs/
    --exclude=/examples/clients/*/var/ --exclude=/examples/clients/*/applications/
)
PROTECT=(
    --filter='P /LICENSE'
    --filter='P /docs/index.md' --filter='P /docs/privacy.md' --filter='P /.claude/'
    --filter='P /.git'
)

# ${DRY[@]+...}: bash 3.2 на macOS під set -u падає на порожньому масиві
rsync -a --delete --itemize-changes ${DRY[@]+"${DRY[@]}"} "${EXCLUDES[@]}" "${PROTECT[@]}" "$ENGINE/" "$TARGET/" \
    | sed 's/^/  /'
echo "== синхронізовано в $TARGET${DRY[@]+ (dry-run)}"

if [[ ${#DRY[@]} -eq 0 ]]; then
    PY="${PYTHON:-$ENGINE/.venv/bin/python}"
    [[ -x "$PY" ]] || PY=python3
    (cd "$TARGET" && "$PY" scripts/check_no_private_data.py --strict)
    echo "== check_no_private_data --strict у публічній копії: чисто"
    echo
    git -C "$TARGET" status --short | head -40
fi

cat <<EOF

Далі руками, в $TARGET:
  git status && git diff            # переглянути, що змінилось
  git add -A && git commit           # закомітити з осмисленим повідомленням
  git push                           # лише після перегляду diff
EOF
