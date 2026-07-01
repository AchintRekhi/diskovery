"""Render aggregated agent results into a self-contained HTML report + JSON.

No templating library, no CDN assets: all CSS/JS/SVG is inlined so the report
is a single portable file that opens offline anywhere. Collapsible sections use
native <details>/<summary> (works without JS and prints cleanly); a little JS
adds table sorting, safety filtering and copy-to-clipboard for suggestions.
"""

from __future__ import annotations

import html
import json
from typing import List

from .core import AgentResult, Safety, human_size

_SAFE_COLORS = {"safe": "#3fb950", "review": "#d29922", "keep": "#8b949e"}
_SAFE_LABEL = {"safe": "Safe to reclaim", "review": "Review first", "keep": "Keep"}


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

    # Reclaimable by agent (safe+review) for the bar chart.
    by_agent = [
        (a, a.reclaimable_bytes + a.review_bytes)
        for a in results if (a.reclaimable_bytes + a.review_bytes) > 0
    ]
    by_agent.sort(key=lambda t: t[1], reverse=True)

    parts: List[str] = []
    parts.append(_head(meta))
    parts.append(_hero(safe, review, machine, meta))
    parts.append(_summary_charts(safe, review, by_agent))
    parts.append(_opportunities(opportunities[:15]))
    parts.append(_filter_bar())
    for a in results:
        parts.append(_agent_section(a))
    parts.append(_footer(meta))
    parts.append(_script())
    parts.append("</body></html>")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _bar(size: int, max_size: int, color: str) -> str:
    pct = 0 if max_size <= 0 else max(1.5, min(100, size / max_size * 100))
    return (f'<div class="bar"><div class="bar-fill" style="width:{pct:.1f}%;'
            f'background:{color}"></div></div>')


def _tag(safety: Safety) -> str:
    v = safety.value
    return (f'<span class="tag tag-{v}" title="{_SAFE_LABEL[v]}">'
            f'{_SAFE_LABEL[v]}</span>')


def _head(meta: dict) -> str:
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Diskovery report — {_e(meta.get('machine', {}).get('hostname', 'this Mac'))}</title>
<style>
:root {{
  --bg:#0d1117; --panel:#161b22; --panel2:#1c2230; --border:#30363d;
  --text:#e6edf3; --muted:#8b949e; --accent:#58a6ff;
  --safe:#3fb950; --review:#d29922; --keep:#8b949e;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text);
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; }}
a {{ color:var(--accent); }}
.wrap {{ max-width:1080px; margin:0 auto; padding:28px 20px 80px; }}
.hero {{ display:flex; flex-wrap:wrap; gap:20px; align-items:flex-end;
  justify-content:space-between; border-bottom:1px solid var(--border); padding-bottom:20px; }}
.hero h1 {{ margin:0; font-size:26px; letter-spacing:-.5px; }}
.hero .logo {{ color:var(--accent); }}
.hero .sub {{ color:var(--muted); font-size:13px; margin-top:4px; }}
.headline {{ text-align:right; }}
.headline .big {{ font-size:34px; font-weight:700; color:var(--safe); }}
.headline .big.rev {{ color:var(--review); font-size:20px; font-weight:600; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
  gap:14px; margin:22px 0; }}
.card {{ background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:16px; }}
.card h3 {{ margin:0 0 10px; font-size:12px; text-transform:uppercase; letter-spacing:.6px;
  color:var(--muted); font-weight:600; }}
.chart-row {{ display:flex; gap:18px; align-items:center; }}
.donut {{ flex:0 0 auto; }}
.legend {{ font-size:13px; }}
.legend div {{ margin:3px 0; }}
.legend .dot {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:7px; }}
.agentbars .row {{ display:grid; grid-template-columns:150px 1fr 92px; gap:10px;
  align-items:center; margin:7px 0; font-size:13px; }}
