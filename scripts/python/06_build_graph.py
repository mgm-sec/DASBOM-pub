#!/usr/bin/env python3
"""
Build a graphology-format JSON dependency graph from per-repo SPDX + CycloneDX files.
Nodes: repos + packages.
Edges:
  - repo  → package  (type: "contains")   — from per-repo SPDX CONTAINS relationships
  - pkg   → pkg      (type: "depends_on") — from per-repo CDX dependency sections
"""

import json
import os
import re
import sys
import math
import random
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone


ECOSYSTEM_COLORS = {
    "pypi":         "#3776ab",
    "npm":          "#cb3837",
    "composer":     "#f28d1a",
    "golang":       "#00add8",
    "maven":        "#c71a36",
    "nuget":        "#004880",
    "swift":        "#f05138",
    "cargo":        "#dea584",
    "cocoapods":    "#ee3322",
    "gem":          "#cc342d",
    "hackage":      "#5e5086",
    "hex":          "#6e4a7e",
    "pub":          "#0175c2",
    "cran":         "#2c7bb6",
    "conda":        "#43b02a",
    "github":       "#6e7681",
    "githubaction": "#2088ff",
    "container":    "#0db7ed",
    "unknown":      "#888888",
}

REPO_COLOR = "#238636"


def parse_purl(purl: str) -> tuple[str, str, str]:
    try:
        rest = purl[len("pkg:"):]
        ecosystem, rest = rest.split("/", 1)
        if "@" in rest:
            name_part, version = rest.rsplit("@", 1)
            version = version.split("?")[0].split("#")[0]
        else:
            name_part = rest
            version = ""
        name = name_part.split("/")[-1]
        return ecosystem.lower(), name, version
    except (ValueError, IndexError):
        return "unknown", purl, ""


def purl_from_pkg(pkg: dict) -> str | None:
    for ref in pkg.get("externalRefs", []):
        if ref.get("referenceType") == "purl":
            return ref["referenceLocator"]
    return None


