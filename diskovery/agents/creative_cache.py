"""CreativeCacheAgent — DaVinci Resolve, Final Cut Pro, Adobe media caches.

Creative apps generate enormous *regenerable* caches (render caches, optimized
& proxy media, media cache files) that are safe to reclaim — while the actual
projects/libraries/originals must be kept. This agent draws that line: caches
are tagged `safe`, projects/libraries `keep`, and in-library generated media
`review` (deletion belongs in the app's own UI).
"""

from __future__ import annotations

import os
from typing import List, Tuple

from ..core import Agent, Finding, Safety, ScanContext, human_size

# Basenames inside a Resolve working dir that are pure cache.
_RESOLVE_CACHE = {"CacheClip", "ProxyMedia", ".gallery", "Optimized Media",
                  "ProxyGenerator", "CacheFusion"}
# Basenames inside a Final Cut library that are regenerable media.
_FCP_GENERATED = {"Render Files", "Transcoded Media", "Analysis Files",
                  "Peaks Cache"}


class CreativeCacheAgent(Agent):
    name = "creative"
    title = "Creative App Caches"
    icon = "🎬"
    slow = False

    def applicable(self, ctx: ScanContext) -> bool:
        markers = [
            ctx.path("Library", "Application Support", "Blackmagic Design"),
            ctx.path("Library", "Application Support", "Adobe"),
            ctx.path("Movies", "DaVinci Resolve"),
        ]
        if any(os.path.exists(p) for p in markers):
            return True
        # Final Cut libraries in ~/Movies
        movies = ctx.path("Movies")
        if os.path.isdir(movies):
            try:
                return any(n.endswith(".fcpbundle") for n in os.listdir(movies))
            except OSError:
                return False
        return False

    def collect(self, ctx: ScanContext) -> Tuple[List[Finding], str, List[str]]:
        findings: List[Finding] = []
        notes: List[str] = []
        reclaimable = 0

        reclaimable += self._resolve(ctx, findings, notes)
        reclaimable += self._final_cut(ctx, findings, notes)
        reclaimable += self._adobe(ctx, findings, notes)

        summary = f"~{human_size(reclaimable)} in creative caches"
        return findings, summary, notes

    # ------------------------------------------------------------------ #
    def _resolve(self, ctx, findings, notes) -> int:
        got = 0
        work = ctx.path("Movies", "DaVinci Resolve")
        if os.path.isdir(work):
            try:
                for name in os.listdir(work):
                    full = os.path.join(work, name)
                    if not os.path.isdir(full):
                        continue
                    sz = ctx.du_bytes(full, timeout=60)
                    if sz <= 0:
                        continue
                    is_cache = name in _RESOLVE_CACHE
                    findings.append(Finding(
                        category="DaVinci Resolve",
                        label=f"DaVinci Resolve / {name}",
                        size_bytes=sz,
                        path=full,
                        safety=Safety.SAFE if is_cache else Safety.KEEP,
                        detail="Render/proxy cache — regenerated on playback."
                               if is_cache else "Resolve working data (keep).",
                        suggestion=(f"rm -rf {self._q(full)}/*" if is_cache else None),
                    ))
                    if is_cache:
                        got += sz
            except OSError:
                pass
        # The support dir holds the project databases — keep, but report size.
        support = ctx.path("Library", "Application Support", "Blackmagic Design",
                           "DaVinci Resolve")
        if os.path.isdir(support):
            sz = ctx.du_bytes(support, timeout=90)
            if sz > 0:
                findings.append(Finding(
                    category="DaVinci Resolve",
                    label="Resolve project database & config",
                    size_bytes=sz,
                    path=support,
                    safety=Safety.KEEP,
                    detail="Contains your project databases — do NOT delete.",
                ))
        if got:
            notes.append(f"~{human_size(got)} of DaVinci render/proxy cache is reclaimable "
                         "(clear via Playback → Delete Render Cache).")
        return got

    def _final_cut(self, ctx, findings, notes) -> int:
        got = 0
        movies = ctx.path("Movies")
        if not os.path.isdir(movies):
            return 0
        try:
            bundles = [os.path.join(movies, n) for n in os.listdir(movies)
                       if n.endswith(".fcpbundle")]
        except OSError:
            bundles = []
        for lib in bundles:
            lib_sz = ctx.du_bytes(lib, timeout=90)
            findings.append(Finding(
                category="Final Cut Pro",
                label=os.path.basename(lib),
                size_bytes=lib_sz,
                path=lib,
                safety=Safety.KEEP,
                detail="Final Cut library (projects + media). Keep.",
            ))
            gen = self._sum_generated(ctx, lib)
            if gen > 0:
                got += gen
                findings.append(Finding(
                    category="Final Cut Pro",
                    label=f"↳ generated media in {os.path.basename(lib)}",
                    size_bytes=gen,
                    path=lib,
                    safety=Safety.REVIEW,
                    detail="Render/transcoded/analysis files inside the library — "
                           "regenerable. Delete from Final Cut: File → Delete "
                           "Generated Library Files.",
                ))
        if got:
            notes.append(f"~{human_size(got)} of Final Cut generated media is regenerable.")
        return got

    def _sum_generated(self, ctx: ScanContext, bundle: str) -> int:
        total = 0
        base_depth = bundle.rstrip(os.sep).count(os.sep)
        for cur, dirs, _files in os.walk(bundle, onerror=lambda e: None):
            if cur.count(os.sep) - base_depth > 6:
                dirs[:] = []
                continue
            for d in list(dirs):
                if d in _FCP_GENERATED:
                    p = os.path.join(cur, d)
                    total += ctx.du_bytes(p, timeout=45)
                    dirs.remove(d)
        return total

    def _adobe(self, ctx, findings, notes) -> int:
        got = 0
        candidates = [
            (["Library", "Application Support", "Adobe", "Common", "Media Cache Files"],
             "Adobe Media Cache Files"),
            (["Library", "Application Support", "Adobe", "Common", "Media Cache"],
             "Adobe Media Cache"),
            (["Library", "Caches", "Adobe"], "Adobe caches"),
        ]
        for rel, label in candidates:
            p = ctx.path(*rel)
            if os.path.isdir(p):
                sz = ctx.du_bytes(p, timeout=60)
                if sz > 0:
                    got += sz
                    findings.append(Finding(
                        category="Adobe",
                        label=label,
                        size_bytes=sz,
                        path=p,
                        safety=Safety.SAFE,
                        detail="Adobe media/peak cache — rebuilt on demand.",
                        suggestion=f"rm -rf {self._q(p)}/*",
                    ))
        return got

    @staticmethod
    def _q(path: str) -> str:
        return "'" + path.replace("'", "'\\''") + "'"
