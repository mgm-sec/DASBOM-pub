/**
 * Tests for output/viz/lib/viz-utils.js
 *
 * Covers:
 *  - verTuple / verAhead / majorGap  (version helpers)
 *  - licenseCategory
 *  - secStatRow / secStatHtml         (HTML stat rendering)
 *  - priScoreColor
 *  - calcPackagePriority
 *  - bfsFromNodes                     (BFS traversal depth)
 *  - passesBaseFilters                (all filter permutations)
 */

const VizUtils = require('../../output/viz/lib/viz-utils.js');

const {
  verTuple, verAhead, majorGap,
  licenseCategory,
  secStatRow, secStatHtml,
  priScoreColor,
  calcPackagePriority,
  bfsFromNodes,
  passesBaseFilters,
} = VizUtils;

// ─────────────────────────────────────────────────────────────────────────────
// verTuple
// ─────────────────────────────────────────────────────────────────────────────
describe('verTuple', () => {
  test('basic semver', () => expect(verTuple('1.2.3')).toEqual([1, 2, 3]));
  test('strips leading v', () => expect(verTuple('v2.0.0')).toEqual([2, 0, 0]));
  test('single segment', () => expect(verTuple('4')).toEqual([4]));
  test('null → [NaN] (String(null)="null")', () => expect(verTuple(null)).toEqual([NaN]));
  test('undefined → [NaN]', () => expect(verTuple(undefined)).toEqual([NaN]));
  test('empty string → [0] (Number("")=0)', () => expect(verTuple('')).toEqual([0]));
  test('date-style version', () => expect(verTuple('20230101')).toEqual([20230101]));
});

// ─────────────────────────────────────────────────────────────────────────────
// verAhead
// ─────────────────────────────────────────────────────────────────────────────
describe('verAhead', () => {
  test('cur > lat  → true',  () => expect(verAhead('2.0.0', '1.9.9')).toBe(true));
  test('cur < lat  → false', () => expect(verAhead('1.0.0', '2.0.0')).toBe(false));
  test('equal      → false', () => expect(verAhead('1.2.3', '1.2.3')).toBe(false));
  test('patch ahead',        () => expect(verAhead('1.0.1', '1.0.0')).toBe(true));
  test('minor behind',       () => expect(verAhead('1.1.0', '1.2.0')).toBe(false));
  test('different lengths — cur ahead', () => expect(verAhead('2', '1.9.9')).toBe(true));
  test('different lengths — cur behind', () => expect(verAhead('1', '1.0.1')).toBe(false));
});

// ─────────────────────────────────────────────────────────────────────────────
// majorGap
// ─────────────────────────────────────────────────────────────────────────────
describe('majorGap', () => {
  test('no gap',         () => expect(majorGap('1.2.3', '1.5.0')).toBe(0));
  test('one major',      () => expect(majorGap('1.0.0', '2.0.0')).toBe(1));
  test('three majors',   () => expect(majorGap('1.0.0', '4.0.0')).toBe(3));
  test('capped at 20',   () => expect(majorGap('0.1.0', '20071211.0.0')).toBe(20));
  test('null current',   () => expect(majorGap(null, '2.0.0')).toBe(0));
  test('null latest',    () => expect(majorGap('1.0.0', null)).toBe(0));
  test('both null',      () => expect(majorGap(null, null)).toBe(0));
  test('non-numeric ver', () => expect(majorGap('abc', '2.0.0')).toBe(0));
  test('cur ahead of lat → 0 (never negative)', () => expect(majorGap('3.0.0', '1.0.0')).toBe(0));
  test('exact cap boundary', () => expect(majorGap('0.0.0', '20.0.0')).toBe(20));
  test('just over cap',      () => expect(majorGap('0.0.0', '21.0.0')).toBe(20));
});

