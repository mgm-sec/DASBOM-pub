#!/usr/bin/env python3
"""
Append today's security + graph stats to output/history.json.
Keeps a rolling window of 365 snapshots.
Run after 09_security_audit.sh each pipeline run.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


MAX_SNAPSHOTS = 365


def main():
    audit_path   = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/security_audit.json")
    graph_path   = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("output/graph/dep_graph.json")
    history_path = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("output/history.json")

    if not audit_path.exists():
        print(f"ERROR: {audit_path} not found — run 09_security_audit.sh first")
        sys.exit(1)

    audit = json.loads(audit_path.read_text())
    graph = json.loads(graph_path.read_text()) if graph_path.exists() else {}
    g_attrs = graph.get("attributes", {})

    # ── Per-severity breakdown from repo_risk_scores ──────────────────────────
    risk_scores   = audit.get("repo_risk_scores", {})
    scored_repos  = [v for v in risk_scores.values() if v.get("score", 0) > 0]
    avg_risk      = round(sum(v["score"] for v in scored_repos) / len(scored_repos), 1) if scored_repos else 0.0
    top_risk_repo = max(risk_scores.items(), key=lambda x: x[1].get("score", 0), default=(None, {}))[0]
    top_risk_score = risk_scores.get(top_risk_repo, {}).get("score", 0.0) if top_risk_repo else 0.0

    # Per-severity counts from packages
    packages = audit.get("packages", {})
    n_critical = sum(1 for p in packages.values() if p.get("max_severity") == "CRITICAL")
    n_high     = sum(1 for p in packages.values() if p.get("max_severity") == "HIGH")
    n_medium   = sum(1 for p in packages.values() if p.get("max_severity") in ("MEDIUM", "MODERATE"))
    n_low      = sum(1 for p in packages.values() if p.get("max_severity") == "LOW")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    snapshot = {
        "date":             today,
        "generated_at":     audit.get("generated_at", ""),
        "total_packages":   audit.get("total_packages", 0),
        "total_repos":      g_attrs.get("total_repos", 0),
        "total_actions":    g_attrs.get("total_actions", 0),
        "total_containers": g_attrs.get("total_containers", 0),
        "with_cve":         audit.get("with_cve", 0),
        "critical":         n_critical,
        "high":             n_high,
        "medium":           n_medium,
        "low":              n_low,
        "outdated":         audit.get("outdated", 0),
        "abandoned":        audit.get("abandoned", 0),
        "confusion_risk":   audit.get("confusion_risk", 0),
        "avg_risk_score":   avg_risk,
        "max_risk_score":   round(top_risk_score, 1),
        "top_risk_repo":    top_risk_repo,
        "avg_completeness": audit.get("avg_completeness"),
    }

    # ── Load existing history or start fresh ──────────────────────────────────
    history = {"snapshots": []}
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text())
        except Exception:
            pass

    snapshots = history.get("snapshots", [])

    # Replace snapshot for today if it already exists (idempotent re-runs)
    snapshots = [s for s in snapshots if s.get("date") != today]
    snapshots.append(snapshot)

    # Keep rolling window
    if len(snapshots) > MAX_SNAPSHOTS:
        snapshots = snapshots[-MAX_SNAPSHOTS:]

    history["snapshots"] = snapshots
    history["last_updated"] = today

    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(history, indent=2))

    print(f"History updated: {len(snapshots)} snapshots → {history_path}")
    print(f"  Today: {today} | {snapshot['total_packages']} pkgs | {snapshot['with_cve']} CVEs | "
          f"{snapshot['outdated']} outdated | avg risk {snapshot['avg_risk_score']}")


if __name__ == "__main__":
    main()
