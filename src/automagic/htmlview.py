"""Self-contained HTML view of the work item DAG with dependency-aware selection."""

from __future__ import annotations

import json

from automagic.merkle import Graph
from automagic.runner import RunState
from automagic.sizing import WorkItem


def view_data(graph: Graph, items: list[WorkItem], state: RunState | None) -> dict:
    def run_of(item: WorkItem):
        return state.items.get(item.id) if state else None

    return {
        "goal": graph.goal,
        "rootHash": graph.root_hash(),
        "stale": bool(state and state.root_hash != graph.root_hash()),
        "items": [
            {
                "id": item.id,
                "dependsOn": item.depends_on,
                "status": run.status if (run := run_of(item)) else "pending",
                "detail": run.detail if run else "",
                "oversized": item.oversized,
                "tokens": item.tokens,
                "lines": item.lines,
                "nodes": [
                    {
                        "id": nid,
                        "title": graph.nodes[nid].title,
                        "content": graph.nodes[nid].content,
                        "files": graph.nodes[nid].files,
                        "hash": graph.node_hash(nid),
                    }
                    for nid in item.node_ids
                ],
            }
            for item in items
        ],
    }


def render_html(graph: Graph, items: list[WorkItem], state: RunState | None) -> str:
    # "</" is escaped so node text can never close the script element.
    data = json.dumps(view_data(graph, items, state)).replace("</", "<\\/")
    return _PAGE.replace("__DATA__", data)


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>automagic plan</title>
<style>
body{font:14px system-ui,sans-serif;margin:1.5rem;color:#222}
h1{font-size:1.2rem;margin:0}.meta{color:#666;margin:.2rem 0 1rem;font-size:12px}
.warn{background:#fff3cd;border:1px solid #e0c36a;padding:.5rem;margin-bottom:1rem}
.layout{display:flex;flex-wrap:wrap;gap:1rem;align-items:flex-start}
.graph{flex:1 1 300px;overflow:auto;min-width:0}
#panel{flex:0 0 340px;max-width:100%;position:sticky;top:1rem;max-height:90vh;overflow:auto}
.card{border:1px solid #bbb;border-radius:6px;padding:.5rem;background:#fff}
.card.explicit{border-color:#1a73e8;box-shadow:0 0 0 2px #cfe0fb}
.card.auto{border-color:#1a73e8;border-style:dashed;background:#f3f8fe}
.card.done{background:#eef7ee;border-color:#7bb77b}
.card.failed,.card.blocked{border-color:#d33}
.edge{fill:none;stroke:#bbb;stroke-width:1.5}
.edge.on{stroke:#1a73e8;stroke-width:2}
.dot{fill:#fff;stroke:#999;stroke-width:2;cursor:pointer}
.dot.auto{fill:#f3f8fe;stroke:#1a73e8;stroke-dasharray:5 3}
.dot.explicit{fill:#e3eefc;stroke:#1a73e8}
.dot.done{fill:#dff0df;stroke:#7bb77b;cursor:default}
.dot.failed,.dot.blocked{stroke:#d33}
.dot.running{stroke:#e0a100}
.halo{fill:none;stroke:#cfe0fb;stroke-width:4}
.lbl{font:600 12px system-ui,sans-serif;text-anchor:middle;pointer-events:none}
.wave{fill:#1a73e8}.wavetxt{font:600 10px system-ui,sans-serif;fill:#fff;text-anchor:middle;
pointer-events:none}
g.n:focus{outline:none}g.n:focus .dot{stroke-width:3.5}
.badge{font-size:11px;padding:1px 6px;border-radius:8px;background:#eee;margin-left:.4rem}
.small{color:#666;font-size:12px}.node{margin:.3rem 0 0 1.2rem}
pre{white-space:pre-wrap;margin:.2rem 0}
#out{margin-top:.3rem;padding:.6rem;background:#111;color:#eee;font-family:ui-monospace,monospace;
overflow-x:auto}
button{margin-right:.5rem}
</style></head><body>
<h1 id="goal"></h1><div class="meta" id="meta"></div><div id="stale"></div>
<p class="small">Tick a work item: its unfinished prerequisites are selected automatically
(dashed). Finished items are never selected. Run the command below to create the
worktrees and start the agents.</p>
<div><button id="all">Select all remaining</button><button id="none">Clear</button></div>
<div class="layout"><div class="graph" id="graph"></div><div id="panel"></div></div>
<div class="small" style="margin-top:1rem">Command, in dependency order</div>
<div id="out"></div><button id="copy" style="margin-top:.5rem">Copy command</button>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const byId = Object.fromEntries(D.items.map(i => [i.id, i]));
const explicit = new Set();
const isDone = id => byId[id].status === 'done';

function closure() {
  const out = new Set(), stack = [...explicit];
  while (stack.length) {
    const id = stack.pop();
    if (out.has(id) || isDone(id)) continue;
    out.add(id);
    stack.push(...byId[id].dependsOn);
  }
  return out;
}
function waves(sel) {
  const rem = new Map([...sel].map(id => [id, byId[id].dependsOn.filter(d => sel.has(d))]));
  const placed = new Set(), out = [];
  while (rem.size) {
    const ready = [...rem].filter(([, ds]) => ds.every(d => placed.has(d)))
      .map(([id]) => id).sort();
    if (!ready.length) break;
    out.push(ready);
    ready.forEach(id => { placed.add(id); rem.delete(id); });
  }
  return out;
}
const level = {};
function lvl(id) {
  if (!(id in level)) level[id] = byId[id].dependsOn.length
    ? 1 + Math.max(...byId[id].dependsOn.map(lvl)) : 0;
  return level[id];
}
function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
const NS = 'http://www.w3.org/2000/svg';
function sv(tag, attrs, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  if (parent) parent.append(e);
  return e;
}
const R = 30, DX = 130, DY = 90, PAD = 44;
let pinned = null, refocus = null;
function toggle(id) {
  if (isDone(id)) return;
  if (explicit.has(id)) explicit.delete(id); else explicit.add(id);
  pinned = refocus = id;
  render();
}
function renderPanel(item, state, sel, waveOf) {
  const panel = document.getElementById('panel');
  panel.replaceChildren();
  if (!item) {
    panel.append(el('div', 'Hover or click a circle to see its work item.', 'small'));
    return;
  }
  const card = el('div', undefined, 'card ' + state + ' ' + item.status);
  const head = el('label');
  const box = el('input');
  box.type = 'checkbox';
  box.checked = sel.has(item.id);
  box.disabled = isDone(item.id);
  box.onchange = () => toggle(item.id);
  head.append(box, el('strong', ' ' + item.id), el('span', item.status, 'badge'));
  if (state === 'auto') head.append(el('span', 'auto-added', 'badge'));
  if (waveOf[item.id]) head.append(el('span', 'wave ' + waveOf[item.id], 'badge'));
  card.append(head);
  const needs = item.dependsOn.length
    ? 'needs ' + item.dependsOn.join(', ') : 'no prerequisites';
  card.append(el('div', needs + ' | ~' + item.lines + ' lines', 'small'));
  if (item.status === 'failed' || item.status === 'blocked')
    card.append(el('div', (item.detail || item.status) + '; selecting it retries it', 'small'));
  if (item.oversized) card.append(el('div', 'oversized work item', 'small'));
  for (const n of item.nodes) {
    const d = el('details', undefined, 'node');
    d.append(el('summary', n.title + ' (' + n.hash.slice(0, 8) + ')'));
    d.append(el('pre', n.content));
    if (n.files.length) d.append(el('div', 'files: ' + n.files.join(', '), 'small'));
    card.append(d);
  }
  panel.append(card);
}
function render() {
  const sel = closure(), ws = waves(sel), waveOf = {};
  ws.forEach((w, n) => w.forEach(id => { waveOf[id] = n + 1; }));
  const depth = Math.max(0, ...D.items.map(i => lvl(i.id)));
  const pos = {}, rows = {};
  for (const item of D.items) {
    const l = lvl(item.id), r = rows[l] = (rows[l] ?? -1) + 1;
    pos[item.id] = [PAD + l * DX, PAD + r * DY];
  }
  const maxRow = Math.max(0, ...Object.values(rows));
  const width = 2 * PAD + depth * DX, height = 2 * PAD + maxRow * DY;
  const svg = sv('svg', { width, height, viewBox: '0 0 ' + width + ' ' + height });
  const defs = sv('defs', {}, svg);
  for (const [name, color] of [['off', '#bbb'], ['on', '#1a73e8']]) {
    const m = sv('marker', { id: 'arrow-' + name, viewBox: '0 0 10 10', refX: 9, refY: 5,
      markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, defs);
    sv('path', { d: 'M0 0L10 5L0 10z', fill: color }, m);
  }
  for (const item of D.items) for (const d of item.dependsOn) {
    const [x1, y1] = pos[d], [x2, y2] = pos[item.id], mx = (x1 + x2) / 2;
    const on = sel.has(d) && sel.has(item.id);
    sv('path', { class: 'edge' + (on ? ' on' : ''),
      d: 'M' + (x1 + R) + ' ' + y1 + 'C' + mx + ' ' + y1 + ' ' + mx + ' ' + y2 + ' '
        + (x2 - R - 2) + ' ' + y2,
      'marker-end': 'url(#arrow-' + (on ? 'on' : 'off') + ')' }, svg);
  }
  const shown = byId[pinned] ? pinned : null;
  let shownState = '';
  for (const item of D.items) {
    const state = isDone(item.id) ? 'done'
      : explicit.has(item.id) ? 'explicit' : sel.has(item.id) ? 'auto' : '';
    if (item.id === shown) shownState = state;
    const [x, y] = pos[item.id];
    const g = sv('g', { class: 'n', 'data-id': item.id, tabindex: 0, role: 'checkbox',
      'aria-checked': sel.has(item.id), 'aria-disabled': isDone(item.id),
      'aria-label': item.id + ', ' + item.status }, svg);
    sv('title', {}, g).textContent = item.id + ' (' + item.status + ')';
    if (state === 'explicit') sv('circle', { class: 'halo', cx: x, cy: y, r: R + 4 }, g);
    sv('circle', { class: 'dot ' + state + ' ' + item.status, cx: x, cy: y, r: R }, g);
    sv('text', { class: 'lbl', x, y: y + 4 }, g).textContent = item.id;
    if (waveOf[item.id]) {
      sv('circle', { class: 'wave', cx: x + R * 0.75, cy: y - R * 0.75, r: 9 }, g);
      sv('text', { class: 'wavetxt', x: x + R * 0.75, y: y - R * 0.75 + 3.5 }, g)
        .textContent = waveOf[item.id];
    }
    const show = () => {
      if (pinned !== item.id) { pinned = item.id; renderPanel(item, state, sel, waveOf); }
    };
    g.onmouseenter = show;
    g.onfocus = show;
    g.onclick = () => toggle(item.id);
    g.onkeydown = e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(item.id); }
    };
  }
  document.getElementById('graph').replaceChildren(svg);
  if (refocus) svg.querySelector('g[data-id="' + refocus + '"]')?.focus();
  refocus = null;
  renderPanel(shown ? byId[shown] : null, shownState, sel, waveOf);
  const ids = ws.flat();
  document.getElementById('out').textContent = ids.length
    ? 'automagic run --only ' + ids.join(',') : '# nothing selected';
}
document.getElementById('goal').textContent = D.goal;
document.getElementById('meta').textContent = 'root hash ' + D.rootHash;
if (D.stale) document.getElementById('stale').append(
  el('div', 'The saved run is stale: the plan changed since it started. Use run --restart.',
    'warn'));
document.getElementById('all').onclick = () => {
  D.items.filter(i => !isDone(i.id)).forEach(i => explicit.add(i.id)); render();
};
document.getElementById('none').onclick = () => { explicit.clear(); render(); };
document.getElementById('copy').onclick = () =>
  navigator.clipboard.writeText(document.getElementById('out').textContent);
render();
</script></body></html>
"""
