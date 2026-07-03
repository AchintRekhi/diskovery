"""Render aggregated agent results into a self-contained HTML report + JSON.

No templating library, no CDN assets: all CSS/JS/SVG is inlined so the report
is a single portable file that opens offline anywhere. Collapsible sections use
native <details>/<summary> (works without JS and prints cleanly); a little JS
adds table sorting, safety filtering, live search and copy-to-clipboard.

Design: a printed technical audit. Warm paper background, ink text, hairline
ledger rules instead of card chrome, New York/Charter serif for display type,
SF Mono for every number and label, and exactly two data hues (green for safe,
amber for review). Flat fills only — no gradients, glows or shadows. All fonts
ship with macOS, so the file stays dependency-free.
"""

from __future__ import annotations

import html
import json
import math
from typing import List

from .core import AgentResult, Safety, human_size

_SAFE_COLORS = {"safe": "#1e7a46", "review": "#a3610c", "keep": "#8a8375"}
_SAFE_LABEL = {"safe": "Safe to reclaim", "review": "Review first", "keep": "Keep"}
_STATUS_LABEL = {"ok": "done", "skipped": "skipped",
                 "error": "failed", "timeout": "timed out"}

# Inline SVG icons (thin strokes, drawn in ink) keyed by agent name.
_ICON_PATHS = {
    "disk": '<line x1="22" y1="12" x2="2" y2="12"/><path d="M5.5 5.1 2 12v6'
            'a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.7 4H7.3'
            'a2 2 0 0 0-1.8 1.1z"/><line x1="6" y1="16" x2="6.01" y2="16"/>'
            '<line x1="10" y1="16" x2="10.01" y2="16"/>',
    "homebrew": '<path d="M6 5h9v14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V5z"/>'
                '<path d="M15 8h2a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2h-2"/>'
                '<path d="M6 5c0-1.2 2-2 4.5-2S15 3.8 15 5"/>'
                '<line x1="9" y1="10" x2="9" y2="16"/>'
                '<line x1="12" y1="10" x2="12" y2="16"/>',
    "python": '<polyline points="16 18 22 12 16 6"/>'
              '<polyline points="8 6 2 12 8 18"/>',
    "node": '<path d="M16.5 9.4 7.55 4.24"/><path d="M21 16V8a2 2 0 0 0-1-1.73'
            'l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4'
            'a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>'
            '<polyline points="3.29 7 12 12 20.71 7"/>'
            '<line x1="12" y1="22" x2="12" y2="12"/>',
    "containers": '<polygon points="12 2 2 7 12 12 22 7 12 2"/>'
                  '<polyline points="2 17 12 22 22 17"/>'
                  '<polyline points="2 12 12 17 22 12"/>',
    "caches": '<path d="m12 3-1.9 4.6L5.5 9.5l4.6 1.9L12 16l1.9-4.6 4.6-1.9'
              '-4.6-1.9z"/><path d="M5 3v4"/><path d="M3 5h4"/>'
              '<path d="M19 17v4"/><path d="M17 19h4"/>',
    "creative": '<rect x="2" y="3" width="20" height="18" rx="2"/>'
                '<line x1="7" y1="3" x2="7" y2="21"/>'
                '<line x1="17" y1="3" x2="17" y2="21"/>'
                '<line x1="2" y1="12" x2="22" y2="12"/>',
    "orphaned": '<path d="M9 10h.01"/><path d="M15 10h.01"/>'
                '<path d="M12 2a8 8 0 0 0-8 8v12l3-3 2.5 2.5L12 19l2.5 2.5'
                'L17 19l3 3V10a8 8 0 0 0-8-8z"/>',
    "largefiles": '<rect x="2" y="4" width="20" height="5" rx="1"/>'
                  '<path d="M4 9v9a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9"/>'
                  '<path d="M10 13h4"/>',
    "duplicates": '<rect x="9" y="9" width="13" height="13" rx="2"/>'
                  '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9'
                  'a2 2 0 0 1 2 2v1"/>',
}
_FALLBACK_ICON = ('<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4'
                  'A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4'
                  'A2 2 0 0 0 21 16z"/>')

_COPY_SVG = ('<svg viewBox="0 0 24 24" width="12" height="12" fill="none" '
             'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
             'stroke-linejoin="round"><rect x="9" y="9" width="13" height="13"'
             ' rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0'
             ' 0 1 2 2v1"/></svg>')

_SEARCH_SVG = ('<svg viewBox="0 0 24 24" width="13" height="13" fill="none" '
               'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
               'stroke-linejoin="round"><circle cx="11" cy="11" r="8"/>'
               '<line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>')


