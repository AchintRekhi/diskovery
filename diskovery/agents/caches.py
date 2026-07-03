"""CacheLogAgent — user caches, logs, and developer caches (Xcode/Simulators).

`~/Library/Caches` is where most day-to-day reclaimable junk hides. We size its
top-level entries individually so the report shows *which* app is hoarding.

Cache dirs that a dedicated agent owns (pip -> PythonAgent, Yarn -> NodeAgent,
Homebrew -> HomebrewAgent, Adobe -> CreativeCacheAgent) are excluded here so no
path is ever reported by two agents.
"""

from __future__ import annotations

import os
from typing import List, Set, Tuple

from ..core import Agent, Finding, Safety, ScanContext, human_size


class CacheLogAgent(Agent):
    name = "caches"
    title = "Caches & Logs"
    icon = "🧹"
    slow = False

    def _owned_elsewhere(self, ctx: ScanContext) -> Set[str]:
        """Basenames under ~/Library/Caches that another agent reports.

        Only cede a dir when the owning agent will actually run: Python/Node
        always do; Homebrew only when brew is installed; Adobe only when the
        CreativeCacheAgent is applicable on this machine.
        """
        owned = {"pip", "Yarn"}
        if ctx.which("brew"):
            owned.add("Homebrew")
        from .creative_cache import CreativeCacheAgent
        if CreativeCacheAgent().applicable(ctx):
            owned.add("Adobe")
        return owned

    def collect(self, ctx: ScanContext) -> Tuple[List[Finding], str, List[str]]:
        findings: List[Finding] = []
        notes: List[str] = []
        total = 0

        # --- ~/Library/Caches per-app breakdown ------------------------------
        caches = ctx.path("Library", "Caches")
        owned = self._owned_elsewhere(ctx)
        entries = [(p, s) for p, s in self._children_sizes(ctx, caches, timeout=25)
                   if os.path.basename(p) not in owned]
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
            # No path on purpose: a parent path here would overlap the per-app
            # rows above; the rollup is distinct extra space.
            findings.append(Finding(
                category="App caches",
                label=f"… {len(rest)} more cache dirs",
                size_bytes=rsz,
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
        # DiagnosticReports lives *inside* Library/Logs; report it on its own
        # row and subtract it from the logs row so no byte is counted twice.
        logs = ctx.path("Library", "Logs")
        diag = ctx.path("Library", "Logs", "DiagnosticReports")
        logs_sz = ctx.du_bytes(logs, timeout=30) if os.path.isdir(logs) else 0
        diag_sz = ctx.du_bytes(diag, timeout=30) if os.path.isdir(diag) else 0
        if diag_sz > 0:
            findings.append(Finding(
                category="Logs",
                label="Diagnostic/crash reports",
                size_bytes=diag_sz,
                path=diag,
                safety=Safety.SAFE,
                detail="Log/diagnostic output.",
                suggestion=f"rm -rf {self._q(diag)}/*",
            ))
        if logs_sz - diag_sz > 0:
            # Pathless: this is the *remainder* of ~/Library/Logs after the
            # diagnostics row above — a path here would nest under it.
            findings.append(Finding(
                category="Logs",
                label="User logs (excl. diagnostics)",
                size_bytes=logs_sz - diag_sz,
                safety=Safety.SAFE,
                detail=f"Log output in {logs}.",
                suggestion=f"rm -rf {self._q(logs)}/*",
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
