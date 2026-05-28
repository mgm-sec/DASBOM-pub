#!/usr/bin/env python3
"""
Radial BFS tree layout for dep_graph.json.

All spacing derived dynamically from node counts:

  TARGET_ARC      arc-length between adjacent nodes in same ring (graph units)
  SUBRING_STEP    radial gap between sub-rings  = TARGET_ARC  (uniform unit)
  MIN_STEP        radial gap between depth layers = TARGET_ARC × LAYER_FACTOR
  MAX_RING_NODES  nodes per ring; denser layers get more sub-rings

Non-overlap guarantee at initial camera zoom:
  Pixel arc-spacing = TARGET_ARC × viewport_px / (ratio × 2 × MAX_R)
  Set ratio = TARGET_ARC × viewport_px / (NODE_DIAM_PX × 2 × MAX_R)
  → nodes are exactly NODE_DIAM_PX apart at that ratio.
  This ratio is stored in graph attributes._initial_camera_ratio.

Run: python3 scripts/python/recompute_layout.py [dep_graph.json]
"""
import sys, json, math, random
from collections import defaultdict, deque, Counter
from pathlib import Path

random.seed(42)

# ── Tunable constants ─────────────────────────────────────────────────────────
TARGET_ARC     = 4000   # arc-length per node  (graph units)
MAX_RING_NODES = 200    # max nodes per ring; excess → extra sub-rings
LAYER_FACTOR   = 6      # layer gap = TARGET_ARC × LAYER_FACTOR
JITTER         = 0.06   # ± fraction of sector for slight placement variation
NODE_DIAM_PX   = 10     # assumed node diameter in pixels (for camera ratio calc)
VIEWPORT_PX    = 1000   # assumed viewport width in pixels (for camera ratio calc)

SUBRING_STEP = TARGET_ARC              # radial gap between sub-rings within a layer
MIN_STEP     = TARGET_ARC * LAYER_FACTOR  # radial gap between depth layers