def compute_totals(results: List[AgentResult]) -> tuple:
    """Global safe/review totals, de-duplicated by path.

    Some paths are legitimately reported by two agents (e.g. the pip cache by
    both PythonAgent and CacheLogAgent). Counting them once keeps the headline
    honest. Path-less aggregate rows (e.g. "… 21 more node_modules") are always
    counted since they represent distinct extra space.
    """
    safe = review = 0
    seen_safe: set = set()
    seen_review: set = set()
    for a in results:
        for f in a.findings:
            if f.size_bytes <= 0:
                continue
            if f.safety is Safety.SAFE:
                if f.path:
                    if f.path in seen_safe:
                        continue
                    seen_safe.add(f.path)
                safe += f.size_bytes
            elif f.safety is Safety.REVIEW:
                if f.path:
                    if f.path in seen_review:
                        continue
                    seen_review.add(f.path)
                review += f.size_bytes
    return safe, review


def build_json(results: List[AgentResult], meta: dict) -> dict:
    safe, review = compute_totals(results)
    return {
        "diskovery_version": meta.get("version"),
        "generated_at": meta.get("generated_at"),
        "machine": meta.get("machine", {}),
        "run": {
            "quick": meta.get("quick", False),
            "duration_s": round(meta.get("duration_s", 0), 2),
        },
        "totals": {
            "reclaimable_safe_bytes": safe,
            "reclaimable_review_bytes": review,
        },
        "agents": [a.to_dict() for a in results],
    }


def build_html(results: List[AgentResult], meta: dict) -> str:
    safe, review = compute_totals(results)
    machine = meta.get("machine", {})

    # Biggest opportunities across all agents (safe or review, real size).
    opportunities = []
    for a in results:
        for f in a.findings:
            if f.safety in (Safety.SAFE, Safety.REVIEW) and f.size_bytes > 0:
                opportunities.append((f.size_bytes, a, f))
    opportunities.sort(key=lambda t: t[0], reverse=True)

    # Reclaimable by agent (safe+review) for the stacked bar chart.
    by_agent = [
        a for a in results if (a.reclaimable_bytes + a.review_bytes) > 0
    ]
    by_agent.sort(key=lambda a: a.reclaimable_bytes + a.review_bytes,
                  reverse=True)

    counts = {"safe": 0, "review": 0, "keep": 0}
    for a in results:
        for f in a.findings:
            counts[f.safety.value] += 1

    parts: List[str] = []
    parts.append(_head(meta))
    parts.append(_masthead(machine, meta))
    parts.append(_headline(safe, review))
    parts.append(_disk_map(safe, review, machine))
    parts.append(_summary_charts(safe, review, by_agent))
    parts.append(_opportunities(opportunities[:12]))
    parts.append(_toolbar(counts))
    parts.append('<section class="findings">'
                 '<div class="rulehead"><h2>Findings, agent by agent</h2></div>')
    for a in results:
        parts.append(_agent_section(a))
    parts.append("</section>")
    parts.append(_footer(meta))
    parts.append(_script())
    parts.append("</div></body></html>")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _icon(name: str, size: int = 17) -> str:
    body = _ICON_PATHS.get(name, _FALLBACK_ICON)
    return (f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" '
            f'fill="none" stroke="currentColor" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true">{body}</svg>')


def _bar(size: int, max_size: int, color: str) -> str:
    pct = 0 if max_size <= 0 else max(1.5, min(100, size / max_size * 100))
    return (f'<div class="bar"><div class="bar-fill" style="width:{pct:.1f}%;'
            f'background:{color}"></div></div>')


def _tag(safety: Safety) -> str:
    v = safety.value
    return (f'<span class="tag tag-{v}" title="{_SAFE_LABEL[v]}">'
            f'<span class="tag-dot"></span>{_SAFE_LABEL[v]}</span>')


def _head(meta: dict) -> str:
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Diskovery — {_e(meta.get('machine', {}).get('hostname', 'this Mac'))}</title>
<style>
:root {{
  --paper:#f5f1e8; --panel:#eee8da; --track:#e4ddcb;
  --ink:#211d15; --muted:#6e675a; --faint:#98917f;
  --hair:rgba(33,29,21,.16); --rule:#211d15;
  --safe:#1e7a46; --review:#a3610c; --keep:#8a8375; --danger:#b3362b;
  --safe-soft:rgba(30,122,70,.1); --review-soft:rgba(163,97,12,.1);
  --serif:"New York","Iowan Old Style",Charter,Georgia,serif;
  --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--paper); color:var(--ink);
  font:14px/1.55 var(--sans); -webkit-font-smoothing:antialiased; }}