def build(spdx_path: Path, per_repo_dir: Path, output_path: Path, repos_dir: Path = Path("repos")) -> None:
    doc = json.loads(spdx_path.read_text())

    # ── Pass 1: per-repo SPDX → packages + repo→pkg membership ──────────────
    packages_meta: dict[str, dict] = {}
    purl_to_repos:  dict[str, list[str]] = {}
    repo_to_purls:  dict[str, list[str]] = {}

    spdx_files = sorted(per_repo_dir.glob("*.spdx.json")) if per_repo_dir.exists() else []
    for repo_spdx in spdx_files:
        repo_name = repo_spdx.stem.replace(".spdx", "")
        try:
            repo_doc = json.loads(repo_spdx.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for pkg in repo_doc.get("packages", []):
            purl = purl_from_pkg(pkg)
            if not purl:
                continue
            if purl not in packages_meta:
                packages_meta[purl] = pkg
            purl_to_repos.setdefault(purl, [])
            if repo_name not in purl_to_repos[purl]:
                purl_to_repos[purl].append(repo_name)
            repo_to_purls.setdefault(repo_name, [])
            if purl not in repo_to_purls[repo_name]:
                repo_to_purls[repo_name].append(purl)

    # Include ALL repos that have an SPDX file, even those with 0 PURL packages
    # (docs/coordination repos appear in graph as repo nodes with 0 packages)
    for repo_spdx in spdx_files:
        repo_name = repo_spdx.stem.replace(".spdx", "")
        repo_to_purls.setdefault(repo_name, [])
    all_repo_names = sorted(repo_to_purls.keys())

    # ── Pass 2: per-repo CDX → pkg→pkg dependency edges ──────────────────────
    # CDX bom-refs are PURLs with a ?package-id= qualifier.
    # The component's `.purl` field is clean (no qualifier).
    pkg_dep_edges: set[tuple[str, str]] = set()
    cdx_with_deps = 0
    purl_to_cdx_license: dict[str, str] = {}
    repos_with_cdx_deps: set[str] = set()

    NOASSERTION_VALUES = {"NOASSERTION", "NONE", ""}

    cdx_files = sorted(per_repo_dir.glob("*.cdx.json")) if per_repo_dir.exists() else []
    for repo_cdx in cdx_files:
        repo_name_cdx = repo_cdx.stem.replace(".cdx", "")
        try:
            cdx_doc = json.loads(repo_cdx.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        # bom-ref (with qualifier) → clean purl; extract CDX licenses
        bom_ref_to_purl: dict[str, str] = {}
        for comp in cdx_doc.get("components", []):
            clean_purl = comp.get("purl", "")
            bom_ref    = comp.get("bom-ref", "")
            if clean_purl and bom_ref and clean_purl in packages_meta:
                bom_ref_to_purl[bom_ref] = clean_purl
                if clean_purl not in purl_to_cdx_license:
                    for lic_entry in comp.get("licenses", []):
                        lic_obj = lic_entry.get("license") or {}
                        lic_id = (lic_obj.get("id") or lic_obj.get("name")
                                  or lic_entry.get("expression") or "").strip()
                        if lic_id and lic_id not in NOASSERTION_VALUES:
                            purl_to_cdx_license[clean_purl] = lic_id
                            break

        # metadata root component (skip — its direct deps are already repo→pkg CONTAINS)
        meta_ref = cdx_doc.get("metadata", {}).get("component", {}).get("bom-ref", "")

        repo_has_deps = False
        for dep in cdx_doc.get("dependencies", []):
            src_ref = dep.get("ref", "")
            if src_ref == meta_ref:
                continue
            src_purl = bom_ref_to_purl.get(src_ref)
            if not src_purl:
                continue
            for tgt_ref in dep.get("dependsOn", []):
                tgt_purl = bom_ref_to_purl.get(tgt_ref)
                if tgt_purl and tgt_purl != src_purl:
                    pkg_dep_edges.add((src_purl, tgt_purl))
                    repo_has_deps = True
        if repo_has_deps:
            cdx_with_deps += 1
            repos_with_cdx_deps.add(repo_name_cdx)

    # ── Direct dep detection via graph topology ───────────────────────────────
    # A package is "direct" in a repo if that repo has CDX dep data AND no
    # other package in that repo depends on it (i.e., it's a dep-graph root
    # within that repo's subgraph).
    purl_to_sources: dict[str, set[str]] = defaultdict(set)
    for src, tgt in pkg_dep_edges:
        purl_to_sources[tgt].add(src)

    direct_in_repos: dict[str, list[str]] = defaultdict(list)
    for purl, repos in purl_to_repos.items():
        for repo in repos:
            if repo not in repos_with_cdx_deps:
                continue  # no dep data → can't determine direct vs transitive
            repo_purls = set(repo_to_purls.get(repo, []))
            if not (repo_purls & purl_to_sources.get(purl, set())):
                direct_in_repos[purl].append(repo)

    # ── Version conflict detection ────────────────────────────────────────────
    name_eco_to_purls: dict[tuple[str, str], list[str]] = defaultdict(list)
    for purl in packages_meta:
        eco, name, _ = parse_purl(purl)
        name_eco_to_purls[(name, eco)].append(purl)

    conflict_info: dict[str, list[dict]] = {}
    total_conflict_names = 0
    for (name, eco), purls in name_eco_to_purls.items():
        if len(purls) > 1:
            total_conflict_names += 1
            ver_list = []
            for p in sorted(purls):
                _, _, ver = parse_purl(p)
                ver_list.append({"version": ver, "repos": purl_to_repos.get(p, [])})
            for p in purls:
                conflict_info[p] = ver_list

    # ── Pass 3: GitHub Actions scanning ──────────────────────────────────────
    # Regex: lines like `  uses: owner/action@version`  (skip local `./` refs)
    ACTION_RE = re.compile(
        r'^\s+uses:\s+([a-zA-Z0-9_.\-]+/[a-zA-Z0-9_.\-/]+@[a-zA-Z0-9_.\-/]+)',
        re.MULTILINE,
    )
    action_nodes: dict[str, dict] = {}
    repo_to_actions: dict[str, list[str]] = {}

    if repos_dir.exists():
        for repo_name in all_repo_names:
            wf_dir = repos_dir / repo_name / ".github" / "workflows"
            if not wf_dir.exists():
                continue
            for wf_file in sorted(wf_dir.glob("*.yml")):
                try:
                    content = wf_file.read_text(errors="replace")
                except OSError:
                    continue
                for m in ACTION_RE.finditer(content):
                    ref = m.group(1).strip()
                    if ref.startswith("."):
                        continue
                    at = ref.rfind("@")
                    name_part, version = ref[:at], ref[at + 1:]
                    purl = f"pkg:githubaction/{name_part}@{version}"
                    if purl not in action_nodes:
                        action_nodes[purl] = {
                            "name": name_part.split("/")[-1],
                            "full_name": name_part,
                            "version": version,
                            "repos": [],
                        }
                    if repo_name not in action_nodes[purl]["repos"]:
                        action_nodes[purl]["repos"].append(repo_name)
                    repo_to_actions.setdefault(repo_name, [])
                    if purl not in repo_to_actions[repo_name]:
                        repo_to_actions[repo_name].append(purl)

    print(f"  GitHub Actions: {len(action_nodes)} unique actions across "
          f"{sum(1 for r in repo_to_actions if repo_to_actions[r])} repos")

    # ── Pass 4: Dockerfile base image scanning ────────────────────────────────
    FROM_RE = re.compile(r'^FROM\s+([^\s#]+)', re.IGNORECASE | re.MULTILINE)
    DOCKER_REGISTRY_PREFIXES = (
        "docker.io/library/", "docker.io/", "registry.hub.docker.com/library/",
    )
    container_nodes: dict[str, dict] = {}
    repo_to_containers: dict[str, list[str]] = {}

    if repos_dir.exists():
        for repo_name in all_repo_names:
            repo_path = repos_dir / repo_name
            dockerfiles: list[Path] = []
            for pattern in ("Dockerfile", "Dockerfile.*", "docker/Dockerfile*", "**/Dockerfile", "**/Dockerfile.*"):
                dockerfiles.extend(repo_path.glob(pattern))
            seen_df: set[str] = set()
            for df in dockerfiles:
                df_str = str(df)
                if ".git" in df_str or df_str in seen_df:
                    continue
                seen_df.add(df_str)
                try:
                    content = df.read_text(errors="replace")
                except OSError:
                    continue
                for m in FROM_RE.finditer(content):
                    image = m.group(1).strip()
                    if not image or image.lower() == "scratch" or image.startswith("$"):
                        continue
                    # Normalize registry prefixes
                    for prefix in DOCKER_REGISTRY_PREFIXES:
                        if image.startswith(prefix):
                            image = image[len(prefix):]
                            break
                    # Split name:tag (handle digests @sha256:... as version)
                    if "@" in image:
                        img_name, img_tag = image.split("@", 1)
                        img_tag = img_tag[:16]  # truncate sha256 digest
                    elif ":" in image.rsplit("/", 1)[-1]:
                        colon = image.rfind(":")
                        img_name, img_tag = image[:colon], image[colon + 1:]
                    else:
                        img_name, img_tag = image, "latest"
                    purl = f"pkg:container/{img_name}@{img_tag}"
                    if purl not in container_nodes:
                        container_nodes[purl] = {
                            "name": img_name.split("/")[-1],
                            "full_name": img_name,
                            "version": img_tag,
                            "repos": [],
                        }
                    if repo_name not in container_nodes[purl]["repos"]:
                        container_nodes[purl]["repos"].append(repo_name)
                    repo_to_containers.setdefault(repo_name, [])
                    if purl not in repo_to_containers[repo_name]:
                        repo_to_containers[repo_name].append(purl)

    print(f"  Container images: {len(container_nodes)} unique images across "
          f"{sum(1 for r in repo_to_containers if repo_to_containers[r])} repos")

    # ── Pre-compute layout positions ──────────────────────────────────────────
    random.seed(42)
    REPO_R = 5000
    PKG_ORBIT_MIN, PKG_ORBIT_MAX = 800, 2500

    repo_positions: dict[str, tuple[float, float]] = {}
    for i, repo_name in enumerate(all_repo_names):
        angle = 2 * math.pi * i / max(len(all_repo_names), 1)
        repo_positions[repo_name] = (REPO_R * math.cos(angle), REPO_R * math.sin(angle))

    ecosystems_list = list(ECOSYSTEM_COLORS.keys())
    pkg_positions: dict[str, tuple[float, float]] = {}
    for purl, repos in purl_to_repos.items():
        if repos:
            cx = sum(repo_positions[r][0] for r in repos if r in repo_positions) / len(repos)
            cy = sum(repo_positions[r][1] for r in repos if r in repo_positions) / len(repos)
        else:
            cx, cy = 0.0, 0.0
        eco = parse_purl(purl)[0]
        eco_idx = ecosystems_list.index(eco) if eco in ecosystems_list else 0
        eco_angle = 2 * math.pi * eco_idx / len(ecosystems_list)
        orbit = random.uniform(PKG_ORBIT_MIN, PKG_ORBIT_MAX)
        jitter = random.uniform(0, 300)
        angle = eco_angle + random.uniform(-0.4, 0.4)
        pkg_positions[purl] = (
            cx + orbit * math.cos(angle) + jitter * random.uniform(-1, 1),
            cy + orbit * math.sin(angle) + jitter * random.uniform(-1, 1),
        )

    # Action and container nodes: position near repos that use them
    ACTION_ORBIT = 3200
    CONTAINER_ORBIT = 3600
    for purl, meta in {**action_nodes, **container_nodes}.items():
        repos = meta["repos"]
        eco = "githubaction" if purl.startswith("pkg:githubaction") else "container"
        orbit_r = ACTION_ORBIT if eco == "githubaction" else CONTAINER_ORBIT
        if repos:
            cx = sum(repo_positions[r][0] for r in repos if r in repo_positions) / len(repos)
            cy = sum(repo_positions[r][1] for r in repos if r in repo_positions) / len(repos)
        else:
            cx, cy = 0.0, 0.0
        eco_idx = ecosystems_list.index(eco) if eco in ecosystems_list else 0
        angle = 2 * math.pi * eco_idx / len(ecosystems_list) + random.uniform(-0.5, 0.5)
        pkg_positions[purl] = (
            cx + orbit_r * math.cos(angle) + random.uniform(-200, 200),
            cy + orbit_r * math.sin(angle) + random.uniform(-200, 200),
        )

    # ── Build nodes ───────────────────────────────────────────────────────────
    nodes: list[dict] = []

    # pkg→pkg out-degree (direct dep count per package)
    out_deg: dict[str, int] = defaultdict(int)
    for src, _ in pkg_dep_edges:
        out_deg[src] += 1

    for repo_name in all_repo_names:
        pkg_count = len(repo_to_purls[repo_name])
        rx, ry = repo_positions.get(repo_name, (0.0, 0.0))
        nodes.append({
            "key": f"repo:{repo_name}",
            "attributes": {
                "label": repo_name,
                "name": repo_name,
                "type": "repository",
                "color": REPO_COLOR,
                "size": max(8, min(30, 8 + pkg_count * 0.08)),
                "x": rx, "y": ry,
                "package_count": pkg_count,
                "ecosystem": "repository",
                "version": "", "purl": "", "repos": [], "repo_count": 0, "license": "",
                "has_conflict": False, "conflict_versions": [],
                "dep_count": 0, "in_org": True,
            },
        })

    for purl, pkg in packages_meta.items():
        ecosystem, name, version = parse_purl(purl)
        repos = purl_to_repos.get(purl, [])
        repo_count = len(repos)
        px, py = pkg_positions.get(purl, (random.uniform(-1000, 1000), random.uniform(-1000, 1000)))

        cdx_lic  = purl_to_cdx_license.get(purl, "")
        spdx_lic = pkg.get("licenseDeclared", "NOASSERTION")
        if cdx_lic and cdx_lic not in NOASSERTION_VALUES:
            license_val, license_source = cdx_lic, "cdx"
        elif spdx_lic and spdx_lic not in NOASSERTION_VALUES:
            license_val, license_source = spdx_lic, "spdx"
        else:
            license_val, license_source = "NOASSERTION", "noassertion"

        nodes.append({
            "key": purl,
            "attributes": {
                "label": f"{name}@{version}" if version else name,
                "name": name,
                "version": version,
                "purl": purl,
                "type": "package",
                "ecosystem": ecosystem,
                "color": ECOSYSTEM_COLORS.get(ecosystem, ECOSYSTEM_COLORS["unknown"]),
                "size": max(3, min(18, 3 + repo_count * 1.5)),
                "x": px, "y": py,
                "repos": repos,
                "repo_count": repo_count,
                "license": license_val,
                "license_source": license_source,
                "direct_in_repos": sorted(direct_in_repos.get(purl, [])),
                "has_conflict": purl in conflict_info,
                "conflict_versions": conflict_info.get(purl, []),
                "dep_count": out_deg.get(purl, 0),
                "in_org": True,
            },
        })

    # ── Build action + container nodes ────────────────────────────────────────
    for purl, meta in action_nodes.items():
        repos = meta["repos"]
        px, py = pkg_positions.get(purl, (random.uniform(-1000, 1000), random.uniform(-1000, 1000)))
        nodes.append({
            "key": purl,
            "attributes": {
                "label": f"{meta['name']}@{meta['version']}",
                "name": meta["name"],
                "version": meta["version"],
                "purl": purl,
                "type": "package",
                "ecosystem": "githubaction",
                "color": ECOSYSTEM_COLORS["githubaction"],
                "size": max(3, min(12, 3 + len(repos) * 1.2)),
                "x": px, "y": py,
                "repos": repos,
                "repo_count": len(repos),
                "license": "NOASSERTION",
                "license_source": "noassertion",
                "direct_in_repos": sorted(repos),
                "has_conflict": False, "conflict_versions": [],
                "dep_count": 0, "in_org": False,
                "full_name": meta["full_name"],
            },
        })

    for purl, meta in container_nodes.items():
        repos = meta["repos"]
        px, py = pkg_positions.get(purl, (random.uniform(-1000, 1000), random.uniform(-1000, 1000)))
        nodes.append({
            "key": purl,
            "attributes": {
                "label": f"{meta['full_name']}:{meta['version']}" if meta["version"] != "latest" else meta["full_name"],
                "name": meta["name"],
                "version": meta["version"],
                "purl": purl,
                "type": "package",
                "ecosystem": "container",
                "color": ECOSYSTEM_COLORS["container"],
                "size": max(3, min(14, 4 + len(repos) * 1.5)),
                "x": px, "y": py,
                "repos": repos,
                "repo_count": len(repos),
                "license": "NOASSERTION",
                "license_source": "noassertion",
                "direct_in_repos": sorted(repos),
                "has_conflict": False, "conflict_versions": [],
                "dep_count": 0, "in_org": False,
                "full_name": meta["full_name"],
            },
        })

    # ── Build edges ───────────────────────────────────────────────────────────
    edges: list[dict] = []

    # repo → package (CONTAINS)
    contains_count = 0
    for repo_name, purls in repo_to_purls.items():
        repo_key = f"repo:{repo_name}"
        for purl in purls:
            edges.append({"source": repo_key, "target": purl, "attributes": {"type": "contains", "_src": "cdx"}})
            contains_count += 1

    # repo → action (USES_ACTION)
    for repo_name, purls in repo_to_actions.items():
        repo_key = f"repo:{repo_name}"
        for purl in purls:
            edges.append({"source": repo_key, "target": purl, "attributes": {"type": "uses_action"}})
            contains_count += 1

    # repo → container image (USES_IMAGE)
    for repo_name, purls in repo_to_containers.items():
        repo_key = f"repo:{repo_name}"
        for purl in purls:
            edges.append({"source": repo_key, "target": purl, "attributes": {"type": "uses_image"}})
            contains_count += 1

    # package → package (DEPENDS_ON)
    depends_count = 0
    for src_purl, tgt_purl in pkg_dep_edges:
        edges.append({"source": src_purl, "target": tgt_purl, "attributes": {"type": "depends_on", "_src": "cdx"}})
        depends_count += 1

    # ── Summary stats ─────────────────────────────────────────────────────────
    ecosystem_counts: dict[str, int] = {}
    for n in nodes:
        if n["attributes"]["type"] == "package":
            eco = n["attributes"]["ecosystem"]
            ecosystem_counts[eco] = ecosystem_counts.get(eco, 0) + 1
    action_edge_count = sum(1 for e in edges if e["attributes"]["type"] == "uses_action")
    container_edge_count = sum(1 for e in edges if e["attributes"]["type"] == "uses_image")

    top_shared = sorted(
        [(p, len(r)) for p, r in purl_to_repos.items()],
        key=lambda x: -x[1],
    )[:20]

    graph = {
        "attributes": {
            "name": f"{os.environ.get('ORG', 'myorg')}-dependency-graph",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_repos": len(all_repo_names),
            "total_packages": len(packages_meta),
            "total_actions": len(action_nodes),
            "total_containers": len(container_nodes),
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "total_contains_edges": contains_count,
            "total_depends_edges": depends_count,
            "total_action_edges": action_edge_count,
            "total_container_edges": container_edge_count,
            "cdx_repos_with_deps": cdx_with_deps,
            "ecosystem_counts": ecosystem_counts,
            "total_version_conflicts": total_conflict_names,
            "top_shared_packages": [{"purl": p, "repo_count": c} for p, c in top_shared],
        },
        "nodes": nodes,
        "edges": edges,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(graph))
    print(
        f"Graph: {len(all_repo_names)} repo nodes + {len(packages_meta)} pkg nodes"
        f" + {len(action_nodes)} action nodes + {len(container_nodes)} container nodes"
        f" = {len(nodes)} total"
    )
    print(f"  Contains edges (repo→pkg):     {sum(1 for e in edges if e['attributes']['type']=='contains'):,}")
    print(f"  Uses-action edges (repo→act):  {action_edge_count:,}")
    print(f"  Uses-image edges (repo→img):   {container_edge_count:,}")
    print(f"  Depends-on edges (pkg→pkg):    {depends_count:,}  (from {cdx_with_deps} repos)")
    print(f"  Total edges: {len(edges):,}")
    print(f"  Version conflicts: {total_conflict_names}")
    print(f"Top shared packages:")
    for purl, count in top_shared[:5]:
        _, name, ver = parse_purl(purl)
        print(f"  {count:3d} repos  {name}@{ver}")


if __name__ == "__main__":
    spdx      = Path(sys.argv[1])
    per_repo  = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("")
    output    = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("output/graph/dep_graph.json")
    repos_dir = Path(sys.argv[4]) if len(sys.argv) > 4 else Path("repos")
    build(spdx, per_repo, output, repos_dir)
