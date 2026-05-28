#!/usr/bin/env bash
# Parse TARGETS (JSON array of GitHub org/repo URLs) → output/repos_to_clone.txt

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/tools.sh"

CLONE_LIST="$PROJECT_ROOT/output/repos_to_clone.txt"
mkdir -p "$PROJECT_ROOT/output"
> "$CLONE_LIST"

# TARGETS is a JSON array of strings, each one of:
#   github.com/ORG              → whole org (all non-archived repos)
#   github.com/OWNER/REPO       → single repo
#   ORG                         → whole org (short form)
#   OWNER/REPO                  → single repo (short form)

TARGET_LIST=$(echo "$TARGETS" | python3 -c "
import json, sys
targets = json.load(sys.stdin)
for t in targets:
    t = t.strip().lower()
    t = t.removeprefix('https://')
    t = t.removeprefix('http://')
    t = t.removeprefix('github.com/')
    t = t.rstrip('/')
    print(t)
")

while IFS= read -r target; do
    [[ -z "$target" ]] && continue
    parts=$(echo "$target" | tr '/' '\n' | wc -l | tr -d ' ')
    if [[ "$parts" -ge 2 ]]; then
        # Single repo: owner/repo
        owner=$(echo "$target" | cut -d'/' -f1)
        repo=$(echo "$target" | cut -d'/' -f2)
        log "Target (repo): $owner/$repo"
        echo "$owner/$repo" >> "$CLONE_LIST"
    else
        # Whole org: fetch all non-archived repo names
        log "Target (org): $target — fetching repo list..."
        gh api "/orgs/$target/repos?type=all" --paginate \
            --jq '.[] | select(.archived == false) | .full_name' >> "$CLONE_LIST" 2>/dev/null || {
            warn "Could not fetch repos for org '$target' (private or not found)"
        }
    fi
done <<< "$TARGET_LIST"

sort -u "$CLONE_LIST" -o "$CLONE_LIST"
TOTAL=$(wc -l < "$CLONE_LIST" | tr -d ' ')
log "Repos to clone: $TOTAL → $CLONE_LIST"