a {{ color:var(--ink); }}
code {{ font-family:var(--mono); }}
.wrap {{ max-width:1000px; margin:0 auto; padding:44px 26px 90px; }}
button, summary, th[data-sort] {{ cursor:pointer; }}
:focus-visible {{ outline:2px solid var(--ink); outline-offset:2px; }}
.k {{ font-family:var(--mono); font-size:10.5px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--muted); }}
.num {{ font-family:var(--mono); font-variant-numeric:tabular-nums; }}

/* ---- masthead ---- */
.masthead {{ border-bottom:2px solid var(--rule); padding-bottom:14px; }}
.masthead .row1 {{ display:flex; align-items:baseline; justify-content:space-between;
  flex-wrap:wrap; gap:8px; }}
.wordmark {{ font-family:var(--mono); font-weight:700; font-size:15px;
  letter-spacing:.34em; text-transform:uppercase; }}
.wordmark .dot {{ color:var(--safe); }}
.masthead .doc {{ font-family:var(--mono); font-size:10.5px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--muted); }}
.dateline {{ display:flex; flex-wrap:wrap; gap:6px 22px; margin-top:12px;
  font-family:var(--mono); font-size:11px; color:var(--muted);
  letter-spacing:.02em; }}
.dateline b {{ color:var(--ink); font-weight:600; }}

/* ---- headline ---- */
.headline {{ display:flex; flex-wrap:wrap; align-items:flex-end; gap:18px 56px;
  padding:34px 0 30px; border-bottom:1px solid var(--hair); }}
.headline .big {{ font-family:var(--serif); font-weight:700; font-size:76px;
  line-height:.95; letter-spacing:-.02em; color:var(--safe);
  font-variant-numeric:lining-nums; }}
.headline .big small {{ font-size:40px; letter-spacing:0; }}
.headline .caption {{ max-width:340px; font-size:14.5px; color:var(--ink);
  font-family:var(--serif); line-height:1.5; }}
.headline .caption .and {{ color:var(--muted); }}
.headline .caption b.rev {{ color:var(--review); font-weight:700; }}

/* ---- disk map ---- */
.diskmap {{ padding:26px 0 24px; border-bottom:1px solid var(--hair); }}
.rulehead {{ display:flex; align-items:baseline; justify-content:space-between;
  gap:14px; margin-bottom:14px; }}
.rulehead h2, .rulehead h3 {{ margin:0; font-family:var(--mono); font-size:10.5px;
  font-weight:600; letter-spacing:.14em; text-transform:uppercase;
  color:var(--ink); }}
.rulehead .aside {{ font-family:var(--mono); font-size:10.5px;
  letter-spacing:.08em; color:var(--muted); }}
.diskbar {{ display:flex; height:26px; border:1px solid var(--ink); }}
.diskbar .seg {{ height:100%; min-width:2px; border-right:1px solid var(--paper); }}
.diskbar .seg:last-child {{ border-right:0; }}
.dlegend {{ display:flex; flex-wrap:wrap; gap:8px 24px; margin-top:10px;
  font-family:var(--mono); font-size:11px; color:var(--muted); }}
.dlegend b {{ color:var(--ink); font-weight:600; }}
.swatch {{ display:inline-block; width:10px; height:10px; margin-right:7px;
  vertical-align:-1px; border:1px solid var(--hair); }}

/* ---- overview grid ---- */
.cards {{ display:grid; grid-template-columns:5fr 7fr; gap:0;
  border-bottom:1px solid var(--hair); }}
.card {{ padding:26px 0 24px; min-width:0; }}
.card + .card {{ border-left:1px solid var(--hair); padding-left:32px; }}
.chart-row {{ display:flex; gap:24px; align-items:center; }}
.legend {{ font-size:13px; }}
.legend > div {{ margin:7px 0; color:var(--muted); }}
.legend b {{ color:var(--ink); font-family:var(--mono); font-size:12.5px; }}
.legend .note {{ font-family:var(--serif); font-style:italic; font-size:13px;
  color:var(--muted); margin-top:12px; }}
.agentbars .row {{ display:grid; grid-template-columns:170px 1fr 78px; gap:12px;
  align-items:center; margin:9px 0; }}
.agentbars .name {{ color:var(--ink); font-size:12.5px; white-space:nowrap;
  overflow:hidden; text-overflow:ellipsis; display:flex; align-items:center;
  gap:9px; }}
.agentbars .name svg {{ flex:0 0 auto; color:var(--muted); }}
.agentbars .val {{ text-align:right; font-family:var(--mono); font-size:11.5px;
  color:var(--ink); }}
