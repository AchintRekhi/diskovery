"""Generate samples/sample-report.html from synthetic data.

No real machine is touched — this exists so people can see what a Diskovery
report looks like without running a scan. Regenerate with:

    python3 scripts/make_sample.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diskovery.core import AgentResult, Finding, Safety  # noqa: E402
from diskovery.render import build_html  # noqa: E402

GB = 1024 ** 3
MB = 1024 ** 2


def F(cat, label, size, safety, detail="", suggestion=None, path=None, count=1):
    return Finding(cat, label, size_bytes=size, safety=safety, detail=detail,
                   suggestion=suggestion, path=path, count=count)


def sample_results():
    return [
        AgentResult("disk", "Disk Overview", "💽", status="ok",
                    summary="212 GB used / 248 GB free",
                    notes=["3 local snapshot(s) present (purgeable space)."],
                    findings=[
            F("Volume", "Macintosh HD — used", 212 * GB, Safety.KEEP,
              "248 GB free of 460 GB", path="/"),
            F("Snapshots", "3 APFS local snapshot(s)", 0, Safety.REVIEW,
              "Local Time Machine snapshots hold deleted files.",
              "tmutil thinlocalsnapshots / 999999999999 4"),
            F("Trash", "Trash", 4200 * MB, Safety.SAFE, "Files already in Trash.",
              "rm -rf ~/.Trash/*", path="/Users/you/.Trash"),
        ]),
        AgentResult("homebrew", "Homebrew", "🍺", status="ok",
                    summary="84 formulae, 12 casks",
                    notes=["6 orphaned dependency formula(e) — `brew autoremove`."],
                    findings=[
            F("Reclaimable", "Download cache & old versions", 3100 * MB, Safety.SAFE,
              "brew estimates ~3.1GB freeable.", "brew cleanup -s",
              path="/Users/you/Library/Caches/Homebrew"),
            F("Orphaned deps", "gdbm", 18 * MB, Safety.REVIEW,
              "Installed as a dependency; nothing left depends on it.", "brew autoremove"),
            F("Orphaned deps", "libtiff", 42 * MB, Safety.REVIEW,
              "Installed as a dependency; nothing left depends on it.", "brew autoremove"),
            F("Outdated", "9 outdated package(s)", 0, Safety.REVIEW,
              "Newer versions available: node, git, python@3.12 …", "brew upgrade"),
        ]),
        AgentResult("python", "Python Environments", "🐍", status="ok",
                    summary="14 venv(s), 220 __pycache__ dirs",
                    notes=["4 virtualenv(s) point at a missing interpreter (broken)."],
                    findings=[
            F("Reclaimable", "pip cache", 890 * MB, Safety.SAFE,
              "Downloaded wheels pip keeps for reinstalls.", "python3 -m pip cache purge"),
            F("Virtualenvs", "Projects/old-scraper/.venv", 320 * MB, Safety.REVIEW,
              "Python 3.8. Base interpreter missing — venv is broken/orphaned.",
              "rm -rf ~/Projects/old-scraper/.venv"),
            F("Virtualenvs", "Projects/ml-thing/.venv", 1400 * MB, Safety.REVIEW,
              "Python 3.11. Last touched 8 months ago.",
              "rm -rf ~/Projects/ml-thing/.venv"),
            F("Bytecode caches", "__pycache__ (220 dirs)", 74 * MB, Safety.SAFE,
              "Compiled .pyc caches — regenerated automatically.",
              "find ~ -type d -name __pycache__ -prune -exec rm -rf {} +"),
            F("Interpreters", "python.org framework 2.7", 210 * MB, Safety.REVIEW,
              "End-of-life Python — consider removing if unused."),
        ]),
        AgentResult("node", "Node / npm", "📦", status="ok",
                    summary="23 node_modules, 6.4 GB",
                    notes=["23 node_modules totalling 6.4 GB."],
                    findings=[
            F("node_modules", "Projects/dashboard", 1200 * MB, Safety.SAFE,
              "Rebuildable with install. Project last touched 5 months ago.",
              "rm -rf ~/Projects/dashboard/node_modules"),
            F("node_modules", "Projects/old-site", 940 * MB, Safety.SAFE,
              "Rebuildable with install. Project last touched 1.2 years ago.",
              "rm -rf ~/Projects/old-site/node_modules"),
            F("node_modules", "… 21 more node_modules", 4260 * MB, Safety.SAFE,
              "Smaller/older dependency trees rolled up."),
            F("Reclaimable", "npm cache", 610 * MB, Safety.SAFE,
              "Package manager download cache.", "npm cache clean --force"),
        ]),
        AgentResult("containers", "Docker / Containers", "🐳", status="ok",
                    summary="daemon down — on-disk footprint only",
                    notes=["Docker daemon is not running — reporting on-disk VM "
                           "footprint only."],
                    findings=[
            F("VM disk", "Docker.raw (VM disk image)", 18 * GB, Safety.REVIEW,
              "Docker Desktop's virtual disk. It does NOT auto-shrink after prune.",
              "# Docker Desktop → Troubleshoot → Clean / Purge data",
              path="/Users/you/Library/Containers/com.docker.docker/.../Docker.raw"),
        ]),
        AgentResult("caches", "Caches & Logs", "🧹", status="ok",
                    summary="~5 GB in user caches", findings=[
            F("App caches", "com.apple.Safari", 1800 * MB, Safety.SAFE,
              "Application cache — rebuilt on demand."),
            F("App caches", "Google/Chrome", 1300 * MB, Safety.SAFE,
              "Application cache — rebuilt on demand."),
            F("Developer caches", "Xcode DerivedData", 9200 * MB, Safety.SAFE,
              "Xcode build intermediates — rebuilt on next build.",
              "rm -rf ~/Library/Developer/Xcode/DerivedData/*"),
            F("Developer caches", "iOS Simulators", 12 * GB, Safety.REVIEW,
              "Installed simulator runtimes — large; delete unused ones.",
              "xcrun simctl delete unavailable"),
        ]),
        AgentResult("creative", "Creative App Caches", "🎬", status="ok",
                    summary="~24 GB in creative caches",
                    notes=["~18 GB of DaVinci render/proxy cache is reclaimable."],
                    findings=[
            F("DaVinci Resolve", "DaVinci Resolve / CacheClip", 14 * GB, Safety.SAFE,
              "Render/proxy cache — regenerated on playback.",
              "rm -rf '~/Movies/DaVinci Resolve/CacheClip'/*"),
            F("DaVinci Resolve", "DaVinci Resolve / ProxyMedia", 4 * GB, Safety.SAFE,
              "Render/proxy cache — regenerated on playback."),
            F("Final Cut Pro", "Wedding2025.fcpbundle", 62 * GB, Safety.KEEP,
              "Final Cut library (projects + media). Keep."),
            F("Final Cut Pro", "↳ generated media in Wedding2025.fcpbundle", 6 * GB,
              Safety.REVIEW, "Render/transcoded files inside the library — regenerable."),
            F("Adobe", "Adobe Media Cache Files", 2100 * MB, Safety.SAFE,
              "Adobe media/peak cache — rebuilt on demand."),
        ]),
        AgentResult("orphaned", "Orphaned App Data", "👻", status="ok",
                    summary="3 possibly-orphaned folder(s)", findings=[
            F("Possibly orphaned", "Sublime Text 3", 240 * MB, Safety.REVIEW,
              "No matching app in /Applications; last modified 1.4 years ago."),
            F("Possibly orphaned", "Vagrant", 80 * MB, Safety.REVIEW,
              "No matching app in /Applications; last modified 2.1 years ago."),
        ]),
        AgentResult("largefiles", "Large & Old Files", "🗄️", status="ok",
                    summary="largest file 8.2 GB",
                    notes=["7 large file(s) untouched for over a year.",
                           "5 installer(s) in Downloads (3.4 GB)."],
                    findings=[
            F("Largest files", "Movies/screen-recordings/demo-4k.mov", 8200 * MB,
              Safety.KEEP, "Largest files in your home (informational)."),
            F("Large & untouched", "Downloads/ubuntu-22.04.iso", 3600 * MB, Safety.REVIEW,
              "≥50 MB and not modified in 1.6 years — archive candidate."),
            F("Downloads", "Installers in Downloads (5)", 3400 * MB, Safety.SAFE,
              "Disk images/archives — usually safe to delete after install."),
            F("By file type", ".mov", 46 * GB, Safety.KEEP,
              "38 file(s) of this type.", count=38),
            F("By file type", ".psd", 12 * GB, Safety.KEEP,
              "61 file(s) of this type.", count=61),
        ]),
        AgentResult("duplicates", "Duplicate Files", "🧬", status="ok",
                    summary="14 dup sets, ~5.9 GB",
                    notes=["14 duplicate set(s); ~5.9 GB reclaimable."],
                    findings=[
            F("Duplicate sets", "export-final.mp4 ×3", 2 * GB, Safety.REVIEW,
              "3 identical copies of 1 GB each. Keep one, remove the rest.", count=3),
            F("Duplicate sets", "dataset.zip ×2", 1400 * MB, Safety.REVIEW,
              "2 identical copies of 1.4 GB each.", count=2),
        ]),
    ]


def main():
    meta = {
        "version": "0.1.0",
        "generated_at": "2026-07-01 02:30",
        "machine": {"hostname": "sample-mac", "os": "26.5.1",
                    "disk": {"size": 460 * GB, "used": 212 * GB, "avail": 248 * GB}},
        "quick": False,
        "duration_s": 47.3,
    }
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "samples", "sample-report.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(build_html(sample_results(), meta))
    print("wrote", out)


if __name__ == "__main__":
    main()
