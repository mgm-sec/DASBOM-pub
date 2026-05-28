#!/usr/bin/env bash
# Build visualization-ready graphology JSON from merged SPDX

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/tools.sh"
activate_venv

SPDX="$PROJECT_ROOT/output/sbom/org/$ORG.spdx.json"
PER_REPO="$PROJECT_ROOT/output/sbom/per_repo"
OUT="$PROJECT_ROOT/output/graph/dep_graph.json"
REPOS_DIR="$PROJECT_ROOT/repos"

[[ -f "$SPDX" ]] || { log "ERROR: $SPDX not found — run 05_merge_sbom.sh first (ORG=$ORG)"; exit 1; }

log "Building dependency graph..."
python_venv "$SCRIPT_DIR/python/06_build_graph.py" \
    "$SPDX" \
    "$PER_REPO" \
    "$OUT" \
    "$REPOS_DIR"

log "Done → $OUT"
