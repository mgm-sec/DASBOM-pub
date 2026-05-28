#!/usr/bin/env python3
"""Merge all per-repo CycloneDX 1.5 JSON files into a single org-level BOM."""

import json
import os
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone


def stable_ref(purl: str) -> str:
    safe = purl.replace(":", "-").replace("/", "-").replace("@", "-").replace("+", "-")
    safe = "".join(c if c.isalnum() or c == "-" else "-" for c in safe)
    return safe[:200]


def merge(input_dir: Path, output_path: Path) -> None:
    cdx_files = sorted(input_dir.glob("*.cdx.json"))
    if not cdx_files:
        print(f"No CycloneDX files in {input_dir}", file=sys.stderr)
        sys.exit(1)

    components: dict[str, dict] = {}     # purl → component
    dep_map: dict[str, set[str]] = {}    # purl → set of dependency purls

    for cdx_file in cdx_files:
        try:
            bom = json.loads(cdx_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"Skip {cdx_file.name}: {e}", file=sys.stderr)
            continue

        # Map local bom-ref → purl for dependency remapping
        local_ref_to_purl: dict[str, str] = {}
        for comp in bom.get("components", []):
            purl = comp.get("purl")
            if not purl:
                continue
            local_ref_to_purl[comp.get("bom-ref", "")] = purl
            if purl not in components:
                comp_copy = {k: v for k, v in comp.items()}
                comp_copy["bom-ref"] = stable_ref(purl)
                components[purl] = comp_copy

        for dep in bom.get("dependencies", []):
            src_purl = local_ref_to_purl.get(dep.get("ref", ""))
            if not src_purl:
                continue
            dep_map.setdefault(src_purl, set())
            for dst_ref in dep.get("dependsOn", []):
                dst_purl = local_ref_to_purl.get(dst_ref)
                if dst_purl:
                    dep_map[src_purl].add(dst_purl)

    # Build root metadata component representing the org
    org = os.environ.get("ORG", "myorg")
    root_ref = f"{org}-org"
    all_component_refs = [stable_ref(p) for p in components]

    dependencies = [
        {
            "ref": root_ref,
            "dependsOn": all_component_refs,
        }
    ] + [
        {
            "ref": stable_ref(purl),
            "dependsOn": [stable_ref(d) for d in sorted(deps)],
        }
        for purl, deps in dep_map.items()
        if deps
    ]

    bom_out = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "metadata": {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tools": [{"name": "sbom-viz-merger", "version": "1.0"}],
            "component": {
                "bom-ref": root_ref,
                "type": "application",
                "name": f"{org}-org",
                "version": "latest",
            },
        },
        "components": list(components.values()),
        "dependencies": dependencies,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(bom_out, indent=2))
    print(f"CycloneDX: {len(components)} unique components, {len(dep_map)} dep entries → {output_path}")


if __name__ == "__main__":
    merge(Path(sys.argv[1]), Path(sys.argv[2]))
