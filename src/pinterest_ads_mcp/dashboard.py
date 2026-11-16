"""A self-contained HTML performance report: KPIs, trends, campaigns, creatives.

One file, no network, no dependencies: the data is embedded as JSON and the
charts are drawn as inline SVG by a small script. It opens in any browser,
follows the system light/dark setting, and can be shared or published as is.
"""

from __future__ import annotations

import html
import json
from datetime import datetime

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series: #2a78d6; --series-wash: rgba(42,120,214,0.10); --context: #c3c2b7;
  --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b;
  --good-text: #006300; --brand: #E60023;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --series: #3987e5; --series-wash: rgba(57,135,229,0.12); --context: #52514e;
    --good-text: #0ca30c;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --series: #3987e5; --series-wash: rgba(57,135,229,0.12); --context: #52514e;
  --good-text: #0ca30c;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1120px; margin: 0 auto; padding: 32px 16px 64px; }
header { display: flex; flex-wrap: wrap; gap: 8px 24px; align-items: baseline;
  justify-content: space-between; margin-bottom: 24px; }
h1 { font-size: 22px; font-weight: 650; margin: 0; letter-spacing: -0.01em; }
h1 .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
  background: var(--brand); margin-right: 10px; transform: translateY(-1px); }
.meta { color: var(--ink-2); font-size: 13px; }
h2 { font-size: 15px; font-weight: 650; margin: 0 0 2px; }
.sub { color: var(--muted); font-size: 12.5px; margin: 0 0 14px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  padding: 20px; margin-bottom: 16px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.grid2 > .card { margin-bottom: 0; }
.grid2 { margin-bottom: 16px; }
@media (max-width: 760px) { .grid2 { grid-template-columns: 1fr; } }

.hero { display: flex; flex-wrap: wrap; gap: 24px 48px; align-items: center; }
.hero-figure .label { color: var(--ink-2); font-size: 13px; }
.hero-figure .value { font-size: 56px; font-weight: 650; line-height: 1.05; letter-spacing: -0.02em; }
.hero-figure .note { color: var(--ink-2); font-size: 13px; margin-top: 4px; }
.tiles { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px;
  flex: 1 1 560px; }
@media (max-width: 560px) { .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.tile { border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }
.tile .label { color: var(--ink-2); font-size: 12.5px; }
.tile .value { font-size: 22px; font-weight: 650; letter-spacing: -0.01em; }
.tile .delta { font-size: 12px; color: var(--ink-2); }
.tile svg { display: block; margin-top: 6px; }
.up-good { color: var(--good-text); } .down-bad { color: var(--critical); }

.insights { margin: 0; padding-left: 18px; }
.insights li { margin: 4px 0; }

.chart { position: relative; }
.chart svg { display: block; width: 100%; height: auto; overflow: visible; }
.tick { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.tip { position: absolute; pointer-events: none; background: var(--surface); color: var(--ink);
  border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-size: 12.5px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.12); white-space: nowrap; opacity: 0; transition: opacity .08s;
  z-index: 2; }
.tip b { font-weight: 650; } .tip .k { color: var(--ink-2); }

.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; text-align: right; border-bottom: 1px solid var(--grid); white-space: nowrap; }
th { color: var(--ink-2); font-weight: 550; font-size: 12px; cursor: default; user-select: none; }
th.sortable { cursor: pointer; } th.sortable:hover { color: var(--ink); }
th[aria-sort] { color: var(--ink); }
td { font-variant-numeric: tabular-nums; }
th:first-child, td:first-child { text-align: left; white-space: normal; min-width: 220px; max-width: 420px; }
tr:last-child td { border-bottom: 0; }
td .name { font-weight: 550; overflow-wrap: anywhere; } td .ctx { color: var(--muted); font-size: 12px; }
.bar { display: inline-block; height: 6px; border-radius: 3px; background: var(--series);
  vertical-align: middle; margin-right: 8px; min-width: 2px; }
.chip { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; color: var(--ink-2); }
.chip i { width: 14px; height: 14px; border-radius: 50%; display: inline-grid; place-items: center;
  font-style: normal; font-size: 9px; font-weight: 700; color: #fff; }
.chip.good i { background: var(--good); } .chip.warning i { background: var(--warning); color: #0b0b0b; }
.chip.critical i { background: var(--critical); } .chip.low i { background: var(--context); color: var(--ink); }
.rows { display: grid; grid-template-columns: max-content 1fr max-content max-content; gap: 10px 14px;
  align-items: center; font-size: 13px; }
.rows .track { height: 8px; border-radius: 4px; background: var(--series-wash); }
.rows .fill { height: 8px; border-radius: 4px; background: var(--series); min-width: 2px; }
.rows .num { font-variant-numeric: tabular-nums; text-align: right; }
.rows .head { color: var(--ink-2); font-size: 12px; }
details summary { cursor: pointer; color: var(--ink-2); font-size: 13px; }
footer { color: var(--muted); font-size: 12px; margin-top: 24px; }
:focus-visible { outline: 2px solid var(--series); outline-offset: 2px; }
</style>
</head>
<body>
<main>
  <header>
    <h1><span class="dot"></span><span id="title"></span></h1>
    <div class="meta" id="meta"></div>
  </header>

  <section class="card hero" aria-label="Headline numbers">
    <div class="hero-figure"><div class="label" id="hero-label"></div>
      <div class="value" id="hero-value"></div><div class="note" id="hero-note"></div></div>
    <div class="tiles" id="tiles"></div>
  </section>

  <section class="card" id="insights-card" hidden>
    <h2 id="headline"></h2><ul class="insights" id="insights"></ul>
  </section>

  <div class="grid2">
    <section class="card"><h2>Daily spend</h2><p class="sub" id="spend-sub"></p>
      <div class="chart" id="spend-chart"></div></section>
    <section class="card"><h2>Daily ROAS</h2><p class="sub" id="roas-sub"></p>
      <div class="chart" id="roas-chart"></div></section>
  </div>

  <section class="card"><h2>Campaigns</h2><p class="sub">Click a column to sort.</p>
    <div class="scroll"><table id="campaigns"></table></div></section>

  <section class="card"><h2>Creatives</h2>
    <p class="sub" id="ads-sub"></p>
    <div class="scroll"><table id="ads"></table></div></section>

  <section class="card" id="age-card"><h2>Who the money reaches</h2>
    <p class="sub">Share of spend by age, with the return on each.</p>
    <div class="rows" id="age"></div></section>

  <section class="card"><details><summary>Daily numbers as a table</summary>
    <div class="scroll" style="margin-top:12px"><table id="daily"></table></div></details></section>

  <footer id="footer"></footer>
</main>

<script id="data" type="application/json">__DATA__</script>
<script>
(() => {
const D = JSON.parse(document.getElementById('data').textContent);
const $ = id => document.getElementById(id);
const NS = 'http://www.w3.org/2000/svg';
const el = (tag, attrs = {}, text) => {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text != null) node.textContent = text;
  return node;
};
const h = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};
const money = v => v == null ? '—' : '$' + (Math.abs(v) >= 10000
  ? (v / 1000).toFixed(Math.abs(v) >= 100000 ? 0 : 1) + 'K' : Math.round(v).toLocaleString());
const money2 = v => v == null ? '—' : '$' + v.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
const ratio = v => v == null ? '—' : v.toFixed(2) + '×';
const pct = v => v == null ? '—' : v.toFixed(2) + '%';
const int = v => v == null ? '—' : Math.round(v).toLocaleString();
const day = s => new Date(s + 'T00:00:00').toLocaleDateString(undefined, {month: 'short', day: 'numeric'});

// ── header ────────────────────────────────────────────────────────────────
$('title').textContent = D.account + ' · Pinterest';
$('meta').textContent = `${day(D.start)} – ${day(D.end)} · ${D.attribution}`;
$('footer').textContent = `Generated ${D.generated}. ${D.source_note}`;

// ── status vs target ──────────────────────────────────────────────────────
const status = row => {
  if (row.purchases < D.low_data_purchases) return ['low', '·', 'Low data'];
  const roas = row.nb_roas == null ? row.roas : row.nb_roas;   // decide on Northbeam when present
  if (D.target_roas == null || roas == null) return null;
  const r = roas / D.target_roas;
  if (r >= 1) return ['good', '✓', 'On target'];
  if (r >= 0.7) return ['warning', '!', 'Below target'];
  return ['critical', '✕', 'Well below'];
};
const chip = row => {
  const s = status(row);
  if (!s) return document.createTextNode('');
  const c = h('span', 'chip ' + s[0]);
  c.append(h('i', null, s[1]), document.createTextNode(s[2]));
  return c;
};

// ── hero + tiles ──────────────────────────────────────────────────────────
const T = D.totals, P = D.previous;
const HERO = D.hero_roas == null ? T.roas : D.hero_roas;
$('hero-label').textContent = D.hero_label || D.roas_label;
$('hero-value').textContent = ratio(HERO);
$('hero-note').textContent = D.target_roas == null ? 'No target set in the account notes'
  : `Target ${ratio(D.target_roas)} · ${HERO == null ? '—' : Math.round(HERO / D.target_roas * 100) + '% of target'}`;

const spark = (values) => {
  const w = 120, hgt = 28, svg = el('svg', {width: w, height: hgt, viewBox: `0 0 ${w} ${hgt}`, 'aria-hidden': 'true'});
  const vals = values.filter(v => v != null);
  if (vals.length < 2) return svg;
  const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  const pts = values.map((v, i) => v == null ? null
    : [i / (values.length - 1) * (w - 6) + 3, hgt - 4 - (v - lo) / span * (hgt - 8)]).filter(Boolean);
  svg.append(el('path', {d: 'M' + pts.map(p => p.join(',')).join('L'), fill: 'none',
    stroke: 'var(--context)', 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}));
  const last = pts[pts.length - 1];
  svg.append(el('circle', {cx: last[0], cy: last[1], r: 4, fill: 'var(--series)', stroke: 'var(--surface)', 'stroke-width': 2}));
  return svg;
};
const tiles = [
  ['Spend', money(T.spend), 'spend', false],
  ['Revenue', money(T.revenue), 'revenue', true],
  ['Purchases', int(T.purchases), 'purchases', true],
  ['CPA', T.cpa == null ? '—' : money2(T.cpa), 'cpa', false, true],
  ['CTR', pct(T.ctr), 'ctr', true],
  ['CPM', T.cpm == null ? '—' : money2(T.cpm), 'cpm', false, true],
];
for (const [label, value, key, upGood, downGood] of tiles) {
  const tile = h('div', 'tile');
  tile.append(h('div', 'label', label), h('div', 'value', value));
  const delta = h('div', 'delta');
  if (P && P[key] && T[key] != null) {
    const change = (T[key] - P[key]) / P[key] * 100;
    const arrow = change >= 0 ? '▲' : '▼';
    const good = upGood ? change >= 0 : downGood ? change <= 0 : null;
    const mark = h('span', good == null ? '' : good ? 'up-good' : 'down-bad', `${arrow} ${Math.abs(change).toFixed(0)}%`);
    delta.append(mark, document.createTextNode(' vs prior period'));
  } else delta.textContent = ' ';
  tile.append(delta, spark(D.daily.map(d => d[key])));
  $('tiles').append(tile);
}

// ── insights ──────────────────────────────────────────────────────────────
if (D.headline || (D.insights && D.insights.length)) {
  $('insights-card').hidden = false;
  $('headline').textContent = D.headline || 'What matters';
  for (const line of D.insights || []) $('insights').append(h('li', null, line));
}

// ── charts ────────────────────────────────────────────────────────────────
// A clean tick step (1, 2, 2.5 or 5 times a power of ten) giving about four gridlines.
const niceStep = v => { if (!(v > 0)) return 1; const raw = v / 4, p = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= raw) return m * p; return 10 * p; };
function chart(host, values, opts) {
  const W = 520, H = 220, L = 44, R = 12, Tp = 12, B = 26, n = values.length;
  const step = niceStep(Math.max(...values.map(v => v || 0), opts.reference || 0) * 1.05);
  const lines = Math.max(1, Math.ceil(Math.max(...values.map(v => v || 0), opts.reference || 0) * 1.05 / step));
  const top = step * lines;
  const x = i => L + (n === 1 ? (W - L - R) / 2 : i * (W - L - R) / (n - 1));
  const slot = (W - L - R) / Math.max(n, 1);
  const y = v => Tp + (H - Tp - B) * (1 - v / top);
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': opts.label});
  for (let g = 0; g <= lines; g++) {
    const v = step * g, yy = y(v);
    svg.append(el('line', {x1: L, x2: W - R, y1: yy, y2: yy, stroke: g ? 'var(--grid)' : 'var(--axis)', 'stroke-width': 1}));
    svg.append(el('text', {x: L - 8, y: yy + 4, 'text-anchor': 'end', class: 'tick'}, opts.tick(v)));
  }
  const every = Math.ceil(n / 7);
  const cx = i => opts.bars ? L + slot * (i + 0.5) : x(i);
  values.forEach((_, i) => { if (i % every === 0 || i === n - 1 && n > 1 && (n - 1) % every > every / 2)
    svg.append(el('text', {x: cx(i), y: H - 6, 'text-anchor': 'middle', class: 'tick'}, day(D.daily[i].date))); });
  if (opts.reference != null) {
    const yy = y(opts.reference);
    svg.append(el('line', {x1: L, x2: W - R, y1: yy, y2: yy, stroke: 'var(--muted)', 'stroke-width': 1}));
    // Left end, so it never collides with the value label at the line's right end.
    svg.append(el('text', {x: L + 4, y: yy - 6, 'text-anchor': 'start', class: 'tick'}, opts.referenceLabel));
  }
  const marks = [];
  if (opts.bars) {
    const bw = Math.min(24, Math.max(2, slot * 0.72));
    values.forEach((v, i) => {
      const hgt = Math.max(0, y(0) - y(v || 0)), r = Math.min(4, hgt, bw / 2), left = cx(i) - bw / 2, base = y(0);
      const d = `M${left},${base}V${base - hgt + r}Q${left},${base - hgt} ${left + r},${base - hgt}H${left + bw - r}Q${left + bw},${base - hgt} ${left + bw},${base - hgt + r}V${base}Z`;
      const bar = el('path', {d, fill: 'var(--series)'}); svg.append(bar); marks.push(bar);
    });
  } else {
    const pts = values.map((v, i) => v == null ? null : [x(i), y(v)]);
    let d = '', pen = false;
    pts.forEach(p => { if (!p) { pen = false; return; } d += (pen ? 'L' : 'M') + p[0] + ',' + p[1]; pen = true; });
    svg.append(el('path', {d, fill: 'none', stroke: 'var(--series)', 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}));
    const lastIndex = pts.map(Boolean).lastIndexOf(true);
    if (lastIndex >= 0) {
      const [px, py] = pts[lastIndex];
      svg.append(el('circle', {cx: px, cy: py, r: 4, fill: 'var(--series)', stroke: 'var(--surface)', 'stroke-width': 2}));
      const flip = px > W - 70;
      svg.append(el('text', {x: flip ? px - 8 : px + 8, y: py - 8, 'text-anchor': flip ? 'end' : 'start', class: 'tick',
        style: 'fill: var(--ink); font-weight: 600'}, opts.value(values[lastIndex])));
    }
  }
  const hair = el('line', {y1: Tp, y2: H - B, stroke: 'var(--axis)', 'stroke-width': 1, opacity: 0}); svg.append(hair);
  const focusDot = el('circle', {r: 4, fill: 'var(--series)', stroke: 'var(--surface)', 'stroke-width': 2, opacity: 0});
  if (!opts.bars) svg.append(focusDot);
  const tip = h('div', 'tip'); host.append(svg, tip);
  const show = i => {
    const d = D.daily[i]; tip.replaceChildren();
    tip.append(h('div', 'k', new Date(d.date + 'T00:00:00').toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'})));
    for (const [k, v] of opts.rows(d)) { const row = h('div'); row.append(h('b', null, v), h('span', 'k', ' ' + k)); tip.append(row); }
    const box = svg.getBoundingClientRect(), px = cx(i) / W * box.width;
    tip.style.left = Math.min(Math.max(px + 12, 0), box.width - tip.offsetWidth - 4) + 'px';
    tip.style.top = '8px'; tip.style.opacity = 1;
    hair.setAttribute('x1', cx(i)); hair.setAttribute('x2', cx(i)); hair.setAttribute('opacity', opts.bars ? 0 : 1);
    marks.forEach((m, j) => m.setAttribute('opacity', j === i ? 1 : 0.55));
    if (!opts.bars && values[i] != null) { focusDot.setAttribute('cx', x(i)); focusDot.setAttribute('cy', y(values[i])); focusDot.setAttribute('opacity', 1); }
  };
  const hide = () => { tip.style.opacity = 0; hair.setAttribute('opacity', 0); focusDot.setAttribute('opacity', 0);
    marks.forEach(m => m.setAttribute('opacity', 1)); };
  svg.addEventListener('pointermove', e => {
    const box = svg.getBoundingClientRect(), px = (e.clientX - box.left) / box.width * W;
    const i = opts.bars ? Math.floor((px - L) / slot) : Math.round((px - L) / ((W - L - R) / Math.max(n - 1, 1)));
    show(Math.min(n - 1, Math.max(0, i)));
  });
  svg.addEventListener('pointerleave', hide);
  svg.setAttribute('tabindex', 0); let cursor = n - 1;
  svg.addEventListener('focus', () => show(cursor)); svg.addEventListener('blur', hide);
  svg.addEventListener('keydown', e => { if (e.key === 'ArrowLeft') cursor = Math.max(0, cursor - 1);
    else if (e.key === 'ArrowRight') cursor = Math.min(n - 1, cursor + 1); else return; e.preventDefault(); show(cursor); });
}
const compact = v => v >= 10000 ? '$' + (v / 1000).toFixed(0) + 'K' : '$' + Math.round(v).toLocaleString();
$('spend-sub').textContent = `${money(T.spend)} over ${D.daily.length} days · ${money(T.spend / Math.max(D.daily.length, 1))} a day`;
chart($('spend-chart'), D.daily.map(d => d.spend), {bars: true, label: 'Daily spend', tick: compact,
  rows: d => [['spend', money2(d.spend)], ['ROAS', ratio(d.roas)], ['purchases', int(d.purchases)]]});
$('roas-sub').textContent = D.target_roas == null ? D.roas_label : `${D.roas_label} against a target of ${ratio(D.target_roas)}`;
chart($('roas-chart'), D.daily.map(d => d.roas), {label: 'Daily ROAS', tick: v => (Number.isInteger(v) ? v : v.toFixed(1)) + '×', value: ratio,
  reference: D.target_roas, referenceLabel: D.target_roas == null ? '' : 'Target ' + ratio(D.target_roas),
  rows: d => [['ROAS', ratio(d.roas)], ['revenue', money2(d.revenue)], ['spend', money2(d.spend)]]});

// ── tables ────────────────────────────────────────────────────────────────
function table(host, rows, cols, sortKey) {
  let key = sortKey, asc = false;
  const maxSpend = Math.max(...rows.map(r => r.spend || 0), 1);
  const draw = () => {
    host.replaceChildren();
    const head = h('tr');
    for (const c of cols) {
      const th = h('th', c.key ? 'sortable' : '', c.title);
      if (c.key) { th.tabIndex = 0; if (c.key === key) th.setAttribute('aria-sort', asc ? 'ascending' : 'descending');
        const go = () => { asc = key === c.key ? !asc : false; key = c.key; draw(); };
        th.addEventListener('click', go); th.addEventListener('keydown', e => { if (e.key === 'Enter') go(); }); }
      head.append(th);
    }
    const thead = h('thead'); thead.append(head);
    const body = h('tbody');
    const sorted = [...rows].sort((a, b) => {
      const va = a[key], vb = b[key];
      if (va == null) return 1; if (vb == null) return -1;
      return (typeof va === 'string' ? va.localeCompare(vb) : va - vb) * (asc ? 1 : -1);
    });
    for (const r of sorted) { const tr = h('tr'); for (const c of cols) { const td = h('td'); td.append(c.cell(r, maxSpend)); tr.append(td); } body.append(tr); }
    host.append(thead, body);
  };
  draw();
}
const nameCell = sub => r => { const box = h('div'); box.append(h('div', 'name', r.name || '(unnamed)'));
  if (sub && r[sub]) box.append(h('div', 'ctx', r[sub])); return box; };
const spendCell = (r, max) => { const box = h('span'); const bar = h('span', 'bar');
  bar.style.width = Math.max(2, (r.spend || 0) / max * 72) + 'px'; box.append(bar, document.createTextNode(money(r.spend))); return box; };
const text = f => r => document.createTextNode(f(r));
const nb = D.has_northbeam ? [{title: 'NB ROAS', key: 'nb_roas', cell: text(r => ratio(r.nb_roas))}] : [];
table($('campaigns'), D.campaigns, [
  {title: 'Campaign', key: 'name', cell: nameCell()},
  {title: 'Spend', key: 'spend', cell: spendCell},
  ...nb,
  {title: D.has_northbeam ? 'Pinterest ROAS' : 'ROAS', key: 'roas', cell: text(r => ratio(r.roas))},
  {title: 'Purchases', key: 'purchases', cell: text(r => int(r.purchases))},
  {title: 'CPA', key: 'cpa', cell: text(r => r.cpa == null ? '—' : money2(r.cpa))},
  {title: 'CTR', key: 'ctr', cell: text(r => pct(r.ctr))},
  {title: 'CPM', key: 'cpm', cell: text(r => r.cpm == null ? '—' : money2(r.cpm))},
  {title: 'Status', cell: chip},
], 'spend');
$('ads-sub').textContent = `Ads with at least ${money(D.min_ad_spend)} of spend. Hook rate is 3-second views over impressions; hold rate is 95% views over 3-second views.`;
table($('ads'), D.ads, [
  {title: 'Ad', key: 'name', cell: nameCell('campaign')},
  {title: 'Spend', key: 'spend', cell: spendCell},
  {title: 'ROAS', key: 'roas', cell: text(r => ratio(r.roas))},
  {title: 'Purchases', key: 'purchases', cell: text(r => int(r.purchases))},
  {title: 'CPA', key: 'cpa', cell: text(r => r.cpa == null ? '—' : money2(r.cpa))},
  {title: 'CTR', key: 'ctr', cell: text(r => pct(r.ctr))},
  {title: 'Hook', key: 'hook_rate', cell: text(r => r.hook_rate == null ? '—' : r.hook_rate.toFixed(1) + '%')},
  {title: 'Hold', key: 'hold_rate', cell: text(r => r.hold_rate == null ? '—' : r.hold_rate.toFixed(2) + '%')},
  {title: 'Status', cell: chip},
], 'spend');
table($('daily'), D.daily.map(d => ({...d, name: d.date})), [
  {title: 'Date', key: 'name', cell: text(r => day(r.date))},
  {title: 'Spend', key: 'spend', cell: text(r => money2(r.spend))},
  {title: 'Revenue', key: 'revenue', cell: text(r => money2(r.revenue))},
  {title: 'ROAS', key: 'roas', cell: text(r => ratio(r.roas))},
  {title: 'Purchases', key: 'purchases', cell: text(r => int(r.purchases))},
  {title: 'CPA', key: 'cpa', cell: text(r => r.cpa == null ? '—' : money2(r.cpa))},
  {title: 'CTR', key: 'ctr', cell: text(r => pct(r.ctr))},
  {title: 'CPM', key: 'cpm', cell: text(r => r.cpm == null ? '—' : money2(r.cpm))},
], 'name');

// ── age ───────────────────────────────────────────────────────────────────
if (!D.age.length) $('age-card').hidden = true;
else {
  const total = D.age.reduce((s, r) => s + r.spend, 0) || 1, host = $('age');
  for (const t of ['Age', 'Share of spend', 'Spend', 'ROAS']) host.append(h('div', 'head' + (t === 'Spend' || t === 'ROAS' ? ' num' : ''), t));
  for (const r of D.age) {
    const track = h('div', 'track'), fill = h('div', 'fill');
    fill.style.width = (r.spend / total * 100).toFixed(1) + '%';
    track.title = `${r.segment}: ${(r.spend / total * 100).toFixed(0)}% of spend`; track.append(fill);
    host.append(h('div', null, r.segment), track, h('div', 'num', `${money(r.spend)} · ${(r.spend / total * 100).toFixed(0)}%`), h('div', 'num', ratio(r.roas)));
  }
}
})();
</script>
</body>
</html>
"""

AGE_ORDER = ["18-24", "25-34", "35-44", "45-49", "50-54", "55-64", "65+"]


def render(data: dict) -> str:
    """Fill the page with data. `data` is described in buyer.pinterest_dashboard."""
    data = dict(data)
    data.setdefault("generated", datetime.now().strftime("%b %-d, %Y at %-I:%M %p"))
    data["age"] = sorted(
        data.get("age") or [],
        key=lambda r: AGE_ORDER.index(r["segment"]) if r["segment"] in AGE_ORDER else len(AGE_ORDER),
    )
    # Account data is untrusted text. Escape "<" so no name can close the JSON
    # block or open a tag; the script reads it back with JSON.parse.
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    title = html.escape(f"{data.get('account', 'Pinterest')} · Pinterest performance")
    return PAGE.replace("__TITLE__", title).replace("__DATA__", payload)
