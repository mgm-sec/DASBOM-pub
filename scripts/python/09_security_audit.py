#!/usr/bin/env python3
"""
Batch security audit for all packages in dep_graph.json:
  - CVE data via OSV.dev batch API
  - Latest version via registry APIs (parallel)
Output: output/security_audit.json
"""

import json, sys, re, time, math, hashlib, urllib.request, urllib.parse, urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from datetime import datetime, timezone, timedelta


TIMEOUT      = 12
MAX_WORKERS  = 30
VERSION_CACHE_DIR = Path("output/cache/versions")
VERSION_CACHE_TTL = timedelta(hours=24)


# ── PURL utilities ────────────────────────────────────────────────────────────

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


def purl_to_osv(purl: str):
    """Returns (ecosystem_str, name_str) for OSV.dev, or None."""
    eco, namepath = parse_purl(purl)
    if not eco or not namepath:
        return None
    parts = namepath.split("/")
    table = {
        "pypi":      ("PyPI",           namepath),
        "npm":       ("npm",            namepath),
        "golang":    ("Go",             namepath),
        "gem":       ("RubyGems",       parts[-1]),
        "cargo":     ("crates.io",      namepath),
        "maven":     ("Maven",          f"{parts[0]}:{parts[1]}" if len(parts) >= 2 else namepath),
        "nuget":     ("NuGet",          namepath),
        "composer":  ("Packagist",      namepath),
        "cocoapods": ("CocoaPods",      parts[-1]),
        "hackage":   ("Hackage",        namepath),
        "hex":       ("Hex",            parts[-1]),
        "pub":       ("Pub",            namepath),
        "cran":      ("CRAN",           namepath),
        "github":    ("GitHub Actions", "/".join(parts[:2])),
    }
    if eco == "swift":
        if len(parts) >= 3:
            host = parts[0]
            org  = parts[1]
            repo = parts[2].removesuffix(".git")
            return ("SwiftURL", f"https://{host}/{org}/{repo}")
        return None
    return table.get(eco)


# ── OSV.dev batch query ───────────────────────────────────────────────────────

def osv_batch_query(queries: list[dict]) -> list[dict]:
    """Submit queries in batches of 1000. Returns results[] parallel to queries."""
    BATCH = 1000
    all_results = []
    for i in range(0, len(queries), BATCH):
        batch = queries[i : i + BATCH]
        payload = json.dumps({"queries": batch}).encode()
        req = urllib.request.Request(
            "https://api.osv.dev/v1/querybatch",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
                all_results.extend(data.get("results", [{}] * len(batch)))
        except Exception as e:
            print(f"  [OSV batch error] {e}")
            all_results.extend([{}] * len(batch))
        if i + BATCH < len(queries):
            time.sleep(0.5)
    return all_results


def osv_fetch_vulns(vuln_ids: list[str]) -> dict[str, dict]:
    """Fetch full vuln details for each ID via /v1/vulns/{id} (parallel).
    Returns mapping vuln_id → full vuln dict."""
    results: dict[str, dict] = {}

    def fetch_one(vid: str) -> tuple[str, dict | None]:
        try:
            url = f"https://api.osv.dev/v1/vulns/{urllib.parse.quote(vid, safe='')}"
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                return vid, json.loads(r.read())
        except Exception:
            return vid, None

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_one, vid): vid for vid in vuln_ids}
        done = 0
        for fut in as_completed(futs):
            vid, data = fut.result()
            if data:
                results[vid] = data
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(vuln_ids)} vulns fetched")
    return results


# ── CVSS v3 score from vector ─────────────────────────────────────────────────

