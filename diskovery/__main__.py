"""Command-line entry point: `python3 -m diskovery`.

Gathers machine facts, runs the agents concurrently, then writes a
self-contained report.html and report.json. Read-only from start to finish.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import platform
import socket
import subprocess
import sys
import time

from . import __version__
from .agents import all_agents
from .core import ScanContext, human_size
from .orchestrator import Progress, run_agents
from .render import build_html, build_json


def _disk_info() -> dict:
    try:
        out = subprocess.run(["df", "-k", "/"], capture_output=True, text=True,
                             timeout=15).stdout
        parts = out.strip().splitlines()[1].split()
        return {
            "size": int(parts[1]) * 1024,
            "used": int(parts[2]) * 1024,
            "avail": int(parts[3]) * 1024,
        }
    except Exception:
        return {}


def _machine() -> dict:
    try:
        host = socket.gethostname().split(".")[0]
    except Exception:
        host = "this-mac"
    return {
        "hostname": host,
        "os": platform.mac_ver()[0] or platform.platform(),
        "disk": _disk_info(),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="diskovery",
        description="Read-only multi-agent macOS storage analyzer.",
    )
    p.add_argument("-o", "--output", default="reports",
                   help="directory to write report.html / report.json (default: ./reports)")
    p.add_argument("--quick", action="store_true",
                   help="skip the slowest passes (deep duplicate hashing, deep walk)")
    p.add_argument("--workers", type=int, default=6,
                   help="max agents to run concurrently (default: 6)")
    p.add_argument("--budget", type=int, default=900,
                   help="overall time budget in seconds before straggler agents are cut (default: 900)")
    p.add_argument("--no-progress", action="store_true", help="suppress live progress")
    p.add_argument("--open", action="store_true", help="open the report when done (macOS)")
    args = p.parse_args(argv)

    home = os.path.expanduser("~")
    ctx = ScanContext(home=home, quick=args.quick)

    agents = all_agents()
    if args.quick:
        agents = [a for a in agents if not getattr(a, "quick_skip", False)]

    machine = _machine()
    print(f"\n◆ Diskovery v{__version__} — scanning {machine['hostname']} "
          f"({'quick' if args.quick else 'thorough'})\n")
    if machine.get("disk", {}).get("size"):
        d = machine["disk"]
        print(f"  Disk: {human_size(d['used'])} used, {human_size(d['avail'])} free "
              f"of {human_size(d['size'])}\n")

    progress = Progress(total=len(agents), enabled=not args.no_progress)
    start = time.time()
    results = run_agents(agents, ctx, max_workers=args.workers,
                         budget_s=args.budget, progress=progress)
    duration = time.time() - start

    meta = {
        "version": __version__,
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "machine": machine,
        "quick": args.quick,
        "duration_s": duration,
    }

    out_dir = os.path.abspath(args.output)
    os.makedirs(out_dir, exist_ok=True)
    html_path = os.path.join(out_dir, "report.html")
    json_path = os.path.join(out_dir, "report.json")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(build_html(results, meta))
    with open(json_path, "w", encoding="utf-8") as fh:
        import json
        json.dump(build_json(results, meta), fh, indent=2)

    safe = sum(a.reclaimable_bytes for a in results)
    review = sum(a.review_bytes for a in results)
    print(f"\n  ✓ Done in {duration:.1f}s — "
          f"{human_size(safe)} safe to reclaim, {human_size(review)} after review.")
    print(f"  → {html_path}\n")

    if args.open:
        subprocess.run(["open", html_path], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
