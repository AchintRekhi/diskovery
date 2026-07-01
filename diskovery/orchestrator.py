"""The orchestrator: discovers agents and runs them concurrently.

Design goals:
  * One misbehaving agent must never sink the whole report. Each runs in its
    own daemon thread; results arrive over a queue; anything that doesn't
    report before the overall budget is marked `timeout` and we move on.
  * Live, line-based progress that works in any terminal (no curses/TUI).
  * Deterministic ordering in the final output regardless of finish order.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from typing import List

from .core import Agent, AgentResult, ScanContext, human_size


def _supports_color() -> bool:
    return sys.stdout.isatty()


class Progress:
    """Minimal, dependency-free live progress printer."""

    def __init__(self, total: int, enabled: bool = True):
        self.total = total
        self.done = 0
        self.enabled = enabled and sys.stdout.isatty()
        self.color = _supports_color()
        self._lock = threading.Lock()

    def _c(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def start(self, agent: Agent) -> None:
        if not self.enabled:
            return
        with self._lock:
            print(f"  {self._c('▶', '36')} {agent.icon} {agent.title} …", flush=True)

    def finish(self, res: AgentResult) -> None:
        with self._lock:
            self.done += 1
            mark = {
                "ok": self._c("✓", "32"),
                "skipped": self._c("–", "90"),
                "error": self._c("✗", "31"),
                "timeout": self._c("⏱", "33"),
            }.get(res.status, "?")
            counter = self._c(f"[{self.done}/{self.total}]", "90")
            if res.status == "ok":
                detail = f"{res.summary}" if res.summary else human_size(res.total_bytes)
                extra = self._c(f"{res.duration_s:.1f}s", "90")
                line = f"{counter} {mark} {res.title} — {detail}  {extra}"
            elif res.status == "skipped":
                line = f"{counter} {mark} {res.title} — {res.summary or 'not applicable'}"
            else:
                line = f"{counter} {mark} {res.title} — {res.error}"
            if self.enabled:
                print(line, flush=True)
            else:
                # non-tty (piped/CI): still emit a terminal-friendly summary line
                print(line, flush=True)


def run_agents(
    agents: List[Agent],
    ctx: ScanContext,
    max_workers: int = 6,
    budget_s: int = 600,
    progress: Progress | None = None,
) -> List[AgentResult]:
    """Run all applicable agents concurrently, returning results in input order."""

    # Filter to applicable agents up front so the counter is honest.
    active: List[Agent] = []
    pre_skipped: List[AgentResult] = []
    for a in agents:
        try:
            ok = a.applicable(ctx)
        except Exception as exc:  # applicability check itself failed
            pre_skipped.append(
                AgentResult(a.name, a.title, a.icon, status="skipped",
                            summary=f"unavailable ({exc})")
            )
            continue
        if ok:
            active.append(a)
        else:
            pre_skipped.append(
                AgentResult(a.name, a.title, a.icon, status="skipped",
                            summary="not applicable on this machine")
            )

    results: dict[str, AgentResult] = {r.name: r for r in pre_skipped}
    if progress:
        for r in pre_skipped:
            progress.finish(r)

    if not active:
        return [results[a.name] for a in agents]

    q: "queue.Queue[AgentResult]" = queue.Queue()
    sem = threading.BoundedSemaphore(max_workers)

    def worker(agent: Agent) -> None:
        with sem:
            if progress:
                progress.start(agent)
            t0 = time.time()
            try:
                findings, summary, notes = agent.collect(ctx)
                res = AgentResult(
                    agent.name, agent.title, agent.icon,
                    status="ok", findings=findings, summary=summary, notes=notes,
                )
            except Exception as exc:  # isolate: never let it escape
                res = AgentResult(
                    agent.name, agent.title, agent.icon,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                )
            res.duration_s = time.time() - t0
            q.put(res)

    for a in active:
        threading.Thread(target=worker, args=(a,), daemon=True, name=a.name).start()

    deadline = time.time() + budget_s
    while len([r for r in results.values()]) < len(agents):
        got = len(results)
        if got >= len(pre_skipped) + len(active):
            break
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        try:
            res = q.get(timeout=min(remaining, 0.5))
        except queue.Empty:
            continue
        results[res.name] = res
        if progress:
            progress.finish(res)

    # Anything still missing blew the budget — record and carry on.
    for a in active:
        if a.name not in results:
            res = AgentResult(
                a.name, a.title, a.icon,
                status="timeout",
                error=f"exceeded {budget_s}s overall budget",
            )
            results[a.name] = res
            if progress:
                progress.finish(res)

    return [results[a.name] for a in agents]
