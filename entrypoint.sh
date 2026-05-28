#!/usr/bin/env bash
set -euo pipefail

echo "╔══════════════════════════════════════════╗"
echo "║       sbom-viz  •  SBOM Visualizer       ║"
echo "╚══════════════════════════════════════════╝"
echo ""

if [[ -z "${GH_TOKEN:-}" ]]; then
    echo "  ⚠  No GH_TOKEN provided. Public repos only."
    echo ""
    echo "     Private repos require a token. Create one at:"
    echo "     https://github.com/settings/tokens"
    echo ""
    echo "     Then restart:"
    echo "     docker run -p 8080:8080 -e GH_TOKEN=<token> <image>"
    echo ""
else
    if echo "$GH_TOKEN" | gh auth login --with-token 2>/dev/null; then
        echo "  ✓  GitHub authenticated"
    else
        echo "  ✗  GH_TOKEN invalid — falling back to public repos only"
    fi
    echo ""
fi

echo "  →  Open http://localhost:8080"
echo ""

exec python3 /app/server.py
