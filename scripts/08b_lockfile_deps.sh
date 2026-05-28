#!/usr/bin/env bash
# Parse lock files (poetry.lock, package-lock.json, yarn.lock, Cargo.lock,
# composer.lock) from cloned repos and inject high-confidence dep edges into
# dep_graph.json.  Run AFTER 06_build_graph.sh, BEFORE 08_enrich_deps.sh.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/tools.sh"
activate_venv

GRAPH="$PROJECT_ROOT/output/graph/dep_graph.json"
REPOS_DIR="$PROJECT_ROOT/repos"

if [[ ! -f "$GRAPH" ]]; then
    die "dep_graph.json not found — run 06_build_graph.sh first"
fi

log "=== Lock file dep enrichment ==="
log "Graph:    $GRAPH"
log "Repos:    $REPOS_DIR"

python_venv "$SCRIPT_DIR/python/08b_lockfile_deps.py" "$GRAPH" "$REPOS_DIR"

log "Done"
