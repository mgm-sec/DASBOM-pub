#!/usr/bin/env python3
"""Merge all per-repo SPDX 2.3 JSON files into a single org-level SPDX document."""

import json
import sys
import os
import uuid
from pathlib import Path
from datetime import datetime, timezone


def purl_from_package(pkg: dict) -> str | None:
    for ref in pkg.get("externalRefs", []):
        if ref.get("referenceType") == "purl":
            return ref["referenceLocator"]
    return None


def spdxid_from_purl(purl: str) -> str:
    safe = purl.replace(":", "-").replace("/", "-").replace("@", "-").replace("+", "-")
    safe = "".join(c if c.isalnum() or c == "-" else "-" for c in safe)
    return f"SPDXRef-{safe[:200]}"


def merge(input_dir: Path, output_path: Path) -> None:
    spdx_files = sorted(input_dir.glob("*.spdx.json"))
    if not spdx_files:
        print(f"No SPDX files in {input_dir}", file=sys.stderr)
        sys.exit(1)

    packages: dict[str, dict] = {}    # purl → package
    relationships: list[dict] = []
    repo_to_purls: dict[str, list[str]] = {}

    for spdx_file in spdx_files:
        repo_name = spdx_file.stem.replace(".spdx", "")
        try:
            doc = json.loads(spdx_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"Skip {spdx_file.name}: {e}", file=sys.stderr)
            continue

        # Build local SPDXID → purl map for relationship remapping
        local_id_to_purl: dict[str, str] = {}
        for pkg in doc.get("packages", []):
            purl = purl_from_package(pkg)
            if not purl:
                continue
            local_id_to_purl[pkg["SPDXID"]] = purl
            if purl not in packages:
                pkg_copy = {k: v for k, v in pkg.items()}
                pkg_copy["SPDXID"] = spdxid_from_purl(purl)
                packages[purl] = pkg_copy
            repo_to_purls.setdefault(repo_name, []).append(purl)

        for rel in doc.get("relationships", []):
            src_id = rel.get("spdxElementId", "")
            dst_id = rel.get("relatedSpdxElement", "")
            rel_type = rel.get("relationshipType", "")

            src_purl = local_id_to_purl.get(src_id)
            dst_purl = local_id_to_purl.get(dst_id)

            if src_purl and dst_purl and rel_type == "DEPENDS_ON":
                relationships.append({
                    "spdxElementId": spdxid_from_purl(src_purl),
                    "relationshipType": "DEPENDS_ON",
                    "relatedSpdxElement": spdxid_from_purl(dst_purl),
                })

    # Add DESCRIBES relationships (document → each unique package)
    for purl, pkg in packages.items():
        relationships.append({
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": pkg["SPDXID"],
        })

    doc_out = {
        "SPDXID": "SPDXRef-DOCUMENT",
        "spdxVersion": "SPDX-2.3",
        "creationInfo": {
            "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "creators": ["Tool: sbom-viz-merger"],
        },
        "name": f"{os.environ.get('ORG', 'myorg')}-org-sbom",
        "dataLicense": "CC0-1.0",
        "documentNamespace": f"https://github.com/{os.environ.get('ORG', 'myorg')}/sbom-{uuid.uuid4()}",
        "packages": list(packages.values()),
        "relationships": relationships,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(doc_out, indent=2))
    print(f"SPDX: {len(packages)} unique packages, {len(relationships)} relationships → {output_path}")


if __name__ == "__main__":
    merge(Path(sys.argv[1]), Path(sys.argv[2]))
