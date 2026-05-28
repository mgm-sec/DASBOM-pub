# DASBOM

Interactive supply chain security visualizer. Point it at any GitHub org or repo — it generates a full SBOM, enriches transitive deps from registries, audits CVEs, and renders an interactive dependency graph in your browser.

---

## ⚠️ Disclaimer

This project is **vibe-coded** — built rapidly with AI assistance. As such:

- **The code may contain bugs, logic errors, or incorrect results.** CVE counts, version comparisons, and graph relationships should be treated as indicative, not authoritative.
- **Do not run this tool on untrusted infrastructure.** The pipeline clones external repositories and executes shell scripts. Malicious repository content could potentially influence shell behavior.
- **No security guarantees.** This tool is not audited. Do not use scan results as the sole basis for security decisions.
- **External API calls are made at runtime** to GitHub, OSV.dev, npm, PyPI, RubyGems, crates.io, and other package registries. Review the pipeline scripts before running in sensitive environments.
- **The Docker container requires a GitHub token** with repo access. Scope it to the minimum necessary (`public_repo` for public repos only).
- The container runs as a **non-root user** with dropped Linux capabilities and a SHA256-pinned base image.

Use at your own risk. Contributions and bug reports welcome.

---

## Security

Vulnerabilities can be reported via [GitHub Issues](https://github.com/mgm-sec/DASBOM-pub/issues). See [SECURITY.md](SECURITY.md) for scope and policy.

---

## Docker

```bash
# Recommended: docker compose (security flags pre-configured)
GH_TOKEN=<your_token> docker compose up --build

# Alternative: plain docker run
docker build -t sbom-viz .
docker run -p 8080:8080 -e GH_TOKEN=<your_token> sbom-viz
```

Open **http://localhost:8080**, enter one or more GitHub targets, click **Initiate Scan**.

Without `GH_TOKEN` the container still runs — public repos only. Create a token at https://github.com/settings/tokens.

### Supported target formats

| Input | What it scans |
|-------|---------------|
| `github.com/myorg` | All non-archived repos in org |
| `github.com/owner/repo` | Single repository |
| `myorg` | Org shorthand |
| `owner/repo` | Repo shorthand |

Mix and match — scan multiple orgs and individual repos in one run. Add or remove targets from within the visualization after the initial scan.

---

## What it produces

- **Interactive dependency graph** — repos → packages → transitive deps, radial BFS layout
- **Security overlay** — CVE badges, abandoned/outdated detection, worst-chain propagation
- **Version conflict detection** — same package, different versions across repos
- **License classification** — Permissive / Copyleft / Proprietary / Unspecified
- **Trend sparklines** — daily snapshots of CVE/package counts over time

---

## Pipeline steps

| Step | Script | What it does |
|------|--------|--------------|
| 00 | `00_check_tools.sh` | Verify required tools (skipped in Docker) |
| 00b | `00b_parse_targets.sh` | Resolve org/repo URLs → `repos_to_clone.txt` |
| 02 | `02_clone_repos.sh` | Shallow-clone repos (8 parallel) |
| 03 | `03_generate_sbom.sh` | `syft` → per-repo SPDX 2.3 + CycloneDX 1.5 |
| 05 | `05_merge_sbom.sh` | Merge into org-level SPDX + CDX |
| 06 | `06_build_graph.sh` | Build graphology JSON — nodes + edges + conflicts |
| 07 | `07_setup_viz.sh` | Vendor sigma.js + graphology |
| 08b | `08b_lockfile_deps.sh` | Full transitive closure from lock files |
| 08 | `08_enrich_deps.sh` | Registry API enrichment + radial BFS layout |
| 09 | `09_security_audit.sh` | OSV.dev CVE scan + latest-version check |
| 10 | `10_snapshot_history.sh` | Append daily snapshot to `history.json` |

---

## Registry coverage

| Ecosystem | Source |
|-----------|--------|
| npm | registry.npmjs.org |
| PyPI | pypi.org |
| RubyGems | rubygems.org |
| Cargo | crates.io |
| Go | proxy.golang.org |
| Composer | repo.packagist.org |
| Maven | repo1.maven.org |
| Swift / GitHub Actions / Docker | metadata only |

---

## Output files

| Path | Description |
|------|-------------|
| `output/viz/index.html` | Self-contained visualization app |
| `output/viz/lib/` | Vendored JS (sigma.js, graphology) |
| `output/graph/dep_graph.json` | Full graph JSON — gitignored, regenerated |
| `output/sbom/` | Per-repo + org-level SBOMs — gitignored |
| `output/cache/` | Registry dep fetch cache — gitignored |
| `repos/` | Cloned repos — gitignored |

---

## Tech stack

- **Graph**: [graphology](https://graphology.github.io) + [sigma.js](https://www.sigmajs.org)
- **SBOMs**: [syft v1.44.0](https://github.com/anchore/syft) (SPDX 2.3 + CycloneDX 1.5)
- **CVEs**: [OSV.dev](https://osv.dev) batch API
- **Layout**: custom radial BFS (`scripts/python/recompute_layout.py`)
- **Web server**: Flask 3.1.3 (Docker mode) — `server.py`
- **Pipeline**: bash + Python 3 stdlib

---

## License

CC0-1.0 — see [LICENSE](LICENSE)
