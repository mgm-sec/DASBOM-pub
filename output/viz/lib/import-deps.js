/**
 * import-deps.js — repo URL parsing, GitHub/GitLab/Bitbucket fetching,
 * OSV audit, registry latest-version checks, graph injection, export.
 * UMD: window.ImportDeps in browser, module.exports in Node.js.
 * Depends on LockfileParsers being loaded first.
 */
(function (factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory(require('./lockfile-parsers.js'));
  } else {
    window.ImportDeps = factory(window.LockfileParsers);
  }
}(function (LF) {
  'use strict';

  // ── URL parsing ─────────────────────────────────────────────────────────────

  /**
   * Parse a GitHub / GitLab / Bitbucket URL.
   * Returns { host, owner, repo, branch, subdir } or null.
   */
  function parseRepoUrl(raw) {
    const url = (raw || '').trim().replace(/\/$/, '').replace(/\.git$/, '');

    // GitHub
    let m = url.match(/github\.com\/([^/?#\s]+)\/([^/?#\s/]+?)(?:\/tree\/([^/?#\s]+)(\/[^?#\s]*)?)?(?:[?#]|$)/);
    if (m) return { host: 'github', owner: m[1], repo: m[2], branch: m[3] || null, subdir: (m[4] || '').replace(/^\//, '') };

    // GitLab (supports subgroups: owner may contain slashes)
    m = url.match(/gitlab\.com\/(.+?)(?:\/-\/tree\/([^/?#\s]+)(\/[^?#\s]*)?)?(?:[?#\s]|$)/);
    if (m) {
      const parts = m[1].split('/');
      const repo = parts.pop();
      const owner = parts.join('/');
      if (!repo || !owner) return null;
      return { host: 'gitlab', owner, repo, branch: m[2] || null, subdir: (m[3] || '').replace(/^\//, '') };
    }

    // Bitbucket
    m = url.match(/bitbucket\.org\/([^/?#\s]+)\/([^/?#\s/]+?)(?:\/src\/([^/?#\s]+)(\/[^?#\s]*)?)?(?:[?#]|$)/);
    if (m) return { host: 'bitbucket', owner: m[1], repo: m[2], branch: m[3] || null, subdir: (m[4] || '').replace(/^\//, '') };

    return null;
  }

  // ── Rate-limited fetch ──────────────────────────────────────────────────────

  const _callLog = [];

  /**
   * Fetch with Authorization header + 3× retry on 429/403.
   * Pass rateLimit = requests-per-hour to throttle proactively.
   */
  async function rateFetch(url, token, rateLimit) {
    if (rateLimit) {
      const now = Date.now();
      const window = now - 3600000;
      while (_callLog.length && _callLog[0] < window) _callLog.shift();
      if (_callLog.length >= rateLimit) {
        const wait = _callLog[0] + 3600000 - now + 100;
        await sleep(Math.min(wait, 60000));
      }
      _callLog.push(Date.now());
    }

    const headers = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;

    for (let attempt = 0; attempt < 3; attempt++) {
      const resp = await fetch(url, { headers });
      if (resp.status === 429 || resp.status === 403) {
        const retryAfter = resp.headers.get('Retry-After') || resp.headers.get('X-RateLimit-Reset');
        let wait = (attempt + 1) * 8000;
        if (retryAfter) {
          const n = parseInt(retryAfter, 10);
          wait = n > 1_000_000 ? n * 1000 - Date.now() : n * 1000; // unix epoch vs seconds
        }
        await sleep(Math.min(wait, 120000));
        continue;
      }
      if (!resp.ok) throw new Error(`HTTP ${resp.status} — ${url}`);
      return resp;
    }
    throw new Error(`Failed after 3 retries: ${url}`);
  }

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  // ── GitHub ──────────────────────────────────────────────────────────────────

  async function githubDefaultBranch(info, token) {
    const resp = await rateFetch(`https://api.github.com/repos/${info.owner}/${info.repo}`, token, info.rateLimit);
    const data = await resp.json();
    return data.default_branch || 'main';
  }

  async function githubGetLockfilePaths(info, token, onProgress) {
    const branch = info.branch || await githubDefaultBranch(info, token);
    const url = `https://api.github.com/repos/${info.owner}/${info.repo}/git/trees/${branch}?recursive=1`;
    onProgress(`GitHub: fetching tree for ${info.owner}/${info.repo}@${branch}…`);
    const resp = await rateFetch(url, token, info.rateLimit);
    const data = await resp.json();
    if (data.truncated) onProgress('Warning: tree truncated (repo >100k files) — some lockfiles may be missed');
    const prefix = info.subdir ? info.subdir + '/' : '';
    return {
      branch,
      paths: (data.tree || [])
        .filter(f => f.type === 'blob' && f.path.startsWith(prefix) && LF.isLockfile(f.path))
        .map(f => f.path),
    };
  }

  async function githubFetchFile(info, branch, path, token) {
    const url = `https://raw.githubusercontent.com/${info.owner}/${info.repo}/${branch}/${path}`;
    const resp = await rateFetch(url, token);
    return resp.text();
  }

  // ── GitLab ──────────────────────────────────────────────────────────────────

  async function gitlabGetLockfilePaths(info, token, onProgress) {
    const project = encodeURIComponent(`${info.owner}/${info.repo}`);
    const branch = info.branch || 'HEAD';
    onProgress(`GitLab: fetching tree for ${info.owner}/${info.repo}…`);
    const paths = [];
    for (let page = 1; page <= 10; page++) {
      const url = `https://gitlab.com/api/v4/projects/${project}/repository/tree?ref=${branch}&recursive=true&per_page=100&page=${page}`;
      const resp = await rateFetch(url, token);
      const items = await resp.json();
      if (!Array.isArray(items) || !items.length) break;
      for (const item of items) {
        if (item.type === 'blob' && LF.isLockfile(item.path)) paths.push(item.path);
      }
      if (items.length < 100) break;
    }
    const prefix = info.subdir ? info.subdir + '/' : '';
    return { branch, paths: paths.filter(p => p.startsWith(prefix)) };
  }

  async function gitlabFetchFile(info, branch, path, token) {
    const project = encodeURIComponent(`${info.owner}/${info.repo}`);
    const filePath = encodeURIComponent(path);
    const url = `https://gitlab.com/api/v4/projects/${project}/repository/files/${filePath}/raw?ref=${branch}`;
    const resp = await rateFetch(url, token);
    return resp.text();
  }

  // ── Bitbucket ───────────────────────────────────────────────────────────────

  async function bitbucketGetLockfilePaths(info, token, onProgress) {
    const branch = info.branch || 'HEAD';
    onProgress(`Bitbucket: fetching file list for ${info.owner}/${info.repo}…`);
    const paths = [];
    let url = `https://api.bitbucket.org/2.0/repositories/${info.owner}/${info.repo}/src/${branch}/?pagelen=100&q=path+~+%22lock%22+OR+path+~+%22go.mod%22`;
    for (let page = 0; page < 5; page++) {
      const resp = await rateFetch(url, token);
      const data = await resp.json();
      for (const item of data.values || []) {
        if (item.type === 'commit_file' && LF.isLockfile(item.path)) paths.push(item.path);
      }
      if (!data.next) break;
      url = data.next;
    }
    return { branch, paths };
  }

  async function bitbucketFetchFile(info, branch, path, token) {
    const url = `https://api.bitbucket.org/2.0/repositories/${info.owner}/${info.repo}/src/${branch}/${path}`;
    const resp = await rateFetch(url, token);
    return resp.text();
  }

  // ── Unified repo fetcher ────────────────────────────────────────────────────

  async function fetchRepoLockfiles(repoUrl, token, onProgress) {
    const info = parseRepoUrl(repoUrl);
    if (!info) throw new Error(`Cannot parse repo URL: ${repoUrl}`);
    info.rateLimit = token ? 4800 : 55; // leave headroom from max (5000/60)

    let getLockfilePaths, fetchFile;
    if (info.host === 'github')    { getLockfilePaths = githubGetLockfilePaths;    fetchFile = githubFetchFile; }
    else if (info.host === 'gitlab') { getLockfilePaths = gitlabGetLockfilePaths;  fetchFile = gitlabFetchFile; }
    else                            { getLockfilePaths = bitbucketGetLockfilePaths; fetchFile = bitbucketFetchFile; }

    const { branch, paths } = await getLockfilePaths(info, token, onProgress);
    onProgress(`Found ${paths.length} lockfile(s) — fetching…`);

    const allPkgs = [];
    for (const path of paths) {
      onProgress(`Parsing ${path}…`);
      try {
        const text = await fetchFile(info, branch, path, token);
        const pkgs = LF.detectAndParse(path, text);
        if (pkgs && pkgs.length) {
          allPkgs.push(...pkgs);
          onProgress(`  ✓ ${pkgs.length} pkgs from ${path.split('/').pop()}`);
        }
      } catch (e) {
        onProgress(`  ✗ ${path}: ${e.message}`);
      }
    }

    const label = `${info.owner}/${info.repo}`;
    const deduped = dedupePackages(allPkgs);
    const enriched = await enrichTransitiveDeps(deduped, onProgress);
    return { info, label, branch, pkgs: enriched };
  }

  // ── Folder upload parser ────────────────────────────────────────────────────

  async function parseFolderFiles(fileList, onProgress) {
    const allPkgs = [];
    // Accept any file whose basename is a known lockfile (including requirements*.txt)
    const files = Array.from(fileList).filter(f => LF.isLockfile(f.webkitRelativePath || f.name));
    onProgress(`Found ${files.length} lockfile(s) in upload`);
    for (const file of files) {
      const displayPath = file.webkitRelativePath || file.name;
      onProgress(`Parsing ${displayPath}…`);
      try {
        const text = await file.text();
        const pkgs = LF.detectAndParse(displayPath, text);
        if (pkgs && pkgs.length) {
          allPkgs.push(...pkgs);
          onProgress(`  ✓ ${pkgs.length} pkgs from ${file.name}`);
        }
      } catch (e) {
        onProgress(`  ✗ ${file.name}: ${e.message}`);
      }
    }
    const deduped = dedupePackages(allPkgs);
    return enrichTransitiveDeps(deduped, onProgress);
  }

  // ── Registry transitive enrichment ──────────────────────────────────────────

  // Ecosystems we can enrich from public registry APIs
  const ENRICHABLE_ECOS = new Set(['npm', 'pypi', 'cargo', 'gem', 'composer', 'golang']);

  function normPkgName(ecosystem, name) {
    if (ecosystem === 'pypi') return name.toLowerCase().replace(/[-_.]+/g, '-');
    if (ecosystem === 'npm' || ecosystem === 'composer') return name.toLowerCase();
    return name;
  }

  /**
   * Fetch a single package's deps (and optionally latest version) from its registry.
   * Returns { version, depNames: string[] } or null.
   */
  async function fetchRegistryPackage(ecosystem, name, version) {
    try {
      if (ecosystem === 'npm') {
        const n = name.startsWith('@') ? name : encodeURIComponent(name);
        const ver = version || 'latest';
        const r = await fetch(`https://registry.npmjs.org/${n}/${ver}`);
        if (!r.ok) return null;
        const d = await r.json();
        return {
          version: d.version,
          depNames: Object.keys({ ...(d.dependencies || {}), ...(d.peerDependencies || {}) }),
        };
      }
      if (ecosystem === 'pypi') {
        const url = version
          ? `https://pypi.org/pypi/${encodeURIComponent(name)}/${encodeURIComponent(version)}/json`
          : `https://pypi.org/pypi/${encodeURIComponent(name)}/json`;
        const r = await fetch(url);
        if (!r.ok) return null;
        const d = await r.json();
        const depNames = (d.info?.requires_dist || []).map(req => {
          const m = req.match(/^([A-Za-z0-9_.\-]+)/);
          return m ? normPkgName('pypi', m[1]) : null;
        }).filter(Boolean);
        return { version: d.info?.version, depNames };
      }
      if (ecosystem === 'cargo') {
        const ne = encodeURIComponent(name);
        const cr = await fetch(`https://crates.io/api/v1/crates/${ne}`,
          { headers: { 'User-Agent': 'sbom-viz/1.0' } });
        if (!cr.ok) return null;
        const cd = await cr.json();
        const ver = version || cd.crate?.newest_version;
        if (!ver) return null;
        const dr = await fetch(`https://crates.io/api/v1/crates/${ne}/${encodeURIComponent(ver)}/dependencies`,
          { headers: { 'User-Agent': 'sbom-viz/1.0' } });
        const depNames = dr.ok
          ? ((await dr.json()).dependencies || []).filter(d => d.kind === 'normal').map(d => d.crate_id)
          : [];
        return { version: ver, depNames };
      }
      if (ecosystem === 'gem') {
        const ne = encodeURIComponent(name);
        const r = await fetch(`https://rubygems.org/api/v1/gems/${ne}.json`);
        if (!r.ok) return null;
        const d = await r.json();
        const ver = version || d.version;
        if (!ver) return null;
        const dr = await fetch(`https://rubygems.org/api/v2/rubygems/${ne}/versions/${encodeURIComponent(ver)}.json`);
        const depNames = dr.ok
          ? ((await dr.json()).dependencies?.runtime || []).map(dep => dep.name)
          : [];
        return { version: ver, depNames };
      }
      if (ecosystem === 'composer') {
        const parts = name.split('/');
        if (parts.length < 2) return null;
        const r = await fetch(`https://repo.packagist.org/p2/${parts[0]}/${parts[1]}.json`);
        if (!r.ok) return null;
        const d = await r.json();
        const versions = Object.values(d.packages || {})[0] || [];
        const match = version
          ? versions.find(v => v.version === version || v.version === `v${version}` || v.version_normalized === version)
          : versions[0];
        if (!match) return null;
        const depNames = Object.keys(match.require || {})
          .filter(d => d !== 'php' && !d.startsWith('ext-') && !d.startsWith('lib-'));
        return { version: (match.version || '').replace(/^v/, ''), depNames };
      }
      if (ecosystem === 'golang') {
        const ver = version || 'latest';
        const url = `https://proxy.golang.org/${name}/@v/${ver}.mod`;
        const r = await fetch(url);
        if (!r.ok) return null;
        const text = await r.text();
        const depNames = [];
        let inBlock = false;
        for (const line of text.split('\n')) {
          const t = line.trim().split('//')[0].trim();
          if (t === 'require (' || t === 'require(') { inBlock = true; continue; }
          if (inBlock && t === ')') { inBlock = false; continue; }
          const src = inBlock ? t : (t.startsWith('require ') ? t.slice(8) : '');
          const m = src.match(/^(\S+)\s+v\S+/);
          if (m && !m[1].startsWith('(')) depNames.push(m[1]);
        }
        return { version: ver === 'latest' ? '' : ver, depNames };
      }
    } catch (_) {}
    return null;
  }

  /**
   * BFS-expand transitive deps via registry for packages with no dep edges.
   * Mutates pkg.deps in place; returns augmented (possibly larger) pkg array.
   */
  async function enrichTransitiveDeps(pkgs, onProgress, maxExternal = 10000) {
    // Only enrich packages that have zero dep edges AND a known version
    const seeds = pkgs.filter(p =>
      p.deps.length === 0 && ENRICHABLE_ECOS.has(p.ecosystem) && p.version
    );
    if (!seeds.length) return pkgs;

    onProgress(`Enriching transitive deps via registry for ${seeds.length} packages…`);

    const byNorm = new Map(); // `eco:normName` → pkg
    for (const p of pkgs) byNorm.set(`${p.ecosystem}:${normPkgName(p.ecosystem, p.name)}`, p);

    const result = [...pkgs];
    const enriched = new Set();
    let queue = [...seeds];
    let addedCount = 0;

    for (let pass = 0; pass < 20 && queue.length && addedCount < maxExternal; pass++) {
      onProgress(`  Enrichment pass ${pass + 1}: ${queue.length} packages…`);
      const nextQueue = [];

      for (let i = 0; i < queue.length; i += 10) {
        const batch = queue.slice(i, i + 10);
        const results = await Promise.allSettled(
          batch.map(p => fetchRegistryPackage(p.ecosystem, p.name, p.version))
        );

        for (let j = 0; j < batch.length; j++) {
          const pkg = batch[j];
          const nk = `${pkg.ecosystem}:${normPkgName(pkg.ecosystem, pkg.name)}`;
          enriched.add(nk);
          const res = results[j];
          if (res.status !== 'fulfilled' || !res.value) continue;

          const { depNames } = res.value;
          pkg.deps = depNames;

          for (const depName of depNames) {
            if (addedCount >= maxExternal) break;
            const dk = `${pkg.ecosystem}:${normPkgName(pkg.ecosystem, depName)}`;
            if (byNorm.has(dk) || enriched.has(dk)) continue;

            // Create stub — fetch its version via registry
            const stub = { name: depName, version: '__pending__', deps: [], ecosystem: pkg.ecosystem };
            byNorm.set(dk, stub);
            result.push(stub);
            addedCount++;
            nextQueue.push(stub);
          }
        }
      }

      // Resolve versions for stubs created this pass
      const pending = nextQueue.filter(p => p.version === '__pending__');
      for (let i = 0; i < pending.length; i += 10) {
        const batch = pending.slice(i, i + 10);
        const results = await Promise.allSettled(
          batch.map(p => fetchRegistryPackage(p.ecosystem, p.name, ''))
        );
        for (let j = 0; j < batch.length; j++) {
          const pkg = batch[j];
          const res = results[j];
          if (res.status === 'fulfilled' && res.value?.version) {
            pkg.version = res.value.version;
            if (res.value.depNames.length) {
              pkg.deps = res.value.depNames;
              enriched.add(`${pkg.ecosystem}:${normPkgName(pkg.ecosystem, pkg.name)}`);
            }
          }
          // Leave version as '__pending__' if fetch failed; filtered out below
        }
      }

      // Only queue stubs that resolved version but still have no deps
      queue = nextQueue.filter(p => p.version && p.version !== '__pending__' && p.deps.length === 0);
    }

    return dedupePackages(result.filter(p => p.version !== '__pending__'));
  }

  // ── OSV.dev security audit ──────────────────────────────────────────────────

  const OSV_ECO = {
    npm: 'npm', pypi: 'PyPI', cargo: 'crates.io',
    composer: 'Packagist', golang: 'Go', gem: 'RubyGems',
  };
  const SEV_RANK = { CRITICAL: 4, HIGH: 3, MODERATE: 2, MEDIUM: 2, LOW: 1, UNKNOWN: 0 };

  async function osvBatchQuery(pkgs) {
    const queries = pkgs
      .filter(p => OSV_ECO[p.ecosystem] && p.version)
      .map(p => ({ package: { name: p.name, ecosystem: OSV_ECO[p.ecosystem] }, version: p.version }));
    if (!queries.length) return {};

    const BATCH = 1000;
    const hits = {}; // purl-ish key → vuln id list
    for (let i = 0; i < queries.length; i += BATCH) {
      const batch = queries.slice(i, i + BATCH);
      try {
        const resp = await fetch('https://api.osv.dev/v1/querybatch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ queries: batch }),
        });
        if (!resp.ok) continue;
        const data = await resp.json();
        (data.results || []).forEach((r, idx) => {
          if (r.vulns && r.vulns.length) {
            const q = batch[idx];
            const key = `${q.package.ecosystem}:${q.package.name}@${q.version}`;
            hits[key] = r.vulns.map(v => v.id);
          }
        });
      } catch (_) {}
    }
    return hits;
  }

  async function osvFetchVuln(id) {
    try {
      const r = await fetch(`https://api.osv.dev/v1/vulns/${id}`);
      return r.ok ? r.json() : null;
    } catch (_) { return null; }
  }

  // ── Registry latest version ─────────────────────────────────────────────────

  async function fetchLatestVersion(pkg) {
    try {
      if (pkg.ecosystem === 'npm') {
        const r = await fetch(`https://registry.npmjs.org/${pkg.name.startsWith('@') ? pkg.name : encodeURIComponent(pkg.name)}/latest`);
        return r.ok ? (await r.json()).version || null : null;
      }
      if (pkg.ecosystem === 'pypi') {
        const r = await fetch(`https://pypi.org/pypi/${encodeURIComponent(pkg.name)}/json`);
        if (!r.ok) return null;
        // Stream only first 128 KB — info.version is always within first 128 KB
        const reader = r.body.getReader();
        const chunks = []; let bytes = 0;
        while (bytes < 131072) {
          const { done, value } = await reader.read();
          if (done) break;
          chunks.push(value); bytes += value.length;
        }
        reader.cancel().catch(() => {});
        const text = new TextDecoder().decode(
          chunks.reduce((a, c) => { const m = new Uint8Array(a.length + c.length); m.set(a); m.set(c, a.length); return m; }, new Uint8Array(0))
        );
        try { return JSON.parse(text).info?.version || null; } catch (_) {
          const m = text.match(/"version"\s*:\s*"([^"]+)"/);
          return m ? m[1] : null;
        }
      }
      if (pkg.ecosystem === 'cargo') {
        const r = await fetch(`https://crates.io/api/v1/crates/${encodeURIComponent(pkg.name)}`);
        return r.ok ? (await r.json()).crate?.newest_version || null : null;
      }
      if (pkg.ecosystem === 'gem') {
        const r = await fetch(`https://rubygems.org/api/v1/gems/${encodeURIComponent(pkg.name)}.json`);
        return r.ok ? (await r.json()).version || null : null;
      }
      if (pkg.ecosystem === 'golang') {
        const name = pkg.name.replace(/\+/g, '%2B');
        const r = await fetch(`https://proxy.golang.org/${name}/@latest`);
        return r.ok ? (await r.json()).Version || null : null;
      }
      if (pkg.ecosystem === 'composer') {
        const parts = pkg.name.split('/');
        if (parts.length < 2) return null;
        const r = await fetch(`https://repo.packagist.org/p2/${parts[0]}/${parts[1]}.json`);
        if (!r.ok) return null;
        const d = await r.json();
        const versions = Object.values(d.packages || {})[0] || [];
        return versions[0]?.version?.replace(/^v/, '') || null;
      }
    } catch (_) {}
    return null;
  }

  // ── Build audit map ─────────────────────────────────────────────────────────

  async function buildImportedAudit(pkgs, onProgress) {
    onProgress(`Querying OSV.dev for ${pkgs.length} packages…`);
    const osvHits = await osvBatchQuery(pkgs);

    const allVulnIds = new Set(Object.values(osvHits).flat());
    onProgress(`Fetching details for ${allVulnIds.size} vulns…`);
    const vulnDetails = {};
    const idArr = [...allVulnIds];
    for (let i = 0; i < idArr.length; i += 10) {
      const batch = idArr.slice(i, i + 10);
      const results = await Promise.all(batch.map(osvFetchVuln));
      results.forEach((d, j) => { if (d) vulnDetails[idArr[i + j]] = d; });
    }

    onProgress(`Checking latest versions for ${pkgs.length} packages…`);
    const latestMap = {};
    for (let i = 0; i < pkgs.length; i += 20) {
      const batch = pkgs.slice(i, i + 20);
      const results = await Promise.all(batch.map(fetchLatestVersion));
      results.forEach((v, j) => { if (v) latestMap[`${batch[j].ecosystem}:${batch[j].name}`] = v; });
    }

    const audit = {};
    for (const pkg of pkgs) {
      const purl = `pkg:${pkg.ecosystem}/${pkg.name}@${pkg.version}`;
      const osvKey = `${OSV_ECO[pkg.ecosystem] || pkg.ecosystem}:${pkg.name}@${pkg.version}`;
      const ids = osvHits[osvKey] || [];
      const vulns = ids.map(id => vulnDetails[id]).filter(Boolean);

      const cve_count = vulns.length;
      const latest = latestMap[`${pkg.ecosystem}:${pkg.name}`] || null;
      const sev = vulns
        .map(v => ((v.database_specific?.severity) || 'UNKNOWN').toUpperCase())
        .reduce((best, s) => (SEV_RANK[s] || 0) > (SEV_RANK[best] || 0) ? s : best, 'UNKNOWN');

      let max_cvss = null;
      for (const v of vulns) {
        for (const s of v.severity || []) {
          if (s.score && s.score.startsWith('CVSS:3')) {
            const score = simpleCvss3(s.score);
            if (score !== null && (max_cvss === null || score > max_cvss)) max_cvss = score;
          }
        }
      }

      audit[purl] = {
        cve_count,
        max_severity: cve_count ? sev : null,
        max_cvss,
        is_latest: latest !== null ? (pkg.version === latest || pkg.version === `v${latest}`) : null,
        latest_version: latest,
        abandoned: false,
        confusion_risk: false,
        fix_version: firstFix(vulns),
        vulns: vulns.slice(0, 5).map(v => ({
          id: v.id,
          severity: ((v.database_specific?.severity) || 'UNKNOWN').toUpperCase(),
          summary: (v.summary || '').slice(0, 200),
          url: `https://osv.dev/vulnerability/${v.id}`,
        })),
      };
    }
    return audit;
  }

  function firstFix(vulns) {
    for (const v of vulns) {
      for (const aff of v.affected || []) {
        for (const rng of aff.ranges || []) {
          const fix = (rng.events || []).find(e => e.fixed);
          if (fix) return fix.fixed;
        }
      }
    }
    return null;
  }

  // Simplified CVSS3 base score approximation (good enough for display)
  function simpleCvss3(vector) {
    if (!vector || !vector.startsWith('CVSS:3')) return null;
    try {
      const parts = vector.split('/').slice(1);
      const map = {};
      for (const p of parts) { const [k, v] = p.split(':'); map[k] = v; }
      const av = { N: 0.85, A: 0.62, L: 0.55, P: 0.2 }[map.AV] ?? 0.85;
      const ac = { L: 0.77, H: 0.44 }[map.AC] ?? 0.77;
      const pr = map.S === 'C'
        ? { N: 0.85, L: 0.68, H: 0.50 }[map.PR] ?? 0.85
        : { N: 0.85, L: 0.62, H: 0.27 }[map.PR] ?? 0.85;
      const ui = { N: 0.85, R: 0.62 }[map.UI] ?? 0.85;
      const sc = { N: 0, L: 0.22, H: 0.56 }[map.S === 'C' ? 'L' : (map.C || 'N')] ?? 0; // simplified
      const si = { N: 0, L: 0.22, H: 0.56 }[map.I || 'N'] ?? 0;
      const sa = { N: 0, L: 0.22, H: 0.56 }[map.A || 'N'] ?? 0;
      const iss = 1 - (1 - sc) * (1 - si) * (1 - sa);
      const impact = map.S === 'C'
        ? 7.52 * (iss - 0.029) - 3.25 * Math.pow(iss - 0.02, 15)
        : 6.42 * iss;
      if (impact <= 0) return 0.0;
      const exploitability = 8.22 * av * ac * pr * ui;
      const base = map.S === 'C'
        ? Math.min(1.08 * (impact + exploitability), 10)
        : Math.min(impact + exploitability, 10);
      return Math.round(base * 10) / 10;
    } catch (_) { return null; }
  }

  // ── Graph node/edge builder ─────────────────────────────────────────────────

  const IMPORTED_REPO_COLOR  = '#7c3aed'; // violet — distinct from org (#58a6ff)
  const IMPORTED_PKG_DEFAULT = '#44475a';
  const REPO_R               = 5000;      // mirrors 06_build_graph.py REPO_R

  /**
   * Build { nodes, edges } from a parsed package list.
   * repoKey is the graph node key for the repository node.
   */
  function buildImportedGraph(label, host, pkgs, existingAngles) {
    const angle = pickFreeAngle(existingAngles || []);
    const cx = REPO_R * Math.cos(angle);
    const cy = REPO_R * Math.sin(angle);
    const repoKey = `imported:${host}:${label}`;

    // ── BFS depth assignment for ring layout ──────────────────────────────
    const nk = (eco, name) => `${eco}:${normPkgName(eco, name)}`;
    const byNormKey = new Map();
    for (const p of pkgs) byNormKey.set(nk(p.ecosystem, p.name), p);

    // Roots = packages that nothing else in this set depends on
    const isDep = new Set();
    for (const p of pkgs) {
      for (const dep of p.deps || []) {
        const dk = nk(p.ecosystem, dep);
        if (byNormKey.has(dk)) isDep.add(dk);
      }
    }
    const roots = pkgs.filter(p => !isDep.has(nk(p.ecosystem, p.name)));
    const bfsStart = roots.length ? roots : pkgs.slice(0, 1);

    const depth = new Map();
    const bfsQ = bfsStart.map(p => [nk(p.ecosystem, p.name), 1]);
    for (let qi = 0; qi < bfsQ.length; qi++) {
      const [key, d] = bfsQ[qi];
      if (depth.has(key)) continue;
      depth.set(key, d);
      const p = byNormKey.get(key);
      if (!p) continue;
      for (const dep of p.deps || []) {
        const dk = nk(p.ecosystem, dep);
        if (!depth.has(dk) && byNormKey.has(dk)) bfsQ.push([dk, d + 1]);
      }
    }
    // Assign unvisited (cycles, isolated) to depth 1
    for (const p of pkgs) {
      if (!depth.has(nk(p.ecosystem, p.name))) depth.set(nk(p.ecosystem, p.name), 1);
    }

    // ── Concentric ring positions ─────────────────────────────────────────
    const byDepth = new Map();
    for (const p of pkgs) {
      const d = depth.get(nk(p.ecosystem, p.name)) || 1;
      if (!byDepth.has(d)) byDepth.set(d, []);
      byDepth.get(d).push(p);
    }
    const maxD = Math.max(...byDepth.keys(), 1);
    // Log-scale radii: depth 1 → MIN_R, deeper levels expand but taper off
    const MIN_R = 500;
    const MAX_R = 4000;
    const ringRadius = (d) => d === 1 ? MIN_R
      : Math.round(MIN_R + (MAX_R - MIN_R) * (Math.log(d) / Math.log(maxD + 1)));

    const pkgPos = new Map(); // normKey → {x, y}
    for (const [d, dpkgs] of byDepth.entries()) {
      const r = ringRadius(d);
      // Spread nodes in an arc centred on `angle`; use full circle when many nodes
      const arc = Math.min(2 * Math.PI, Math.max(Math.PI / 2, (dpkgs.length * 90) / r));
      dpkgs.forEach((p, i) => {
        const frac = dpkgs.length === 1 ? 0 : (i / (dpkgs.length - 1) - 0.5);
        const pa = angle + frac * arc;
        pkgPos.set(nk(p.ecosystem, p.name), { x: cx + r * Math.cos(pa), y: cy + r * Math.sin(pa) });
      });
    }

    // ── Build nodes + edges ───────────────────────────────────────────────
    const nodes = [{
      key: repoKey,
      attributes: {
        _type: 'repository', _name: label, label,
        _imported: true, _import_host: host,
        _in_org: false, _pkg_count: pkgs.length,
        size: 18, color: IMPORTED_REPO_COLOR, _orig_color: IMPORTED_REPO_COLOR,
        x: cx, y: cy,
      }
    }];
    const edges = [];
    const pkgMap = {}; // `eco:name@ver` → purl key

    for (const pkg of pkgs) {
      const key = `pkg:${pkg.ecosystem}/${pkg.name}@${pkg.version}`;
      pkgMap[`${pkg.ecosystem}:${pkg.name}@${pkg.version}`] = key;

      if (!nodes.find(n => n.key === key)) {
        const pos = pkgPos.get(nk(pkg.ecosystem, pkg.name)) || { x: cx + MIN_R, y: cy };
        const d = depth.get(nk(pkg.ecosystem, pkg.name)) || 1;
        // Larger size for shallower (more central) packages
        const sz = Math.max(3, 8 - d);
        nodes.push({
          key,
          attributes: {
            _type: 'package', _name: pkg.name, _version: pkg.version,
            _ecosystem: pkg.ecosystem, label: `${pkg.name}@${pkg.version}`,
            _purl: key, _in_org: false, _imported: true,
            _repos: [label], _repo_count: 1, _usage_score: 0,
            _dep_depth: d,
            size: sz, color: IMPORTED_PKG_DEFAULT, _orig_color: IMPORTED_PKG_DEFAULT,
            x: pos.x, y: pos.y,
          }
        });
      }

      edges.push({
        key: `${repoKey}→${key}`,
        source: repoKey, target: key,
        attributes: { _type: 'contains', size: 0.4, color: '#21262d' }
      });
    }

    // depends_on edges — normalized name matching handles pypi/npm case differences
    const normIndex = new Map(); // `eco:normName` → purl key
    for (const pkg of pkgs) {
      normIndex.set(nk(pkg.ecosystem, pkg.name),
        pkgMap[`${pkg.ecosystem}:${pkg.name}@${pkg.version}`]);
    }

    for (const pkg of pkgs) {
      const srcKey = pkgMap[`${pkg.ecosystem}:${pkg.name}@${pkg.version}`];
      for (const depName of pkg.deps || []) {
        const tgtKey = normIndex.get(nk(pkg.ecosystem, depName));
        if (tgtKey && tgtKey !== srcKey) {
          edges.push({
            key: `${srcKey}⟶${tgtKey}`,
            source: srcKey, target: tgtKey,
            attributes: { _type: 'depends_on', size: 0.3, color: '#1d4e3a' }
          });
        }
      }
    }

    return { repoKey, angle, nodes, edges };
  }

  function pickFreeAngle(usedAngles) {
    if (!usedAngles.length) return Math.random() * 2 * Math.PI;
    // Find biggest angular gap, place new node in its middle
    const sorted = [...usedAngles].sort((a, b) => a - b);
    let bestGap = 0, bestAngle = 0;
    for (let i = 0; i < sorted.length; i++) {
      const next = sorted[(i + 1) % sorted.length];
      const gap = (next - sorted[i] + 2 * Math.PI) % (2 * Math.PI);
      if (gap > bestGap) { bestGap = gap; bestAngle = sorted[i] + gap / 2; }
    }
    return bestAngle % (2 * Math.PI);
  }

  // ── Export augmented graph ──────────────────────────────────────────────────

  function exportAugmentedGraph(graph, secAudit) {
    const nodes = [];
    graph.forEachNode((key, attrs) => nodes.push({ key, attributes: attrs }));
    const edges = [];
    graph.forEachEdge((key, attrs, src, tgt) => edges.push({ key, source: src, target: tgt, attributes: attrs }));

    const blob = new Blob([JSON.stringify({
      exported_at: new Date().toISOString(),
      nodes, edges,
      security_audit: secAudit,
    }, null, 2)], { type: 'application/json' });

    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `sbom-graph-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }

  // ── Helpers ─────────────────────────────────────────────────────────────────

  function dedupePackages(pkgs) {
    const seen = new Set();
    return pkgs.filter(p => {
      const k = `${p.ecosystem}:${p.name}@${p.version}`;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }

  return {
    parseRepoUrl,
    fetchRepoLockfiles,
    parseFolderFiles,
    enrichTransitiveDeps,
    fetchRegistryPackage,
    buildImportedAudit,
    buildImportedGraph,
    exportAugmentedGraph,
    dedupePackages,
    pickFreeAngle,
    normPkgName,
    simpleCvss3,
    IMPORTED_REPO_COLOR,
    REPO_R,
  };
}));
