#!/usr/bin/env python3
"""
Multi-pass dependency enrichment for dep_graph.json.

Pass 1: Enrich org packages with registry dep edges (within-org only).
Pass 2+: For unresolved dep names (not in org), create external stub nodes,
         fetch their latest version, add edges, then enrich those too.

Supported ecosystems: npm, PyPI, RubyGems, cargo, golang, composer, maven.
External node positions: outer ring beyond galaxy (radius ~13000).
Results cached in output/cache/deps/ to avoid re-fetching.
"""

import json, sys, re, hashlib, math, random, urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

CACHE_DIR    = Path("output/cache/deps")
MAX_WORKERS  = 30
TIMEOUT      = 12
MAX_PASSES   = 500        # safety ceiling; loop breaks early on convergence
MAX_EXTERNAL = 10_000_000  # effectively unlimited external nodes
GALAXY_R     = 13000.0    # outer ring radius for external nodes

SUPPORTED_ECOS = ("pypi", "npm", "gem", "cargo", "golang", "composer", "maven")


# ── PURL / name utilities ─────────────────────────────────────────────────────

def norm_pypi(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_purl(purl: str):
    if not purl or not purl.startswith("pkg:"):
        return None, None
    without = purl[4:]
    main = without.split("?")[0].split("#")[0]
    slash = main.find("/")
    if slash == -1:
        return None, None
    eco  = main[:slash].lower()
    rest = main[slash + 1:]
    at   = rest.rfind("@")
    namepath = urllib.parse.unquote(rest[:at] if at != -1 else rest)
    return eco, namepath


def make_purl(eco: str, namepath: str, version: str) -> str:
    """Construct canonical PURL. npm scoped packages: @ → %40."""
    if eco == "npm":
        enc = namepath.replace("@", "%40").replace("/", "%2F") if namepath.startswith("@") else namepath
        return f"pkg:npm/{enc}@{version}"
    elif eco == "maven":
        # namepath = "group:artifact" → pkg:maven/group/artifact@version
        parts = namepath.split(":", 1)
        if len(parts) == 2:
            return f"pkg:maven/{urllib.parse.quote(parts[0], safe='')}/{urllib.parse.quote(parts[1], safe='')}@{version}"
    return f"pkg:{eco}/{urllib.parse.quote(namepath, safe='@/-.')}@{version}"


def lookup_key(eco: str, namepath: str) -> tuple:
    """Key used in eco_name_to_purls reverse lookup."""
    parts = namepath.split("/")
    name  = parts[-1]
    if eco == "pypi":
        return ("pypi", norm_pypi(name))
    elif eco == "npm":
        return ("npm", namepath)         # full scoped name
    elif eco == "gem":
        return ("gem", name)
    elif eco == "cargo":
        return ("cargo", name)
    elif eco == "golang":
        return ("golang", namepath)      # full module path
    elif eco == "composer":
        return ("composer", namepath)    # vendor/package
    elif eco == "maven":
        # namepath = "group/artifact" in graph, "group:artifact" from registry
        if ":" in namepath:
            g, a = namepath.split(":", 1)
            return ("maven", f"{g}:{a}")
        elif len(parts) >= 2:
            return ("maven", f"{parts[0]}:{parts[1]}")
    return (eco, namepath)


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def cached_fetch(cache_key: str, fetch_fn):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    h  = hashlib.sha1(cache_key.encode()).hexdigest()[:20]
    cp = CACHE_DIR / f"{h}.json"
    if cp.exists():
        try:
            return json.loads(cp.read_text())
        except Exception:
            pass
    result = fetch_fn()
    try:
        cp.write_text(json.dumps(result))
    except Exception:
        pass
    return result


def http_get_json(url: str, headers: dict | None = None) -> dict | None:
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())
    except Exception:
        return None


def http_get_text(url: str, headers: dict | None = None) -> str | None:
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return None


# ── Latest-version fetchers (for external node creation) ─────────────────────