// ─────────────────────────────────────────────────────────────────────────────
// licenseCategory
// ─────────────────────────────────────────────────────────────────────────────
describe('licenseCategory', () => {
  test('MIT → permissive',        () => expect(licenseCategory('MIT')).toBe('permissive'));
  test('Apache-2.0 → permissive', () => expect(licenseCategory('Apache-2.0')).toBe('permissive'));
  test('BSD-3-Clause → permissive', () => expect(licenseCategory('BSD-3-Clause')).toBe('permissive'));
  test('ISC → permissive',        () => expect(licenseCategory('ISC')).toBe('permissive'));
  test('CC0-1.0 → permissive',    () => expect(licenseCategory('CC0-1.0')).toBe('permissive'));
  test('GPL-2.0 → copyleft',      () => expect(licenseCategory('GPL-2.0')).toBe('copyleft'));
  test('GPL-3.0 → copyleft',      () => expect(licenseCategory('GPL-3.0')).toBe('copyleft'));
  test('AGPL-3.0 → copyleft',     () => expect(licenseCategory('AGPL-3.0')).toBe('copyleft'));
  test('EUPL-1.2 → copyleft',     () => expect(licenseCategory('EUPL-1.2')).toBe('copyleft'));
  test('LGPL-2.1 → weak-copyleft', () => expect(licenseCategory('LGPL-2.1')).toBe('weak-copyleft'));
  test('MPL-2.0 → weak-copyleft', () => expect(licenseCategory('MPL-2.0')).toBe('weak-copyleft'));
  test('NOASSERTION → unspecified', () => expect(licenseCategory('NOASSERTION')).toBe('unspecified'));
  test('null → unspecified',      () => expect(licenseCategory(null)).toBe('unspecified'));
  test('empty → unspecified',     () => expect(licenseCategory('')).toBe('unspecified'));
  test('LicenseRef-Unlicensed → proprietary', () => expect(licenseCategory('LicenseRef-Unlicensed')).toBe('proprietary'));
  test('unknown string → other',  () => expect(licenseCategory('Proprietary-Custom-XYZ')).toBe('other'));
  test('case-insensitive mit',    () => expect(licenseCategory('mit')).toBe('permissive'));
});

// ─────────────────────────────────────────────────────────────────────────────
// secStatRow
// ─────────────────────────────────────────────────────────────────────────────
describe('secStatRow', () => {
  test('count=0 returns empty string', () => expect(secStatRow('#fff', 'Test', 0, 10)).toBe(''));
  test('contains label',  () => expect(secStatRow('#red', 'MyLabel', 3, 10)).toContain('MyLabel'));
  test('contains count',  () => expect(secStatRow('#red', 'X', 3, 10)).toContain('3'));
  test('contains pct 30%', () => expect(secStatRow('#red', 'X', 3, 10)).toContain('30%'));
  test('100% when all',   () => expect(secStatRow('#red', 'X', 10, 10)).toContain('100%'));
  test('0% when total=0', () => expect(secStatRow('#red', 'X', 1, 0)).toContain('0%'));
});

