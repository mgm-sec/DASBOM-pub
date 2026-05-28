#!/usr/bin/env bash
# Full pipeline: fetch → clone → SBOM → merge → graph → viz
# Usage: ./refresh_all.sh [--skip-clone] [--skip-sbom]
#        ORG=myorg ./refresh_all.sh   (target a different GitHub org)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts"
# Export ORG so it propagates into sourced scripts; default set in lib/tools.sh
export ORG="${ORG:-myorg}"

SKIP_CLONE=0
SKIP_SBOM=0
for arg in "$@"; do
  case "$arg" in
    --skip-clone) SKIP_CLONE=1 ;;
    --skip-sbom)  SKIP_SBOM=1  ;;
  esac
done

run() {
  echo ""
  echo "══════════════════════════════════════════"
  echo " $1"
  echo "══════════════════════════════════════════"
  bash "$SCRIPT_DIR/$1"
}

run 00_check_tools.sh
run 01_fetch_overview.sh

if [[ $SKIP_CLONE -eq 0 ]]; then
  run 02_clone_repos.sh
else
  echo "[skip] 02_clone_repos.sh"
fi

if [[ $SKIP_SBOM -eq 0 ]]; then
  run 03_generate_sbom.sh
  run 05_merge_sbom.sh
  run 06_build_graph.sh
else
  echo "[skip] 03/05/06 (SBOM generation)"
fi

run 07_setup_viz.sh
run 08b_lockfile_deps.sh
run 08_enrich_deps.sh
run 09_security_audit.sh
run 10_snapshot_history.sh

echo ""
echo "══════════════════════════════════════════"
echo " All done."
echo " Overview CSV : output/repos_overview.csv"
echo " Org SPDX     : output/sbom/org/$ORG.spdx.json"
echo " Org CDX      : output/sbom/org/$ORG.cdx.json"
echo " Dep graph    : output/graph/dep_graph.json"
echo " Visualize    : ./scripts/serve_viz.sh"
echo "══════════════════════════════════════════"
