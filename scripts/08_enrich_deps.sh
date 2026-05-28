#!/usr/bin/env bash
# Enrich dep_graph.json with pkg→pkg edges from npm/PyPI/gem/cargo registries.
# Packages without CDX dep data are queried from their registry.
# Results cached in output/cache/deps/

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/tools.sh"

GRAPH="$PROJECT_ROOT/output/graph/dep_graph.json"

if [[ ! -f "$GRAPH" ]]; then
    die "dep_graph.json not found — run 06_build_graph.sh first"
fi

log "=== Enriching dependency edges from registries ==="
log "Graph: $GRAPH"
log "Cache: $PROJECT_ROOT/output/cache/deps/"

python_venv "$SCRIPT_DIR/python/08_enrich_deps.py" "$GRAPH"

log "Done"
