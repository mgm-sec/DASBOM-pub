#!/usr/bin/env bash
# Serve the visualization locally

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PORT="${1:-8080}"
VIZ_ROOT="$PROJECT_ROOT/output"

echo "Serving at http://localhost:$PORT/viz/index.html"
echo "Press Ctrl+C to stop."
cd "$VIZ_ROOT" && python3 -m http.server "$PORT"
