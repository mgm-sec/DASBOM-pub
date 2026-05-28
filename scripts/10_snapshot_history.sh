#!/usr/bin/env bash
# Append today's stats to output/history.json (rolling 365-snapshot window).
# Run after 09_security_audit.sh.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/tools.sh"
activate_venv

AUDIT="$PROJECT_ROOT/output/security_audit.json"
GRAPH="$PROJECT_ROOT/output/graph/dep_graph.json"
HISTORY="$PROJECT_ROOT/output/history.json"

[[ -f "$AUDIT" ]] || die "security_audit.json not found — run 09_security_audit.sh first"

log "=== Snapshot history ==="
python_venv "$SCRIPT_DIR/python/10_snapshot_history.py" "$AUDIT" "$GRAPH" "$HISTORY"
log "Done → $HISTORY"