.stack {{ display:flex; height:11px; background:var(--track); }}
.stack .s {{ height:100%; }}
.bar {{ background:var(--track); height:5px; margin-top:6px; max-width:170px; }}
.bar-fill {{ height:100%; }}

/* ---- top wins ---- */
.opps-wrap {{ padding:26px 0 20px; border-bottom:1px solid var(--hair); }}
.opps {{ list-style:none; padding:0; margin:0; column-gap:44px; }}
.opps li {{ display:flex; gap:14px; align-items:baseline; padding:7px 0;
  border-bottom:1px solid var(--hair); font-size:13.5px;
  break-inside:avoid; }}
.opps li:last-child {{ border-bottom:0; }}
.opps .rank {{ font-family:var(--mono); color:var(--faint); font-size:10.5px;
  min-width:16px; text-align:right; }}
.opps .n {{ font-family:var(--mono); font-size:12.5px; font-weight:700;
  min-width:74px; }}
.opps .what {{ flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap; }}
.opps .who {{ color:var(--faint); font-size:12px; }}
@media (min-width:760px) {{ .opps {{ columns:2; }} }}

/* ---- toolbar ---- */
.toolbar {{ position:sticky; top:0; z-index:20; display:flex; gap:8px;
  align-items:center; flex-wrap:wrap; padding:12px 0;
  background:var(--paper); border-bottom:1px solid var(--hair); }}
.chip {{ border:1px solid var(--ink); background:transparent; color:var(--muted);
  padding:5px 12px; font-family:var(--mono); font-size:10.5px;
  letter-spacing:.1em; text-transform:uppercase; user-select:none;
  display:inline-flex; align-items:center; gap:7px;
  border-color:var(--hair); transition:border-color .15s,color .15s,background .15s; }}
.chip .cdot {{ width:7px; height:7px; background:var(--faint); }}
.chip[data-f="safe"] .cdot {{ background:var(--safe); }}
.chip[data-f="review"] .cdot {{ background:var(--review); }}
.chip[data-f="keep"] .cdot {{ background:var(--keep); }}
.chip .ct {{ color:var(--faint); }}
.chip.active {{ color:var(--ink); border-color:var(--ink); }}
.chip:hover {{ color:var(--ink); }}
.search {{ flex:1; min-width:170px; max-width:330px; margin-left:auto;
  position:relative; }}
.search input {{ width:100%; background:transparent; border:1px solid var(--hair);
  color:var(--ink); padding:6px 12px 6px 31px; font-family:var(--mono);
  font-size:11.5px; }}
.search input::placeholder {{ color:var(--faint); }}
.search input:focus {{ outline:none; border-color:var(--ink); }}
.search svg {{ position:absolute; left:10px; top:50%; transform:translateY(-50%);
  color:var(--faint); pointer-events:none; }}
.tbtn {{ background:none; border:none; color:var(--muted); font-family:var(--mono);
  font-size:10.5px; letter-spacing:.1em; text-transform:uppercase;
  padding:5px 4px; transition:color .15s; }}
.tbtn:hover {{ color:var(--ink); }}

/* ---- agent sections ---- */
.findings {{ margin-top:30px; }}
.findings > .rulehead {{ border-top:2px solid var(--rule); padding-top:12px; }}
.findings > .rulehead h2 {{ font-family:var(--serif); font-size:20px;
  font-weight:700; letter-spacing:0; text-transform:none; }}
details.agent {{ border-bottom:1px solid var(--hair); }}
details.agent > summary {{ list-style:none; padding:16px 0;
  display:flex; align-items:center; gap:14px; }}
details.agent > summary::-webkit-details-marker {{ display:none; }}
summary:hover .t {{ text-decoration:underline; text-underline-offset:3px; }}
summary .ic {{ color:var(--ink); display:flex; flex:0 0 auto; }}
summary .t {{ font-family:var(--serif); font-weight:700; font-size:17px; }}
summary .status {{ font-family:var(--mono); font-size:9.5px; letter-spacing:.14em;
  text-transform:uppercase; padding:2px 7px; border:1px solid var(--hair);
  color:var(--muted); }}
summary .status.error, summary .status.timeout {{ color:var(--danger);
  border-color:var(--danger); }}
summary .s {{ color:var(--muted); font-size:12px; margin-left:auto;
  text-align:right; font-family:var(--mono); min-width:0; }}