def cvss3_score(vector: str) -> float | None:
    """Parse CVSS v3.x vector string and return base score (0.0–10.0)."""
    if not vector or not vector.startswith("CVSS:"):
        return None
    parts = vector.split("/")
    if len(parts) < 9:
        return None
    metrics: dict[str, str] = {}
    for part in parts[1:]:
        if ":" in part:
            k, v = part.split(":", 1)
            metrics[k] = v

    AV  = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.20}.get(metrics.get("AV",""), None)
    AC  = {"L": 0.77, "H": 0.44}.get(metrics.get("AC",""), None)
    S   = metrics.get("S", "U")
    PR_map = {"N": (0.85, 0.85), "L": (0.62, 0.68), "H": (0.27, 0.50)}
    pr_vals = PR_map.get(metrics.get("PR",""), None)
    PR  = pr_vals[1 if S == "C" else 0] if pr_vals else None
    UI  = {"N": 0.85, "R": 0.62}.get(metrics.get("UI",""), None)
    CIA = {"N": 0.00, "L": 0.22, "H": 0.56}
    C   = CIA.get(metrics.get("C",""), None)
    I   = CIA.get(metrics.get("I",""), None)
    A   = CIA.get(metrics.get("A",""), None)

    if any(x is None for x in [AV, AC, PR, UI, C, I, A]):
        return None

    import math
    def roundup(x: float) -> float:
        return math.ceil(x * 10) / 10

    ISS = 1 - (1 - C) * (1 - I) * (1 - A)
    if S == "U":
        impact = 6.42 * ISS
    else:
        impact = 7.52 * (ISS - 0.029) - 3.25 * ((ISS - 0.02) ** 15)
    exploitability = 8.22 * AV * AC * PR * UI

    if impact <= 0:
        return 0.0
    if S == "U":
        return roundup(min(impact + exploitability, 10.0))
    else:
        return roundup(min(1.08 * (impact + exploitability), 10.0))


# ── Latest version + release age fetchers ────────────────────────────────────

def _cached_fetch(key: str, fetch_fn):
    """Return cached (version, age_str) if fresh, else call fetch_fn() and cache result.
    Failures (version=None) are NOT cached so they are retried on the next run."""
    VERSION_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = VERSION_CACHE_DIR / (hashlib.sha1(key.encode()).hexdigest() + ".json")
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            saved_at = datetime.fromisoformat(cached["_saved_at"])
            if datetime.now(timezone.utc) - saved_at < VERSION_CACHE_TTL:
                return cached.get("version"), cached.get("age")
        except Exception:
            pass
    result = fetch_fn()
    if result[0] is not None:  # only cache successes
        try:
            cache_file.write_text(json.dumps({
                "_saved_at": datetime.now(timezone.utc).isoformat(),
                "version": result[0],
                "age": result[1],
            }))
        except Exception:
            pass
    return result


def _urlopen_limited(req_or_url, max_bytes: int = 512 * 1024) -> bytes:
    """Open URL and read up to max_bytes to prevent giant download hangs."""
    with urllib.request.urlopen(req_or_url, timeout=TIMEOUT) as r:
        chunks = []
        read = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            read += len(chunk)
            if read > max_bytes:
                # Read enough — parse what we have (may fail, caller handles)
                chunks.append(chunk)
                break
            chunks.append(chunk)
        return b"".join(chunks)


def fetch_latest_and_age(eco: str, namepath: str) -> tuple[str | None, str | None]:
    """Returns (latest_version, last_release_iso_date). Either may be None."""
    key = f"{eco}:{namepath}"
    return _cached_fetch(key, lambda: _fetch_latest_and_age_uncached(eco, namepath))