def compute_layout(nodes: list, edges: list, graph_attrs: dict) -> None:

    # ── Adjacency ─────────────────────────────────────────────────────────────
    contains_out: dict[str, list] = defaultdict(list)
    depends_out:  dict[str, list] = defaultdict(list)
    for e in edges:
        t, s, tgt = e["attributes"].get("type", ""), e["source"], e["target"]
        if t in ("contains", "uses_image"):
            contains_out[s].append(tgt)
        elif t == "depends_on":
            depends_out[s].append(tgt)

    repo_nodes = [n for n in nodes if n["attributes"]["type"] == "repository"]
    pkg_nodes  = [n for n in nodes if n["attributes"]["type"] == "package"]

    # ── BFS spanning tree ─────────────────────────────────────────────────────
    bfs_depth:    dict[str, int]  = {}
    bfs_children: dict[str, list] = defaultdict(list)
    bfs_roots:    list            = []

    for rn in repo_nodes:
        rk = rn["key"]
        if rk not in bfs_depth:
            bfs_depth[rk] = 0
            bfs_roots.append(rk)

    q: deque = deque(bfs_roots)
    while q:
        key = q.popleft()
        d   = bfs_depth[key]
        nxt = contains_out[key] if d == 0 else depends_out[key]
        for child in nxt:
            if child not in bfs_depth:
                bfs_depth[child] = d + 1
                bfs_children[key].append(child)
                q.append(child)

    max_d        = max(bfs_depth.values(), default=0)
    orphan_depth = max_d + 2
    for n in pkg_nodes:
        if n["key"] not in bfs_depth:
            bfs_depth[n["key"]] = orphan_depth

    # ── Subtree sizes ─────────────────────────────────────────────────────────
    subtree: dict[str, int] = {}

    def calc_subtree(key: str) -> int:
        s = 1 + sum(calc_subtree(c) for c in bfs_children[key])
        subtree[key] = s
        return s

    sys.setrecursionlimit(50000)
    for rk in bfs_roots:
        calc_subtree(rk)

    total_tree = sum(subtree.get(rk, 1) for rk in bfs_roots)

    # ── Angular sectors ────────────────────────────────────────────────────────
    sec_start: dict[str, float] = {}
    sec_end:   dict[str, float] = {}

    cursor = -math.pi
    for rk in bfs_roots:
        frac          = subtree.get(rk, 1) / max(total_tree, 1)
        sec_start[rk] = cursor
        sec_end[rk]   = cursor + frac * 2 * math.pi
        cursor        = sec_end[rk]

    def assign_sectors(key: str) -> None:
        children = bfs_children[key]
        if not children:
            return
        parent_arc  = sec_end[key] - sec_start[key]
        child_total = sum(subtree.get(c, 1) for c in children)
        c_cursor    = sec_start[key]
        for child in children:
            frac             = subtree.get(child, 1) / max(child_total, 1)
            sec_start[child] = c_cursor
            sec_end[child]   = c_cursor + parent_arc * frac
            c_cursor         = sec_end[child]
            assign_sectors(child)

    for rk in bfs_roots:
        assign_sectors(rk)

    orphans = [n["key"] for n in pkg_nodes if bfs_depth[n["key"]] == orphan_depth]
    for i, ok in enumerate(orphans):
        a             = -math.pi + 2 * math.pi * (i + 0.5) / max(len(orphans), 1)
        half          = math.pi / max(len(orphans), 1)
        sec_start[ok] = a - half
        sec_end[ok]   = a + half

    # ── Sub-ring assignments (dynamic: count = ceil(layer_nodes / MAX_RING_NODES)) ──
    layer_counts: Counter = Counter(bfs_depth.values())
    all_depths            = sorted(layer_counts.keys())

    subring:        dict[str, int] = {}
    layer_subrings: dict[int, int] = {}

    for d in all_depths:
        keys_at_d = [k for k, v in bfs_depth.items() if v == d]
        n_sr      = max(1, math.ceil(len(keys_at_d) / MAX_RING_NODES))
        layer_subrings[d] = n_sr
        # Heavier subtrees on inner sub-rings (lower index = smaller radius)
        ks = sorted(keys_at_d, key=lambda k: subtree.get(k, 1), reverse=True)
        for i, k in enumerate(ks):
            subring[k] = i // MAX_RING_NODES

    # ── Radius per depth layer ─────────────────────────────────────────────────
    #
    # base_r[d]: radius of sub-ring 0 for depth d.
    #   r_needed = min(nodes, MAX_RING_NODES) × TARGET_ARC / (2π)
    #     → circumference exactly fits MAX_RING_NODES nodes at TARGET_ARC each.
    #   base_r[d] = max(prev_outer + MIN_STEP, r_needed)
    #   prev_outer = base_r[d-1] + (n_sr - 1) × SUBRING_STEP
    #
    # Sub-rings use SUBRING_STEP = TARGET_ARC so radial and arc spacing match.

    layer_r_base: dict[int, float] = {}
    prev_outer = 0.0

    for d in all_depths:
        n_ring   = min(layer_counts[d], MAX_RING_NODES)
        r_needed = n_ring * TARGET_ARC / (2 * math.pi)
        base_r   = max(prev_outer + MIN_STEP, r_needed)
        layer_r_base[d] = base_r
        n_sr       = layer_subrings.get(d, 1)
        prev_outer = base_r + (n_sr - 1) * SUBRING_STEP

    # ── Place every node ──────────────────────────────────────────────────────
    pos: dict[str, tuple] = {}

    for n in repo_nodes + pkg_nodes:
        key = n["key"]
        d   = bfs_depth.get(key, orphan_depth)
        sr  = subring.get(key, 0)
        rad = layer_r_base.get(d, prev_outer) + sr * SUBRING_STEP
        s   = sec_start.get(key, 0.0)
        e   = sec_end.get(key, 0.0)
        arc = e - s
        mid = (s + e) / 2 + random.uniform(-arc * JITTER, arc * JITTER)
        pos[key] = (rad * math.cos(mid), rad * math.sin(mid))

    for n in nodes:
        key = n["key"]
        if key in pos:
            n["attributes"]["x"] = pos[key][0]
            n["attributes"]["y"] = pos[key][1]

    # ── Dynamic camera ratio ───────────────────────────────────────────────────
    # Non-overlap condition at camera ratio r_cam:
    #   pixel_arc = TARGET_ARC × VIEWPORT_PX / (r_cam × 2 × MAX_R) ≥ NODE_DIAM_PX
    # → r_cam = TARGET_ARC × VIEWPORT_PX / (NODE_DIAM_PX × 2 × MAX_R)
    # Clamp to [0.05, 0.6] for usability.
    max_r     = prev_outer  # last layer's outer edge
    r_cam_raw = TARGET_ARC * VIEWPORT_PX / (NODE_DIAM_PX * 2.0 * max_r)
    r_cam     = round(min(0.6, max(0.05, r_cam_raw)), 4)
    graph_attrs["_initial_camera_ratio"] = r_cam

    # ── Summary ───────────────────────────────────────────────────────────────
    def layer_outer(d: int) -> float:
        return layer_r_base.get(d, 0) + (layer_subrings.get(d, 1) - 1) * SUBRING_STEP

    print(f"  Repos={len(repo_nodes)}  Pkg_layers={max_d}  Orphans={len(orphans)}")
    print(f"  TARGET_ARC={TARGET_ARC:,}  SUBRING_STEP={SUBRING_STEP:,}  "
          f"MIN_STEP={MIN_STEP:,}  MAX_RING_NODES={MAX_RING_NODES}")
    print(f"  REPO_R={layer_r_base.get(0,0):,.0f}  "
          f"L1_base={layer_r_base.get(1,0):,.0f}  "
          f"L1_subrings={layer_subrings.get(1,1)}  "
          f"L1_outer={layer_outer(1):,.0f}")
    print(f"  MAX_R={max_r:,.0f}  → initial_camera_ratio={r_cam}")
    for d in all_depths:
        n   = layer_counts[d]
        ns  = layer_subrings.get(d, 1)
        print(f"    d={d:2d}  nodes={n:5d}  rings={ns:2d}  "
              f"r=[{layer_r_base.get(d,0):,.0f}…{layer_outer(d):,.0f}]")


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/graph/dep_graph.json")
    print(f"Loading {path}...")
    g          = json.loads(path.read_text())
    nodes      = g["nodes"]
    edges      = g["edges"]
    attrs      = g.setdefault("attributes", {})
    print(f"  Nodes={len(nodes):,}  Edges={len(edges):,}")
    compute_layout(nodes, edges, attrs)
    print(f"Writing {path}...")
    path.write_text(json.dumps(g, separators=(",", ":")))
    print("Done.")


if __name__ == "__main__":
    main()
