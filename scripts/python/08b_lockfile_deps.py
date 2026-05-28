#!/usr/bin/env python3
"""
Parse lock files from cloned repos and inject high-confidence dep edges into
dep_graph.json.

Creates NEW nodes for lockfile packages not yet in the graph, capturing the
full transitive closure regardless of depth.

Supports:
  npm/node : package-lock.json (v2/v3), yarn.lock v1+v2 (berry), pnpm-lock.yaml
  Python   : poetry.lock, uv.lock, requirements*.txt (pip-compile + plain)
  Rust     : Cargo.lock
  PHP      : composer.lock
  Ruby     : Gemfile.lock
  Go       : go.mod (direct deps only)
  Swift    : Package.resolved (nodes only, no dep graph)

Source priority (most → least trustable):
  lockfile > cdx (from 06_build_graph) > registry (from 08_enrich_deps)
"""

import json
import math
import random
import re
import sys
import urllib.parse
from pathlib import Path
from collections import defaultdict


# ── PURL helpers ──────────────────────────────────────────────────────────────

def parse_purl(purl: str):
    if not purl or not purl.startswith("pkg:"):
        return None, None, None
    without = purl[4:]
    main = without.split("?")[0].split("#")[0]
    slash = main.find("/")
    if slash == -1:
        return None, None, None
    eco  = main[:slash].lower()
    rest = main[slash + 1:]
    at   = rest.rfind("@")
    if at == -1:
        return eco, urllib.parse.unquote(rest), ""
    return eco, urllib.parse.unquote(rest[:at]), urllib.parse.unquote(rest[at + 1:])


