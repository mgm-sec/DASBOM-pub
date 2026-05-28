#!/usr/bin/env bash
# Download and vendor visualization JS libraries (offline use)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/tools.sh"

LIB_DIR="$PROJECT_ROOT/output/viz/lib"
mkdir -p "$LIB_DIR"

download_if_missing() {
    local url="$1" dest="$2" optional="${3:-}"
    if [[ ! -f "$dest" ]]; then
        log "Downloading $(basename "$dest")..."
        curl -fsSL "$url" -o "$dest" || { [[ "$optional" == "optional" ]] && { log "  (optional, skipping)"; return 0; } || die "Failed to download $url"; }
    else
        log "$(basename "$dest") already present"
    fi
}

# graphology UMD build
download_if_missing \
    "https://cdn.jsdelivr.net/npm/graphology@0.25.4/dist/graphology.umd.min.js" \
    "$LIB_DIR/graphology.umd.min.js"

# sigma.js v2 (not v3 — stable API)
download_if_missing \
    "https://cdn.jsdelivr.net/npm/sigma@2.4.0/build/sigma.min.js" \
    "$LIB_DIR/sigma.min.js"

# graphology forceatlas2 layout (optional — not used in pre-computed layout)
download_if_missing \
    "https://cdn.jsdelivr.net/npm/graphology-layout-forceatlas2@0.10.1/dist/graphology-layout-forceatlas2.min.js" \
    "$LIB_DIR/graphology-layout-forceatlas2.min.js" optional

# graphology-communities-louvain for clustering (optional)
download_if_missing \
    "https://cdn.jsdelivr.net/npm/graphology-communities-louvain@2.0.1/dist/graphology-communities-louvain.min.js" \
    "$LIB_DIR/graphology-communities-louvain.min.js" optional

log "All viz libs vendored to $LIB_DIR"
