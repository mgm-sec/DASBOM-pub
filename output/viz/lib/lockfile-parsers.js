/**
 * lockfile-parsers.js — pure lockfile parsers for browser + Node.js (Jest).
 * Each parser returns Array<{name, version, deps: string[], ecosystem}>.
 * UMD: window.LockfileParsers in browser, module.exports in Node.js.
 */
(function (factory) {
  if (typeof module !== 'undefined' && module.exports) module.exports = factory();
  else window.LockfileParsers = factory();
}(function () {
  'use strict';

  // ── package-lock.json (npm lockfileVersion 2/3) ──────────────────────────

  function parsePackageLockJson(text) {
    let data;
    try { data = JSON.parse(text); } catch (_) { return []; }
    const pkgs = [];
    const packages = data.packages || {};
    for (const [path, info] of Object.entries(packages)) {
      if (!path || !info.version) continue; // skip root ""
      // 'node_modules/foo' → 'foo', 'node_modules/@babel/core' → '@babel/core'
      // 'node_modules/a/node_modules/b' → 'b'
      const bare = path.startsWith('node_modules/') ? path.slice('node_modules/'.length) : path;
      const name = bare.split('/node_modules/').pop();
      if (!name) continue;
      const deps = [
        ...Object.keys(info.dependencies || {}),
        ...Object.keys(info.peerDependencies || {}),
        ...Object.keys(info.optionalDependencies || {}),
      ];
      pkgs.push({ name, version: info.version, deps, ecosystem: 'npm' });
    }
    return pkgs;
  }

  // ── yarn.lock (v1 classic + v2 berry) ────────────────────────────────────

  function parseYarnLock(text) {
    return text.includes('__metadata:') ? parseYarnLockV2(text) : parseYarnLockV1(text);
  }

  function parseYarnLockV1(text) {
    const pkgs = [];
    const lines = text.split('\n');
    let current = null;
    let inDeps = false;
    let depIndent = 0;

    for (const line of lines) {
      if (!line || line.startsWith('#')) { inDeps = false; continue; }
      // Entry header: no leading space, ends with ':'
      if (!/^\s/.test(line) && line.trimEnd().endsWith(':')) {
        if (current) pkgs.push(current);
        const specs = line.trimEnd().replace(/:$/, '').split(',').map(s => s.trim().replace(/^"|"$/g, ''));
        const first = specs[0];
        const at = first.lastIndexOf('@');
        const name = (at > 0 ? first.slice(0, at) : first).trim();
        current = { name, version: '', deps: [], ecosystem: 'npm' };
        inDeps = false;
        continue;
      }
      if (!current) continue;
      const vm = line.match(/^\s+version\s+"([^"]+)"/);
      if (vm) { current.version = vm[1]; inDeps = false; continue; }
      if (/^\s+dependencies:/.test(line)) {
        inDeps = true;
        depIndent = line.search(/\S/) + 2;
        continue;
      }
      if (inDeps) {
        const indent = line.search(/\S/);
        if (indent < depIndent) { inDeps = false; continue; }
        const dm = line.trim().match(/^"?([^@\s"]+)/);
        if (dm) current.deps.push(dm[1]);
      }
    }
    if (current) pkgs.push(current);
    return dedupeByKey(pkgs);
  }

  function parseYarnLockV2(text) {
    const pkgs = [];
    const blocks = text.split(/\n\n+/);
    for (const block of blocks) {
      const firstLine = block.split('\n')[0];
      if (!firstLine || firstLine.startsWith(' ') || firstLine.startsWith('#') || firstLine.startsWith('__')) continue;
      const specs = firstLine.replace(/:$/, '').split(', ').map(s => s.trim().replace(/^"|"$/g, ''));
      const first = specs[0];
      const at = first.lastIndexOf('@');
      const name = (at > 0 ? first.slice(0, at) : first).trim();
      let version = '';
      const deps = [];
      let inDeps = false;
      for (const line of block.split('\n').slice(1)) {
        const vm = line.match(/^\s+version:\s+(\S+)/);
        if (vm) { version = vm[1].replace(/^"|"$/g, ''); continue; }
        if (/^\s+dependencies:/.test(line)) { inDeps = true; continue; }
        if (inDeps && /^\s{2,}\S/.test(line)) {
          const dm = line.trim().match(/^"?([^@\s"]+)/);
          if (dm) deps.push(dm[1]);
        } else if (inDeps && line.trim() && !/^\s/.test(line)) {
          inDeps = false;
        }
      }
      if (name && version) pkgs.push({ name, version, deps, ecosystem: 'npm' });
    }
    return dedupeByKey(pkgs);
  }

  // ── pnpm-lock.yaml (v6 packages: + v9 snapshots:) ────────────────────────

  function parsePnpmLockYaml(text) {
    const pkgs = [];
    const lines = text.split('\n');

    function stripPeer(s) {
      let result = s;
      for (let i = 0; i < 10 && result.includes('('); i++) {
        result = result.replace(/\([^()]*\)/g, '');
      }
      return result.trim();
    }
    function unquote(s) { return s.replace(/^['"]|['"]$/g, ''); }

    // Use snapshots: (v9) if present, else packages: (v6)
    const snapshotsIdx = lines.findIndex(l => l === 'snapshots:');
    const packagesIdx  = lines.findIndex(l => l === 'packages:');
    const startIdx = snapshotsIdx >= 0 ? snapshotsIdx : packagesIdx;
    if (startIdx === -1) return pkgs;

    // Section ends at next zero-indent non-empty line
    let endIdx = lines.length;
    for (let i = startIdx + 1; i < lines.length; i++) {
      const l = lines[i];
      if (l && /^\S/.test(l)) { endIdx = i; break; }
    }

    let current = null;
    let inDeps = false;

    for (let i = startIdx + 1; i < endIdx; i++) {
      const rawLine = lines[i];
      const trimmed = rawLine.trim();
      if (!trimmed) continue;
      const indent = rawLine.length - rawLine.trimStart().length;

      if (indent === 2) {
        if (current) pkgs.push(current);
        inDeps = false;
        // Remove leading '/', unquote, strip peer suffix
        const rawKey = unquote(trimmed.replace(/:$/, '')).replace(/^\//, '');
        const key = stripPeer(rawKey);
        const lastAt = key.lastIndexOf('@');
        if (lastAt > 0) {
          const name = key.slice(0, lastAt);
          const version = key.slice(lastAt + 1);
          current = (name && version) ? { name, version, deps: [], ecosystem: 'npm' } : null;
        } else {
          current = null;
        }
        continue;
      }

      if (!current) continue;

      if (indent === 4) {
        if (trimmed === 'dependencies:' || trimmed === 'optionalDependencies:') {
          inDeps = true;
        } else if (trimmed.endsWith(':')) {
          inDeps = false;
        }
        continue;
      }

      if (inDeps && indent >= 6) {
        const m = trimmed.match(/^['"]?(@?[^'":\s][^'":\s]*)['"]?\s*:/);
        if (m) current.deps.push(m[1].replace(/^\//, ''));
      }
    }
    if (current) pkgs.push(current);
    return pkgs;
  }

  // ── poetry.lock ───────────────────────────────────────────────────────────

  function parsePoetryLock(text) {
    const pkgs = [];
    const blocks = text.split(/(?=\[\[package\]\])/);
    for (const block of blocks) {
      if (!block.startsWith('[[package]]')) continue;
      const name    = (block.match(/^name\s*=\s*"([^"]+)"/m) || [])[1];
      const version = (block.match(/^version\s*=\s*"([^"]+)"/m) || [])[1];
      const deps = [];
      const depsSection = block.match(/\[package\.dependencies\]([\s\S]*?)(?=\[|$)/);
      if (depsSection) {
        for (const line of depsSection[1].split('\n')) {
          const m = line.match(/^([A-Za-z0-9_\-\.]+)\s*=/);
          if (m) deps.push(m[1].trim());
        }
      }
      if (name) pkgs.push({ name, version: version || '', deps, ecosystem: 'pypi' });
    }
    return pkgs;
  }

  // ── uv.lock ───────────────────────────────────────────────────────────────

  function parseUvLock(text) {
    const pkgs = [];
    const blocks = text.split(/(?=\[\[package\]\])/);
    for (const block of blocks) {
      if (!block.startsWith('[[package]]')) continue;
      const name    = (block.match(/^name\s*=\s*"([^"]+)"/m) || [])[1];
      const version = (block.match(/^version\s*=\s*"([^"]+)"/m) || [])[1];
      const deps = [];
      // dependencies = [ {name = "x", ...}, ... ]
      const depBlock = block.match(/^dependencies\s*=\s*\[([\s\S]*?)\]/m);
      if (depBlock) {
        for (const m of depBlock[1].matchAll(/name\s*=\s*"([^"]+)"/g)) {
          deps.push(m[1]);
        }
      }
      if (name && version) pkgs.push({ name, version, deps, ecosystem: 'pypi' });
    }
    return pkgs;
  }

  // ── Cargo.lock ────────────────────────────────────────────────────────────

  function parseCargoLock(text) {
    const pkgs = [];
    const blocks = text.split(/(?=\[\[package\]\])/);
    for (const block of blocks) {
      if (!block.startsWith('[[package]]')) continue;
      const name    = (block.match(/^name\s*=\s*"([^"]+)"/m) || [])[1];
      const version = (block.match(/^version\s*=\s*"([^"]+)"/m) || [])[1];
      const deps = [];
      const depSection = block.match(/^dependencies\s*=\s*\[([\s\S]*?)\]/m);
      if (depSection) {
        for (const line of depSection[1].split('\n')) {
          const m = line.match(/"([^"]+)"/);
          // "serde 1.0.160 (registry+...)" → first word is name
          if (m) deps.push(m[1].split(' ')[0]);
        }
      }
      if (name) pkgs.push({ name, version: version || '', deps, ecosystem: 'cargo' });
    }
    return pkgs;
  }

  // ── composer.lock ─────────────────────────────────────────────────────────

  function parseComposerLock(text) {
    let data;
    try { data = JSON.parse(text); } catch (_) { return []; }
    const pkgs = [];
    const all = [...(data.packages || []), ...(data['packages-dev'] || [])];
    for (const pkg of all) {
      const deps = Object.keys(pkg.require || {})
        .filter(d => d !== 'php' && !d.startsWith('ext-') && !d.startsWith('lib-'));
      pkgs.push({
        name: pkg.name,
        version: (pkg.version || '').replace(/^v/, ''),
        deps,
        ecosystem: 'composer',
      });
    }
    return pkgs;
  }

  // ── go.mod ────────────────────────────────────────────────────────────────

  function parseGoMod(text) {
    const pkgs = [];
    const lines = text.split('\n');
    let inBlock = false;
    for (const line of lines) {
      const t = line.trim();
      if (t.startsWith('//')) continue;
      if (t === 'require (' || t === 'require(') { inBlock = true; continue; }
      if (inBlock && t === ')') { inBlock = false; continue; }
      const src = (inBlock ? t : (t.startsWith('require ') ? t.slice(8) : '')).split('//')[0].trim();
      if (!src) continue;
      const m = src.match(/^(\S+)\s+(v\S+)/);
      if (m && !m[1].startsWith('(')) {
        pkgs.push({ name: m[1], version: m[2], deps: [], ecosystem: 'golang' });
      }
    }
    return pkgs;
  }

  // ── Gemfile.lock ──────────────────────────────────────────────────────────

  function parseGemfileLock(text) {
    const pkgs = [];
    const lines = text.split('\n');
    let inSpecs = false;
    let inGem = false;
    let current = null;
    for (const line of lines) {
      if (/^GEM\s*$/.test(line.trim())) { inGem = true; continue; }
      if (inGem && line.trim() === 'specs:') { inSpecs = true; continue; }
      if (inSpecs && /^\S/.test(line) && line.trim()) { inSpecs = false; inGem = false; current = null; }
      if (!inSpecs) continue;
      // 4-space indent = gem entry
      const m4 = line.match(/^    (\S[^(]*?)\s+\(([^)]+)\)\s*$/);
      if (m4) {
        current = { name: m4[1].trim(), version: m4[2].split(',')[0].trim(), deps: [], ecosystem: 'gem' };
        pkgs.push(current);
        continue;
      }
      // 6-space indent = dep of current gem
      if (current && /^      \S/.test(line)) {
        const dm = line.trim().match(/^([A-Za-z0-9_\-\.]+)/);
        if (dm) current.deps.push(dm[1]);
      }
    }
    return pkgs;
  }

  // ── requirements*.txt (pip-compile style with # via comments) ────────────

  function parseRequirementsTxt(text) {
    const lines = text.split('\n');
    const pkgs = [];
    const pkgMap = {}; // norm_name → pkg

    function norm(n) { return n.toLowerCase().replace(/[-_.]+/g, '-'); }

    // First pass: collect all pinned packages
    for (const line of lines) {
      if (line.startsWith('#') || !line.trim()) continue;
      const m = line.match(/^([A-Za-z0-9_\-\.]+)==([^\s;\\]+)/);
      if (m) {
        const pkg = { name: norm(m[1]), version: m[2], deps: [], ecosystem: 'pypi' };
        pkgs.push(pkg);
        pkgMap[norm(m[1])] = pkg;
      }
    }

    // Second pass: parse # via comments to reconstruct dep edges
    // "certifi==...  # via requests" means requests depends on certifi
    let currentPkg = null;
    let inVia = false;
    let viaValues = [];

    function commitVia() {
      if (!currentPkg || !viaValues.length) { viaValues = []; inVia = false; return; }
      for (const requirer of viaValues) {
        if (requirer.startsWith('-')) continue; // skip -r requirements.in etc
        const src = pkgMap[norm(requirer)];
        if (src && !src.deps.includes(currentPkg.name)) src.deps.push(currentPkg.name);
      }
      viaValues = [];
      inVia = false;
    }

    for (const line of lines) {
      const t = line.trim();
      if (!t) { commitVia(); currentPkg = null; continue; }

      if (!t.startsWith('#')) {
        commitVia();
        const m = line.match(/^([A-Za-z0-9_\-\.]+)==/);
        currentPkg = m ? (pkgMap[norm(m[1])] || null) : null;
        inVia = false;
        continue;
      }

      // "# via pkgname" (single)
      const singleM = t.match(/^#\s+via\s+(\S+)\s*$/);
      if (singleM) {
        commitVia();
        viaValues = [singleM[1]];
        commitVia();
        continue;
      }

      // "# via" alone → multi-line block
      if (/^#\s+via\s*$/.test(t)) {
        commitVia();
        inVia = true;
        viaValues = [];
        continue;
      }

      // Continuation of multi-line via: "#   pkgname"
      if (inVia && /^#\s{2,}\S/.test(t)) {
        const entry = t.match(/^#\s+(\S+)\s*$/);
        if (entry) viaValues.push(entry[1]);
        continue;
      }

      if (inVia) { commitVia(); }
    }
    commitVia();

    return pkgs;
  }

  // ── Package.resolved (Swift SPM v1 + v2) ─────────────────────────────────

  function parsePackageResolved(text) {
    let data;
    try { data = JSON.parse(text); } catch (_) { return []; }
    // v1: data.object.pins, v2: data.pins
    const pins = data.pins || (data.object && data.object.pins) || [];
    const pkgs = [];
    for (const pin of pins) {
      const name = pin.identity || pin.package;
      const version = (pin.state && (pin.state.version || pin.state.branch)) || '';
      if (name) pkgs.push({ name, version, deps: [], ecosystem: 'swift' });
    }
    return pkgs;
  }

  // ── Shared helpers ────────────────────────────────────────────────────────

  function dedupeByKey(pkgs) {
    const seen = new Set();
    return pkgs.filter(p => {
      const k = `${p.name}@${p.version}`;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }

  // ── Dispatch ──────────────────────────────────────────────────────────────

  const LOCKFILE_NAMES = {
    'package-lock.json': parsePackageLockJson,
    'yarn.lock':         parseYarnLock,
    'pnpm-lock.yaml':    parsePnpmLockYaml,
    'poetry.lock':       parsePoetryLock,
    'uv.lock':           parseUvLock,
    'Cargo.lock':        parseCargoLock,
    'composer.lock':     parseComposerLock,
    'go.mod':            parseGoMod,
    'Gemfile.lock':      parseGemfileLock,
    'Package.resolved':  parsePackageResolved,
  };

  function isLockfile(filepath) {
    const base = filepath.split('/').pop();
    if (Object.prototype.hasOwnProperty.call(LOCKFILE_NAMES, base)) return true;
    // requirements*.txt (pip-compile output)
    if (/^requirements[^/]*\.txt$/i.test(base)) return true;
    return false;
  }

  function detectAndParse(filepath, text) {
    const base = filepath.split('/').pop();
    if (Object.prototype.hasOwnProperty.call(LOCKFILE_NAMES, base)) return LOCKFILE_NAMES[base](text);
    if (/^requirements[^/]*\.txt$/i.test(base)) return parseRequirementsTxt(text);
    return null;
  }

  return {
    parsePackageLockJson,
    parseYarnLock, parseYarnLockV1, parseYarnLockV2,
    parsePnpmLockYaml,
    parsePoetryLock,
    parseUvLock,
    parseCargoLock,
    parseComposerLock,
    parseGoMod,
    parseGemfileLock,
    parseRequirementsTxt,
    parsePackageResolved,
    isLockfile,
    detectAndParse,
    LOCKFILE_NAMES,
  };
}));
