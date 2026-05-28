#!/usr/bin/env bash
# Merge per-repo SBOMs into org-level SPDX + CycloneDX

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/tools.sh"
activate_venv

PER_REPO="$PROJECT_ROOT/output/sbom/per_repo"
ORG_DIR="$PROJECT_ROOT/output/sbom/org"
mkdir -p "$ORG_DIR"

log "Merging SPDX files..."
python_venv "$SCRIPT_DIR/python/05_merge_spdx.py" \
    "$PER_REPO" \
    "$ORG_DIR/$ORG.spdx.json"

log "Merging CycloneDX files..."
python_venv "$SCRIPT_DIR/python/05_merge_cdx.py" \
    "$PER_REPO" \
    "$ORG_DIR/$ORG.cdx.json"

log "Done. Org-level SBOMs in $ORG_DIR"