summary .s b {{ color:var(--safe); font-weight:700; }}
summary .chev {{ color:var(--faint); transition:transform .15s; flex:0 0 auto; }}
details[open] summary .chev {{ transform:rotate(90deg); }}
.notes {{ margin:0 0 14px; padding:9px 14px; border-left:2px solid var(--ink);
  font-family:var(--serif); font-style:italic; font-size:13.5px;
  color:var(--muted); background:var(--panel); }}

/* ---- tables ---- */
table {{ width:100%; border-collapse:collapse; font-size:13px; margin-bottom:18px; }}
th,td {{ text-align:left; padding:10px 14px 10px 0; vertical-align:top;
  border-top:1px solid var(--hair); }}
td {{ overflow-wrap:anywhere; }}
th {{ font-family:var(--mono); color:var(--muted); font-weight:600;
  user-select:none; white-space:nowrap; font-size:10px; letter-spacing:.14em;
  text-transform:uppercase; border-top:0; }}
th:hover {{ color:var(--ink); }}
td.size {{ white-space:nowrap; width:180px; }}
td.size .n {{ font-family:var(--mono); font-size:12.5px; font-weight:600;
  display:inline-block; min-width:64px; }}
td:last-child {{ width:130px; }}
.path {{ color:var(--faint); font-size:11px; word-break:break-all;
  font-family:var(--mono); margin-top:3px; }}
.detail {{ color:var(--muted); font-size:12.5px; margin-top:3px; }}
.tag {{ font-family:var(--mono); font-size:10px; letter-spacing:.08em;
  text-transform:uppercase; white-space:nowrap; font-weight:600;
  display:inline-flex; align-items:center; gap:6px; }}
.tag-dot {{ width:7px; height:7px; background:currentColor; }}
.tag-safe {{ color:var(--safe); }}
.tag-review {{ color:var(--review); }}
.tag-keep {{ color:var(--keep); }}
.cmd {{ display:flex; gap:8px; align-items:center; margin-top:7px; flex-wrap:wrap; }}
.cmd code {{ min-width:120px; }}
code.sg {{ background:var(--panel); border:1px solid var(--hair);
  padding:4px 9px; font-size:11.5px; color:var(--ink); word-break:break-all; }}
button.copy {{ background:transparent; border:1px solid var(--hair);
  color:var(--muted); padding:4px 9px; font-family:var(--mono); font-size:10px;
  letter-spacing:.1em; text-transform:uppercase; flex:0 0 auto;
  display:inline-flex; align-items:center; gap:6px;
  transition:color .15s,border-color .15s; }}
button.copy:hover {{ color:var(--ink); border-color:var(--ink); }}
button.copy.done {{ color:var(--safe); border-color:var(--safe); }}

/* ---- footer ---- */
.disclaimer {{ display:flex; gap:13px; border-top:2px solid var(--rule);
  padding:16px 0 0; margin-top:34px; font-family:var(--serif); font-size:14px;
  color:var(--muted); line-height:1.6; }}
.disclaimer b {{ color:var(--ink); }}
.disclaimer svg {{ flex:0 0 auto; color:var(--ink); margin-top:2px; }}
.foot {{ margin-top:20px; font-family:var(--mono); font-size:10.5px;
  letter-spacing:.06em; color:var(--faint); }}
.foot code.sg {{ font-size:10.5px; padding:2px 6px; }}

@media (max-width:720px) {{
  .cards {{ grid-template-columns:1fr; }}
  .card + .card {{ border-left:0; border-top:1px solid var(--hair);
    padding-left:0; }}
  .headline .big {{ font-size:54px; }}
  .agentbars .row {{ grid-template-columns:120px 1fr 70px; }}
  td.size {{ width:110px; }}
  td:last-child {{ width:34px; }}
  .tag {{ font-size:0; gap:0; }}
  .tag .tag-dot {{ width:9px; height:9px; }}
  .opps .tag {{ display:none; }}
  summary .s {{ display:none; }}
}}
@media (prefers-reduced-motion: reduce) {{
  * {{ transition:none !important; }}
}}
@media print {{
  .toolbar, button.copy {{ display:none; }}
  .wrap {{ padding:0; }}
}}
</style></head><body><div class="wrap">"""


_SHIELD_SVG = ('<svg viewBox="0 0 24 24" width="17" height="17" fill="none" '
               'stroke="currentColor" stroke-width="1.6" stroke-linecap="round"'
               ' stroke-linejoin="round"><path d="M20 13c0 5-3.5 7.5-7.66 8.95'
               'a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5'
               '-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5'
               'a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/></svg>')


def _masthead(machine: dict, meta: dict) -> str:
    disk = machine.get("disk", {})
    facts = []
    if machine.get("hostname"):
        facts.append(f"Machine <b>{_e(machine['hostname'])}</b>")
    if machine.get("os"):
        facts.append(f"macOS <b>{_e(machine['os'])}</b>")
    if disk.get("size"):
        facts.append(f"Disk <b>{human_size(disk.get('used', 0))}</b> used of "
                     f"<b>{human_size(disk.get('size', 0))}</b>")
    facts.append(f"Scan <b>{'quick' if meta.get('quick') else 'thorough'}</b> "
                 f"in <b>{meta.get('duration_s', 0):.0f}s</b>")
    facts.append(f"<b>{_e(meta.get('generated_at', ''))}</b>")
    dateline = "".join(f"<span>{f}</span>" for f in facts)
    return f"""<header class="masthead">
  <div class="row1">
    <div class="wordmark">Diskovery<span class="dot">.</span></div>
    <div class="doc">Storage audit · read-only</div>
  </div>
  <div class="dateline">{dateline}</div>
