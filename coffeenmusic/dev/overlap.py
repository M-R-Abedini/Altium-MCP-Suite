"""Text/body collision audit for a sheet_dump.txt - the hard gate that keeps
text overlap from reaching human review.

Flags:
  TEXTxTEXT   two text boxes intersect
  TEXTxWIRE   a wire passes through a text box interior
  TEXTxBODY   a text box intersects a component's drawn body
  BODYxBODY   two drawn bodies intersect

House-style tolerance: the reference sheets set a resistor's value baseline ON
its own wire, so boxes are shrunk by EDGE_TOL before wire tests, and a text
box is never tested against wires that END on its owner's pins (the label's
own net wire). Simplest robust rule: shrink and require strict interior.

Usage: python dev/overlap.py sheet_dump.txt
Exit code 0 = clean, 1 = collisions found.
"""
import sys
from collections import defaultdict

EDGE_TOL = 20      # mils shrink per edge before wire/body tests
IGNORE_SELF = True # a PARAM/DESIG box may touch its own component's body edge


def load(path):
    recs = defaultdict(list)
    for line in open(path, encoding="cp1252", errors="replace"):
        f = line.rstrip("\n").split("|")
        if f and f[0]:
            recs[f[0]].append(f[1:])
    return recs


def boxes(recs):
    out = []
    for kind, owner, text, x1, y1, x2, y2 in (r for r in recs.get("TEXT", [])):
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
        if x2 < x1: x1, x2 = x2, x1
        if y2 < y1: y1, y2 = y2, y1
        out.append({"kind": kind, "owner": owner, "text": text,
                    "box": (x1, y1, x2, y2)})
    return out


def bodies(recs):
    return {d: (int(a), int(b), int(c), int(e))
            for d, a, b, c, e in recs.get("BODY", [])}


def rect_overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def seg_hits_box(seg, box):
    x1, y1, x2, y2 = seg
    bx1, by1, bx2, by2 = box
    bx1 += EDGE_TOL; by1 += EDGE_TOL; bx2 -= EDGE_TOL; by2 -= EDGE_TOL
    if bx1 >= bx2 or by1 >= by2:
        return False
    if y1 == y2:                       # horizontal
        if not (by1 < y1 < by2):
            return False
        lo, hi = sorted((x1, x2))
        return lo < bx2 and hi > bx1
    if x1 == x2:                       # vertical
        if not (bx1 < x1 < bx2):
            return False
        lo, hi = sorted((y1, y2))
        return lo < by2 and hi > by1
    return False


def audit(recs):
    txt = boxes(recs)
    bod = bodies(recs)
    segs = [tuple(map(int, w)) for w in recs.get("WIRE", [])]
    pins = defaultdict(set)
    for p in recs.get("PIN", []):
        pins[p[0]].add((int(p[2]), int(p[3])))

    problems = []

    for i in range(len(txt)):
        for j in range(i + 1, len(txt)):
            if rect_overlap(txt[i]["box"], txt[j]["box"]):
                problems.append(("TEXTxTEXT",
                    f'{txt[i]["kind"]}:{txt[i]["text"]!r}({txt[i]["owner"]}) x '
                    f'{txt[j]["kind"]}:{txt[j]["text"]!r}({txt[j]["owner"]}) '
                    f'at {txt[i]["box"]}'))

    for t in txt:
        own = pins.get(t["owner"], set())
        for seg in segs:
            # a wire that ends on the owner's own pin is that part's own net
            # run; house style deliberately puts value text along it
            if (seg[:2] in own) or (seg[2:] in own):
                continue
            if seg_hits_box(seg, t["box"]):
                problems.append(("TEXTxWIRE",
                    f'{t["kind"]}:{t["text"]!r}({t["owner"]}) box {t["box"]} '
                    f'crossed by wire {seg}'))
                break

    for t in txt:
        for d, bb in bod.items():
            if IGNORE_SELF and t["owner"] == d:
                continue
            sh = (t["box"][0] + EDGE_TOL, t["box"][1] + EDGE_TOL,
                  t["box"][2] - EDGE_TOL, t["box"][3] - EDGE_TOL)
            if sh[0] < sh[2] and sh[1] < sh[3] and rect_overlap(sh, bb):
                problems.append(("TEXTxBODY",
                    f'{t["kind"]}:{t["text"]!r}({t["owner"]}) box {t["box"]} '
                    f'on body of {d} {bb}'))

    dl = sorted(bod.items())
    for i in range(len(dl)):
        for j in range(i + 1, len(dl)):
            if rect_overlap(dl[i][1], dl[j][1]):
                problems.append(("BODYxBODY",
                    f'{dl[i][0]} {dl[i][1]} x {dl[j][0]} {dl[j][1]}'))
    return problems


if __name__ == "__main__":
    probs = audit(load(sys.argv[1]))
    for kind, msg in probs:
        print(f"{kind}: {msg}")
    print(f"{'CLEAN' if not probs else f'{len(probs)} collisions'}")
    sys.exit(1 if probs else 0)