.agentbars .name {{ color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.agentbars .val {{ text-align:right; font-variant-numeric:tabular-nums; }}
.bar {{ background:#0b0f16; border-radius:6px; height:12px; overflow:hidden; }}
.bar-fill {{ height:100%; border-radius:6px; }}
.section {{ margin:26px 0; }}
.section h2 {{ font-size:16px; border-bottom:1px solid var(--border); padding-bottom:8px; }}
details.agent {{ background:var(--panel); border:1px solid var(--border);
  border-radius:12px; margin:12px 0; overflow:hidden; }}
details.agent > summary {{ list-style:none; cursor:pointer; padding:15px 18px;
  display:flex; align-items:center; gap:12px; }}
details.agent > summary::-webkit-details-marker {{ display:none; }}
summary .ic {{ font-size:20px; }}
summary .t {{ font-weight:600; font-size:15px; }}
summary .s {{ color:var(--muted); font-size:13px; margin-left:auto; text-align:right; }}
summary .chev {{ color:var(--muted); transition:transform .15s; }}
details[open] summary .chev {{ transform:rotate(90deg); }}
.status {{ font-size:11px; padding:2px 8px; border-radius:20px; border:1px solid var(--border); }}
.status.ok {{ color:var(--safe); }} .status.skipped {{ color:var(--muted); }}
.status.error,.status.timeout {{ color:#f85149; }}
.notes {{ margin:0 18px 12px; padding:10px 14px; background:var(--panel2);
  border-left:3px solid var(--accent); border-radius:6px; font-size:13px; color:var(--muted); }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th,td {{ text-align:left; padding:9px 18px; border-top:1px solid var(--border); vertical-align:top; }}
th {{ color:var(--muted); font-weight:600; cursor:pointer; user-select:none; white-space:nowrap; }}
th:hover {{ color:var(--text); }}
td.size {{ font-variant-numeric:tabular-nums; white-space:nowrap; width:200px; }}
td.size .n {{ display:inline-block; min-width:66px; }}
.path {{ color:var(--muted); font-size:12px; word-break:break-all; }}
.tag {{ font-size:11px; padding:2px 9px; border-radius:20px; white-space:nowrap; font-weight:600; }}
.tag-safe {{ background:rgba(63,185,80,.15); color:var(--safe); }}
.tag-review {{ background:rgba(210,153,34,.15); color:var(--review); }}
.tag-keep {{ background:rgba(139,148,158,.14); color:var(--keep); }}
.cmd {{ display:flex; gap:8px; align-items:center; margin-top:6px; }}
code.sg {{ background:#0b0f16; border:1px solid var(--border); border-radius:6px;
  padding:3px 8px; font-size:12px; color:#e6edf3; word-break:break-all; }}
button.copy {{ background:var(--panel2); border:1px solid var(--border); color:var(--muted);
  border-radius:6px; padding:3px 9px; font-size:11px; cursor:pointer; }}
button.copy:hover {{ color:var(--text); }}
.filters {{ display:flex; gap:8px; align-items:center; margin:18px 0; flex-wrap:wrap; }}
.chip {{ border:1px solid var(--border); background:var(--panel); color:var(--muted);
  border-radius:20px; padding:5px 14px; font-size:12px; cursor:pointer; }}
.chip.active {{ color:var(--text); border-color:var(--accent); }}
.opps li {{ margin:6px 0; display:flex; gap:10px; align-items:center; }}
.opps .n {{ font-variant-numeric:tabular-nums; min-width:74px; color:var(--safe); font-weight:600; }}
.foot {{ margin-top:40px; padding-top:18px; border-top:1px solid var(--border);
  color:var(--muted); font-size:12px; }}
.disclaimer {{ background:rgba(210,153,34,.08); border:1px solid rgba(210,153,34,.3);
  border-radius:10px; padding:12px 16px; margin:20px 0; font-size:13px; }}
</style></head><body><div class="wrap">"""


def _hero(safe: int, review: int, machine: dict, meta: dict) -> str:
    disk = machine.get("disk", {})
    disk_line = ""
    if disk.get("size"):
        disk_line = (f"{human_size(disk.get('used', 0))} used · "
                     f"{human_size(disk.get('avail', 0))} free of "
                     f"{human_size(disk.get('size', 0))}")
    return f"""<div class="hero">
  <div>
    <h1><span class="logo">◆ Diskovery</span></h1>
    <div class="sub">{_e(machine.get('hostname',''))} · macOS {_e(machine.get('os',''))}
      · {_e(disk_line)}</div>
    <div class="sub">Generated {_e(meta.get('generated_at',''))}
      · {'quick' if meta.get('quick') else 'thorough'} scan
      · {meta.get('duration_s',0):.0f}s</div>
  </div>
  <div class="headline">
    <div class="big">{human_size(safe)}</div>
    <div class="sub">safe to reclaim now</div>
    <div class="big rev">+ {human_size(review)}</div>
    <div class="sub">after review</div>
  </div>
</div>"""


def _summary_charts(safe: int, review: int, by_agent) -> str:
    total = safe + review
    donut = _donut(safe, review)
    rows = []
    mx = by_agent[0][1] if by_agent else 0
    for a, val in by_agent[:8]:
        rows.append(f"""<div class="row"><div class="name">{a.icon} {_e(a.title)}</div>
          {_bar(val, mx, '#58a6ff')}<div class="val">{human_size(val)}</div></div>""")
    return f"""<div class="cards">
  <div class="card">
    <h3>Reclaimable breakdown</h3>
    <div class="chart-row">
      <div class="donut">{donut}</div>
      <div class="legend">
        <div><span class="dot" style="background:var(--safe)"></span>
          Safe · <b>{human_size(safe)}</b></div>
        <div><span class="dot" style="background:var(--review)"></span>
          Review · <b>{human_size(review)}</b></div>
        <div style="color:var(--muted);margin-top:6px">Total opportunity
          <b>{human_size(total)}</b></div>
      </div>
    </div>
  </div>
  <div class="card">
    <h3>Reclaimable by agent</h3>
    <div class="agentbars">{''.join(rows) or '<div class="sub">Nothing reclaimable found.</div>'}</div>
  </div>
</div>"""


def _donut(safe: int, review: int) -> str:
    total = safe + review
    r = 42
    import math
    circ = 2 * math.pi * r
    safe_len = 0 if total == 0 else circ * safe / total
    seg = f"""<circle cx="55" cy="55" r="{r}" fill="none" stroke="#21262d" stroke-width="14"/>
      <circle cx="55" cy="55" r="{r}" fill="none" stroke="var(--review)" stroke-width="14"
        stroke-dasharray="{circ:.1f}" transform="rotate(-90 55 55)"/>
      <circle cx="55" cy="55" r="{r}" fill="none" stroke="var(--safe)" stroke-width="14"
        stroke-dasharray="{safe_len:.1f} {circ:.1f}" transform="rotate(-90 55 55)"/>"""
    pct = 0 if total == 0 else round(safe / total * 100)
    return f"""<svg width="110" height="110" viewBox="0 0 110 110">{seg}
      <text x="55" y="52" text-anchor="middle" fill="#e6edf3" font-size="20"
        font-weight="700">{pct}%</text>
      <text x="55" y="70" text-anchor="middle" fill="#8b949e" font-size="10">safe</text></svg>"""


def _opportunities(opps) -> str:
    if not opps:
        return ""
    items = []
    for size, a, f in opps:
        items.append(f"""<li><span class="n">{human_size(size)}</span>
          <span>{a.icon} {_e(f.label)}</span> {_tag(f.safety)}</li>""")
    return f"""<div class="card" style="margin-bottom:22px">
      <h3>Biggest opportunities</h3>
      <ul class="opps" style="list-style:none;padding:0;margin:0">{''.join(items)}</ul></div>"""


def _filter_bar() -> str:
    return """<div class="filters">
      <span style="color:var(--muted);font-size:12px">Show:</span>
      <span class="chip active" data-f="all" onclick="flt(this)">All</span>
      <span class="chip active" data-f="safe" onclick="flt(this)">Safe</span>
      <span class="chip active" data-f="review" onclick="flt(this)">Review</span>
      <span class="chip active" data-f="keep" onclick="flt(this)">Keep</span>
    </div>"""


def _agent_section(a: AgentResult) -> str:
    status_txt = a.status
    summary_right = _e(a.summary) if a.status == "ok" else _e(a.error or a.summary)
    notes_html = ""
    if a.notes:
        notes_html = '<div class="notes">' + "<br>".join(_e(n) for n in a.notes) + "</div>"

    rows = []
    mx = max((f.size_bytes for f in a.findings), default=0)
    for f in a.findings:
        color = _SAFE_COLORS[f.safety.value]
        sug = ""
        if f.suggestion:
            sug = f"""<div class="cmd"><code class="sg">{_e(f.suggestion)}</code>
              <button class="copy" onclick="cp(this)">copy</button></div>"""
        path = f'<div class="path">{_e(f.path)}</div>' if f.path else ""
        rows.append(f"""<tr data-safety="{f.safety.value}" data-size="{f.size_bytes}">
          <td><b>{_e(f.label)}</b>{path}
            <div style="color:var(--muted);font-size:12px;margin-top:3px">{_e(f.detail)}</div>{sug}</td>
          <td class="size"><span class="n">{human_size(f.size_bytes)}</span>{_bar(f.size_bytes, mx, color)}</td>
          <td>{_tag(f.safety)}</td></tr>""")

    body = ""
    if a.findings:
        body = f"""{notes_html}<table>
          <thead><tr>
            <th onclick="srt(this,0,'s')">Item</th>
            <th onclick="srt(this,1,'n')">Size</th>
            <th onclick="srt(this,2,'s')">Safety</th>
          </tr></thead><tbody>{''.join(rows)}</tbody></table>"""
    else:
        body = notes_html or '<div class="notes">Nothing notable found.</div>'

    return f"""<details class="agent" {'open' if a.findings else ''}>
      <summary>
        <span class="ic">{a.icon}</span>
        <span class="t">{_e(a.title)}</span>
        <span class="status {status_txt}">{status_txt}</span>
        <span class="s">{summary_right}</span>
        <span class="chev">▸</span>
      </summary>{body}</details>"""


def _footer(meta: dict) -> str:
    return f"""<div class="disclaimer">
      <b>Read-only report.</b> Diskovery never deletes or modifies anything.
      Every command shown is a suggestion for <i>you</i> to run after reviewing.
      Sizes are on-disk estimates and totals may include some overlap between
      categories — treat them as guidance, not exact figures.
    </div>
    <div class="foot">
      Generated by Diskovery v{_e(meta.get('version',''))} ·
      {_e(meta.get('generated_at',''))} ·
      {'quick' if meta.get('quick') else 'thorough'} scan in {meta.get('duration_s',0):.1f}s.
      Re-run any time with <code class="sg">python3 -m diskovery</code>.
    </div>"""


def _script() -> str:
    return """<script>
function cp(btn){
  var code = btn.previousElementSibling.innerText;
  navigator.clipboard.writeText(code).then(function(){
    var o=btn.innerText; btn.innerText='copied'; setTimeout(function(){btn.innerText=o;},1200);
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
function flt(chip){
  var f=chip.getAttribute('data-f');
  if(f==='all'){
    var on=!chip.classList.contains('active')||Object.keys(active).some(function(k){return k!=='all'&&!active[k];});
    ['all','safe','review','keep'].forEach(function(k){active[k]=on;});
  } else {
    active[f]=!active[f];
  }
  document.querySelectorAll('.chip').forEach(function(c){
    var cf=c.getAttribute('data-f'); c.classList.toggle('active',active[cf]);
  });
  document.querySelectorAll('tr[data-safety]').forEach(function(r){
    r.style.display=active[r.getAttribute('data-safety')]?'':'none';
  });
}
</script>"""