</header>"""


def _headline(safe: int, review: int) -> str:
    n = human_size(safe).split(" ")
    big = f"{n[0]}<small> {n[1]}</small>" if len(n) == 2 else human_size(safe)
    return f"""<div class="headline">
  <div>
    <div class="k" style="margin-bottom:10px">Reclaimable now</div>
    <div class="big">{big}</div>
  </div>
  <p class="caption">can be freed today with the copy-paste commands in this
    report, <span class="and">and another</span> <b class="rev">{human_size(review)}</b>
    <span class="and">once you've had a look at what it is.</span></p>
</div>"""


def _disk_map(safe: int, review: int, machine: dict) -> str:
    """Full-width stacked bar of the whole disk: everything else / safe /
    review / free — the single most useful at-a-glance visual."""
    disk = machine.get("disk", {})
    size = disk.get("size", 0)
    used = disk.get("used", 0)
    avail = disk.get("avail", 0)
    if not size:
        return ""
    safe_c = min(safe, used)
    review_c = min(review, max(0, used - safe_c))
    other = max(0, used - safe_c - review_c)
    segs = [
        (other, "#4a4438", "In use"),
        (safe_c, "var(--safe)", "Safe to reclaim"),
        (review_c, "var(--review)", "Reclaim after review"),
        (avail, "var(--paper)", "Free"),
    ]
    seg_html = "".join(
        f'<div class="seg" style="width:{max(0.4, v / size * 100):.2f}%;'
        f'background:{bg}" title="{label}: {human_size(v)}"></div>'
        for v, bg, label in segs if v > 0
    )
    legend = f"""<div class="dlegend">
      <span><span class="swatch" style="background:#4a4438"></span>In use
        <b>{human_size(other)}</b></span>
      <span><span class="swatch" style="background:var(--safe)"></span>Safe to reclaim
        <b>{human_size(safe_c)}</b></span>
      <span><span class="swatch" style="background:var(--review)"></span>After review
        <b>{human_size(review_c)}</b></span>
      <span><span class="swatch" style="background:var(--paper)"></span>Free
        <b>{human_size(avail)}</b></span>
    </div>"""
    return f"""<div class="diskmap">
      <div class="rulehead"><h3>The disk, end to end</h3>
        <span class="aside">{human_size(size)} total</span></div>
      <div class="diskbar">{seg_html}</div>{legend}</div>"""


def _summary_charts(safe: int, review: int, by_agent) -> str:
    total = safe + review
    donut = _donut(safe, review)
    rows = []
    mx = max(((a.reclaimable_bytes + a.review_bytes) for a in by_agent),
             default=0)
    for a in by_agent[:8]:
        val = a.reclaimable_bytes + a.review_bytes
        w = 0 if mx <= 0 else val / mx * 100
        ws = 0 if val <= 0 else a.reclaimable_bytes / val * 100
        wr = 100 - ws
        rows.append(f"""<div class="row">
          <div class="name">{_icon(a.name, 15)}<span>{_e(a.title)}</span></div>
          <div class="stack" style="width:{max(2.0, w):.1f}%">
            <div class="s" style="width:{ws:.1f}%;background:var(--safe)"></div>
            <div class="s" style="width:{wr:.1f}%;background:var(--review)"></div>
          </div>
          <div class="val">{human_size(val)}</div></div>""")
    pct = 0 if total == 0 else round(safe / total * 100)
    return f"""<div class="cards">
  <div class="card">
    <div class="rulehead"><h3>Reclaimable space</h3></div>
    <div class="chart-row">
      <div>{donut}</div>
      <div class="legend">
        <div><span class="swatch" style="background:var(--safe)"></span>
          Safe to reclaim <b>{human_size(safe)}</b></div>
        <div><span class="swatch" style="background:var(--review)"></span>
          Needs review <b>{human_size(review)}</b></div>
        <div class="note">{pct}% of what was found needs no review at all.</div>
      </div>
    </div>
  </div>
  <div class="card">
    <div class="rulehead"><h3>Where it sits</h3>
      <span class="aside">safe + review, by agent</span></div>
    <div class="agentbars">{''.join(rows) or
      '<div style="color:var(--faint);font-size:13px">Nothing reclaimable found — the disk is tidy.</div>'}</div>
  </div>
