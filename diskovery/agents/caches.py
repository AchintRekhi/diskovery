"""CacheLogAgent — user caches, logs, and developer caches (Xcode/Simulators).

`~/Library/Caches` is where most day-to-day reclaimable junk hides. We size its
top-level entries individually so the report shows *which* app is hoarding.
"""

from __future__ import annotations

import os
from typing import List, Tuple

from ..core import Agent, Finding, Safety, ScanContext, human_size


class CacheLogAgent(Agent):
    name = "caches"
    title = "Caches & Logs"
    icon = "🧹"
    slow = False

    def collect(self, ctx: ScanContext) -> Tuple[List[Finding], str, List[str]]:
        findings: List[Finding] = []
        notes: List[str] = []
        total = 0

        # --- ~/Library/Caches per-app breakdown ------------------------------
        caches = ctx.path("Library", "Caches")
        entries = self._children_sizes(ctx, caches, timeout=25)
        for path, sz in entries[:12]:
            total += sz
            findings.append(Finding(
                category="App caches",
                label=os.path.basename(path),
                size_bytes=sz,
                path=path,
                safety=Safety.SAFE,
                detail="Application cache — rebuilt on demand.",
            ))
        rest = entries[12:]
        if rest:
            rsz = sum(s for _, s in rest)
            total += rsz
            findings.append(Finding(
                category="App caches",
                label=f"… {len(rest)} more cache dirs",
                size_bytes=rsz,
                path=caches,
                safety=Safety.SAFE,
                detail="Smaller caches rolled up.",
            ))
        if entries:
            findings.append(Finding(
                category="App caches",
                label="Clear user caches",
                size_bytes=0,
                safety=Safety.SAFE,
                detail="Suggestion only — apps regenerate what they need.",
                suggestion="rm -rf ~/Library/Caches/*",
            ))

        # --- logs & diagnostics ---------------------------------------------
        for rel, label in [
            (["Library", "Logs"], "User logs"),
            (["Library", "Logs", "DiagnosticReports"], "Diagnostic/crash reports"),
        ]:
            p = ctx.path(*rel)
            if os.path.isdir(p):
                sz = ctx.du_bytes(p, timeout=30)
                if sz > 0:
                    findings.append(Finding(
                        category="Logs",
                        label=label,
                        size_bytes=sz,
                        path=p,
                        safety=Safety.SAFE,
                        detail="Log/diagnostic output.",
                        suggestion=f"rm -rf {self._q(p)}/*",
                    ))

        # --- developer caches (Xcode / Simulators) --------------------------
        dev = [
            (["Library", "Developer", "Xcode", "DerivedData"],
             "Xcode DerivedData", Safety.SAFE,
             "Xcode build intermediates — safe to delete, rebuilt on next build.",
             "rm -rf ~/Library/Developer/Xcode/DerivedData/*"),
            (["Library", "Developer", "Xcode", "iOS DeviceSupport"],
             "Xcode iOS DeviceSupport", Safety.REVIEW,
             "Debug symbols per iOS version — regenerated when you attach a device.",
             None),
            (["Library", "Developer", "CoreSimulator", "Caches"],
             "CoreSimulator caches", Safety.SAFE,
             "Simulator caches.", None),
            (["Library", "Developer", "CoreSimulator", "Devices"],
             "iOS Simulators", Safety.REVIEW,
             "Installed simulator runtimes/devices — large; delete unused ones.",
             "xcrun simctl delete unavailable"),
        ]
        for rel, label, safety, detail, cmd in dev:
            p = ctx.path(*rel)
            if os.path.isdir(p):
                sz = ctx.du_bytes(p, timeout=60)
                if sz > 0:
                    findings.append(Finding(
                        category="Developer caches",
                        label=label,
                        size_bytes=sz,
                        path=p,
                        safety=safety,
                        detail=detail,
                        suggestion=cmd,
                    ))

        summary = f"~{human_size(total)} in user caches"
        return findings, summary, notes

    # ------------------------------------------------------------------ #
    def _children_sizes(self, ctx: ScanContext, parent: str, timeout: int):
        out = []
        if not os.path.isdir(parent):
            return out
        try:
            for name in os.listdir(parent):
                full = os.path.join(parent, name)
                if os.path.islink(full):
                    continue
                if os.path.isdir(full):
                    out.append((full, ctx.du_bytes(full, timeout=timeout)))
                else:
                    out.append((full, ctx._safe_size(full)))
        except OSError:
            pass
        out.sort(key=lambda x: x[1], reverse=True)
        return out

    @staticmethod
    def _q(path: str) -> str:
        return "'" + path.replace("'", "'\\''") + "'"
