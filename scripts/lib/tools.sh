#!/usr/bin/env bash
# Shared tool management — sourced by all scripts

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"
GOBIN="${GOPATH:-$HOME/go}/bin"

# Target GitHub org — override with: ORG=myorg ./refresh_all.sh
ORG="${ORG:-myorg}"

log()  { echo "[$(date +%H:%M:%S)] $*"; }
warn() { echo "[WARN] $*" >&2; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

ensure_brew() {
    local pkg="$1" cmd="${2:-$1}"
    if ! command -v "$cmd" &>/dev/null; then
        log "brew install $pkg"
        brew install "$pkg" || die "brew install $pkg failed"
    fi
}

ensure_npm_global() {
    local pkg="$1" cmd="${2:-$1}"
    if ! command -v "$cmd" &>/dev/null; then
        log "npm install -g $pkg"
        npm install -g "$pkg" || die "npm install -g $pkg failed"
    fi
}

setup_venv() {
    if [[ ! -d "$VENV_DIR" ]]; then
        log "Creating Python venv at $VENV_DIR"
        python3 -m venv "$VENV_DIR"
    fi
}

ensure_pip() {
    local pkg="$1"
    setup_venv
    if ! "$VENV_DIR/bin/pip" show "$pkg" &>/dev/null 2>&1; then
        log "pip install $pkg"
        "$VENV_DIR/bin/pip" install "$pkg" -q || die "pip install $pkg failed"
    fi
}

ensure_pip_multi() {
    # Install multiple pip packages at once (faster)
    setup_venv
    local missing=()
    for pkg in "$@"; do
        "$VENV_DIR/bin/pip" show "$pkg" &>/dev/null 2>&1 || missing+=("$pkg")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        log "pip install ${missing[*]}"
        "$VENV_DIR/bin/pip" install "${missing[@]}" -q || die "pip install failed"
    fi
}

ensure_go_tool() {
    local import_path="$1" cmd="$2"
    ensure_brew go go
    if ! command -v "$cmd" &>/dev/null && [[ ! -f "$GOBIN/$cmd" ]]; then
        log "go install $import_path"
        go install "$import_path" || die "go install $import_path failed"
    fi
    export PATH="$GOBIN:$PATH"
}

activate_venv() {
    setup_venv
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
    export PATH="$VENV_DIR/bin:$PATH"
}


python_venv() {
    activate_venv
    "$VENV_DIR/bin/python3" "$@"
}