</div>"""


def _donut(safe: int, review: int) -> str:
    total = safe + review
    r = 40
    circ = 2 * math.pi * r
    if total == 0:
        arcs = (f'<circle cx="55" cy="55" r="{r}" fill="none" '
                f'stroke="var(--track)" stroke-width="18"/>')
    else:
        safe_len = circ * safe / total
        arcs = (
            f'<circle cx="55" cy="55" r="{r}" fill="none" '
            f'stroke="var(--review)" stroke-width="18"/>'
            f'<circle cx="55" cy="55" r="{r}" fill="none" '
            f'stroke="var(--safe)" stroke-width="18" '
            f'stroke-dasharray="{safe_len:.1f} {circ:.1f}" '
            f'transform="rotate(-90 55 55)"/>')
    return f"""<svg width="124" height="124" viewBox="0 0 110 110" role="img"
      aria-label="Reclaimable space: safe versus needs review">{arcs}
      <circle cx="55" cy="55" r="30" fill="var(--paper)"/>
      <text x="55" y="53" text-anchor="middle" fill="#211d15" font-size="15"
        font-weight="700" font-family="ui-monospace,Menlo,monospace">{human_size(total)}</text>
      <text x="55" y="69" text-anchor="middle" fill="#98917f" font-size="8"
        letter-spacing="1.5" font-family="ui-monospace,Menlo,monospace">FOUND</text></svg>"""


def _opportunities(opps) -> str:
    if not opps:
        return ""
    items = []
    for i, (size, a, f) in enumerate(opps, 1):
        color = _SAFE_COLORS[f.safety.value]
        items.append(f"""<li>
          <span class="rank">{i:02d}</span>
          <span class="n" style="color:{color}">{human_size(size)}</span>
          <span class="what">{_e(f.label)}
            <span class="who">— {_e(a.title)}</span></span>
          {_tag(f.safety)}</li>""")
    return f"""<div class="opps-wrap">
      <div class="rulehead"><h3>Biggest wins first</h3>
        <span class="aside">top {len(opps)} findings by size</span></div>
      <ul class="opps">{''.join(items)}</ul></div>"""


def _toolbar(counts: dict) -> str:
    def chip(f, label):
        ct = counts.get(f, 0)
        n = f'<span class="ct">{ct}</span>' if f != "all" else ""
        dot = '<span class="cdot"></span>' if f != "all" else ""
        return (f'<button class="chip active" data-f="{f}" onclick="flt(this)">'
                f'{dot}{label}{n}</button>')
    return f"""<div class="toolbar">
      {chip('all', 'All')}
      {chip('safe', 'Safe')}
      {chip('review', 'Review')}
      {chip('keep', 'Keep')}
      <div class="search">{_SEARCH_SVG}
        <input id="q" type="search" placeholder="search findings…"
          oninput="src(this.value)" aria-label="Search findings"></div>
      <button class="tbtn" onclick="tgl(true)">Expand</button>
      <button class="tbtn" onclick="tgl(false)">Collapse</button>
    </div>"""


def _agent_section(a: AgentResult) -> str:
    status_label = _STATUS_LABEL.get(a.status, a.status)
    reclaim = a.reclaimable_bytes + a.review_bytes
    if a.status == "ok":
        right = _e(a.summary)
        if reclaim > 0:
            right = f"<b>{human_size(reclaim)}</b> · {right}"
    else:
        right = _e(a.error or a.summary)
    notes_html = ""
    if a.notes:
        notes_html = ('<div class="notes">'
                      + "<br>".join(_e(n) for n in a.notes) + "</div>")

    rows = []
    mx = max((f.size_bytes for f in a.findings), default=0)
    for f in a.findings:
        color = _SAFE_COLORS[f.safety.value]
        sug = ""
        if f.suggestion:
            sug = f"""<div class="cmd"><code class="sg">{_e(f.suggestion)}</code>
              <button class="copy" onclick="cp(this)" aria-label="Copy command">
                {_COPY_SVG}Copy</button></div>"""
        path = f'<div class="path">{_e(f.path)}</div>' if f.path else ""
        detail = f'<div class="detail">{_e(f.detail)}</div>' if f.detail else ""
        rows.append(f"""<tr data-safety="{f.safety.value}" data-size="{f.size_bytes}">
          <td><b>{_e(f.label)}</b>{path}{detail}{sug}</td>
          <td class="size"><span class="n">{human_size(f.size_bytes)}</span>{_bar(f.size_bytes, mx, color)}</td>
          <td>{_tag(f.safety)}</td></tr>""")

    if a.findings:
        body = f"""{notes_html}<table>
          <thead><tr>
            <th data-sort onclick="srt(this,0,'s')">Item</th>
            <th data-sort onclick="srt(this,1,'n')">Size</th>
            <th data-sort onclick="srt(this,2,'s')">Safety</th>
          </tr></thead><tbody>{''.join(rows)}</tbody></table>"""
    else:
        body = notes_html or ('<div class="notes">Nothing notable here — '
                              'this corner of the Mac is already tidy.</div>')

    return f"""<details class="agent" {'open' if a.findings else ''}>
      <summary>
        <span class="ic">{_icon(a.name)}</span>
        <span class="t">{_e(a.title)}</span>
        <span class="status {a.status}">{status_label}</span>
        <span class="s">{right}</span>
        <span class="chev">{_icon_chev()}</span>
      </summary>{body}</details>"""


def _icon_chev() -> str:
    return ('<svg viewBox="0 0 24 24" width="14" height="14" fill="none" '
            'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            'stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>')


def _footer(meta: dict) -> str:
    return f"""<div class="disclaimer">{_SHIELD_SVG}
      <div><b>Diskovery never deletes anything.</b> This report is read-only —
      every command above is a suggestion for <i>you</i> to review and run.
      Sizes are on-disk estimates and categories can overlap slightly, so treat
      totals as a well-informed guide rather than an exact figure.</div>
    </div>
    <div class="foot">
      Diskovery v{_e(meta.get('version', ''))} ·
      {_e(meta.get('generated_at', ''))} ·
      {'quick' if meta.get('quick') else 'thorough'} scan in {meta.get('duration_s', 0):.1f}s ·
      re-run with <code class="sg">python3 -m diskovery</code>
    </div>"""


def _script() -> str:
    return """<script>