def fetch_latest_version(eco: str, namepath: str) -> str | None:
    """Fetch latest published version for a package name."""
    name = namepath.split("/")[-1]
    try:
        if eco == "npm":
            enc = urllib.parse.quote(namepath, safe="@/")
            d   = http_get_json(f"https://registry.npmjs.org/{enc}/latest")
            return d["version"] if d else None

        elif eco == "pypi":
            d = http_get_json(f"https://pypi.org/pypi/{urllib.parse.quote(namepath)}/json")
            return d["info"]["version"] if d else None

        elif eco == "golang":
            d = http_get_json(f"https://proxy.golang.org/{namepath}/@latest")
            return (d.get("Version") or "").lstrip("v") or None

        elif eco == "gem":
            d = http_get_json(f"https://rubygems.org/api/v1/gems/{urllib.parse.quote(name)}.json")
            return d.get("version") if d else None

        elif eco == "cargo":
            d = http_get_json(
                f"https://crates.io/api/v1/crates/{urllib.parse.quote(name)}",
                headers={"User-Agent": "sbom-viz/1.0"},
            )
            return d["crate"]["max_version"] if d else None

        elif eco == "composer":
            url = f"https://repo.packagist.org/p2/{urllib.parse.quote(namepath, safe='/')}.json"
            d   = http_get_json(url)
            if d:
                pkgs = d.get("packages", {}).get(namepath, [])
                for p in pkgs:
                    v = p.get("version", "")
                    if not v.startswith("dev-") and not re.search(r"-(alpha|beta|rc)\d*$", v, re.I):
                        return v.lstrip("v")
                if pkgs:
                    return pkgs[0].get("version", "").lstrip("v") or None

        elif eco == "maven":
            # namepath = "group:artifact"
            parts = namepath.split(":", 1) if ":" in namepath else namepath.split("/", 1)
            if len(parts) == 2:
                url = (f"https://search.maven.org/solrsearch/select?"
                       f"q=g:{urllib.parse.quote(parts[0])}+AND+a:{urllib.parse.quote(parts[1])}"
                       f"&rows=1&wt=json")
                d = http_get_json(url)
                if d:
                    docs = d.get("response", {}).get("docs", [])
                    if docs:
                        return docs[0].get("latestVersion")
    except Exception:
        pass
    return None


# ── Registry license fetcher ─────────────────────────────────────────────────

def fetch_license_registry(eco: str, namepath: str, version: str) -> str | None:
    """Fetch license from registry. Returns license string or None."""
    name = namepath.split("/")[-1]

    def fetch():
        try:
            if eco == "npm":
                enc = urllib.parse.quote(namepath, safe="@/")
                d = http_get_json(f"https://registry.npmjs.org/{enc}/{urllib.parse.quote(version)}")
                if d:
                    lic = d.get("license")
                    if isinstance(lic, dict):
                        lic = lic.get("type") or lic.get("name", "")
                    return (lic or "").strip() or None

            elif eco == "pypi":
                d = http_get_json(f"https://pypi.org/pypi/{urllib.parse.quote(namepath)}/{urllib.parse.quote(version)}/json")
                if d:
                    return (d.get("info", {}).get("license") or "").strip() or None

            elif eco == "cargo":
                d = http_get_json(
                    f"https://crates.io/api/v1/crates/{urllib.parse.quote(name)}/{urllib.parse.quote(version)}",
                    headers={"User-Agent": "sbom-viz/1.0"},
                )
                if d:
                    return (d.get("version", {}).get("license") or "").strip() or None

            elif eco == "gem":
                d = http_get_json(
                    f"https://rubygems.org/api/v2/rubygems/{urllib.parse.quote(name)}/versions/{urllib.parse.quote(version)}.json"
                )
                if d:
                    lics = d.get("licenses") or []
                    return ", ".join(lics) if lics else None

            elif eco == "composer":
                url = f"https://repo.packagist.org/p2/{urllib.parse.quote(namepath, safe='/')}.json"
                d = http_get_json(url)
                if d:
                    ver_norm = version.lstrip("v")
                    versions = d.get("packages", {}).get(namepath, [])
                    target = next((p for p in versions if p.get("version", "").lstrip("v") == ver_norm), None)
                    if target is None and versions:
                        target = versions[0]
                    if target:
                        lics = target.get("license", [])
                        if isinstance(lics, list):
                            return ", ".join(lics) or None
                        return (lics or "").strip() or None

            elif eco == "maven":
                parts = namepath.split("/", 1)
                if len(parts) == 2:
                    group_path = parts[0].replace(".", "/")
                    url = f"https://repo1.maven.org/maven2/{group_path}/{parts[1]}/{version}/{parts[1]}-{version}.pom"
                    text = http_get_text(url)
                    if text:
                        try:
                            root = ET.fromstring(text)
                            ns = {"m": "http://maven.apache.org/POM/4.0.0"}
                            lic_el = root.find(".//m:license/m:name", ns)
                            if lic_el is None:
                                lic_el = root.find(".//license/name")
                            if lic_el is not None:
                                return (lic_el.text or "").strip() or None
                        except ET.ParseError:
                            pass
        except Exception:
            pass
        return None

    return cached_fetch(f"license:{eco}:{namepath}:{version}", fetch)


