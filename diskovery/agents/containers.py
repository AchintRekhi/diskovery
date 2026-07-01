"""ContainerAgent — Docker images/containers/volumes/build-cache + VM disk.

Crucially this must work when the Docker daemon is *down* (as it is on this
machine): we can't ask `docker system df`, but we can still measure the Docker
Desktop VM disk footprint on disk, which is often the biggest surprise.
"""

from __future__ import annotations

import os
import re
from typing import List, Tuple

from ..core import Agent, Finding, Safety, ScanContext, human_size

_UNIT = {"B": 1, "KB": 10**3, "KIB": 2**10, "MB": 10**6, "MIB": 2**20,
         "GB": 10**9, "GIB": 2**30, "TB": 10**12, "TIB": 2**40}


def _parse_docker_size(s: str) -> int:
    s = s.strip()
    m = re.match(r"([\d.]+)\s*([A-Za-z]+)", s)
    if not m:
        return 0
    num = float(m.group(1))
    unit = m.group(2).upper()
    return int(num * _UNIT.get(unit, 1))


class ContainerAgent(Agent):
    name = "containers"
    title = "Docker / Containers"
    icon = "🐳"
    slow = False

    def _vm_data_dirs(self, ctx: ScanContext) -> List[str]:
        return [
            ctx.path("Library", "Containers", "com.docker.docker", "Data"),
            ctx.path("Library", "Group Containers", "group.com.docker"),
        ]

    def applicable(self, ctx: ScanContext) -> bool:
        if ctx.which("docker"):
            return True
        return any(os.path.exists(p) for p in self._vm_data_dirs(ctx))

    def collect(self, ctx: ScanContext) -> Tuple[List[Finding], str, List[str]]:
        findings: List[Finding] = []
        notes: List[str] = []

        daemon_up = False
        if ctx.which("docker"):
            rc, _, _ = ctx.run(["docker", "info"], timeout=12)
            daemon_up = rc == 0

        if daemon_up:
            findings += self._live(ctx, notes)
        else:
            notes.append("Docker daemon is not running — live image/volume "
                         "breakdown unavailable; reporting on-disk VM footprint only.")

        findings += self._vm_footprint(ctx, notes, daemon_up)

        if daemon_up:
            summary = "daemon up — " + (f"{human_size(sum(f.size_bytes for f in findings if f.safety is Safety.SAFE))} reclaimable")
        else:
            summary = "daemon down — on-disk footprint only"
        return findings, summary, notes

    # ------------------------------------------------------------------ #
    def _live(self, ctx: ScanContext, notes: List[str]) -> List[Finding]:
        out: List[Finding] = []
        fmt = "{{.Type}}|{{.Size}}|{{.Reclaimable}}"
        rc, txt, _ = ctx.run(["docker", "system", "df", "--format", fmt], timeout=30)
        if rc != 0:
            return out
        prune = {
            "Images": "docker image prune -a",
            "Containers": "docker container prune",
            "Local Volumes": "docker volume prune",
            "Build Cache": "docker builder prune -a",
        }
        for line in txt.strip().splitlines():
            parts = line.split("|")
            if len(parts) < 3:
                continue
            typ, size_s, recl_s = parts[0].strip(), parts[1].strip(), parts[2].strip()
            total = _parse_docker_size(size_s)
            recl = _parse_docker_size(recl_s)
            if total == 0 and recl == 0:
                continue
            out.append(Finding(
                category="Docker objects",
                label=typ,
                size_bytes=recl,
                safety=Safety.SAFE if recl > 0 else Safety.KEEP,
                detail=f"Total {human_size(total)}, reclaimable {human_size(recl)}.",
                suggestion=prune.get(typ, "docker system prune"),
            ))
        if out:
            notes.append("Reclaim broadly with `docker system prune -a --volumes` "
                         "(removes all unused images/containers/volumes).")
        return out

    def _vm_footprint(self, ctx: ScanContext, notes: List[str],
                      daemon_up: bool) -> List[Finding]:
        out: List[Finding] = []
        # The single big file on Docker Desktop / macOS.
        raw_candidates = [
            ctx.path("Library", "Containers", "com.docker.docker", "Data",
                     "vms", "0", "data", "Docker.raw"),
            ctx.path("Library", "Containers", "com.docker.docker", "Data",
                     "vms", "0", "Docker.raw"),
        ]
        raw = next((p for p in raw_candidates if os.path.exists(p)), None)
        if raw:
            on_disk = ctx.du_bytes(raw, timeout=30)  # du is sparse-aware
            out.append(Finding(
                category="VM disk",
                label="Docker.raw (VM disk image)",
                size_bytes=on_disk,
                path=raw,
                safety=Safety.REVIEW,
                detail="Docker Desktop's virtual disk. It does NOT auto-shrink "
                       "after prune; reclaim via Docker Desktop → Troubleshoot → "
                       "'Clean / Purge data', or delete if you no longer use Docker.",
                suggestion="# Docker Desktop → Settings → Troubleshoot → Clean / Purge data",
            ))
        else:
            # No single raw file — measure the whole Data dir as a fallback.
            data = ctx.path("Library", "Containers", "com.docker.docker", "Data")
            if os.path.isdir(data):
                sz = ctx.du_bytes(data, timeout=60)
                if sz:
                    out.append(Finding(
                        category="VM disk",
                        label="Docker Desktop data",
                        size_bytes=sz,
                        path=data,
                        safety=Safety.REVIEW,
                        detail="Docker Desktop backing data.",
                    ))
        return out
