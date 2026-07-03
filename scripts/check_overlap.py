"""Overlap checker: finds findings counted twice across (or within) agents.

Reads a Diskovery report.json and reports, for the safe and review classes
separately (those are the classes summed into headline totals):
  1. exact same path reported by more than one finding
  2. a finding whose path sits inside another counted finding's path
     (parent/child containment -> the child's bytes are counted twice)

KEEP findings are informational and never summed, so they're ignored.
"""

import json
import os
import sys
from collections import defaultdict


def main(path):
    with open(path) as fh:
        rep = json.load(fh)

    rows = []  # (safety, path, size, agent, label)
    for a in rep["agents"]:
        for f in a["findings"]:
            if f["safety"] not in ("safe", "review"):
                continue
            if f["size_bytes"] <= 0:
                continue
            rows.append((f["safety"], f["path"], f["size_bytes"],
                         a["name"], f["label"]))

    problems = 0
    for cls in ("safe", "review"):
        cls_rows = [r for r in rows if r[0] == cls and r[1]]
        # 1) exact duplicates
        by_path = defaultdict(list)
        for r in cls_rows:
            by_path[r[1]].append(r)
        print(f"\n=== class: {cls} ({len(cls_rows)} counted findings with paths) ===")
        for p, rs in sorted(by_path.items()):
            if len(rs) > 1:
                agents = {r[3] for r in rs}
                note = "(deduped in totals: exact path match)" if len(agents) > 1 or len(rs) > 1 else ""
                print(f"  [EXACT] {p}")
                for r in rs:
                    print(f"          {r[3]:12s} {r[4]!r} {r[2]:,} B")
                problems += 1
        # 2) containment (child inside parent) — NOT caught by exact dedup
        paths = sorted(by_path.keys())
        for i, parent in enumerate(paths):
            pre = parent.rstrip(os.sep) + os.sep
            for child in paths[i + 1:]:
                if not child.startswith(pre):
                    break
                pr = by_path[parent][0]
                cr = by_path[child][0]
                print(f"  [NESTED] {child}")
                print(f"           inside {parent}")
                print(f"           parent: {pr[3]:12s} {pr[4]!r} {pr[2]:,} B")
                print(f"           child : {cr[3]:12s} {cr[4]!r} {cr[2]:,} B  <-- double counted")
                problems += 1

    # pathless aggregates, listed for eyeballing (counted blind by design)
    blind = [(s, ag, lb, sz) for (s, p, sz, ag, lb) in rows if not p]
    if blind:
        print(f"\n=== pathless aggregate rows (always counted — verify by eye) ===")
        for s, ag, lb, sz in blind:
            print(f"  [{s:6s}] {ag:12s} {lb!r} {sz:,} B")

    print(f"\n{'!! ' + str(problems) + ' overlap problem(s) found' if problems else 'No overlaps found.'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
