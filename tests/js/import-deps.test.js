/**
 * Tests for output/viz/lib/import-deps.js
 *
 * Only tests pure functions that don't need network/DOM:
 *  - parseRepoUrl
 *  - dedupePackages
 *  - pickFreeAngle
 *  - buildImportedGraph
 *  - simpleCvss3
 */

const ID = require('../../output/viz/lib/import-deps.js');

// ─────────────────────────────────────────────────────────────────────────────
// parseRepoUrl
// ─────────────────────────────────────────────────────────────────────────────
describe('parseRepoUrl', () => {
  describe('GitHub', () => {
    test('basic URL', () => {
      const r = ID.parseRepoUrl('https://github.com/owner/repo');
      expect(r).toMatchObject({ host: 'github', owner: 'owner', repo: 'repo' });
    });
    test('trailing slash stripped', () => {
      const r = ID.parseRepoUrl('https://github.com/owner/repo/');
      expect(r).toMatchObject({ host: 'github', owner: 'owner', repo: 'repo' });
    });
    test('.git suffix stripped', () => {
      const r = ID.parseRepoUrl('https://github.com/owner/repo.git');
      expect(r).toMatchObject({ host: 'github', owner: 'owner', repo: 'repo' });
    });
    test('with branch', () => {
      const r = ID.parseRepoUrl('https://github.com/owner/repo/tree/main');
      expect(r).toMatchObject({ host: 'github', branch: 'main', subdir: '' });
    });
    test('with branch and subdir', () => {
      const r = ID.parseRepoUrl('https://github.com/owner/repo/tree/main/apps/api');
      expect(r).toMatchObject({ host: 'github', branch: 'main', subdir: 'apps/api' });
    });
    test('no branch → branch null', () => {
      const r = ID.parseRepoUrl('https://github.com/owner/repo');
      expect(r.branch).toBeNull();
    });
    test('hyphenated owner/repo', () => {
      const r = ID.parseRepoUrl('https://github.com/my-org/my-cool-repo');
      expect(r).toMatchObject({ owner: 'my-org', repo: 'my-cool-repo' });
    });
    test('numeric owner', () => {
      const r = ID.parseRepoUrl('https://github.com/123org/repo');
      expect(r.owner).toBe('123org');
    });
  });

  describe('GitLab', () => {
    test('basic URL', () => {
      const r = ID.parseRepoUrl('https://gitlab.com/owner/repo');
      expect(r).toMatchObject({ host: 'gitlab', owner: 'owner', repo: 'repo' });
    });
    test('subgroup', () => {
      const r = ID.parseRepoUrl('https://gitlab.com/group/subgroup/repo');
      expect(r).toMatchObject({ host: 'gitlab', owner: 'group/subgroup', repo: 'repo' });
    });
    test('with branch via tree', () => {
      const r = ID.parseRepoUrl('https://gitlab.com/owner/repo/-/tree/develop');
      expect(r).toMatchObject({ host: 'gitlab', branch: 'develop' });
    });
  });

  describe('Bitbucket', () => {
    test('basic URL', () => {
      const r = ID.parseRepoUrl('https://bitbucket.org/owner/repo');
      expect(r).toMatchObject({ host: 'bitbucket', owner: 'owner', repo: 'repo' });
    });
    test('with src/branch', () => {
      const r = ID.parseRepoUrl('https://bitbucket.org/owner/repo/src/main');
      expect(r).toMatchObject({ host: 'bitbucket', branch: 'main' });
    });
  });

  describe('edge cases', () => {
    test('empty string → null', () => expect(ID.parseRepoUrl('')).toBeNull());
    test('null → null',         () => expect(ID.parseRepoUrl(null)).toBeNull());
    test('bare hostname → null', () => expect(ID.parseRepoUrl('https://github.com')).toBeNull());
    test('only owner → null',    () => expect(ID.parseRepoUrl('https://github.com/owner')).toBeNull());
    test('unknown host → null',  () => expect(ID.parseRepoUrl('https://example.com/owner/repo')).toBeNull());
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// dedupePackages
// ─────────────────────────────────────────────────────────────────────────────
describe('dedupePackages', () => {
  const p = (eco, name, version) => ({ ecosystem: eco, name, version, deps: [] });

  test('no dupes → unchanged length', () => {
    const pkgs = [p('npm', 'lodash', '4.17.21'), p('npm', 'express', '4.18.2')];
    expect(ID.dedupePackages(pkgs)).toHaveLength(2);
  });

  test('exact duplicate removed', () => {
    const pkgs = [p('npm', 'lodash', '4.17.21'), p('npm', 'lodash', '4.17.21')];
    expect(ID.dedupePackages(pkgs)).toHaveLength(1);
  });

  test('same name different version → both kept', () => {
    const pkgs = [p('npm', 'lodash', '4.17.21'), p('npm', 'lodash', '3.10.0')];
    expect(ID.dedupePackages(pkgs)).toHaveLength(2);
  });

  test('same name different ecosystem → both kept', () => {
    const pkgs = [p('npm', 'yaml', '1.0.0'), p('pypi', 'yaml', '1.0.0')];
    expect(ID.dedupePackages(pkgs)).toHaveLength(2);
  });

  test('empty array → empty', () => {
    expect(ID.dedupePackages([])).toHaveLength(0);
  });

  test('preserves first occurrence', () => {
    const a = { ...p('npm', 'lodash', '4.17.21'), deps: ['foo'] };
    const b = { ...p('npm', 'lodash', '4.17.21'), deps: [] };
    const r = ID.dedupePackages([a, b]);
    expect(r[0].deps).toContain('foo');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// pickFreeAngle
// ─────────────────────────────────────────────────────────────────────────────
describe('pickFreeAngle', () => {
  test('no used angles → returns a number', () => {
    expect(typeof ID.pickFreeAngle([])).toBe('number');
  });

  test('result in [0, 2π)', () => {
    for (let i = 0; i < 10; i++) {
      const a = ID.pickFreeAngle([Math.random() * 6]);
      expect(a).toBeGreaterThanOrEqual(0);
      expect(a).toBeLessThan(2 * Math.PI + 0.001);
    }
  });

  test('with many angles, result is in the largest gap', () => {
    // 3 repos at 0, π/2, π → biggest gap is between π and 2π (= π wide)
    const used = [0, Math.PI / 2, Math.PI];
    const a = ID.pickFreeAngle(used);
    // Midpoint of [π, 2π] = 3π/2
    expect(a).toBeCloseTo(3 * Math.PI / 2, 1);
  });

  test('deterministic for fixed used angles', () => {
    const used = [0.5, 2.5, 4.5];
    expect(ID.pickFreeAngle(used)).toBeCloseTo(ID.pickFreeAngle(used), 5);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// buildImportedGraph
// ─────────────────────────────────────────────────────────────────────────────
describe('buildImportedGraph', () => {
  const pkgs = [
    { name: 'lodash',  version: '4.17.21', deps: [],         ecosystem: 'npm' },
    { name: 'express', version: '4.18.2',  deps: ['lodash'], ecosystem: 'npm' },
    { name: 'debug',   version: '2.6.9',   deps: [],         ecosystem: 'npm' },
  ];

  const result = ID.buildImportedGraph('myorg/myrepo', 'github', pkgs, []);

  test('returns { repoKey, angle, nodes, edges }', () => {
    expect(result).toHaveProperty('repoKey');
    expect(result).toHaveProperty('angle');
    expect(result).toHaveProperty('nodes');
    expect(result).toHaveProperty('edges');
  });

  test('first node is repo node', () => {
    expect(result.nodes[0].attributes._type).toBe('repository');
  });

  test('repo node has correct label', () => {
    expect(result.nodes[0].attributes.label).toBe('myorg/myrepo');
  });

  test('repo node marked _imported', () => {
    expect(result.nodes[0].attributes._imported).toBe(true);
  });

  test('repo node is not _in_org', () => {
    expect(result.nodes[0].attributes._in_org).toBe(false);
  });

  test('repo node has position', () => {
    expect(typeof result.nodes[0].attributes.x).toBe('number');
    expect(typeof result.nodes[0].attributes.y).toBe('number');
  });

  test('repo positioned at REPO_R distance from origin', () => {
    const { x, y } = result.nodes[0].attributes;
    const dist = Math.sqrt(x * x + y * y);
    expect(dist).toBeCloseTo(ID.REPO_R, 0);
  });

  test('package nodes count = pkgs.length', () => {
    const pkgNodes = result.nodes.filter(n => n.attributes._type === 'package');
    expect(pkgNodes).toHaveLength(3);
  });

  test('contains edges = pkgs.length', () => {
    const containsEdges = result.edges.filter(e => e.attributes._type === 'contains');
    expect(containsEdges).toHaveLength(3);
  });

  test('all contains edges from repoKey', () => {
    result.edges.filter(e => e.attributes._type === 'contains')
      .forEach(e => expect(e.source).toBe(result.repoKey));
  });

  test('depends_on edge for express→lodash exists', () => {
    const hasDepEdge = result.edges.some(
      e => e.attributes._type === 'depends_on' &&
           e.source.includes('express') && e.target.includes('lodash')
    );
    expect(hasDepEdge).toBe(true);
  });

  test('no self-edges', () => {
    result.edges.forEach(e => expect(e.source).not.toBe(e.target));
  });

  test('package nodes have purl as key', () => {
    const pkgNode = result.nodes.find(n => n.attributes._name === 'lodash');
    expect(pkgNode.key).toMatch(/^pkg:npm\/lodash@/);
  });

  test('package nodes have x/y coordinates', () => {
    result.nodes.filter(n => n.attributes._type === 'package').forEach(n => {
      expect(typeof n.attributes.x).toBe('number');
      expect(typeof n.attributes.y).toBe('number');
    });
  });

  test('empty pkgs → only repo node, no edges', () => {
    const r = ID.buildImportedGraph('x/y', 'github', [], []);
    expect(r.nodes).toHaveLength(1);
    expect(r.edges).toHaveLength(0);
  });

  test('repoKey format', () => {
    expect(result.repoKey).toBe('imported:github:myorg/myrepo');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// simpleCvss3
// ─────────────────────────────────────────────────────────────────────────────
describe('simpleCvss3', () => {
  test('high vector >= 9.0', () => {
    const s = ID.simpleCvss3('CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H');
    expect(s).toBeGreaterThanOrEqual(9.0);
  });

  test('low vector < 4.0', () => {
    const s = ID.simpleCvss3('CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N');
    expect(s).toBeLessThan(4.0);
  });

  test('null → null', () => expect(ID.simpleCvss3(null)).toBeNull());
  test('empty → null', () => expect(ID.simpleCvss3('')).toBeNull());
  test('non-CVSS3 string → null', () => expect(ID.simpleCvss3('CVSS:2.0/AV:N')).toBeNull());
  test('returns number', () => {
    const s = ID.simpleCvss3('CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H');
    expect(typeof s).toBe('number');
  });
  test('one decimal place', () => {
    const s = ID.simpleCvss3('CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H');
    expect(s).toBe(Math.round(s * 10) / 10);
  });
  test('score ≤ 10', () => {
    const s = ID.simpleCvss3('CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H');
    expect(s).toBeLessThanOrEqual(10);
  });
  test('score >= 0', () => {
    const s = ID.simpleCvss3('CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:N/I:N/A:N');
    expect(s).toBeGreaterThanOrEqual(0);
  });
});