# ── Per-ecosystem dep fetchers ────────────────────────────────────────────────

def get_pypi_deps(name: str, version: str) -> list[str]:
    def fetch():
        d = http_get_json(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/{urllib.parse.quote(version)}/json")
        if not d:
            return []
        requires = d.get("info", {}).get("requires_dist") or []
        names = []
        for req in requires:
            req = req.strip()
            m = re.match(r'^([A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?)', req)
            if m:
                names.append(norm_pypi(m.group(1)))
        return names
    return cached_fetch(f"pypi:{name}:{version}", fetch)


def get_npm_deps(name: str, version: str) -> list[str]:
    def fetch():
        enc = urllib.parse.quote(name, safe="@/")
        d   = http_get_json(f"https://registry.npmjs.org/{enc}/{urllib.parse.quote(version)}")
        if not d:
            return []
        deps  = list(d.get("dependencies", {}).keys())
        deps += list(d.get("peerDependencies", {}).keys())
        deps += list(d.get("optionalDependencies", {}).keys())
        return list(dict.fromkeys(deps))
    return cached_fetch(f"npm:{name}:{version}", fetch)


def get_gem_deps(name: str, version: str) -> list[str]:
    def fetch():
        d = http_get_json(
            f"https://rubygems.org/api/v2/rubygems/{urllib.parse.quote(name)}/versions/{urllib.parse.quote(version)}.json"
        )
        if not d:
            return []
        return [dep["name"] for dep in d.get("dependencies", {}).get("runtime", [])]
    return cached_fetch(f"gem:{name}:{version}", fetch)


def get_cargo_deps(name: str, version: str) -> list[str]:
    def fetch():
        d = http_get_json(
            f"https://crates.io/api/v1/crates/{urllib.parse.quote(name)}/{urllib.parse.quote(version)}/dependencies",
            headers={"User-Agent": "sbom-viz/1.0"},
        )
        if not d:
            return []
        return [dep["crate_id"] for dep in d.get("dependencies", []) if dep.get("kind") == "normal"]
    return cached_fetch(f"cargo:{name}:{version}", fetch)


def get_golang_deps(namepath: str, version: str) -> list[str]:
    def fetch():
        v   = version if version.startswith("v") else f"v{version}"
        url = f"https://proxy.golang.org/{namepath}/@v/{urllib.parse.quote(v, safe='')}.mod"
        text = http_get_text(url)
        if not text:
            return []
        deps: list[str] = []
        in_require = False
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("//"):
                continue
            if line.startswith("require ("):
                in_require = True; continue
            if line.startswith("require ") and not in_require:
                parts = line[8:].split()
                if parts:
                    deps.append(parts[0])
                continue
            if in_require:
                if line == ")":
                    in_require = False; continue
                parts = line.split()
                if parts and not parts[0].startswith("//"):
                    deps.append(parts[0])
        return deps
    return cached_fetch(f"golang:{namepath}:{version}", fetch)


def get_composer_deps(namepath: str, version: str) -> list[str]:
    def fetch():
        url = f"https://repo.packagist.org/p2/{urllib.parse.quote(namepath, safe='/')}.json"
        d   = http_get_json(url)
        if not d:
            return []
        versions = d.get("packages", {}).get(namepath, [])
        # Try exact version match (with/without v-prefix)
        target = None
        ver_norm = version.lstrip("v")
        for pkg in versions:
            pv = pkg.get("version", "").lstrip("v")
            if pv == ver_norm:
                target = pkg; break
        if target is None and versions:
            target = versions[0]
        if target is None:
            return []
        requires = target.get("require", {})
        return [k for k in requires if k != "php" and not k.startswith("ext-") and "/" in k]
    return cached_fetch(f"composer:{namepath}:{version}", fetch)


def get_maven_deps(group: str, artifact: str, version: str) -> list[str]:
    def fetch():
        group_path = group.replace(".", "/")
        url = (f"https://repo1.maven.org/maven2/{group_path}/{artifact}"
               f"/{version}/{artifact}-{version}.pom")
        text = http_get_text(url)
        if not text:
            return []
        deps: list[str] = []
        try:
            root = ET.fromstring(text)
            ns   = {"m": "http://maven.apache.org/POM/4.0.0"}
            dep_list = root.findall(".//m:dependency", ns)
            if not dep_list:
                dep_list = root.findall(".//dependency")
            for dep in dep_list:
                g = dep.find("m:groupId", ns)
                if g is None: g = dep.find("groupId")
                a = dep.find("m:artifactId", ns)
                if a is None: a = dep.find("artifactId")
                scope_el = dep.find("m:scope", ns)
                if scope_el is None: scope_el = dep.find("scope")
                if g is not None and a is not None:
                    scope = (scope_el.text or "").lower() if scope_el is not None else "compile"
                    if scope not in ("test", "provided", "system"):
                        gid = (g.text or "").strip()
                        aid = (a.text or "").strip()
                        if gid and aid and not gid.startswith("${"):
                            deps.append(f"{gid}:{aid}")
        except ET.ParseError:
            pass
        return deps
    return cached_fetch(f"maven:{group}:{artifact}:{version}", fetch)


def fetch_deps_for_node(eco: str, namepath: str, version: str) -> list[str] | None:
    parts = namepath.split("/")
    name  = parts[-1]
    try:
        if eco == "pypi":    return get_pypi_deps(namepath, version)
        elif eco == "npm":   return get_npm_deps(namepath, version)
        elif eco == "gem":   return get_gem_deps(name, version)
        elif eco == "cargo": return get_cargo_deps(name, version)
        elif eco == "golang":  return get_golang_deps(namepath, version)
        elif eco == "composer": return get_composer_deps(namepath, version)
        elif eco == "maven":
            if len(parts) >= 2:
                return get_maven_deps(parts[0], parts[1], version)
    except Exception:
        pass
    return None


# ── External node creation ────────────────────────────────────────────────────

def make_external_node(eco: str, namepath: str, version: str, angle: float) -> dict:
    purl  = make_purl(eco, namepath, version)
    name  = namepath.split("/")[-1]
    r     = GALAXY_R + random.uniform(0, 2000)
    label = f"{name}@{version}"
    return {
        "key": purl,
        "attributes": {
            "type":       "package",
            "name":       name,
            "version":    version,
            "ecosystem":  eco,
            "purl":       purl,
            "label":      label,
            "in_org":     False,
            "repos":      [],
            "repo_count": 0,
            "license":    "",
            "dep_count":  0,
            "has_conflict": False,
            "conflict_versions": [],
            "size":  3,
            "color": "#1e2433",
            "x":     r * math.cos(angle),
            "y":     r * math.sin(angle),
        }
    }


# ── Layout recomputation (delegates to recompute_layout.py) ──────────────────

def _recompute_layout(nodes: list, edges: list, graph_attrs: dict) -> None:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from recompute_layout import compute_layout
    compute_layout(nodes, edges, graph_attrs)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    graph_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/graph/dep_graph.json")

    print(f"Loading {graph_path}...")
    g      = json.loads(graph_path.read_text())
    nodes  = g["nodes"]
    edges  = g["edges"]

    # Mark all existing nodes as in_org (06_build_graph doesn't set this yet)
    for n in nodes:
        if n["attributes"]["type"] == "package":
            n["attributes"].setdefault("in_org", True)

    # ── Build reverse lookup ─────────────────────────────────────────────────
    eco_name_to_purls: dict[tuple, list[str]] = defaultdict(list)

    def register_node(n: dict):
        purl = n["key"]
        eco, namepath = parse_purl(purl)
        if not eco: return
        key = lookup_key(eco, namepath)
        if purl not in eco_name_to_purls[key]:
            eco_name_to_purls[key].append(purl)

    for n in nodes:
        if n["attributes"]["type"] == "package":
            register_node(n)

    existing_edge_pairs: set[tuple[str, str]] = {
        (e["source"], e["target"]) for e in edges if e["attributes"]["type"] == "depends_on"
    }

    # ── Multi-pass enrichment ────────────────────────────────────────────────
    total_new_edges  = 0
    total_external   = 0
    already_enriched: set[str] = set()
    angle_counter    = [0]   # mutable for closure

    def next_angle():
        a = 2 * math.pi * angle_counter[0] / max(1, MAX_EXTERNAL)
        angle_counter[0] += 1
        return a

    for pass_num in range(MAX_PASSES):
        # Find packages needing enrichment this pass (skip already enriched)
        has_dep_edge: set[str] = {e["source"] for e in edges if e["attributes"]["type"] == "depends_on"}
        to_enrich = [
            n for n in nodes
            if n["attributes"]["type"] == "package"
            and n["key"] not in has_dep_edge
            and n["key"] not in already_enriched
            and parse_purl(n["key"])[0] in SUPPORTED_ECOS
        ]

        by_eco = defaultdict(int)
        for n in to_enrich:
            by_eco[parse_purl(n["key"])[0]] += 1
        label = "org" if pass_num == 0 else f"external pass {pass_num}"
        print(f"\nPass {pass_num} ({label}): {len(to_enrich)} packages to enrich")
        for eco, cnt in sorted(by_eco.items(), key=lambda x: -x[1]):
            print(f"  {eco}: {cnt}")

        if not to_enrich:
            print(f"  Converged after {pass_num} passes.")
            break

        new_edges:      set[tuple[str, str]] = set()
        unresolved:     dict[tuple, set[str]] = defaultdict(set)   # (eco,namepath) → {source purls}
        done = errors = 0

        def process(n: dict) -> tuple[list, list]:
            purl    = n["key"]
            eco, namepath = parse_purl(purl)
            version = n["attributes"].get("version", "")
            if not version:
                return [], []
            dep_names = fetch_deps_for_node(eco, namepath, version)
            if dep_names is None:
                return [], []
            found    = []
            unres    = []
            for dep_name in dep_names:
                key     = lookup_key(eco, dep_name)
                targets = eco_name_to_purls.get(key, [])
                if targets:
                    for tgt in targets:
                        if tgt != purl:
                            found.append((purl, tgt))
                else:
                    unres.append((eco, dep_name, purl))
            return found, unres

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(process, n): n for n in to_enrich}
            for fut in as_completed(futures):
                try:
                    found, unres = fut.result()
                    for pair in found:
                        if pair not in existing_edge_pairs:
                            new_edges.add(pair)
                            existing_edge_pairs.add(pair)
                    for eco, dep_name, src in unres:
                        unresolved[(eco, dep_name)].add(src)
                except Exception:
                    errors += 1
                done += 1
                if done % 300 == 0:
                    print(f"  {done}/{len(to_enrich)} processed, {len(new_edges)} new edges")

        # Add new edges to graph
        for src, tgt in new_edges:
            edges.append({"source": src, "target": tgt, "attributes": {"type": "depends_on"}})
        total_new_edges += len(new_edges)
        already_enriched.update(n["key"] for n in to_enrich)
        print(f"  → {len(new_edges)} new edges, {len(unresolved)} unresolved dep types, {errors} errors")

        # ── Create external stub nodes for unresolved deps ───────────────────
        if total_external < MAX_EXTERNAL and unresolved:
            print(f"  Creating external nodes for {len(unresolved)} unresolved dep names...")

            def fetch_ext(item):
                (eco, dep_name), sources = item
                key = lookup_key(eco, dep_name)
                if key in eco_name_to_purls:
                    return None  # already exists (race)
                version = fetch_latest_version(eco, dep_name)
                if not version:
                    return None
                return (eco, dep_name, version, sources)

            ext_candidates = list(unresolved.items())
            ext_done = 0
            ext_created = 0

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                futures = {ex.submit(fetch_ext, item): item for item in ext_candidates}
                for fut in as_completed(futures):
                    if total_external >= MAX_EXTERNAL:
                        break
                    try:
                        result = fut.result()
                        if result is None:
                            continue
                        eco, dep_name, version, sources = result
                        key  = lookup_key(eco, dep_name)
                        if key in eco_name_to_purls:
                            continue   # already registered (another future created it first)
                        purl = make_purl(eco, dep_name, version)
                        if purl in {n["key"] for n in nodes}:
                            # PURL already exists under different lookup key — register it
                            eco_name_to_purls[key].append(purl)
                        else:
                            node = make_external_node(eco, dep_name, version, next_angle())
                            nodes.append(node)
                            eco_name_to_purls[key].append(purl)
                            total_external += 1
                            ext_created += 1
                        # Add dep edges from sources to this external node
                        for src in sources:
                            pair = (src, purl)
                            if pair not in existing_edge_pairs:
                                edges.append({"source": src, "target": purl, "attributes": {"type": "depends_on"}})
                                existing_edge_pairs.add(pair)
                    except Exception:
                        pass
                    ext_done += 1
                    if ext_done % 500 == 0:
                        print(f"    {ext_done}/{len(ext_candidates)} version lookups, {ext_created} new nodes")

            print(f"  → {ext_created} external nodes created (total {total_external})")

    # ── Registry license enrichment (fallback for NOASSERTION packages) ─────
    NOASSERTION_VALUES = {"NOASSERTION", "NONE", ""}
    needs_license = [
        n for n in nodes
        if n["attributes"]["type"] == "package"
        and n["attributes"].get("in_org", True)
        and n["attributes"].get("license", "") in NOASSERTION_VALUES
        and parse_purl(n["key"])[0] in SUPPORTED_ECOS
        and n["attributes"].get("version", "")
    ]
    print(f"\nLicense enrichment: {len(needs_license)} packages need registry lookup...")

    def enrich_license(n: dict) -> tuple[str, str | None]:
        purl = n["key"]
        eco, namepath = parse_purl(purl)
        version = n["attributes"].get("version", "")
        if not eco or not namepath or not version:
            return purl, None
        return purl, fetch_license_registry(eco, namepath, version)

    purl_to_reg_license: dict[str, str] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(enrich_license, n): n for n in needs_license}
        for fut in as_completed(futs):
            purl, lic = fut.result()
            if lic and lic not in NOASSERTION_VALUES:
                purl_to_reg_license[purl] = lic
            done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(needs_license)} done, {len(purl_to_reg_license)} found")

    print(f"  Registry licenses found: {len(purl_to_reg_license)}/{len(needs_license)}")

    for n in nodes:
        if n["attributes"]["type"] != "package":
            continue
        purl = n["key"]
        if purl in purl_to_reg_license:
            n["attributes"]["license"] = purl_to_reg_license[purl]
            n["attributes"]["license_source"] = "registry"
        elif "license_source" not in n["attributes"]:
            n["attributes"]["license_source"] = "noassertion"

    # ── Update graph stats ───────────────────────────────────────────────────
    depends_total = sum(1 for e in edges if e["attributes"]["type"] == "depends_on")
    g["attributes"]["total_depends_edges"]  = depends_total
    g["attributes"]["total_edges"]          = len(edges)
    g["attributes"]["total_nodes"]          = len(nodes)
    g["attributes"]["total_packages"]       = sum(1 for n in nodes if n["attributes"]["type"] == "package")
    g["attributes"]["total_external_nodes"] = total_external
    g["attributes"]["registry_enriched"]    = True

    # Update dep_count on all nodes
    out_deg: dict[str, int] = defaultdict(int)
    for e in edges:
        if e["attributes"]["type"] == "depends_on":
            out_deg[e["source"]] += 1
    for n in nodes:
        if n["attributes"]["type"] == "package":
            n["attributes"]["dep_count"] = out_deg.get(n["key"], n["attributes"].get("dep_count", 0))

    print(f"\nRecomputing layout positions...")
    _recompute_layout(nodes, edges, g["attributes"])

    graph_path.write_text(json.dumps(g, separators=(",", ":")))
    print(f"\nWrote {graph_path}")
    print(f"  Total nodes:          {len(nodes):,}  (+{total_external} external)")
    print(f"  Total depends_on:     {depends_total:,}")
    print(f"  Total edges:          {len(edges):,}")


if __name__ == "__main__":
    main()