// ─────────────────────────────────────────────────────────────────────────────
// secStatHtml
// ─────────────────────────────────────────────────────────────────────────────
describe('secStatHtml', () => {
  const pkg = (overrides) => ({
    cve_count: 0, is_latest: true, abandoned: false, confusion_risk: false,
    ...overrides,
  });

  test('empty array', () => {
    const r = secStatHtml([]);
    expect(r.total).toBe(0);
    expect(r.withCVE).toBe(0);
  });

  test('CVE takes priority over outdated', () => {
    const r = secStatHtml([pkg({ cve_count: 2, is_latest: false })]);
    expect(r.withCVE).toBe(1);
    expect(r.outdated).toBe(0);
  });

  test('outdated before abandoned', () => {
    const r = secStatHtml([pkg({ is_latest: false, abandoned: true })]);
    expect(r.outdated).toBe(1);
    expect(r.abandoned).toBe(0);
  });

  test('up-to-date counted as ok', () => {
    const r = secStatHtml([pkg({ is_latest: true })]);
    expect(r.ok).toBe(1);
  });

  test('noData when is_latest=null', () => {
    const r = secStatHtml([pkg({ is_latest: null })]);
    expect(r.noData).toBe(1);
  });

  test('confusion risk counted separately (non-exclusive)', () => {
    const r = secStatHtml([pkg({ confusion_risk: true, is_latest: true })]);
    expect(r.confusion).toBe(1);
    expect(r.ok).toBe(1);  // still counted as ok
  });

  test('totals sum correctly', () => {
    const pkgs = [
      pkg({ cve_count: 1 }),
      pkg({ is_latest: false }),
      pkg({ abandoned: true }),
      pkg({ is_latest: true }),
      pkg({ is_latest: null }),
    ];
    const r = secStatHtml(pkgs);
    expect(r.withCVE + r.outdated + r.abandoned + r.ok + r.noData).toBe(5);
    expect(r.total).toBe(5);
  });

  test('html contains package count', () => {
    const r = secStatHtml([pkg()]);
    expect(r.html).toContain('1');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// priScoreColor
// ─────────────────────────────────────────────────────────────────────────────
describe('priScoreColor', () => {
  test('≥70 → red',       () => expect(priScoreColor(70)).toBe('#dc2626'));
  test('≥40 → amber',     () => expect(priScoreColor(40)).toBe('#e3a020'));
  test('≥18 → yellow',    () => expect(priScoreColor(18)).toBe('#c4a000'));
  test('<18 → green',     () => expect(priScoreColor(17)).toBe('#3e8a50'));
  test('0 → green',       () => expect(priScoreColor(0)).toBe('#3e8a50'));
  test('100 → red',       () => expect(priScoreColor(100)).toBe('#dc2626'));
  test('69 → amber',      () => expect(priScoreColor(69)).toBe('#e3a020'));
  test('39 → yellow',     () => expect(priScoreColor(39)).toBe('#c4a000'));
});

// ─────────────────────────────────────────────────────────────────────────────
// calcPackagePriority
// ─────────────────────────────────────────────────────────────────────────────
describe('calcPackagePriority', () => {
  const makeAudit = (overrides) => ({
    cve_count: 0, max_severity: null, max_cvss: null,
    is_latest: true, abandoned: false, confusion_risk: false,
    latest_version: null,
    ...overrides,
  });

  test('no audit → 0', () => {
    expect(calcPackagePriority('pkg:npm/foo@1', { _usage_score: 0 }, {}, 0)).toBe(0);
  });

  test('CVE raises score significantly', () => {
    const audit = { 'pkg:npm/foo@1': makeAudit({ cve_count: 1, max_severity: 'CRITICAL', max_cvss: 9.5 }) };
    const score = calcPackagePriority('pkg:npm/foo@1', { _usage_score: 0, _version: '1.0.0' }, audit, 0);
    expect(score).toBeGreaterThan(30);
  });

  test('high reach amplifies CVE score', () => {
    const audit = { 'pkg:npm/foo@1': makeAudit({ cve_count: 1, max_severity: 'HIGH', max_cvss: 7.0 }) };
    const lowScore  = calcPackagePriority('pkg:npm/foo@1', { _usage_score: 1,   _version: '1.0.0' }, audit, 1000);
    const highScore = calcPackagePriority('pkg:npm/foo@1', { _usage_score: 1000, _version: '1.0.0' }, audit, 1000);
    expect(highScore).toBeGreaterThan(lowScore);
  });

  test('breaking major gap adds penalty on CVE package', () => {
    const audit = { 'pkg:npm/foo@1': makeAudit({ cve_count: 1, max_cvss: 7.0, latest_version: '5.0.0' }) };
    const gapScore  = calcPackagePriority('pkg:npm/foo@1', { _usage_score: 0, _version: '1.0.0' }, audit, 0);
    const noGapScore = calcPackagePriority('pkg:npm/foo@1', { _usage_score: 0, _version: '5.0.0' }, audit, 0);
    expect(gapScore).toBeGreaterThan(noGapScore);
  });

  test('abandoned (no CVE) adds score', () => {
    const audit = { 'pkg:npm/foo@1': makeAudit({ abandoned: true, is_latest: true }) };
    const score = calcPackagePriority('pkg:npm/foo@1', { _usage_score: 0 }, audit, 0);
    expect(score).toBeGreaterThan(0);
  });

  test('outdated (no CVE, not abandoned) adds score', () => {
    const audit = { 'pkg:npm/foo@1': makeAudit({ is_latest: false, latest_version: '2.0.0' }) };
    const score = calcPackagePriority('pkg:npm/foo@1', { _usage_score: 0, _version: '1.0.0' }, audit, 0);
    expect(score).toBeGreaterThan(0);
  });

  test('confusion risk adds score', () => {
    const audit = { 'pkg:npm/foo@1': makeAudit({ confusion_risk: true, is_latest: true }) };
    const score = calcPackagePriority('pkg:npm/foo@1', { _usage_score: 0 }, audit, 0);
    expect(score).toBeGreaterThan(0);
  });

  test('returns integer (Math.round)', () => {
    const audit = { 'pkg:npm/foo@1': makeAudit({ cve_count: 1, max_cvss: 5.0 }) };
    const score = calcPackagePriority('pkg:npm/foo@1', { _usage_score: 3 }, audit, 100);
    expect(Number.isInteger(score)).toBe(true);
  });

  test('date-versioned latest does not inflate score (majorGap capped)', () => {
    const audit = { 'pkg:npm/robot@0.1': makeAudit({ cve_count: 1, max_cvss: 5.0, latest_version: '20071211.0.0' }) };
    const score = calcPackagePriority('pkg:npm/robot@0.1', { _usage_score: 0, _version: '0.1.0' }, audit, 0);
    // majorGap capped at 20 — score should be finite and reasonable, not millions
    expect(score).toBeLessThan(10000);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// bfsFromNodes
// ─────────────────────────────────────────────────────────────────────────────
describe('bfsFromNodes', () => {
  // Build a simple adjacency map as getNeighbors
  const adj = {
    A: ['B', 'C'],
    B: ['D'],
    C: ['D', 'E'],
    D: [],
    E: ['F'],
    F: [],
  };
  const getNeighbors = (n) => adj[n] || [];
  const nodeExists   = (n) => n in adj;

  test('depth 0 → only seeds', () => {
    const r = bfsFromNodes(['A'], 0, getNeighbors, nodeExists);
    expect([...r]).toEqual(['A']);
  });

  test('depth 1 → seeds + direct neighbors', () => {
    const r = bfsFromNodes(['A'], 1, getNeighbors, nodeExists);
    expect(r.has('A')).toBe(true);
    expect(r.has('B')).toBe(true);
    expect(r.has('C')).toBe(true);
    expect(r.has('D')).toBe(false);
  });

  test('depth 2 → reaches D and E', () => {
    const r = bfsFromNodes(['A'], 2, getNeighbors, nodeExists);
    expect(r.has('D')).toBe(true);
    expect(r.has('E')).toBe(true);
    expect(r.has('F')).toBe(false);
  });

  test('depth 3 → reaches F', () => {
    const r = bfsFromNodes(['A'], 3, getNeighbors, nodeExists);
    expect(r.has('F')).toBe(true);
  });

  test('full traversal reaches all nodes', () => {
    const r = bfsFromNodes(['A'], 99, getNeighbors, nodeExists);
    expect(r.size).toBe(6);
  });

  test('seed not in graph skipped', () => {
    const r = bfsFromNodes(['MISSING'], 1, getNeighbors, nodeExists);
    expect(r.size).toBe(0);
  });

  test('multiple seeds', () => {
    const r = bfsFromNodes(['D', 'E'], 1, getNeighbors, nodeExists);
    expect(r.has('D')).toBe(true);
    expect(r.has('E')).toBe(true);
    expect(r.has('F')).toBe(true);
  });

  test('cycles do not cause infinite loop', () => {
    const cycAdj = { X: ['Y'], Y: ['X'] };
    const r = bfsFromNodes(['X'], 100, n => cycAdj[n] || [], n => n in cycAdj);
    expect(r.size).toBe(2);
  });

  test('no nodeExists arg — still works', () => {
    // Seeds that "exist" — no filtering
    const r = bfsFromNodes(['A'], 1, getNeighbors);
    expect(r.has('A')).toBe(true);
    expect(r.has('B')).toBe(true);
  });

  test('returns a Set', () => {
    const r = bfsFromNodes(['A'], 1, getNeighbors, nodeExists);
    expect(r).toBeInstanceOf(Set);
  });

  test('depth 8 chain — all 8 layers reachable', () => {
    const chainAdj = {};
    for (let i = 0; i < 9; i++) chainAdj[`N${i}`] = i < 8 ? [`N${i+1}`] : [];
    const r = bfsFromNodes(['N0'], 8, n => chainAdj[n] || [], n => n in chainAdj);
    for (let i = 0; i <= 8; i++) expect(r.has(`N${i}`)).toBe(true);
  });

  test('depth 8 chain — layer 9 NOT included at maxDepth=8', () => {
    const chainAdj = {};
    for (let i = 0; i < 10; i++) chainAdj[`N${i}`] = i < 9 ? [`N${i+1}`] : [];
    const r = bfsFromNodes(['N0'], 8, n => chainAdj[n] || [], n => n in chainAdj);
    expect(r.has('N9')).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// passesBaseFilters
// ─────────────────────────────────────────────────────────────────────────────
describe('passesBaseFilters', () => {
  const makeState = (overrides = {}) => ({
    showExternal: false,
    ecosystems: new Set(['npm', 'pypi']),
    minRepos: 1,
    license: null,
    conflictsOnly: false,
    cveOnly: false,
    outdatedOnly: false,
    abandonedOnly: false,
    confusionOnly: false,
    minSeverity: null,
    search: '',
    ...overrides,
  });

  const makePkg = (overrides = {}) => ({
    _type: 'package',
    _in_org: true,
    _ecosystem: 'npm',
    _repo_count: 3,
    _license: 'MIT',
    _has_conflict: false,
    _name: 'lodash',
    _purl: 'pkg:npm/lodash@4.17',
    _repos: ['repo-a'],
    label: 'lodash',
    ...overrides,
  });

  const makeAud = (overrides = {}) => ({
    cve_count: 0,
    is_latest: true,
    abandoned: false,
    confusion_risk: false,
    max_severity: null,
    ...overrides,
  });

  test('basic passing package', () => {
    expect(passesBaseFilters('pkg:npm/lodash@4', makePkg(), makeState(), {})).toBe(true);
  });

  test('external pkg hidden when showExternal=false', () => {
    const pkg = makePkg({ _in_org: false });
    expect(passesBaseFilters('k', pkg, makeState({ showExternal: false }), {})).toBe(false);
  });

  test('external pkg visible when showExternal=true', () => {
    const pkg = makePkg({ _in_org: false });
    expect(passesBaseFilters('k', pkg, makeState({ showExternal: true }), {})).toBe(true);
  });

  test('ecosystem filter excludes non-matching', () => {
    const pkg = makePkg({ _ecosystem: 'cargo' });
    expect(passesBaseFilters('k', pkg, makeState({ ecosystems: new Set(['npm']) }), {})).toBe(false);
  });

  test('minRepos filter excludes low-count org pkg', () => {
    const pkg = makePkg({ _in_org: true, _repo_count: 1 });
    expect(passesBaseFilters('k', pkg, makeState({ minRepos: 2 }), {})).toBe(false);
  });

  test('minRepos does NOT apply to external pkgs', () => {
    const pkg = makePkg({ _in_org: false, _repo_count: 1 });
    expect(passesBaseFilters('k', pkg, makeState({ minRepos: 2, showExternal: true }), {})).toBe(true);
  });

  test('license filter matches correctly', () => {
    const pkg = makePkg({ _license: 'MIT' });
    expect(passesBaseFilters('k', pkg, makeState({ license: 'permissive' }), {})).toBe(true);
    expect(passesBaseFilters('k', pkg, makeState({ license: 'copyleft' }), {})).toBe(false);
  });

  test('conflictsOnly hides pkg without conflict', () => {
    const pkg = makePkg({ _has_conflict: false });
    expect(passesBaseFilters('k', pkg, makeState({ conflictsOnly: true }), {})).toBe(false);
  });

  test('conflictsOnly passes pkg with conflict', () => {
    const pkg = makePkg({ _has_conflict: true });
    expect(passesBaseFilters('k', pkg, makeState({ conflictsOnly: true }), {})).toBe(true);
  });

  test('cveOnly hides pkg without CVEs', () => {
    const audit = { k: makeAud({ cve_count: 0 }) };
    expect(passesBaseFilters('k', makePkg(), makeState({ cveOnly: true }), audit)).toBe(false);
  });

  test('cveOnly passes pkg with CVEs', () => {
    const audit = { k: makeAud({ cve_count: 2 }) };
    expect(passesBaseFilters('k', makePkg(), makeState({ cveOnly: true }), audit)).toBe(true);
  });

  test('outdatedOnly', () => {
    const audit = { k: makeAud({ is_latest: false }) };
    expect(passesBaseFilters('k', makePkg(), makeState({ outdatedOnly: true }), audit)).toBe(true);
    const audit2 = { k: makeAud({ is_latest: true }) };
    expect(passesBaseFilters('k', makePkg(), makeState({ outdatedOnly: true }), audit2)).toBe(false);
  });

  test('abandonedOnly', () => {
    const audit = { k: makeAud({ abandoned: true }) };
    expect(passesBaseFilters('k', makePkg(), makeState({ abandonedOnly: true }), audit)).toBe(true);
    expect(passesBaseFilters('k', makePkg(), makeState({ abandonedOnly: true }), {})).toBe(false);
  });

  test('confusionOnly', () => {
    const audit = { k: makeAud({ confusion_risk: true }) };
    expect(passesBaseFilters('k', makePkg(), makeState({ confusionOnly: true }), audit)).toBe(true);
    const audit2 = { k: makeAud({ confusion_risk: false }) };
    expect(passesBaseFilters('k', makePkg(), makeState({ confusionOnly: true }), audit2)).toBe(false);
  });

  test('minSeverity CRITICAL excludes HIGH pkg', () => {
    const audit = { k: makeAud({ cve_count: 1, max_severity: 'HIGH' }) };
    expect(passesBaseFilters('k', makePkg(), makeState({ minSeverity: 'CRITICAL' }), audit)).toBe(false);
  });

  test('minSeverity HIGH includes HIGH pkg', () => {
    const audit = { k: makeAud({ cve_count: 1, max_severity: 'HIGH' }) };
    expect(passesBaseFilters('k', makePkg(), makeState({ minSeverity: 'HIGH' }), audit)).toBe(true);
  });

  test('minSeverity HIGH includes CRITICAL pkg', () => {
    const audit = { k: makeAud({ cve_count: 1, max_severity: 'CRITICAL' }) };
    expect(passesBaseFilters('k', makePkg(), makeState({ minSeverity: 'HIGH' }), audit)).toBe(true);
  });

  test('minSeverity with no CVE → excluded', () => {
    const audit = { k: makeAud({ cve_count: 0, max_severity: 'CRITICAL' }) };
    expect(passesBaseFilters('k', makePkg(), makeState({ minSeverity: 'LOW' }), audit)).toBe(false);
  });

  test('search by name', () => {
    const pkg = makePkg({ _name: 'lodash' });
    expect(passesBaseFilters('k', pkg, makeState({ search: 'lodash' }), {})).toBe(true);
    expect(passesBaseFilters('k', pkg, makeState({ search: 'express' }), {})).toBe(false);
  });

  test('search by purl', () => {
    const pkg = makePkg({ _purl: 'pkg:npm/lodash@4' });
    expect(passesBaseFilters('k', pkg, makeState({ search: 'pkg:npm' }), {})).toBe(true);
  });

  test('search by repo name', () => {
    const pkg = makePkg({ _repos: ['my-api-service'] });
    expect(passesBaseFilters('k', pkg, makeState({ search: 'my-api' }), {})).toBe(true);
  });

  test('search case-insensitive', () => {
    const pkg = makePkg({ _name: 'Lodash' });
    expect(passesBaseFilters('k', pkg, makeState({ search: 'lodash' }), {})).toBe(true);
  });

  test('non-package node passes all package filters', () => {
    const repo = { _type: 'repository', _name: 'my-repo', label: 'my-repo', _repos: [] };
    expect(passesBaseFilters('k', repo, makeState({ cveOnly: true, ecosystems: new Set() }), {})).toBe(true);
  });

  test('search applies to non-package nodes too', () => {
    const repo = { _type: 'repository', label: 'special-repo', _name: 'special-repo', _repos: [] };
    expect(passesBaseFilters('k', repo, makeState({ search: 'special' }), {})).toBe(true);
    expect(passesBaseFilters('k', repo, makeState({ search: 'other' }), {})).toBe(false);
  });
});
