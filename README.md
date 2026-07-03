# ◆ Diskovery

A **read-only, multi-agent macOS storage analyzer**. Diskovery crawls the parts
of your Mac that quietly fill up — package managers, caches, dev cruft,
containers, creative-app render caches, duplicates, stale giant files — and
produces a single self-contained **HTML report** telling you exactly what's
reclaimable and what to keep.

Zero third-party dependencies. Nothing is ever deleted or modified — Diskovery
only measures, and hands you copy-paste cleanup commands to run yourself.

```bash
python3 -m diskovery            # thorough scan -> ./reports/report.html
python3 -m diskovery --quick    # skip the slowest passes
python3 -m diskovery --open     # open the report when done
```

## How it works

An **orchestrator** runs a fleet of specialized **agents** concurrently. Each
agent owns one domain, does a read-only sweep, and returns normalized findings.
One agent hanging or crashing can never sink the report — each runs isolated
with an overall time budget. Results are aggregated into `report.html` (with a
machine-readable `report.json` alongside). Every path is owned by exactly one
agent, so no byte is ever counted twice in the totals.

## The report

A single HTML file with zero external assets — it opens offline, prints
cleanly, and works without JavaScript (JS just adds sorting, filtering and
copy buttons). It gives you, top to bottom:

- **Headline** — how much you can free right now, and how much more after review
- **Your disk at a glance** — one bar mapping the whole disk: in use / safe to
  reclaim / needs review / free
- **Reclaimable space & where it's hiding** — a donut of safe vs. review, and
  a per-agent breakdown
- **Biggest wins first** — the largest findings across all agents, ranked
- **Per-agent detail** — sortable tables with paths, explanations, safety tags
  and one-click-copy cleanup commands
- **Toolbar** — filter by safety, live-search across findings/paths/commands,
  expand/collapse everything

Every finding carries a **safety tag** so you can tell junk from treasure at a
glance:

| Tag | Meaning |
| --- | --- |
| 🟢 `safe` | Caches / build artifacts / rebuildable — low risk to delete |
| 🟠 `review` | Heuristic or uncertain — eyeball it before removing |
| ⚪ `keep` | Real data (projects, media, libraries) — don't delete |

## The agents

| Agent | Icon | What it analyzes |
| --- | --- | --- |
| **DiskAgent** | 💽 | Volume capacity, APFS local snapshots, Trash, top-level home breakdown |
| **HomebrewAgent** | 🍺 | Formulae/casks, orphaned dependencies (`autoremove`), outdated packages, reclaimable download cache |
| **PythonAgent** | 🐍 | Interpreters (incl. EOL versions), pip cache, `__pycache__`, and a system-wide sweep for **loose/broken virtualenvs** |
| **NodeAgent** | 📦 | `node_modules` sprawl across projects, global packages, npm/yarn/pnpm caches |
| **ContainerAgent** | 🐳 | Docker images/containers/volumes/build cache + the VM disk image — **works even when the daemon is down** |
| **CacheLogAgent** | 🧹 | `~/Library/Caches` per-app, logs, diagnostics, and Xcode/Simulator caches |
| **CreativeCacheAgent** | 🎬 | DaVinci Resolve render/proxy cache, Final Cut generated media, Adobe media cache |
| **OrphanedAppDataAgent** | 👻 | Application Support folders with no matching installed app (stale, flag-only) |
| **LargeOldFilesAgent** | 🗄️ | Biggest files, large files untouched for 1y+, Downloads installers, size-by-type |
| **DuplicateFilesAgent** | 🧬 | Content-identical files (size → partial-hash → full-hash), reclaimable space |

## Scope & safety

- Scans your home directory, `/Applications`, the Homebrew prefix, and Docker
  data — everything a user owns. **Never uses sudo**, never touches `/System`.
- **Read-only.** Cleanup commands in the report are suggestions for you to run.
- Generated reports contain real paths and are **git-ignored** by default.

## Options

```
-o, --output DIR   where to write report.html/report.json (default: ./reports)
    --quick        skip the slowest passes (deep duplicate hashing, deep walk)
    --workers N    max agents running concurrently (default: 6)
    --budget SEC   overall time budget before straggler agents are cut (default: 900)
    --no-progress  suppress live progress
    --open         open the report when finished (macOS)
```

## Requirements

- macOS
- Python 3.9+ (the one already on your Mac is fine — no venv, no `pip install`)

See [`samples/sample-report.html`](samples/sample-report.html) for what the
output looks like (rendered from synthetic data — no real machine involved).
