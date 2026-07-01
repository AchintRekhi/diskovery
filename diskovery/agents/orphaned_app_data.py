"""OrphanedAppDataAgent — leftovers in Application Support for uninstalled apps.

This is inherently heuristic: matching a support folder back to an installed
app is fuzzy, so to keep false positives down we only flag a folder when ALL
of these hold: it doesn't match any installed app, it isn't a known
vendor/system folder, and it hasn't been modified in a long time. Everything is
tagged `review` and flag-only — never an auto-delete.
"""

from __future__ import annotations

import os
import re
from typing import List, Set, Tuple

from ..core import Agent, Finding, Safety, ScanContext, human_size, human_age

# Vendors / system folders that legitimately exist without a same-named .app.
_KNOWN = {
    "apple", "com.apple", "crashreporter", "mobilesync", "addressbook",
    "google", "microsoft", "mozilla", "adobe", "blackmagic design",
    "blackmagic", "zoom.us", "zoom", "slack", "spotify", "discord",
    "code", "com.apple.tcc", "syncservices", "knowledge", "caches",
    "icdd", "coresimulator", "developer", "provisioning profiles",
    "app store", "firefox", "chromium", "brave", "docker",
}
_MIN_STALE_DAYS = 120


class OrphanedAppDataAgent(Agent):
    name = "orphaned"
    title = "Orphaned App Data"
    icon = "👻"
    slow = False

    def collect(self, ctx: ScanContext) -> Tuple[List[Finding], str, List[str]]:
        findings: List[Finding] = []
        notes: List[str] = []
        installed = self._installed_app_tokens()

        support = ctx.path("Library", "Application Support")
        candidates: List[Tuple[str, int, float]] = []
        if os.path.isdir(support):
            try:
                names = os.listdir(support)
            except OSError:
                names = []
            for name in names:
                full = os.path.join(support, name)
                if not os.path.isdir(full) or os.path.islink(full):
                    continue
                low = name.lower()
                if self._is_known(low):
                    continue
                if self._matches_app(low, installed):
                    continue
                try:
                    age = ctx.now - os.path.getmtime(full)
                except OSError:
                    age = 0
                if age < _MIN_STALE_DAYS * 86400:
                    continue  # recently used — probably still relevant
                sz = ctx.du_bytes(full, timeout=45)
                if sz > 0:
                    candidates.append((full, sz, age))

        candidates.sort(key=lambda x: x[1], reverse=True)
        for full, sz, age in candidates[:20]:
            findings.append(Finding(
                category="Possibly orphaned",
                label=os.path.basename(full),
                size_bytes=sz,
                path=full,
                safety=Safety.REVIEW,
                detail=f"No matching app in /Applications; last modified "
                       f"{human_age(age)} ago. Verify the app is gone before removing.",
                suggestion=f"# verify first, then: rm -rf {self._q(full)}",
            ))
        if candidates:
            notes.append(f"{len(candidates)} Application Support folder(s) have no "
                         "matching installed app and are stale.")
        summary = f"{len(candidates)} possibly-orphaned folder(s)"
        return findings, summary, notes

    # ------------------------------------------------------------------ #
    def _installed_app_tokens(self) -> Set[str]:
        tokens: Set[str] = set()
        roots = ["/Applications", os.path.expanduser("~/Applications"),
                 "/System/Applications"]
        for root in roots:
            if not os.path.isdir(root):
                continue
            try:
                entries = os.listdir(root)
            except OSError:
                continue
            for e in entries:
                if not e.endswith(".app"):
                    continue
                base = e[:-4].lower()
                tokens.add(base)
                for tok in re.split(r"[^a-z0-9]+", base):
                    if len(tok) >= 3:
                        tokens.add(tok)
        return tokens

    def _matches_app(self, low: str, installed: Set[str]) -> bool:
        if low in installed:
            return True
        for tok in re.split(r"[^a-z0-9]+", low):
            if len(tok) >= 3 and tok in installed:
                return True
        # substring either direction for compound names
        for app in installed:
            if len(app) >= 4 and (app in low or low in app):
                return True
        return False

    def _is_known(self, low: str) -> bool:
        return any(k in low for k in _KNOWN)

    @staticmethod
    def _q(path: str) -> str:
        return "'" + path.replace("'", "'\\''") + "'"
