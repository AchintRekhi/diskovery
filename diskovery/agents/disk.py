"""DiskAgent — the baseline: where is the space actually going?

Volumes, APFS local snapshots, the Trash, and a top-level breakdown of the
home directory so the rest of the report has context.
"""

from __future__ import annotations

import os
from typing import List, Tuple

from ..core import Agent, Finding, Safety, ScanContext, human_size


class DiskAgent(Agent):
    name = "disk"
    title = "Disk Overview"
    icon = "💽"
    slow = False

    def collect(self, ctx: ScanContext) -> Tuple[List[Finding], str, List[str]]:
        findings: List[Finding] = []
        notes: List[str] = []

        # --- boot volume capacity via df -------------------------------------
        used = avail = size = 0
        rc, out, _ = ctx.run(["df", "-k", "/"], timeout=15)
        if rc == 0:
            lines = out.strip().splitlines()
            if len(lines) >= 2:
                parts = lines[1].split()
                # Filesystem 512/1K-blocks Used Avail ...  (df -k => 1K blocks)
                try:
                    size = int(parts[1]) * 1024
                    used = int(parts[2]) * 1024
                    avail = int(parts[3]) * 1024
                except (ValueError, IndexError):
                    pass
        if size:
            findings.append(Finding(
                category="Volume",
                label="Macintosh HD — used",
                size_bytes=used,
                path="/",
                safety=Safety.KEEP,
                detail=f"{human_size(avail)} free of {human_size(size)}",
            ))

        # --- APFS local (Time Machine) snapshots -----------------------------
        rc, out, _ = ctx.run(["tmutil", "listlocalsnapshots", "/"], timeout=20)
        snaps = [l for l in out.splitlines() if "com.apple.TimeMachine" in l]
        if snaps:
            findings.append(Finding(
                category="Snapshots",
                label=f"{len(snaps)} APFS local snapshot(s)",
                size_bytes=0,
                safety=Safety.REVIEW,
                detail="Local Time Machine snapshots hold deleted files and can "
                       "silently occupy 'purgeable' space macOS reclaims on demand.",
                suggestion="tmutil thinlocalsnapshots / 999999999999 4",
            ))
            notes.append(f"{len(snaps)} local snapshot(s) present (purgeable space).")

        # --- Trash -----------------------------------------------------------
        trash = ctx.path(".Trash")
        if os.path.isdir(trash):
            tsize = ctx.du_bytes(trash, timeout=30)
            if tsize > 0:
                findings.append(Finding(
                    category="Trash",
                    label="Trash",
                    size_bytes=tsize,
                    path=trash,
                    safety=Safety.SAFE,
                    detail="Files already sent to Trash.",
                    suggestion="rm -rf ~/.Trash/*",
                ))

        # --- top-level home breakdown ---------------------------------------
        home = ctx.home
        skip = {".Trash"}
        children: List[Tuple[str, int]] = []
        try:
            for name in os.listdir(home):
                if name in skip or name.startswith(".") and name not in (".cache",):
                    # keep dotfolders out of the *breakdown* except notable caches;
                    # they're covered by other agents. Still measure .cache below.
                    if name != ".cache":
                        continue
                full = os.path.join(home, name)
                if os.path.isdir(full) and not os.path.islink(full):
                    children.append((full, ctx.du_bytes(full, timeout=90)))
        except OSError:
            pass
        children.sort(key=lambda x: x[1], reverse=True)
        for full, sz in children[:12]:
            if sz <= 0:
                continue
            findings.append(Finding(
                category="Home breakdown",
                label=f"~/{os.path.basename(full)}",
                size_bytes=sz,
                path=full,
                safety=Safety.KEEP,
                detail="Top-level home directory (informational).",
            ))

        summary = (
            f"{human_size(used)} used / {human_size(avail)} free"
            if size else "disk overview"
        )
        return findings, summary, notes