function cp(btn){
  var code = btn.parentElement.querySelector('code').innerText;
  navigator.clipboard.writeText(code).then(function(){
    btn.classList.add('done');
    var svg = btn.querySelector('svg').outerHTML;
    btn.innerHTML = svg + 'Copied';
    setTimeout(function(){ btn.classList.remove('done');
      btn.innerHTML = svg + 'Copy'; }, 1400);
  });
}
function srt(th,col,type){
  var tb=th.closest('table').querySelector('tbody');
  var rows=[].slice.call(tb.rows);
  var asc=th._asc=!th._asc;
  rows.sort(function(a,b){
    if(type==='n'){
      var x=+a.getAttribute('data-size'), y=+b.getAttribute('data-size');
      return asc?x-y:y-x;
    }
    var x=a.cells[col].innerText.toLowerCase(), y=b.cells[col].innerText.toLowerCase();
    return asc?x.localeCompare(y):y.localeCompare(x);
  });
  rows.forEach(function(r){tb.appendChild(r);});
}
var active={all:true,safe:true,review:true,keep:true};
var query='';
function apply(){
  document.querySelectorAll('tr[data-safety]').forEach(function(r){
    var okSafety = active[r.getAttribute('data-safety')];
    var okQuery = !query || r.innerText.toLowerCase().indexOf(query) !== -1;
    r.style.display = (okSafety && okQuery) ? '' : 'none';
  });
}
function flt(chip){
  var f=chip.getAttribute('data-f');
  if(f==='all'){
    var on=!chip.classList.contains('active')||Object.keys(active).some(function(k){return k!=='all'&&!active[k];});
    ['all','safe','review','keep'].forEach(function(k){active[k]=on;});
  } else {
    active[f]=!active[f];
    active.all=active.safe&&active.review&&active.keep;
  }
  document.querySelectorAll('.chip').forEach(function(c){
    var cf=c.getAttribute('data-f'); c.classList.toggle('active',active[cf]);
  });
  apply();
}
function src(v){ query=v.trim().toLowerCase(); apply();
  if(query) tgl(true); }
function tgl(open){
  document.querySelectorAll('details.agent').forEach(function(d){ d.open=open; });
}
</script>"""
