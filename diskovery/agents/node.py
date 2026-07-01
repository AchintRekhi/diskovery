"""NodeAgent — node_modules sprawl, global packages, and package-manager caches.

`node_modules` is the classic disk hog on a dev Mac: dozens of copies, each
rebuildable with a single install. We find every top-level `node_modules`
(never descending into nested ones) and size them.
"""

from __future__ import annotations

import os
from typing import List, Tuple

from ..core import Agent, Finding, Safety, ScanContext, human_size, human_age

_SKIP = {"Library", ".Trash", ".git", "Applications", "Pictures", "Movies", "Music"}


class NodeAgent(Agent):
    name = "node"
    title = "Node / npm"
    icon = "📦"
    slow = False

    def collect(self, ctx: ScanContext) -> Tuple[List[Finding], str, List[str]]:
        findings: List[Finding] = []
        notes: List[str] = []

        mods = self._find_node_modules(ctx)
        total = 0
        for path, sz, age in mods:
            total += sz
        # Show the biggest individually; roll the rest into one aggregate row.
        mods.sort(key=lambda x: x[1], reverse=True)
        shown = mods[:15]
        for path, sz, age in shown:
            proj = os.path.dirname(path)
            findings.append(Finding(
                category="node_modules",
                label=os.path.relpath(proj, ctx.home),
                size_bytes=sz,
                path=path,
                safety=Safety.SAFE,
                detail=f"Rebuildable with install. Project last touched {human_age(age)} ago."
                       if age else "Rebuildable dependency tree.",
                suggestion=f"rm -rf {self._q(path)}   # then `npm install` when needed",
            ))
        rest = mods[15:]
        if rest:
            rsz = sum(s for _, s, _ in rest)
            findings.append(Finding(
                category="node_modules",
                label=f"… {len(rest)} more node_modules",
                size_bytes=rsz,
                safety=Safety.SAFE,
                detail="Smaller/older dependency trees rolled up.",
            ))
        if mods:
            notes.append(f"{len(mods)} node_modules totalling {human_size(total)}.")

        findings += self._caches(ctx)
        findings += self._globals(ctx)

        summary = f"{len(mods)} node_modules, {human_size(total)}"
        return findings, summary, notes

    # ------------------------------------------------------------------ #
    def _find_node_modules(self, ctx: ScanContext):
        home = ctx.home
        base_depth = home.rstrip(os.sep).count(os.sep)
        found = []
        for cur, dirs, _files in os.walk(home, onerror=lambda e: None):
            depth = cur.count(os.sep) - base_depth
            if depth > 12:
                dirs[:] = []
                continue
            dirs[:] = [
                d for d in dirs
                if d not in _SKIP and not d.endswith(".app")
                and not d.endswith(".photoslibrary")
            ]
            if "node_modules" in dirs:
                p = os.path.join(cur, "node_modules")
                sz = ctx.du_bytes(p, timeout=45)
                try:
                    age = ctx.now - os.path.getmtime(cur)
                except OSError:
                    age = 0
                found.append((p, sz, age))
                dirs.remove("node_modules")  # don't recurse into it
        return found

    def _caches(self, ctx: ScanContext) -> List[Finding]:
        out: List[Finding] = []
        candidates = [
            (ctx.path(".npm", "_cacache"), "npm cache", "npm cache clean --force"),
            (ctx.path("Library", "Caches", "Yarn"), "Yarn cache", "yarn cache clean"),
            (ctx.path(".cache", "yarn"), "Yarn cache (legacy)", "yarn cache clean"),
            (ctx.path("Library", "pnpm", "store"), "pnpm store", "pnpm store prune"),
            (ctx.path(".pnpm-store"), "pnpm store (legacy)", "pnpm store prune"),
        ]
        for path, label, cmd in candidates:
            if os.path.isdir(path):
                sz = ctx.du_bytes(path, timeout=45)
                if sz > 0:
                    out.append(Finding(
                        category="Reclaimable",
                        label=label,
                        size_bytes=sz,
                        path=path,
                        safety=Safety.SAFE,
                        detail="Package manager download cache.",
                        suggestion=cmd,
                    ))
        return out

    def _globals(self, ctx: ScanContext) -> List[Finding]:
        out: List[Finding] = []
        rc, root, _ = ctx.run(["npm", "root", "-g"], timeout=20)
        root = root.strip()
        if rc == 0 and root and os.path.isdir(root):
            sz = ctx.du_bytes(root, timeout=45)
            if sz > 0:
                out.append(Finding(
                    category="Global packages",
                    label="npm global root",
                    size_bytes=sz,
                    path=root,
                    safety=Safety.REVIEW,
                    detail="Globally installed CLIs/packages. Prune with `npm ls -g`.",
                    suggestion="npm ls -g --depth=0",
                ))
        return out

    @staticmethod
    def _q(path: str) -> str:
        return "'" + path.replace("'", "'\\''") + "'"
