#!/usr/bin/env python3
"""sbom-viz web server — input UI, pipeline orchestration, viz serving."""

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"
STATIC = ROOT / "static"

app = Flask(__name__, static_folder=None)

# ── state ─────────────────────────────────────────────────────────────────────
_state = {"status": "idle", "targets": [], "error": None}
_log: list[str] = []
_log_lock = threading.Lock()
_state_lock = threading.Lock()


def _append(line: str):
    with _log_lock:
        _log.append(line)


def _set_status(s: str, err: str | None = None):
    with _state_lock:
        _state["status"] = s
        _state["error"] = err


# ── pipeline ──────────────────────────────────────────────────────────────────
STEPS = [
    "scripts/00b_parse_targets.sh",
    "scripts/02_clone_repos.sh",
    "scripts/03_generate_sbom.sh",
    "scripts/05_merge_sbom.sh",
    "scripts/06_build_graph.sh",
    "scripts/07_setup_viz.sh",
    "scripts/08b_lockfile_deps.sh",
    "scripts/08_enrich_deps.sh",
    "scripts/09_security_audit.sh",
    "scripts/10_snapshot_history.sh",
]


def _run_pipeline(targets: list[str], clear_repos: bool = False):
    global _log
    with _log_lock:
        _log = []
    _set_status("scanning")

    if clear_repos:
        repos_dir = ROOT / "repos"
        if repos_dir.exists():
            _append("[setup] Clearing repos...")
            shutil.rmtree(repos_dir)
            repos_dir.mkdir()

    for d in ["output/sbom", "output/graph"]:
        p = ROOT / d
        if p.exists():
            shutil.rmtree(p)

    env = os.environ.copy()
    env["TARGETS"] = json.dumps(targets)
    env["PROJECT_ROOT"] = str(ROOT)
    env["DOCKER"] = "1"

    for step in STEPS:
        _append(f"\n▶ {step}")
        try:
            proc = subprocess.Popen(
                ["bash", step],
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert proc.stdout
            for line in proc.stdout:
                _append(line.rstrip())
            proc.wait()
            if proc.returncode != 0:
                _set_status("error", f"{step} exited {proc.returncode}")
                _append(f"✗ {step} failed")
                return
        except Exception as exc:
            _set_status("error", str(exc))
            _append(f"✗ {exc}")
            return
        _append(f"✓ {step}")

    _set_status("done")
    _append("\n✓ Scan complete — open /viz/ to explore the graph")


# ── API routes ────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(STATIC, "ui.html")


@app.route("/scan", methods=["POST"])
def scan():
    data = request.get_json() or {}
    targets = [t.strip() for t in data.get("targets", []) if t.strip()]
    if not targets:
        return jsonify(error="No targets provided"), 400
    with _state_lock:
        if _state["status"] == "scanning":
            return jsonify(error="Scan already running"), 409
        _state["targets"] = targets
    threading.Thread(target=_run_pipeline, args=(targets, True), daemon=True).start()
    return jsonify(ok=True)


@app.route("/add", methods=["POST"])
def add_target():
    data = request.get_json() or {}
    target = data.get("target", "").strip()
    if not target:
        return jsonify(error="No target"), 400
    with _state_lock:
        if _state["status"] == "scanning":
            return jsonify(error="Scan already running"), 409
        if target not in _state["targets"]:
            _state["targets"].append(target)
        targets = list(_state["targets"])
    # Keep existing repos — only new ones will be cloned
    threading.Thread(target=_run_pipeline, args=(targets, False), daemon=True).start()
    return jsonify(ok=True)


@app.route("/remove", methods=["POST"])
def remove_target():
    data = request.get_json() or {}
    target = data.get("target", "").strip()
    with _state_lock:
        if _state["status"] == "scanning":
            return jsonify(error="Scan already running"), 409
        _state["targets"] = [t for t in _state["targets"] if t != target]
        targets = list(_state["targets"])
    if not targets:
        _set_status("idle")
        return jsonify(ok=True)
    # Remove requires full re-scan with clean repos
    threading.Thread(target=_run_pipeline, args=(targets, True), daemon=True).start()
    return jsonify(ok=True)


@app.route("/status")
def status():
    with _state_lock:
        return jsonify(
            status=_state["status"],
            targets=list(_state["targets"]),
            error=_state["error"],
        )


@app.route("/stream")
def stream():
    """SSE log stream. Cursor-based so multiple clients work simultaneously."""
    def generate():
        cursor = 0
        while True:
            with _log_lock:
                chunk = _log[cursor:]
            for line in chunk:
                yield f"data: {json.dumps(line)}\n\n"
            cursor += len(chunk)

            with _state_lock:
                finished = _state["status"] in ("done", "error")
                payload = {"status": _state["status"], "error": _state["error"]}

            if finished and cursor >= len(_log):
                yield f"event: done\ndata: {json.dumps(payload)}\n\n"
                return

            time.sleep(0.15)
            yield ": ping\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── viz file serving ──────────────────────────────────────────────────────────
# index.html fetches: ../graph/dep_graph.json, ../security_audit.json, ../history.json
# When served from /viz/, ../ resolves to /

@app.route("/viz/")
@app.route("/viz/index.html")
def viz_index():
    return send_from_directory(OUTPUT / "viz", "index.html")


@app.route("/viz/lib/<path:filename>")
def viz_lib(filename):
    return send_from_directory(OUTPUT / "viz" / "lib", filename)


@app.route("/graph/<path:filename>")
def graph_file(filename):
    return send_from_directory(OUTPUT / "graph", filename)


@app.route("/security_audit.json")
def security_audit():
    return send_from_directory(OUTPUT, "security_audit.json")


@app.route("/history.json")
def history():
    return send_from_directory(OUTPUT, "history.json")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, threaded=True)
