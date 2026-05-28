#!/usr/bin/env bash
# Run syft on each cloned repo → SPDX JSON + CycloneDX JSON

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/tools.sh"

ensure_brew syft

REPOS_DIR="$PROJECT_ROOT/repos"
OUT_DIR="$PROJECT_ROOT/output/sbom/per_repo"
ERROR_LOG="$PROJECT_ROOT/output/sbom_errors.log"
mkdir -p "$OUT_DIR" "$PROJECT_ROOT/output"
: > "$ERROR_LOG"

REPOS=$(ls -d "$REPOS_DIR"/*/ 2>/dev/null | xargs -I{} basename {})
TOTAL=$(echo "$REPOS" | wc -l | tr -d ' ')
log "$TOTAL repos to scan (4 parallel)"

scan_repo() {
    local name="$1"
    local src="$REPOS_DIR/$name"
    local spdx_out="$OUT_DIR/${name}.spdx.json"
    local cdx_out="$OUT_DIR/${name}.cdx.json"

    if [[ ! -d "$src" ]]; then return 0; fi

    syft scan "$src" \
        -o "spdx-json=$spdx_out" \
        -o "cyclonedx-json=$cdx_out" \
        --quiet 2>/dev/null || {
        echo "$name: syft failed" >> "$ERROR_LOG"
        return 0
    }
    echo "OK $name"
}

export -f scan_repo
export REPOS_DIR OUT_DIR ERROR_LOG PROJECT_ROOT

echo "$REPOS" | xargs -P 4 -I{} bash -c 'scan_repo "$@"' _ {}

DONE=$(ls "$OUT_DIR"/*.spdx.json 2>/dev/null | wc -l | tr -d ' ')
ERRORS=$(wc -l < "$ERROR_LOG" | tr -d ' ')
log "Done: $DONE SBOMs generated | $ERRORS errors → $ERROR_LOG"