def _fetch_latest_and_age_uncached(eco: str, namepath: str) -> tuple[str | None, str | None]:
    parts = namepath.split("/")
    name  = parts[-1]
    try:
        if eco == "pypi":
            # info.version is always within first 64 KB (before "releases" section).
            # For huge packages (>256 KB), JSON parse fails on truncated data → regex fallback.
            url = f"https://pypi.org/pypi/{urllib.parse.quote(namepath)}/json"
            raw = _urlopen_limited(urllib.request.Request(url), 64 * 1024)
            try:
                data = json.loads(raw)
                latest = data["info"]["version"]
                releases = data.get("releases", {}).get(latest, [])
                age_str = releases[0]["upload_time"] if releases else None
            except (json.JSONDecodeError, KeyError, ValueError):
                # Truncated — extract version via regex (first "version" key = info.version)
                m = re.search(rb'"version"\s*:\s*"([^"]+)"', raw)
                latest = m.group(1).decode() if m else None
                age_str = None
            return latest, age_str

        elif eco == "npm":
            # dist-tags.latest is within the first ~200 bytes of the abbreviated packument.
            # `modified` is at the very end (160 KB+) — skip it; not needed for is_latest.
            url = f"https://registry.npmjs.org/{urllib.parse.quote(namepath, safe='@/')}"
            req = urllib.request.Request(url, headers={
                "Accept": "application/vnd.npm.install-v1+json",
            })
            raw = _urlopen_limited(req, 4 * 1024)
            m = re.search(rb'"latest"\s*:\s*"([^"]+)"', raw)
            latest = m.group(1).decode() if m else None
            return latest, None

        elif eco == "maven":
            if len(parts) < 2:
                return None, None
            group, artifact = parts[0], parts[1]
            url = (f"https://search.maven.org/solrsearch/select"
                   f"?q=g:{urllib.parse.quote(group)}+AND+a:{urllib.parse.quote(artifact)}"
                   f"&rows=1&wt=json")
            req = urllib.request.Request(url, headers={"User-Agent": "sbom-viz/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.loads(r.read(32 * 1024))
            docs = (data.get("response") or {}).get("docs") or []
            if not docs:
                return None, None
            latest = docs[0].get("latestVersion")
            ts_ms  = docs[0].get("timestamp")
            age_str = (datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
                       if ts_ms else None)
            return latest, age_str

        elif eco == "golang":
            url = f"https://proxy.golang.org/{namepath}/@latest"
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                data = json.loads(r.read(32 * 1024))
            return data.get("Version"), data.get("Time")

        elif eco == "gem":
            url = f"https://rubygems.org/api/v1/versions/{urllib.parse.quote(name)}/latest.json"
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                data = json.loads(r.read(32 * 1024))
            return data.get("version"), data.get("created_at")

        elif eco == "cargo":
            req = urllib.request.Request(
                f"https://crates.io/api/v1/crates/{urllib.parse.quote(name)}",
                headers={"User-Agent": "sbom-viz/1.0"},
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.loads(r.read(64 * 1024))
            crate = data.get("crate", {})
            return crate.get("max_version"), crate.get("updated_at")

        elif eco == "nuget":
            url = f"https://api.nuget.org/v3-flatcontainer/{namepath.lower()}/index.json"
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                vs = json.loads(r.read(32 * 1024)).get("versions", [])
            return (vs[-1] if vs else None), None

        elif eco == "composer":
            # Use packagist v2 API — much lighter than /packages/{name}.json
            if "/" not in namepath:
                return None, None
            vendor, pkg = namepath.split("/", 1)
            url = f"https://repo.packagist.org/p2/{urllib.parse.quote(vendor)}/{urllib.parse.quote(pkg)}.json"
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                raw = json.loads(r.read(256 * 1024))
            versions_list = list((raw.get("packages") or {}).values())
            versions_list = versions_list[0] if versions_list else []
            stable = [v for v in versions_list
                      if not (v.get("version", "").startswith("dev-")
                              or re.search(r"-(alpha|beta|rc)\d*$", v.get("version", ""), re.I))]
            best = stable[0] if stable else (versions_list[0] if versions_list else None)
            if not best:
                return None, None
            return (best.get("version") or "").lstrip("v"), best.get("time")

    except Exception:
        pass
    return None, None


def fetch_latest(eco: str, namepath: str) -> str | None:
    return fetch_latest_and_age(eco, namepath)[0]


# ── Confusion risk: check if package exists on public registry ────────────────

def check_public_registry_exists(eco: str, namepath: str) -> bool | None:
    """Returns True if found on public registry, False if 404, None if unknown."""
    parts = namepath.split("/")
    name  = parts[-1]
    try:
        if eco == "composer":
            url = f"https://packagist.org/packages/{namepath}.json"
        elif eco == "npm":
            url = f"https://registry.npmjs.org/{urllib.parse.quote(namepath, safe='@/')}"
        elif eco == "pypi":
            url = f"https://pypi.org/pypi/{urllib.parse.quote(namepath)}/json"
        else:
            return None
        req = urllib.request.Request(url, headers={"User-Agent": "sbom-viz/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
    except Exception:
        pass
    return None


# ── CVE helpers ───────────────────────────────────────────────────────────────

SEV_RANK = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}


def severity_of(vuln: dict) -> str:
    s = (vuln.get("database_specific") or {}).get("severity", "")
    return s.upper() if s else "UNKNOWN"


def cvss_score_of(vuln: dict) -> float | None:
    for entry in (vuln.get("severity") or []):
        score = cvss3_score(entry.get("score", ""))
        if score is not None:
            return score
    return None


def max_severity(vulns: list[dict]) -> str | None:
    if not vulns:
        return None
    return max((severity_of(v) for v in vulns), key=lambda s: SEV_RANK.get(s, 0))


def max_cvss(vulns: list[dict]) -> float | None:
    scores = [cvss_score_of(v) for v in vulns]
    scores = [s for s in scores if s is not None]
    return max(scores) if scores else None


def fixed_in(vuln: dict) -> str | None:
    for aff in vuln.get("affected", []):
        for rng in aff.get("ranges", []):
            fix = next((e.get("fixed") for e in rng.get("events", []) if e.get("fixed")), None)
            if fix:
                return fix
    return None


def compact_vuln(v: dict) -> dict:
    alias = next((a for a in v.get("aliases", []) if a.startswith("CVE-")), v.get("id", ""))
    return {
        "id":         v.get("id", ""),
        "alias":      alias,
        "severity":   severity_of(v),
        "cvss_score": cvss_score_of(v),
        "summary":    (v.get("summary") or "")[:200],
        "fixed":      fixed_in(v),
        "url":        f"https://osv.dev/vulnerability/{v.get('id', '')}",
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    graph_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/graph/dep_graph.json")
    audit_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("output/security_audit.json")

    print(f"Loading {graph_path}...")
    g = json.loads(graph_path.read_text())
    pkg_nodes = [n for n in g["nodes"] if n["attributes"]["type"] == "package"]
    print(f"  {len(pkg_nodes)} package nodes")

    # ── OSV batch ────────────────────────────────────────────────────────────
    print("Building OSV.dev batch queries...")
    osv_queries = []
    purl_to_osv_idx: dict[str, int] = {}

    for n in pkg_nodes:
        purl    = n["key"]
        version = n["attributes"].get("version", "")
        osv     = purl_to_osv(purl)
        if osv and version:
            eco_str, name_str = osv
            purl_to_osv_idx[purl] = len(osv_queries)
            osv_queries.append({
                "package": {"name": name_str, "ecosystem": eco_str},
                "version": version,
            })

    print(f"  {len(osv_queries)} queries for OSV-supported ecosystems "
          f"({len(pkg_nodes)-len(osv_queries)} packages skipped — no OSV support)")
    osv_results = osv_batch_query(osv_queries)
    print(f"  OSV batch done. Collecting unique vuln IDs...")

    # ── Fetch full vuln details for severity + CVSS scores ───────────────────
    unique_ids: set[str] = set()
    for res in osv_results:
        for v in (res.get("vulns") or []):
            vid = v.get("id")
            if vid:
                unique_ids.add(vid)
    print(f"  {len(unique_ids)} unique vuln IDs — fetching full details...")
    full_vulns = osv_fetch_vulns(list(unique_ids))
    print(f"  Full vuln details: {len(full_vulns)}/{len(unique_ids)} fetched.")

    # ── Latest version (parallel) ────────────────────────────────────────────
    ABANDONED_THRESHOLD = timedelta(days=365 * 2)  # 2 years without release = abandoned
    now_utc = datetime.now(timezone.utc)

    print(f"Fetching latest versions + release ages ({len(pkg_nodes)} packages, {MAX_WORKERS} workers)...")

    def fetch_latest_node(n):
        purl = n["key"]
        eco, namepath = parse_purl(purl)
        if not eco or not namepath:
            return purl, None, None
        latest, age_str = fetch_latest_and_age(eco, namepath)
        return purl, latest, age_str

    purl_to_latest:      dict[str, str] = {}
    purl_to_last_release: dict[str, str] = {}  # purl → ISO date string
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_latest_node, n): n for n in pkg_nodes}
        for fut in as_completed(futs):
            purl, latest, age_str = fut.result()
            if latest:
                purl_to_latest[purl] = latest
            if age_str:
                purl_to_last_release[purl] = age_str[:10]  # keep YYYY-MM-DD
            done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(pkg_nodes)} done, {len(purl_to_latest)} versions found")
    print(f"  Latest versions: found {len(purl_to_latest)}/{len(pkg_nodes)}")
    print(f"  Release dates:   found {len(purl_to_last_release)}/{len(pkg_nodes)}")

    # ── Confusion risk detection ──────────────────────────────────────────────
    # Check org-namespaced packages (composer vendor/pkg, npm @org/pkg) that
    # are NOT on the public registry — prime targets for dependency confusion attacks.
    CONFUSION_ECOS = ("composer", "npm")

    def looks_namespaced(eco: str, namepath: str) -> bool:
        if eco == "composer" and "/" in namepath:
            vendor = namepath.split("/")[0]
            return vendor not in ("php", "ext", "lib", "pear", "pecl")
        if eco == "npm" and namepath.startswith("@"):
            return True
        return False

    confusion_candidates = [
        n for n in pkg_nodes
        if n["attributes"].get("in_org", True)
        and parse_purl(n["key"])[0] in CONFUSION_ECOS
        and looks_namespaced(*parse_purl(n["key"]))
        and n["key"] not in purl_to_latest  # not found on public registry during version fetch
    ]
    print(f"Checking {len(confusion_candidates)} potential confusion-risk packages...")

    purl_confusion_risk: dict[str, bool] = {}

    def check_confusion(n):
        purl = n["key"]
        eco, namepath = parse_purl(purl)
        exists = check_public_registry_exists(eco, namepath)
        return purl, exists

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(check_confusion, n): n for n in confusion_candidates}
        for fut in as_completed(futs):
            purl, exists = fut.result()
            if exists is False:
                purl_confusion_risk[purl] = True

    print(f"  Confusion risk packages: {len(purl_confusion_risk)}")

    # ── Assemble audit ────────────────────────────────────────────────────────
    print("Assembling audit data...")
    audit: dict[str, dict] = {}

    for n in pkg_nodes:
        purl    = n["key"]
        current = n["attributes"].get("version", "")
        latest  = purl_to_latest.get(purl)

        raw_vulns: list[dict] = []
        if purl in purl_to_osv_idx:
            idx = purl_to_osv_idx[purl]
            if idx < len(osv_results):
                raw_vulns = osv_results[idx].get("vulns") or []

        # Replace stub vulns with full details when available
        vulns = [full_vulns.get(v.get("id"), v) for v in raw_vulns]

        max_sev = max_severity(vulns)

        # version comparison (strip leading v)
        def strip_v(s): return (s or "").lstrip("v")
        is_latest = (strip_v(latest) == strip_v(current)) if latest and current else None

        last_release = purl_to_last_release.get(purl)
        abandoned = False
        if last_release:
            try:
                release_dt = datetime.fromisoformat(last_release.replace("Z", "+00:00"))
                if release_dt.tzinfo is None:
                    release_dt = release_dt.replace(tzinfo=timezone.utc)
                abandoned = (now_utc - release_dt) > ABANDONED_THRESHOLD
            except (ValueError, TypeError):
                pass

        audit[purl] = {
            "cve_count":      len(vulns),
            "max_severity":   max_sev,
            "max_cvss":       max_cvss(vulns),
            "latest_version": latest,
            "current_version": current,
            "is_latest":      is_latest,
            "last_release":   last_release,
            "abandoned":      abandoned,
            "confusion_risk": purl_confusion_risk.get(purl, False),
            "vulns":          [compact_vuln(v) for v in vulns[:10]],
        }

    # ── Summary stats ─────────────────────────────────────────────────────────
    with_cve   = sum(1 for v in audit.values() if v["cve_count"] > 0)
    outdated   = sum(1 for v in audit.values() if v["is_latest"] is False)
    abandoned  = sum(1 for v in audit.values() if v.get("abandoned"))
    confusion  = sum(1 for v in audit.values() if v.get("confusion_risk"))
    critical   = sum(1 for v in audit.values() if v["max_severity"] == "CRITICAL")
    high       = sum(1 for v in audit.values() if v["max_severity"] == "HIGH")
    print(f"\nAudit summary:")
    print(f"  Packages with CVEs:  {with_cve}")
    print(f"    CRITICAL: {critical}")
    print(f"    HIGH:     {high}")
    print(f"  Outdated:        {outdated}")
    print(f"  Abandoned (2y+): {abandoned}")
    print(f"  Confusion risk:  {confusion}")
    print(f"  Up to date:      {sum(1 for v in audit.values() if v['is_latest'] is True)}")
    print(f"  No data:         {sum(1 for v in audit.values() if v['is_latest'] is None)}")

    # ── Load SBOM completeness scores (from 08b_lockfile_deps) ───────────────
    completeness_path = graph_path.parent / "repo_completeness.json"
    repo_completeness: dict[str, dict] = {}
    if completeness_path.exists():
        try:
            repo_completeness = json.loads(completeness_path.read_text())
            print(f"Loaded completeness for {len(repo_completeness)} repos")
        except Exception:
            pass

    # ── Build purl metadata maps from graph ───────────────────────────────────
    purl_to_repos:   dict[str, list[str]] = {}
    purl_to_license: dict[str, str] = {}
    for n in g["nodes"]:
        a = n["attributes"]
        if a.get("type") == "package":
            purl_to_repos[n["key"]]   = a.get("repos", [])
            purl_to_license[n["key"]] = a.get("license", "")

    repo_to_purls: dict[str, list[str]] = defaultdict(list)
    for purl, repos in purl_to_repos.items():
        for repo in repos:
            repo_to_purls[repo].append(purl)

    # ── CVE blast radius: cve_id → affected repos + packages ─────────────────
    print("Building CVE blast radius index...")
    cve_blast_radius: dict[str, dict] = {}
    for purl, aud_data in audit.items():
        if aud_data["cve_count"] == 0:
            continue
        repos = purl_to_repos.get(purl, [])
        for v in aud_data["vulns"]:
            cve_key = v["alias"] or v["id"]
            if not cve_key:
                continue
            if cve_key not in cve_blast_radius:
                cve_blast_radius[cve_key] = {
                    "id":       v["id"],
                    "alias":    v["alias"],
                    "severity": v["severity"],
                    "cvss_score": v["cvss_score"],
                    "summary":  v["summary"],
                    "fixed":    v["fixed"],
                    "url":      v["url"],
                    "_repos":   set(),
                    "_pkgs":    set(),
                }
            cve_blast_radius[cve_key]["_repos"].update(repos)
            cve_blast_radius[cve_key]["_pkgs"].add(purl)

    for data in cve_blast_radius.values():
        data["affected_repos"]         = sorted(data.pop("_repos"))
        data["affected_repo_count"]    = len(data["affected_repos"])
        data["affected_packages"]      = sorted(data.pop("_pkgs"))
        data["affected_package_count"] = len(data["affected_packages"])

    print(f"  {len(cve_blast_radius)} unique CVEs with blast radius data")

    # ── Repo risk scores ──────────────────────────────────────────────────────
    print("Computing repo risk scores...")
    COPYLEFT_MARKERS = ("GPL", "AGPL", "LGPL", "MPL", "EUPL", "CDDL", "EUPL")

    repo_risk_raw: dict[str, float] = {}
    repo_risk_details: dict[str, dict] = {}

    for repo_name, purls in repo_to_purls.items():
        total = len(purls)
        if total == 0:
            continue
        n_critical = n_high = n_medium = n_low = n_outdated = n_copyleft = n_abandoned = 0
        for purl in purls:
            aud = audit.get(purl, {})
            sev = aud.get("max_severity") or ""
            if sev == "CRITICAL":              n_critical += 1
            elif sev == "HIGH":                n_high += 1
            elif sev in ("MEDIUM","MODERATE"): n_medium += 1
            elif sev == "LOW":                 n_low += 1
            if aud.get("is_latest") is False:  n_outdated += 1
            if aud.get("abandoned"):           n_abandoned += 1
            lic = purl_to_license.get(purl, "")
            if any(m in lic.upper() for m in COPYLEFT_MARKERS):
                n_copyleft += 1

        outdated_ratio  = n_outdated  / total
        copyleft_ratio  = n_copyleft  / total
        abandoned_ratio = n_abandoned / total

        comp = repo_completeness.get(repo_name, {})
        comp_score = comp.get("score") if comp.get("has_lock_file") else None
        comp_gap   = max(0.0, 1.0 - (comp_score / 100.0)) if comp_score is not None else 0.0

        raw = (n_critical * 10 + n_high * 3 + n_medium * 1 + n_low * 0.2
               + outdated_ratio * 20 + copyleft_ratio * 5 + abandoned_ratio * 8
               + comp_gap * 15)
        repo_risk_raw[repo_name] = raw
        repo_risk_details[repo_name] = {
            "score": 0.0,   # filled after normalization
            "raw":   round(raw, 2),
            "critical": n_critical, "high": n_high,
            "medium": n_medium,     "low": n_low,
            "outdated":  n_outdated,  "copyleft":  n_copyleft,
            "abandoned": n_abandoned,
            "total_packages":  total,
            "outdated_ratio":  round(outdated_ratio, 3),
            "copyleft_ratio":  round(copyleft_ratio, 3),
            "abandoned_ratio": round(abandoned_ratio, 3),
            "completeness_score": comp_score,
            "lock_count":  comp.get("lock_count"),
            "sbom_count":  comp.get("sbom_count", total),
            "has_lock_file": comp.get("has_lock_file", False),
        }

    max_raw = max(repo_risk_raw.values()) if repo_risk_raw else 1.0
    for repo_name, raw in repo_risk_raw.items():
        score = round(math.log1p(raw) / math.log1p(max(max_raw, 1)) * 100, 1)
        repo_risk_details[repo_name]["score"] = score

    # Ensure every repo node gets an entry (zero-package repos have no risk)
    all_repo_names = {n["attributes"]["name"] for n in g["nodes"] if n["attributes"].get("type") == "repository"}
    zero_entry = {"score": 0.0, "raw": 0.0, "critical": 0, "high": 0, "medium": 0, "low": 0,
                  "outdated": 0, "copyleft": 0, "abandoned": 0, "total_packages": 0,
                  "outdated_ratio": 0.0, "copyleft_ratio": 0.0, "abandoned_ratio": 0.0,
                  "completeness_score": None, "lock_count": None, "sbom_count": 0, "has_lock_file": False}
    for repo_name in all_repo_names:
        if repo_name not in repo_risk_details:
            repo_risk_details[repo_name] = dict(zero_entry)

    top5 = sorted(repo_risk_details.items(), key=lambda x: -x[1]["score"])[:5]
    print(f"  {len(repo_risk_details)} repos scored. Top: " + ", ".join(f"{r}({d['score']})" for r, d in top5))

    with_comp = [d for d in repo_risk_details.values() if d.get("completeness_score") is not None]
    avg_completeness = round(sum(d["completeness_score"] for d in with_comp) / len(with_comp), 1) if with_comp else None
    if avg_completeness is not None:
        print(f"  Avg SBOM completeness: {avg_completeness}% ({len(with_comp)} repos with lock files)")

    out = {
        "generated_at":    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_packages":  len(pkg_nodes),
        "with_cve":        with_cve,
        "outdated":        outdated,
        "abandoned":       abandoned,
        "confusion_risk":  confusion,
        "avg_completeness": avg_completeness,
        "packages":           audit,
        "cve_blast_radius":   cve_blast_radius,
        "repo_risk_scores":   repo_risk_details,
    }

    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(out))
    print(f"\nWrote {audit_path} ({audit_path.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
