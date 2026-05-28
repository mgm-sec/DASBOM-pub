#!/usr/bin/env bash
# Verify and install all required tools

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/tools.sh"

log "=== Tool Setup ==="

if [[ "${DOCKER:-}" == "1" ]]; then
    log "Docker mode — tools pre-installed in image"
else
    # System tools via brew (macOS only)
    ensure_brew syft
    ensure_brew jq
    # Python venv
    setup_venv
fi

log "=== All tools ready ==="
log "  syft:        $(syft version 2>/dev/null | head -1)"
log "  jq:          $(jq --version 2>/dev/null)"
log "  python:      $(python3 --version 2>/dev/null)"
