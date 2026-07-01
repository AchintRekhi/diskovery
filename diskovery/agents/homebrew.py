"""HomebrewAgent — formulae/casks, orphaned deps, outdated, reclaimable cache.

Everything here shells out to `brew` in dry-run/read modes only. The one
number we care most about is *reclaimable* space, so we measure the download
cache directly (byte-accurate) rather than trusting the pretty estimate.
"""

from __future__ import annotations

import os
from typing import List, Tuple

from ..core import Agent, Finding, Safety, ScanContext, human_size


class HomebrewAgent(Agent):
    name = "homebrew"
    title = "Homebrew"
    icon = "🍺"
    slow = False

    def applicable(self, ctx: ScanContext) -> bool:
        return ctx.which("brew") is not None

    def _brew_prefix(self, ctx: ScanContext) -> str:
        rc, out, _ = ctx.run(["brew", "--prefix"], timeout=15)
        return out.strip() if rc == 0 and out.strip() else "/opt/homebrew"

    def collect(self, ctx: ScanContext) -> Tuple[List[Finding], str, List[str]]:
        findings: List[Finding] = []
        notes: List[str] = []
        prefix = self._brew_prefix(ctx)
        cellar = os.path.join(prefix, "Cellar")
        caskroom = os.path.join(prefix, "Caskroom")

        # --- installed footprint (informational) -----------------------------
        n_formula = 0
        rc, out, _ = ctx.run(["brew", "list", "--formula"], timeout=30)
        if rc == 0:
            n_formula = len([x for x in out.split() if x])
        n_cask = 0
        rc, out, _ = ctx.run(["brew", "list", "--cask"], timeout=30)
        if rc == 0:
            n_cask = len([x for x in out.split() if x])

        cellar_sz = ctx.du_bytes(cellar, timeout=90) if os.path.isdir(cellar) else 0
        if cellar_sz:
            findings.append(Finding(
                category="Installed",
                label=f"Cellar — {n_formula} formula(e)",
                size_bytes=cellar_sz,
                path=cellar,
                safety=Safety.KEEP,
                detail="Installed formulae. Kept unless individually removed.",
            ))
        cask_sz = ctx.du_bytes(caskroom, timeout=60) if os.path.isdir(caskroom) else 0
        if cask_sz:
            findings.append(Finding(
                category="Installed",
                label=f"Caskroom — {n_cask} cask(s)",
                size_bytes=cask_sz,
                path=caskroom,
                safety=Safety.KEEP,
                detail="Installed casks (GUI apps managed by brew).",
            ))

        # --- reclaimable download cache --------------------------------------
        rc, cache_dir, _ = ctx.run(["brew", "--cache"], timeout=15)
        cache_dir = cache_dir.strip()
        est = self._cleanup_estimate(ctx)
        if cache_dir and os.path.isdir(cache_dir):
            csz = ctx.du_bytes(cache_dir, timeout=60)
            if csz > 0:
                findings.append(Finding(
                    category="Reclaimable",
                    label="Download cache & old versions",
                    size_bytes=csz,
                    path=cache_dir,
                    safety=Safety.SAFE,
                    detail=(f"brew estimates ~{est} freeable. " if est else "")
                           + "Cached downloads and superseded versions.",
                    suggestion="brew cleanup -s && rm -rf $(brew --cache)",
                ))

        # --- orphaned dependencies (autoremove candidates) -------------------
        rc, out, _ = ctx.run(["brew", "autoremove", "-n"], timeout=90)
        orphans = self._parse_names(out)
        if orphans:
            for name in orphans:
                sz = ctx.du_bytes(os.path.join(cellar, name), timeout=20)
                findings.append(Finding(
                    category="Orphaned deps",
                    label=name,
                    size_bytes=sz,
                    path=os.path.join(cellar, name),
                    safety=Safety.REVIEW,
                    detail="Installed as a dependency; nothing left depends on it.",
                    suggestion="brew autoremove",
                ))
            notes.append(f"{len(orphans)} orphaned dependency formula(e) — `brew autoremove`.")

        # --- outdated (informational) ----------------------------------------
        rc, out, _ = ctx.run(["brew", "outdated", "--quiet"], timeout=60)
        outdated = [x for x in out.split() if x] if rc == 0 else []
        if outdated:
            findings.append(Finding(
                category="Outdated",
                label=f"{len(outdated)} outdated package(s)",
                size_bytes=0,
                safety=Safety.REVIEW,
                detail="Newer versions available: " + ", ".join(outdated[:10])
                       + (" …" if len(outdated) > 10 else ""),
                suggestion="brew upgrade",
            ))
            notes.append(f"{len(outdated)} package(s) outdated.")

        summary = f"{n_formula} formulae, {n_cask} casks"
        return findings, summary, notes

    # ------------------------------------------------------------------ #
    def _cleanup_estimate(self, ctx: ScanContext) -> str:
        rc, out, err = ctx.run(["brew", "cleanup", "-n"], timeout=120)
        text = (out + "\n" + err)
        for line in text.splitlines():
            low = line.lower()
            if "free approximately" in low or "would free" in low:
                # grab the token after 'approximately'
                for kw in ("approximately", "free"):
                    if kw in low:
                        tail = line.split(kw, 1)[1].strip()
                        tok = tail.split()[0].rstrip(".") if tail.split() else ""
                        if tok and tok[0].isdigit():
                            return tok
        return ""

    @staticmethod
    def _parse_names(text: str) -> List[str]:
        names = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("==>") or line.startswith("Warning"):
                continue
            # bare formula tokens on their own line
            if " " not in line and "/" not in line:
                names.append(line)
        return names
