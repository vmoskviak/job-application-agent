#!/usr/bin/env bash
# Sync the engine into its public portfolio copy (github.com/vmoskviak/job-application-agent).
#
#     scripts/sync_public.sh [path to the public clone] [--dry-run]
#
# Copies the files with rsync; commits and pushes nothing. After the copy it runs two checks in
# the target and fails if either finds something:
#   1. check_no_private_data.py --strict with the private clients' terms (names, contacts, job ids);
#   2. the same script with --companies against .companies-baseline, the accepted generic hits
#      (first words of tracker companies such as «Stadt» or «Reply»): a term absent from the
#      baseline is either an employer name that leaked into a docstring or a new generic word to
#      accept by adding one line there. The pre-push hook of the public clone runs both as well.
# Then by hand: git status, git diff, commit, push.
#
# Left out of the public copy and why:
#   .git .venv __pycache__ .pytest_cache .ruff_cache   build and tool state
#   .claude/                                            local session settings; the public clone has its own settings.json
#   .superpowers/ docs/superpowers/                     working plans and specs with the history of real clients
#   the files listed in .public-exclude                 employer-specific portal modules, their test, the companies
#                                                       baseline; the list itself stays private
# Protected in the target (never touched): LICENSE, docs/index.md, docs/privacy.md.
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
    echo "target is not a git clone: $TARGET" >&2
    exit 1
fi

EXCLUDES=(
    # /.git without a slash: in a worktree it is a file, in the target a directory that must stay
    --exclude=/.git --exclude=/.venv --exclude=__pycache__/ --exclude=.pytest_cache/
    --exclude=/.claude/ --exclude=/.superpowers/ --exclude=/docs/superpowers/
    --exclude-from="$ENGINE/.public-exclude"
    --exclude=.DS_Store --exclude=.ruff_cache/ --exclude=/var/ --exclude=/tmp/ --exclude=/logs/
    --exclude=/examples/clients/*/var/ --exclude=/examples/clients/*/applications/
)
PROTECT=(
    --filter='P /LICENSE'
    --filter='P /docs/index.md' --filter='P /docs/privacy.md' --filter='P /.claude/'
    --filter='P /.git'
)

# ${DRY[@]+...}: bash 3.2 on macOS fails on an empty array under set -u
rsync -a --delete --itemize-changes ${DRY[@]+"${DRY[@]}"} "${EXCLUDES[@]}" "${PROTECT[@]}" "$ENGINE/" "$TARGET/" \
    | sed 's/^/  /'
echo "== synced into $TARGET${DRY[@]+ (dry-run)}"

if [[ ${#DRY[@]} -eq 0 ]]; then
    PY="${PYTHON:-$ENGINE/.venv/bin/python}"
    [[ -x "$PY" ]] || PY=python3
    # The check resolves the clients folder relative to the repository it runs in; in the public
    # clone that folder does not exist and the check would silently shrink to its structural part.
    export JOBSEARCH_CLIENTS_DIR="${JOBSEARCH_CLIENTS_DIR:-$ENGINE/../jobsearch-clients/clients}"
    (cd "$TARGET" && "$PY" scripts/check_no_private_data.py --strict)
    echo "== check_no_private_data --strict in the public copy (client terms from $JOBSEARCH_CLIENTS_DIR): clean"
    (cd "$TARGET" && "$PY" scripts/check_no_private_data.py --companies --baseline "$ENGINE/.companies-baseline")
    echo "== companies against $ENGINE/.companies-baseline: no new terms"
    echo
    git -C "$TARGET" status --short | head -40
fi

cat <<EOF

Next, by hand, in $TARGET:
  git status && git diff            # review what changed
  git add -A && git commit           # commit with a meaningful message
  git push                           # only after reviewing the diff
EOF
