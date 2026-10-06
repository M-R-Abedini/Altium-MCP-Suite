"""Build a pin-to-net map from a sheet_dump.txt, and compare two of them.

This is the ground truth for "the rebuilt circuit matches the example": the
partition of pins into nets must be identical, and named nets (labels, power
ports, sheet ports) must carry the same names. Coordinates never matter.

Connectivity rules (mirroring Altium's compiler):
  - a wire segment connects its two endpoints
  - two segments sharing an endpoint connect
  - a point landing on a segment INTERIOR connects only if something sits
    there: a junction, a pin hot end, or another wire's endpoint WITH junction
    (plain crossings do not connect)
  - pins connect at their hot end (endpoint or interior touch)
  - net labels / power ports / sheet ports name the cluster they touch

Usage:
    python dev/netlist.py extract dump.txt out.json
    python dev/netlist.py compare a.json b.json
"""
import json
import sys
from collections import defaultdict


def load(path):
    recs = defaultdict(list)
    for line in open(path, encoding="cp1252", errors="replace"):
        f = line.rstrip("\n").split("|")
        if f and f[0]:
            recs[f[0]].append(f[1:])
    return recs


class DSU:
    def __init__(self):
        self.p = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def on_interior(pt, seg):
    (x, y), (x1, y1, x2, y2) = pt, seg
    if x1 == x2 == x and min(y1, y2) < y < max(y1, y2):
        return True
    if y1 == y2 == y and min(x1, x2) < x < max(x1, x2):
        return True
    return False


def build(recs):
    segs = [tuple(map(int, w)) for w in recs.get("WIRE", [])]
    juncs = {(int(j[0]), int(j[1])) for j in recs.get("JUNC", [])}
    pins = {}       # (desig, pin) -> (x, y)
    for p in recs.get("PIN", []):
        pins[(p[0], p[1])] = (int(p[2]), int(p[3]))

    dsu = DSU()
    for s in segs:
        dsu.union(("pt",) + s[:2], ("pt",) + s[2:])

    # attachment points that may land on an interior: junctions, pins, and
    # every segment endpoint (a T where one wire ends on another's middle is a
    # connection in Altium even without a junction dot being drawn)
    attach = set(juncs) | set(pins.values())
    for s in segs:
        attach.add(s[:2])
        attach.add(s[2:])
    for pt in attach:
        for s in segs:
            if on_interior(pt, s):
                # endpoints of OTHER wires only connect through a junction;
                # pins and junctions connect by touching
                if pt in juncs or pt in pins.values():
                    dsu.union(("pt",) + pt, ("pt",) + s[:2])
                elif pt in juncs:
                    dsu.union(("pt",) + pt, ("pt",) + s[:2])

    for key, pt in pins.items():
        dsu.union(("pin",) + key, ("pt",) + pt)

    # name clusters
    names = defaultdict(list)   # root -> [(priority, name)]
    for x, y, text, _style in ((int(a[0]), int(a[1]), a[2], a[3]) for a in recs.get("PWR", [])):
        names[dsu.find(("pt", x, y))].append((0, text))
    # A port connects at whichever of its ends touches a wire: Location, or
    # Location +/- Width along x (horizontal ports). Try each candidate as an
    # exact wire-endpoint first, then as a segment-interior touch.
    for rec in recs.get("PORT", []):
        x, y, text, w = int(rec[0]), int(rec[1]), rec[2], int(rec[5])
        attached = False
        for cand in ((x, y), (x + w, y), (x - w, y)):
            if ("pt",) + cand in dsu.p:
                names[dsu.find(("pt",) + cand)].append((1, text))
                attached = True
                break
        if not attached:
            for cand in ((x, y), (x + w, y), (x - w, y)):
                for s_ in segs:
                    if on_interior(cand, s_):
                        names[dsu.find(("pt",) + s_[:2])].append((1, text))
                        attached = True
                        break
                if attached:
                    break
    for x, y, text in ((int(a[0]), int(a[1]), a[2]) for a in recs.get("NLBL", [])):
        pt = (x, y)
        root = None
        if ("pt",) + pt in dsu.p:
            root = dsu.find(("pt",) + pt)
        else:
            for s in segs:
                if on_interior(pt, s):
                    root = dsu.find(("pt",) + s[:2])
                    break
        if root:
            names[root].append((2, text))

    nets = defaultdict(set)
    for key in pins:
        nets[dsu.find(("pin",) + key)].add(f"{key[0]}.{key[1]}")

    out = {}
    anon = 0
    for root, members in nets.items():
        cands = sorted(names.get(root, []))
        if cands:
            name = cands[0][1]
        else:
            anon += 1
            name = None
        out.setdefault(name, []).append(sorted(members))

    # flatten: named nets keyed by name (merging same-named clusters, since a
    # shared label means one net even without a drawn wire between clusters)
    named = {}
    anon_nets = []
    for name, groups in out.items():
        if name is None:
            anon_nets.extend(groups)
        else:
            merged = sorted({m for g in groups for m in g})
            named[name] = merged
    return {"named": named, "anonymous": sorted(anon_nets),
            "parts": {p[0]: {"libref": p[1], "id": p[2], "comment": p[3]}
                      for p in recs.get("COMP", [])}}


def compare(a, b):
    ok = True
    pa, pb = a["parts"], b["parts"]
    if set(pa) != set(pb):
        ok = False
        print("PART MISMATCH:")
        print("  only in A:", sorted(set(pa) - set(pb)))
        print("  only in B:", sorted(set(pb) - set(pa)))
    for d in sorted(set(pa) & set(pb)):
        if pa[d]["id"] != pb[d]["id"]:
            ok = False
            print(f"PART {d}: id {pa[d]['id']!r} != {pb[d]['id']!r}")

    na, nb = a["named"], b["named"]
    for name in sorted(set(na) | set(nb)):
        sa, sb = set(na.get(name, [])), set(nb.get(name, []))
        if sa != sb:
            ok = False
            print(f"NET {name}:")
            if sa - sb:
                print("  only in A:", sorted(sa - sb))
            if sb - sa:
                print("  only in B:", sorted(sb - sa))

    aa = {frozenset(g) for g in a["anonymous"]}
    ab = {frozenset(g) for g in b["anonymous"]}
    if aa != ab:
        ok = False
        for g in sorted(aa - ab, key=sorted):
            print("ANON net only in A:", sorted(g))
        for g in sorted(ab - aa, key=sorted):
            print("ANON net only in B:", sorted(g))

    print("MATCH" if ok else "DIFFER")
    return ok


if __name__ == "__main__":
    if sys.argv[1] == "extract":
        data = build(load(sys.argv[2]))
        json.dump(data, open(sys.argv[3], "w"), indent=1, sort_keys=True)
        print(f"nets: {len(data['named'])} named, {len(data['anonymous'])} anonymous; "
              f"parts: {len(data['parts'])}")
    elif sys.argv[1] == "compare":
        ok = compare(json.load(open(sys.argv[2])), json.load(open(sys.argv[3])))
        sys.exit(0 if ok else 1)
