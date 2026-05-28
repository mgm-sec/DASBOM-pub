#!/usr/bin/env bash
# Clone or update repos — supports both ORG mode and explicit repos_to_clone.txt

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/tools.sh"

REPOS_DIR="$PROJECT_ROOT/repos"
ERROR_LOG="$PROJECT_ROOT/output/clone_errors.log"
CLONE_LIST="$PROJECT_ROOT/output/repos_to_clone.txt"
mkdir -p "$REPOS_DIR" "$PROJECT_ROOT/output"
: > "$ERROR_LOG"

# Clone or update by full name (owner/repo)
clone_or_pull_full() {
    local full_name="$1"
    local name="${full_name##*/}"
    local dest="$REPOS_DIR/$name"

    if [[ -d "$dest/.git" ]]; then
        git -C "$dest" fetch --quiet origin 2>/dev/null || true
        git -C "$dest" reset --hard "origin/$(git -C "$dest" rev-parse --abbrev-ref HEAD)" --quiet 2>/dev/null || true
    else
        gh repo clone "$full_name" "$dest" -- --quiet 2>/dev/null || {
            echo "$full_name: clone failed" >> "$ERROR_LOG"
            return 0
        }
    fi
    echo "OK $name"
}
export -f clone_or_pull_full
export REPOS_DIR ERROR_LOG

if [[ -f "$CLONE_LIST" && -s "$CLONE_LIST" ]]; then
    # Docker / multi-target mode: use pre-parsed list
    TOTAL=$(wc -l < "$CLONE_LIST" | tr -d ' ')
    log "$TOTAL repos to clone/update (8 parallel)"
    cat "$CLONE_LIST" | xargs -P 8 -I{} bash -c 'clone_or_pull_full "$@"' _ {}
else
    # Fallback: whole-org mode via ORG env var
    log "Fetching non-archived repo list for org: $ORG"
    REPO_NAMES=$(gh api "/orgs/$ORG/repos?type=all" --paginate \
        --jq '.[] | select(.archived == false) | .full_name')
    TOTAL=$(echo "$REPO_NAMES" | wc -l | tr -d ' ')
    log "$TOTAL active repos to clone/update (8 parallel)"

    export ORG
    echo "$REPO_NAMES" | xargs -P 8 -I{} bash -c 'clone_or_pull_full "$@"' _ {}
fi

CLONED=$(ls -d "$REPOS_DIR"/*/ 2>/dev/null | wc -l | tr -d ' ')
ERRORS=$(wc -l < "$ERROR_LOG" | tr -d ' ')
log "Done: $CLONED repos in $REPOS_DIR | $ERRORS errors"
