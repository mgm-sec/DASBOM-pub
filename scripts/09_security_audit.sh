#!/usr/bin/env bash
# Batch security audit for all packages:
#   - CVE data from OSV.dev (batch API)
#   - Latest version from registries (parallel)
# Output: output/security_audit.json

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/tools.sh"

GRAPH="$PROJECT_ROOT/output/graph/dep_graph.json"
AUDIT="$PROJECT_ROOT/output/security_audit.json"

if [[ ! -f "$GRAPH" ]]; then
    die "dep_graph.json not found — run 06_build_graph.sh first"
fi

log "=== Security Audit ==="
log "Graph:  $GRAPH"
log "Output: $AUDIT"

python_venv "$SCRIPT_DIR/python/09_security_audit.py" "$GRAPH" "$AUDIT"

log "Done → $AUDIT"
