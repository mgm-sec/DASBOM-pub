/**
 * viz-utils.js — pure, testable functions shared between index.html and Jest tests.
 * UMD: window.VizUtils in browser, module.exports in Node.js.
 */
(function (factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else {
    window.VizUtils = factory();
  }
}(function () {
  'use strict';

  // ── Version helpers ─────────────────────────────────────────────────────────

  function verTuple(s) {
    try { return String(s).replace(/^v/, '').split('.').map(Number); }
    catch (_) { return []; }
  }

  function verAhead(cur, lat) {
    const a = verTuple(cur), b = verTuple(lat);
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      if ((a[i] || 0) > (b[i] || 0)) return true;
      if ((a[i] || 0) < (b[i] || 0)) return false;
    }
    return false;
  }

  function majorGap(current, latest) {
    if (!current || !latest) return 0;
    const cur = verTuple(current)[0];
    const lat = verTuple(latest)[0];
    if (cur == null || lat == null || isNaN(cur) || isNaN(lat)) return 0;
    // Cap at 20 — prevents date-versioned packages (e.g. 0.1.0 → 20071211) from inflating scores
    return Math.min(20, Math.max(0, lat - cur));
  }

  // ── License categorisation ───────────────────────────────────────────────────

  function licenseCategory(lic) {
    if (!lic || lic === 'NOASSERTION') return 'unspecified';
    if (/licenseref-unlicensed|licenseref-see-license/i.test(lic)) return 'proprietary';
    if (/agpl|\bgpl-[23]\b|\bgpl\b|eupl|cc-by-sa/i.test(lic)) return 'copyleft';
    if (/lgpl|mpl-|cddl|cpl-/i.test(lic)) return 'weak-copyleft';
    if (/mit\b|apache|bsd|isc|0bsd|blueoak|cc0|afl-|zlib|psf|wtfpl|unlicense/i.test(lic)) return 'permissive';
    return 'other';
  }

  // ── Security stats ───────────────────────────────────────────────────────────

  function secStatRow(color, label, count, total) {
    if (!count) return '';
    const pct = total > 0 ? Math.round(count / total * 100) : 0;
    return `<div class="sec-stat-row">
    <span class="sec-stat-dot" style="background:${color}"></span>
    <span class="sec-stat-lbl">${label}</span>
    <span class="sec-stat-cnt" style="color:${color}">${count.toLocaleString()}</span>
    <span class="sec-stat-pct">${pct}%</span>
  </div>
  <div class="sec-bar"><div class="sec-bar-fill" style="width:${pct}%;background:${color}"></div></div>`;
  }

  /**
   * Categorise an array of audit objects (from secAudit) into mutually-exclusive
   * buckets.  Priority: CVE > outdated > abandoned > ok > noData.
   * Returns counts + pre-rendered HTML snippet.
   */
  function secStatHtml(pkgAudits) {
    let withCVE = 0, totalCveIds = 0, abandoned = 0, outdated = 0, ok = 0, noData = 0;
    pkgAudits.forEach(p => {
      if (p.cve_count > 0)           { withCVE++; totalCveIds += p.cve_count; }
      else if (p.is_latest === false) outdated++;
      else if (p.abandoned === true)  abandoned++;
      else if (p.is_latest === true)  ok++;
      else                            noData++;
    });
    const confusion = pkgAudits.filter(p => p.confusion_risk === true).length;
    const total = pkgAudits.length;
    const cveLabel = withCVE > 0
      ? `With CVEs <span style="color:#8b949e;font-size:9px">${totalCveIds.toLocaleString()} IDs · ${withCVE} pkgs</span>`
      : 'With CVEs';
    const html = `<div style="font-size:10px;color:#6e7681;margin-bottom:5px">${total.toLocaleString()} packages audited</div>
    ${secStatRow('#ff7b72', cveLabel, withCVE, total)}
    ${secStatRow('#c084fc', 'Abandoned', abandoned, total)}
    ${secStatRow('#e3b341', 'Outdated', outdated, total)}
    ${secStatRow('#3fb950', 'Up to date', ok, total)}
    ${secStatRow('#6e7681', 'No data', noData, total)}
    ${confusion ? secStatRow('#f97316', '🎭 Confusion risk', confusion, total) : ''}`;
    return { withCVE, totalCveIds, abandoned, outdated, ok, noData, confusion, total, html };
  }

  // ── Priority scoring ─────────────────────────────────────────────────────────

  function priScoreColor(s) {
    if (s >= 70) return '#dc2626';
    if (s >= 40) return '#e3a020';
    if (s >= 18) return '#c4a000';
    return '#3e8a50';
  }

  /**
   * Compute a priority score for a package node.
   * @param {string}  key          — node key (PURL)
   * @param {object}  attrs        — node attributes (_version, _usage_score, …)
   * @param {object}  secAudit     — map of purl → audit object
   * @param {number}  maxUsageScore — maximum usage score across all packages (for normalisation)
   */
  function calcPackagePriority(key, attrs, secAudit, maxUsageScore) {
    const aud  = secAudit[key];
    const us   = attrs._usage_score || 0;
    const norm = maxUsageScore > 0 ? Math.log2(1 + us) / Math.log2(1 + maxUsageScore) : 0;
    let score  = 0;

    // CVE — dominant factor; reach amplifies impact up to 2.5×
    if (aud && aud.cve_count > 0) {
      const cvss = aud.max_cvss != null
        ? aud.max_cvss
        : ({ CRITICAL: 9.0, HIGH: 7.0, MEDIUM: 5.0, LOW: 2.0 }[aud.max_severity] || 4.0);
      score += cvss * Math.sqrt(aud.cve_count) * (1 + norm * 1.5) * 4;
      // Breaking change makes fix harder — penalty per major version behind
      const gap = majorGap(attrs._version, aud.latest_version);
      if (gap > 0) score += gap * 5 * (1 + norm);
    }

    // Reach signal — widespread deps carry implicit risk
    score += norm * 20;

    // Abandoned, no CVE — no upstream fixes possible
    if (aud && aud.abandoned && !(aud.cve_count > 0)) score += 5 + norm * 15;

    // Outdated, no CVE, not abandoned — actionable; breaking change raises urgency
    if (aud && aud.is_latest === false && !(aud.cve_count > 0) && !aud.abandoned) {
      const gap = majorGap(attrs._version, aud.latest_version);
      score += 2 + norm * 8 + gap * 6 * (1 + norm * 0.5);
    }

    // Confusion risk — supply chain attack vector
    if (aud && aud.confusion_risk) score += 10;

    return Math.round(score);
  }

  // ── BFS ──────────────────────────────────────────────────────────────────────

  /**
   * BFS from seed nodes up to maxDepth hops.
   * @param {string[]}   seeds        — starting node keys
   * @param {number}     maxDepth     — maximum hops
   * @param {Function}   getNeighbors — (nodeKey) → string[]  next reachable nodes
   * @param {Function}   [nodeExists] — (nodeKey) → boolean   (default: always true)
   * @returns {Set<string>} all visited node keys (including seeds)
   */
  function bfsFromNodes(seeds, maxDepth, getNeighbors, nodeExists) {
    const exists = nodeExists || function () { return true; };
    const visited = new Set();
    seeds.forEach(function (s) { if (exists(s)) visited.add(s); });
    let frontier = Array.from(visited);
    for (let d = 0; d < maxDepth; d++) {
      const next = [];
      frontier.forEach(function (node) {
        getNeighbors(node).forEach(function (n) {
          if (!visited.has(n)) { visited.add(n); next.push(n); }
        });
      });
      if (!next.length) break;
      frontier = next;
    }
    return visited;
  }

  // ── Filter helpers ───────────────────────────────────────────────────────────

  const SEV_RANK = { CRITICAL: 4, HIGH: 3, MODERATE: 2, MEDIUM: 2, LOW: 1, UNKNOWN: 0 };

  /**
   * Returns true if a node (key + attrs) passes all active base filters.
   * @param {string} key       — node key
   * @param {object} a         — node attributes
   * @param {object} state     — filter state (ecosystems, minRepos, cveOnly, …)
   * @param {object} secAudit  — purl → audit map
   */
  function passesBaseFilters(key, a, state, secAudit) {
    if (a._type === 'package') {
      if (!a._in_org && !state.showExternal)             return false;
      if (state.ecosystems && !state.ecosystems.has(a._ecosystem)) return false;
      if (a._in_org && state.minRepos > 1 && a._repo_count < state.minRepos) return false;
      if (state.license && licenseCategory(a._license) !== state.license) return false;
      if (state.conflictsOnly && !a._has_conflict) return false;
      const aud = secAudit[key];
      if (state.cveOnly)     { if (!aud || aud.cve_count === 0) return false; }
      if (state.outdatedOnly){ if (!aud || aud.is_latest !== false) return false; }
      if (state.abandonedOnly){ if (!aud || !aud.abandoned) return false; }
      if (state.confusionOnly){ if (!aud || !aud.confusion_risk) return false; }
      if (state.minSeverity) {
        if (!aud || aud.cve_count === 0) return false;
        if ((SEV_RANK[aud.max_severity] || 0) < (SEV_RANK[state.minSeverity] || 0)) return false;
      }
    }
    if (state.search) {
      const q = state.search;
      if (!(a._name   || '').toLowerCase().includes(q) &&
          !(a._purl   || '').toLowerCase().includes(q) &&
          !((a._repos || []).some(r => r.toLowerCase().includes(q))) &&
          !(a.label   || '').toLowerCase().includes(q)) return false;
    }
    return true;
  }

  return {
    verTuple, verAhead, majorGap,
    licenseCategory,
    secStatRow, secStatHtml,
    priScoreColor, calcPackagePriority,
    bfsFromNodes,
    passesBaseFilters,
  };
}));