def norm_pypi(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def norm_npm(name: str) -> str:
    return name.lower()


def eco_key(eco: str, name: str) -> tuple:
    if eco == "pypi":
        return ("pypi", norm_pypi(name))
    elif eco == "npm":
        return ("npm", norm_npm(name))
    elif eco == "cargo":
        return ("cargo", name.lower())
    elif eco == "composer":
        return ("composer", name.lower())
    else:
        return (eco, name.lower())


def make_purl(eco: str, name: str, version: str) -> str:
    enc_name = urllib.parse.quote(name, safe="@/:.-")
    enc_ver  = urllib.parse.quote(version, safe=".-+")
    return f"pkg:{eco}/{enc_name}@{enc_ver}"


def make_lockfile_stub(eco: str, name: str, version: str) -> dict:
    """Create a new package node for a lockfile-sourced package."""
    purl  = make_purl(eco, name, version)
    angle = random.uniform(0, 2 * math.pi)
    r     = 13000 + random.uniform(0, 2000)
    return {
        "key": purl,
        "attributes": {
            "type":              "package",
            "name":              name,
            "version":           version,
            "ecosystem":         eco,
            "purl":              purl,
            "label":             f"{name}@{version}",
            "in_org":            False,
            "_src":              "lockfile",
            "repos":             [],
            "repo_count":        0,
            "license":           "",
            "dep_count":         0,
            "has_conflict":      False,
            "conflict_versions": [],
            "size":              3,
            "color":             "#1e2433",
            "x":                 r * math.cos(angle),
            "y":                 r * math.sin(angle),
        }
    }


# ── Lock file parsers ─────────────────────────────────────────────────────────

def parse_poetry_lock(path: Path) -> list[tuple[str, str, list[str]]]:
    """Returns list of (name, version, [dep_name, ...])."""
    content = path.read_text(errors="replace")
    packages = []
    blocks = re.split(r'^\[\[package\]\]', content, flags=re.MULTILINE)
    for block in blocks[1:]:
        name_m = re.search(r'^name\s*=\s*"([^"]+)"', block, re.MULTILINE)
        ver_m  = re.search(r'^version\s*=\s*"([^"]+)"', block, re.MULTILINE)
        if not name_m or not ver_m:
            continue
        name    = name_m.group(1)
        version = ver_m.group(1)
        dep_names: list[str] = []
        dep_block_m = re.search(r'\[package\.dependencies\](.*?)(?=\[|\Z)', block, re.DOTALL)
        if dep_block_m:
            for dep_line in dep_block_m.group(1).splitlines():
                dep_m = re.match(r'^\s*([A-Za-z0-9_.\-]+)\s*=', dep_line)
                if dep_m:
                    dep_names.append(dep_m.group(1))
        packages.append((name, version, dep_names))
    return packages


def parse_package_lock_json(path: Path) -> list[tuple[str, str, list[str]]]:
    """npm package-lock.json v2/v3 — handles nested node_modules paths."""
    try:
        data = json.loads(path.read_text(errors="replace"))
    except (json.JSONDecodeError, OSError):
        return []
    packages_section = data.get("packages", {})
    result = []
    for pkg_path, info in packages_section.items():
        if not pkg_path:
            continue
        # "node_modules/a/node_modules/b" → "b"  (last segment after any /node_modules/)
        bare = pkg_path[len("node_modules/"):] if pkg_path.startswith("node_modules/") else pkg_path
        name = bare.split("/node_modules/")[-1]
        version = info.get("version", "")
        if not version:
            continue
        dep_names = list((info.get("dependencies") or {}).keys())
        result.append((name, version, dep_names))
    return result


def parse_yarn_lock_v1(path: Path) -> list[tuple[str, str, list[str]]]:
    """yarn.lock v1 (classic) — returns [] if not v1."""
    content = path.read_text(errors="replace")
    if "# yarn lockfile v1" not in content[:200]:
        return []
    result = []
    blocks = re.split(r'\n\n', content)
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue
        header = lines[0]
        if not header or header.startswith("#"):
            continue
        header_clean = header.strip().rstrip(":")
        first_entry  = header_clean.split(",")[0].strip().strip('"')
        at_idx = first_entry.rfind("@")
        if at_idx <= 0:
            continue
        name = first_entry[:at_idx]
        ver_m = re.search(r'^\s+version\s+"([^"]+)"', block, re.MULTILINE)
        if not ver_m:
            continue
        version = ver_m.group(1)
        dep_names: list[str] = []
        dep_block_m = re.search(r'^\s+dependencies:\s*\n((?:\s+\S.*\n)*)', block, re.MULTILINE)
        if dep_block_m:
            for dep_line in dep_block_m.group(1).splitlines():
                dep_m = re.match(r'^\s+"?([^"@\s]+)"?\s+', dep_line)
                if dep_m:
                    dep_names.append(dep_m.group(1))
        result.append((name, version, dep_names))
    return result


def parse_yarn_lock_v2(path: Path) -> list[tuple[str, str, list[str]]]:
    """yarn.lock v2/berry (detected by __metadata: header)."""
    content = path.read_text(errors="replace")
    if "__metadata:" not in content[:500]:
        return []
    result = []
    blocks = re.split(r'\n\n+', content)
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue
        header = lines[0]
        if not header.endswith(":") or header.startswith("__metadata"):
            continue
        # '"lodash@npm:^4.17.21, lodash@npm:^4.0.0":' → name = "lodash"
        first_entry = header.rstrip(":").split(",")[0].strip().strip('"')
        m = re.match(r'^(@?[^@]+)@', first_entry)
        if not m:
            continue
        name = m.group(1)

        version = None
        dep_names: list[str] = []
        in_deps  = False

        for line in lines[1:]:
            stripped = line.lstrip()
            indent   = len(line) - len(stripped)
            if indent == 2 and stripped.startswith("version:"):
                version  = stripped.split(":", 1)[1].strip().strip('"')
                in_deps  = False
            elif indent == 2 and stripped.startswith("dependencies:"):
                in_deps = True
            elif in_deps and indent == 4 and stripped:
                dep_m = re.match(r'"?([^":]+)"?:', stripped)
                if dep_m:
                    dep_names.append(dep_m.group(1).strip())
            elif in_deps and indent < 4:
                in_deps = False

        if name and version:
            result.append((name, version, dep_names))
    return result


def parse_yarn_lock(path: Path) -> list[tuple[str, str, list[str]]]:
    """Dispatch to v1 or v2 parser based on content."""
    content_start = path.read_text(errors="replace")[:500]
    if "__metadata:" in content_start:
        return parse_yarn_lock_v2(path)
    return parse_yarn_lock_v1(path)


def parse_cargo_lock(path: Path) -> list[tuple[str, str, list[str]]]:
    """Cargo.lock [[package]] blocks."""
    content = path.read_text(errors="replace")
    packages = []
    blocks = re.split(r'^\[\[package\]\]', content, flags=re.MULTILINE)
    for block in blocks[1:]:
        name_m = re.search(r'^name\s*=\s*"([^"]+)"', block, re.MULTILINE)
        ver_m  = re.search(r'^version\s*=\s*"([^"]+)"', block, re.MULTILINE)
        if not name_m or not ver_m:
            continue
        name    = name_m.group(1)
        version = ver_m.group(1)
        dep_names: list[str] = []
        deps_m = re.search(r'^dependencies\s*=\s*\[(.*?)\]', block, re.DOTALL | re.MULTILINE)
        if deps_m:
            for dep_str in re.findall(r'"([^"]+)"', deps_m.group(1)):
                dep_names.append(dep_str.split()[0])
        packages.append((name, version, dep_names))
    return packages


def parse_composer_lock(path: Path) -> list[tuple[str, str, list[str]]]:
    """composer.lock — excludes php/ext-/lib- virtual constraints."""
    try:
        data = json.loads(path.read_text(errors="replace"))
    except (json.JSONDecodeError, OSError):
        return []
    _SKIP = ("php", "ext-", "lib-", "hhvm")
    result = []
    for pkg in data.get("packages", []) + data.get("packages-dev", []):
        name    = pkg.get("name", "")
        version = pkg.get("version", "").lstrip("v")
        if not name or not version:
            continue
        dep_names = [
            k for k in pkg.get("require", {}).keys()
            if not any(k.startswith(p) for p in _SKIP)
        ]
        result.append((name, version, dep_names))
    return result


def parse_go_mod(path: Path) -> list[tuple[str, str, list[str]]]:
    """go.mod — direct deps only (full transitives require go list -m all)."""
    content = path.read_text(errors="replace")
    result  = []
    in_require = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        if stripped == "require (":
            in_require = True
            continue
        if in_require and stripped == ")":
            in_require = False
            continue
        if in_require or stripped.startswith("require "):
            m = re.match(r'(?:require\s+)?([^\s]+)\s+(v[\d][^\s]*)', stripped)
            if m:
                module_path = m.group(1)
                version     = m.group(2).lstrip("v")
                result.append((module_path, version, []))
    return result


def parse_gemfile_lock(path: Path) -> list[tuple[str, str, list[str]]]:
    """Gemfile.lock GEM/specs section — full transitive closure."""
    content = path.read_text(errors="replace")
    result  = []
    in_specs     = False
    current_name: str | None = None
    current_ver:  str | None = None
    dep_names:    list[str]  = []

    for line in content.splitlines():
        if re.match(r'^\s{2}specs:\s*$', line):
            in_specs = True
            continue
        if in_specs and line and not line[0].isspace():
            if current_name and current_ver is not None:
                result.append((current_name, current_ver, dep_names))
            in_specs     = False
            current_name = None
            current_ver  = None
            dep_names    = []
            continue
        if not in_specs:
            continue

        # 4-space: gem entry line
        if re.match(r'^    \S', line):
            if current_name and current_ver is not None:
                result.append((current_name, current_ver, dep_names))
            m = re.match(r'^    ([A-Za-z0-9_.\-]+)\s+\(([^)]+)\)', line)
            if m:
                current_name = m.group(1)
                current_ver  = m.group(2).split(",")[0].strip()
                dep_names    = []
            else:
                current_name = None
                current_ver  = None
                dep_names    = []
        # 6-space: dep of current gem
        elif re.match(r'^      \S', line) and current_name:
            m = re.match(r'^      ([A-Za-z0-9_.\-]+)', line)
            if m:
                dep_names.append(m.group(1))

    if current_name and current_ver is not None:
        result.append((current_name, current_ver, dep_names))
    return result


def parse_uv_lock(path: Path) -> list[tuple[str, str, list[str]]]:
    """uv.lock — full transitive closure, TOML-like [[package]] blocks."""
    content = path.read_text(errors="replace")
    if "[[package]]" not in content:
        return []
    packages = []
    blocks = re.split(r'^\[\[package\]\]', content, flags=re.MULTILINE)
    for block in blocks[1:]:
        name_m = re.search(r'^name\s*=\s*"([^"]+)"', block, re.MULTILINE)
        ver_m  = re.search(r'^version\s*=\s*"([^"]+)"', block, re.MULTILINE)
        if not name_m or not ver_m:
            continue
        name    = name_m.group(1)
        version = ver_m.group(1)
        dep_names: list[str] = []
        deps_m = re.search(r'^dependencies\s*=\s*\[(.*?)\]', block, re.DOTALL | re.MULTILINE)
        if deps_m:
            for dep_name in re.findall(r'\{\s*name\s*=\s*"([^"]+)"', deps_m.group(1)):
                dep_names.append(dep_name)
        packages.append((name, version, dep_names))
    return packages


def parse_pnpm_lock_yaml(path: Path) -> list[tuple[str, str, list[str]]]:
    """pnpm-lock.yaml v6+ — snapshots: section contains full transitive closure."""
    content = path.read_text(errors="replace")
    if "lockfileVersion:" not in content[:300]:
        return []

    def strip_peer(s: str) -> str:
        prev = None
        while prev != s:
            prev = s
            s = re.sub(r'\([^()]*\)', '', s)
        return s.strip()

    def parse_pkg_key(raw: str) -> tuple:
        key = strip_peer(raw.strip().strip("'\""))
        at  = key.rfind("@")
        if at <= 0:
            return None, None
        return key[:at], key[at + 1:]

    result   = []
    in_snaps = False
    cur_name = cur_ver = None
    dep_names: list[str] = []
    in_deps  = False

    for line in content.splitlines():
        if re.match(r'^snapshots:\s*$', line):
            if cur_name:
                result.append((cur_name, cur_ver, dep_names))
            in_snaps = True
            cur_name = cur_ver = None
            dep_names = []
            in_deps = False
            continue
        if in_snaps and line and not line[0].isspace():
            if cur_name:
                result.append((cur_name, cur_ver, dep_names))
            in_snaps = False
            cur_name = cur_ver = None
            dep_names = []
            in_deps = False
            continue
        if not in_snaps:
            continue

        stripped = line.lstrip()
        indent   = len(line) - len(stripped)
        if not stripped or stripped.startswith("#"):
            continue

        if indent == 2:
            # Package entry: 'name@ver': or 'name@ver': {}
            if stripped.startswith("'") or stripped.startswith('"'):
                q    = stripped[0]
                end  = stripped.find(q, 1)
                key_raw = stripped[1:end] if end > 1 else stripped[1:].rstrip(":")
            else:
                key_raw = stripped.split(":")[0]
            if cur_name:
                result.append((cur_name, cur_ver, dep_names))
            cur_name, cur_ver = parse_pkg_key(key_raw)
            dep_names = []
            in_deps   = False
        elif indent == 4 and stripped.rstrip().rstrip(":") == "dependencies":
            in_deps = True
        elif in_deps and indent == 6 and ":" in stripped:
            colon    = stripped.index(":")
            dep_name = stripped[:colon].strip().strip("'\"")
            if dep_name:
                dep_names.append(dep_name)
        elif in_deps and indent < 6:
            in_deps = False

    if cur_name:
        result.append((cur_name, cur_ver, dep_names))
    return result


def parse_requirements_txt(path: Path) -> list[tuple[str, str, list[str]]]:
    """
    pip requirements*.txt — handles both plain and pip-compile output.
    pip-compile files (with '# via' comments) yield full transitive edges.
    Plain files yield nodes only (no dep edges).
    Only packages with pinned '==' versions are included.
    """
    content = path.read_text(errors="replace")
    lines   = content.splitlines()

    pkg_versions: dict[str, str] = {}   # norm_name → version
    pkg_original: dict[str, str] = {}   # norm_name → original_name
    # depends_on[requirer_norm] = [dep_norms_it_requires]
    depends_on: dict[str, list[str]] = {}

    current_pkg: str | None = None
    in_via = False

    for line in lines:
        stripped = line.strip()
        raw_indent = len(line) - len(line.lstrip())

        if not stripped:
            current_pkg = None
            in_via = False
            continue

        if stripped.startswith("#"):
            if current_pkg is None:
                continue
            # "# via pkgname" (single) or "# via" (start multi-line)
            single_m = re.match(r'^#\s+via\s+([A-Za-z0-9_.\-]+)\s*$', stripped)
            if single_m:
                req_norm = norm_pypi(single_m.group(1))
                if not req_norm.startswith("-"):
                    depends_on.setdefault(req_norm, [])
                    if current_pkg not in depends_on[req_norm]:
                        depends_on[req_norm].append(current_pkg)
                in_via = True
            elif re.match(r'^#\s+via\s*$', stripped):
                in_via = True
            elif in_via:
                # Multi-line continuation: "#   pkgname"
                cont_m = re.match(r'^#\s{2,}([A-Za-z0-9_.\-]+)\s*$', stripped)
                if cont_m:
                    req_norm = norm_pypi(cont_m.group(1))
                    if not req_norm.startswith("-"):
                        depends_on.setdefault(req_norm, [])
                        if current_pkg not in depends_on[req_norm]:
                            depends_on[req_norm].append(current_pkg)
            continue

        if stripped.startswith("-") or stripped.startswith("http") or "@" in stripped[:4]:
            current_pkg = None
            in_via = False
            continue

        # Package line — only accept pinned == versions for node creation
        m = re.match(r'^([A-Za-z0-9_.\-]+)\s*===?\s*([\d][^\s;,#]*)', stripped)
        if m:
            raw_name = m.group(1)
            version  = m.group(2).strip()
            norm     = norm_pypi(raw_name)
            if norm not in pkg_versions:
                pkg_versions[norm] = version
                pkg_original[norm] = raw_name
            current_pkg = norm
            in_via = False
        else:
            current_pkg = None
            in_via = False

    result = []
    for norm, version in pkg_versions.items():
        if not version:
            continue
        deps = [pkg_original.get(d, d) for d in depends_on.get(norm, [])]
        result.append((pkg_original[norm], version, deps))
    return result


def parse_package_resolved(path: Path) -> list[tuple[str, str, list[str]]]:
    """Swift Package.resolved — pins array, nodes only (no dep graph available)."""
    try:
        data = json.loads(path.read_text(errors="replace"))
    except (json.JSONDecodeError, OSError):
        return []
    result = []
    for pin in data.get("pins", []):
        identity = pin.get("identity", "")
        state    = pin.get("state", {})
        version  = state.get("version", "")
        if identity and version:
            result.append((identity, version, []))
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    graph_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/graph/dep_graph.json")
    repos_dir  = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("repos")

    print(f"Loading {graph_path}...")
    g = json.loads(graph_path.read_text())

    # Build reverse lookups from existing graph nodes
    purl_to_node: dict[str, dict] = {n["key"]: n for n in g["nodes"]}
    eco_name_to_purl: dict[tuple, str] = {}
    for purl, node in purl_to_node.items():
        a = node["attributes"]
        if a.get("type") != "package":
            continue
        eco, namepath, _ = parse_purl(purl)
        if not eco or not namepath:
            continue
        eco_name_to_purl[eco_key(eco, namepath.split("/")[-1] if eco == "cargo" else namepath)] = purl

    # Build edge sets for dedup
    existing_edges: set[tuple[str, str]] = set()
    edge_index: dict[tuple[str, str], int] = {}
    for i, e in enumerate(g["edges"]):
        k = (e["source"], e["target"])
        existing_edges.add(k)
        edge_index[k] = i

    repo_nodes = {
        n["attributes"]["name"]: n["key"]
        for n in g["nodes"]
        if n["attributes"].get("type") == "repository"
    }

    repo_sbom_count: dict[str, int] = defaultdict(int)
    for node in g["nodes"]:
        a = node["attributes"]
        if a.get("type") == "package" and a.get("in_org", True):
            for r in a.get("repos", []):
                repo_sbom_count[r] += 1

    repo_lock_pkgs:  dict[str, set] = defaultdict(set)
    repo_lock_found: dict[str, set] = defaultdict(set)

    # Per-node dep completeness tracking
    purl_dep_declared: dict[str, int] = defaultdict(int)
    purl_dep_resolved: dict[str, int] = defaultdict(int)

    new_nodes   = 0
    new_edges   = 0
    updated_src = 0
    repos_processed = 0

    LOCK_FILES = [
        ("pypi",     "poetry.lock",       parse_poetry_lock),
        ("npm",      "package-lock.json", parse_package_lock_json),
        ("npm",      "yarn.lock",         parse_yarn_lock),
        ("cargo",    "Cargo.lock",        parse_cargo_lock),
        ("composer", "composer.lock",     parse_composer_lock),
        ("golang",   "go.mod",            parse_go_mod),
        ("gem",      "Gemfile.lock",      parse_gemfile_lock),
        ("pypi",     "uv.lock",           parse_uv_lock),
        ("npm",      "pnpm-lock.yaml",    parse_pnpm_lock_yaml),
        ("swift",    "Package.resolved",  parse_package_resolved),
    ]

    for repo_name in sorted(repo_nodes.keys()):
        repo_path = repos_dir / repo_name
        if not repo_path.exists():
            continue

        repo_processed = False

        # Build full list of (eco, path, parser_fn) to process
        lock_tasks: list[tuple[str, Path, object]] = [
            (eco, repo_path / filename, parser_fn)
            for eco, filename, parser_fn in LOCK_FILES
        ]
        # requirements*.txt — glob for all variants
        for req_path in sorted(repo_path.glob("requirements*.txt")):
            lock_tasks.append(("pypi", req_path, parse_requirements_txt))

        for eco, lock_path, parser_fn in lock_tasks:
            if not lock_path.exists():
                continue

            try:
                entries = parser_fn(lock_path)
            except Exception as e:
                print(f"  [WARN] {repo_name}/{lock_path.name}: {e}")
                continue

            if not entries:
                continue

            # ── Phase 1 fix: create stub nodes for packages not yet in graph ──
            lock_name_to_purl: dict[str, str] = {}
            for pkg_name, version, _ in entries:
                k = eco_key(eco, pkg_name)
                if k in eco_name_to_purl:
                    lock_name_to_purl[pkg_name] = eco_name_to_purl[k]
                elif version:
                    node = make_lockfile_stub(eco, pkg_name, version)
                    purl = node["key"]
                    if purl not in purl_to_node:
                        g["nodes"].append(node)
                        purl_to_node[purl] = node
                        eco_name_to_purl[k] = purl
                        new_nodes += 1
                    else:
                        eco_name_to_purl[k] = purl
                    lock_name_to_purl[pkg_name] = purl

            # Track completeness per repo
            for pkg_name, _, _ in entries:
                repo_lock_pkgs[repo_name].add(pkg_name)
                if pkg_name in lock_name_to_purl:
                    repo_lock_found[repo_name].add(pkg_name)

            # Add / confirm edges + track per-node dep completeness
            for pkg_name, _version, dep_names in entries:
                src_purl = lock_name_to_purl.get(pkg_name)
                if not src_purl or not dep_names:
                    continue
                purl_dep_declared[src_purl] += len(dep_names)
                for dep_name in dep_names:
                    tgt_purl = lock_name_to_purl.get(dep_name)
                    if not tgt_purl or tgt_purl == src_purl:
                        continue
                    purl_dep_resolved[src_purl] += 1
                    edge_key = (src_purl, tgt_purl)
                    if edge_key in existing_edges:
                        idx = edge_index.get(edge_key)
                        if idx is not None:
                            attrs = g["edges"][idx].get("attributes", {})
                            if attrs.get("_src") != "lockfile":
                                attrs["_src"] = "lockfile"
                                updated_src += 1
                    else:
                        g["edges"].append({
                            "source": src_purl,
                            "target": tgt_purl,
                            "attributes": {"type": "depends_on", "_src": "lockfile"},
                        })
                        existing_edges.add(edge_key)
                        edge_index[edge_key] = len(g["edges"]) - 1
                        new_edges += 1

            repo_processed = True

        if repo_processed:
            repos_processed += 1

    # ── Per-node dep completeness attribute ───────────────────────────────────
    for purl, declared in purl_dep_declared.items():
        if declared > 0 and purl in purl_to_node:
            resolved = purl_dep_resolved.get(purl, 0)
            score    = round(resolved / declared, 3)
            purl_to_node[purl]["attributes"]["_dep_completeness"] = score

    # ── Repo-level completeness scoring ──────────────────────────────────────
    completeness: dict[str, dict] = {}
    for repo_name in repo_nodes:
        has_lock = repo_name in repo_lock_pkgs
        sbom_cnt = repo_sbom_count.get(repo_name, 0)
        if has_lock:
            total = len(repo_lock_pkgs[repo_name])
            found = len(repo_lock_found[repo_name])
            score = round(min(found / total * 100, 100.0), 1) if total > 0 else 100.0
            completeness[repo_name] = {
                "has_lock_file": True,
                "lock_count":    total,
                "lock_found":    found,
                "sbom_count":    sbom_cnt,
                "score":         score,
            }
        else:
            completeness[repo_name] = {
                "has_lock_file": False,
                "sbom_count":    sbom_cnt,
            }

    completeness_path = graph_path.parent / "repo_completeness.json"
    completeness_path.write_text(json.dumps(completeness))

    scored = [v for v in completeness.values() if v.get("has_lock_file")]
    avg_score = round(sum(v["score"] for v in scored) / len(scored), 1) if scored else 0.0
    print(f"  Completeness: {len(scored)} repos with lock files, avg score {avg_score}%")

    # Update graph attributes
    g["attributes"]["total_nodes"]                = len(g["nodes"])
    g["attributes"]["total_edges"]                = len(g["edges"])
    g["attributes"]["lockfile_new_nodes"]         = new_nodes
    g["attributes"]["lockfile_new_edges"]         = new_edges
    g["attributes"]["lockfile_confirmed_edges"]   = updated_src
    g["attributes"]["lockfile_repos"]             = repos_processed

    graph_path.write_text(json.dumps(g))
    print(f"Lock file enrichment done:")
    print(f"  Repos with lock files: {repos_processed}")
    print(f"  New nodes created:     {new_nodes}")
    print(f"  New edges added:       {new_edges}")
    print(f"  CDX edges confirmed:   {updated_src}")
    print(f"  Total nodes now:       {len(g['nodes']):,}")
    print(f"  Total edges now:       {len(g['edges']):,}")


if __name__ == "__main__":
    main()
