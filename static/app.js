"use strict";

// ------------------------------------------------------------------ state
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
};
let unit = store.get("envmon.unit", "C");
let range = store.get("envmon.range", "1h");
let status = null;
let hist = null;
let hoverT = null;            // shared crosshair time across all charts
let historyTimer = null;

const REFRESH = { "15m": 2000, "1h": 5000, "6h": 30000, "24h": 60000 };
const RANGE_LABEL = { "15m": "15 min", "1h": "1 hour", "6h": "6 hours", "24h": "24 hours" };

const HPA_TO_PSI = 0.0145037738;
const toF = (c) => c * 9 / 5 + 32;
const tempConv = (v) => (unit === "F" ? toF(v) : v);
const tempUnit = () => (unit === "F" ? "°F" : "°C");

const METRICS = {
  temperature_c:      { title: "Temperature",        unit: tempUnit,  conv: tempConv,        dec: 1, minSpan: 1 },
  humidity_pct:       { title: "Relative humidity",  unit: () => "%", conv: (v) => v,         dec: 1, minSpan: 4 },
  pressure_hpa:       { title: "Pressure",           unit: () => "psi", conv: (v) => v * HPA_TO_PSI, dec: 3, minSpan: 0.03 },
  gas_resistance_ohm: { title: "Gas resistance",     unit: () => "kΩ", conv: (v) => v / 1000, dec: 1, minSpan: 2 },
  air_quality:        { title: "Air quality (est.)", unit: () => "/ 100", conv: (v) => v,     dec: 0, fixed: [0, 100] },
};
const fmt = (v, dec) => (v == null || Number.isNaN(v) ? "–" :
  v.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec }));

// ------------------------------------------------------------------ data
async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

async function pollStatus() {
  try {
    status = await getJSON("/api/status");
  } catch {
    status = null;
  }
  renderStatus();
  renderTiles();
}

async function loadHistory() {
  clearTimeout(historyTimer);
  try {
    hist = await getJSON(`/api/history?range=${range}`);
    drawAll();
    renderTiles();
  } catch { /* keep last data */ }
  historyTimer = setTimeout(loadHistory, REFRESH[range]);
}

async function loadLogs() {
  try {
    const files = await getJSON("/api/logs");
    const lg = status?.logging;
    const cur = lg?.active ? lg.file : null;
    const rows = files.map((f) => {
      const size = fmtBytes(f.bytes);
      const rec = f.name === cur ? '<span class="rec">recording</span>' : "";
      const name = encodeURIComponent(f.name);
      const del = f.name === cur
        ? '<button class="link-btn danger" disabled title="Stop logging before deleting this file">Delete</button>'
        : `<button class="link-btn danger" data-delete="${escapeHtml(f.name)}">Delete</button>`;
      return `<tr><td>${escapeHtml(f.name)}${rec}</td><td class="r">${size}</td>
              <td class="r actions"><a href="/logs/${name}">Download</a>${del}</td></tr>`;
    });
    document.getElementById("log-rows").innerHTML =
      rows.join("") || '<tr><td colspan="3" class="muted">No log files yet</td></tr>';
  } catch { /* ignore */ }
}

// Delete: first click arms the button ("Confirm delete"), a second click within 4 s deletes.
document.getElementById("log-rows").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-delete]");
  if (!btn) return;
  const errEl = document.getElementById("log-error");
  if (!btn.classList.contains("armed")) {
    btn.classList.add("armed");
    btn.textContent = "Confirm delete";
    btn._timer = setTimeout(() => { btn.classList.remove("armed"); btn.textContent = "Delete"; }, 4000);
    return;
  }
  clearTimeout(btn._timer);
  btn.disabled = true;
  btn.textContent = "Deleting…";
  try {
    const r = await fetch(`/api/logs/${encodeURIComponent(btn.dataset.delete)}`, { method: "DELETE" });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.error || `Server returned ${r.status}`);
    }
    errEl.hidden = true;
  } catch (err) {
    errEl.textContent = `⚠ Could not delete ${btn.dataset.delete}: ${err.message}`;
    errEl.hidden = false;
  }
  loadLogs();
});

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ------------------------------------------------------------------ header + tiles
function renderStatus() {
  const pill = document.getElementById("pill");
  const text = document.getElementById("pill-text");
  const sub = document.getElementById("sub");
  pill.className = "pill";
  if (!status) {
    text.textContent = "Server unreachable";
    pill.classList.add("warn");
    return;
  }
  const stale = status.latest && (status.server_time - status.latest.timestamp > 5);
  if (status.simulated) {
    text.textContent = "Simulated data";
    pill.classList.add("live");
  } else if (status.online && !stale) {
    text.textContent = `Live · ${status.sensor} @ ${status.address}`;
    pill.classList.add("live");
  } else {
    text.textContent = status.error ? "Sensor error" : "Waiting for sensor";
    pill.classList.add("warn");
    pill.title = status.error || "";
  }
  const parts = [`Sampling every ${status.interval_s} s`];
  if (status.error) parts.push(`⚠ ${status.error}`);
  sub.textContent = parts.join(" · ");
  renderLogging(status.logging);
}

// ------------------------------------------------------------------ logging control
let lastLogFile = null;
let logBusy = false;

const fmtBytes = (b) => (b >= 1024 ** 3 ? `${(b / 1024 ** 3).toFixed(2)} GB`
  : b >= 1024 ** 2 ? `${(b / 1024 ** 2).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);

function renderLogging(lg) {
  const btn = document.getElementById("log-btn");
  if (!logBusy) btn.disabled = false;
  btn.classList.toggle("recording", lg.active);
  btn.textContent = lg.active ? "Stop logging" : "Start logging";
  const key = `${lg.active}:${lg.file}`;
  if (key !== lastLogFile) { lastLogFile = key; loadLogs(); }
}

document.getElementById("log-btn").addEventListener("click", async () => {
  const btn = document.getElementById("log-btn");
  const errEl = document.getElementById("log-error");
  const starting = !status?.logging?.active;
  logBusy = true; btn.disabled = true; errEl.hidden = true;
  try {
    const r = await fetch(`/api/logging/${starting ? "start" : "stop"}`, { method: "POST" });
    if (!r.ok) throw new Error(r.status);
    logBusy = false;
    await pollStatus();
  } catch {
    logBusy = false;
    btn.disabled = false;
    errEl.textContent = `⚠ Could not ${starting ? "start" : "stop"} logging. Is the server reachable?`;
    errEl.hidden = false;
  }
});

function renderTiles() {
  const L = status?.latest;
  document.querySelectorAll(".tile").forEach((tile) => {
    const key = tile.dataset.key;
    const m = METRICS[key];
    const num = tile.querySelector(".num");
    const rangeEl = tile.querySelector(".range");
    let v = L ? L[key] : null;

    if (key === "dew_point_c") {
      num.textContent = fmt(v == null ? null : tempConv(v), 1);
      return;
    }
    num.textContent = fmt(v == null ? null : m.conv(v), m.dec);

    const s = hist?.stats?.[key];
    if (s && s.min != null) {
      rangeEl.textContent = `${RANGE_LABEL[range]}: ${fmt(m.conv(s.min), m.dec)} – ${fmt(m.conv(s.max), m.dec)}`;
    } else {
      rangeEl.textContent = key === "gas_resistance_ohm" && L && v == null ? "Heater warming up…"
        : key === "air_quality" ? "" : "–";
    }
  });
  document.querySelectorAll("[data-temp-unit]").forEach((e) => (e.textContent = tempUnit()));

  const badge = document.getElementById("aq-badge");
  if (L && L.air_quality_status) {
    const label = { good: "Good", moderate: "Moderate", poor: "Poor", bad: "Bad" }[L.air_quality_status];
    badge.dataset.s = L.air_quality_status;
    badge.textContent = label;
    badge.hidden = false;
  } else if (L && L.aq_burn_in_remaining_s > 0) {
    const s = L.aq_burn_in_remaining_s;
    badge.dataset.s = "cal";
    badge.textContent = `Calibrating · ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} left`;
    badge.hidden = false;
  } else {
    badge.hidden = true;
  }

  for (const c of charts) {
    const v = L ? L[c.key] : null;
    c.now.textContent = v == null ? "" : `${fmt(c.m.conv(v), c.m.dec)} ${c.m.unit()}`;
  }
}

// ------------------------------------------------------------------ charts
const charts = [];

function buildCharts() {
  const host = document.getElementById("charts");
  for (const [key, m] of Object.entries(METRICS)) {
    const el = document.createElement("article");
    el.className = "chart";
    el.innerHTML = `<header><h3>${m.title}</h3><span class="now"></span></header>
      <canvas role="img" aria-label="${m.title} over time"></canvas>
      <div class="empty" hidden>No data in this range yet</div><div class="tip" hidden></div>`;
    host.appendChild(el);
    const c = { key, m, el, canvas: el.querySelector("canvas"), now: el.querySelector(".now"),
                tip: el.querySelector(".tip"), empty: el.querySelector(".empty"), geom: null };
    const move = (e) => {
      if (!c.geom) return;
      const r = c.canvas.getBoundingClientRect();
      const x = e.clientX - r.left;
      const g = c.geom;
      hoverT = x < g.L || x > g.R ? null : g.t0 + ((x - g.L) / (g.R - g.L)) * (g.t1 - g.t0);
      drawAll();
    };
    c.canvas.addEventListener("pointermove", move);
    c.canvas.addEventListener("pointerdown", move);
    c.canvas.addEventListener("pointerleave", () => { hoverT = null; drawAll(); });
    charts.push(c);
  }
  new ResizeObserver(() => drawAll()).observe(host);
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function niceTicks(lo, hi, count = 4) {
  const span = hi - lo;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((f) => f * mag).find((s) => span / s <= count) || 10 * mag;
  const start = Math.floor(lo / step) * step;
  const end = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let v = start; v <= end + step / 2; v += step) ticks.push(+v.toFixed(10));
  return { ticks, lo: start, hi: end, step };
}

function timeTicks(t0, t1, px) {
  const steps = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600];
  const want = Math.max(2, Math.floor(px / 90));
  const step = steps.find((s) => (t1 - t0) / s <= want) || 21600;
  const off = new Date().getTimezoneOffset() * 60;   // align to local clock
  const out = [];
  for (let t = Math.ceil((t0 - off) / step) * step + off; t <= t1; t += step) out.push(t);
  return out;
}

const hhmm = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const hhmmss = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function drawAll() {
  for (const c of charts) drawChart(c);
}

function drawChart(c) {
  const { canvas, m } = c;
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth, H = canvas.clientHeight;
  if (!W) return;
  if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  const C = { ink2: cssVar("--ink-2"), muted: cssVar("--muted"), grid: cssVar("--grid"),
              axis: cssVar("--axis"), series: cssVar("--series"), surface: cssVar("--surface") };

  const ts = hist?.series?.t || [];
  const raw = hist?.series?.[c.key] || [];
  const vals = raw.map((v) => (v == null ? null : m.conv(v)));
  const present = vals.filter((v) => v != null);
  c.empty.hidden = present.length > 0;
  c.tip.hidden = true;
  if (!hist) return;

  // y domain
  let lo, hi;
  if (m.fixed) [lo, hi] = m.fixed;
  else if (present.length) {
    lo = Math.min(...present); hi = Math.max(...present);
    const span = Math.max(hi - lo, m.minSpan * (c.key === "temperature_c" && unit === "F" ? 1.8 : 1));
    const mid = (lo + hi) / 2;
    lo = mid - span * 0.6; hi = mid + span * 0.6;
  } else { lo = 0; hi = 1; }
  // Never show a negative axis for data that can't be negative (gas, humidity, …).
  if (present.length && Math.min(...present) >= 0) lo = Math.max(lo, 0);
  const yt = niceTicks(lo, hi);
  lo = yt.lo; hi = yt.hi;
  const decY = Math.max(0, -Math.floor(Math.log10(yt.step) + 1e-9));

  ctx.font = `11px ${cssVar("--font") || "system-ui"}`;
  const labelW = Math.max(...yt.ticks.map((v) => ctx.measureText(fmt(v, decY)).width));
  const L = Math.ceil(labelW) + 10, R = W - 6, T = 8, B = H - 22;
  const t0 = hist.start, t1 = hist.end;
  const X = (t) => L + ((t - t0) / (t1 - t0)) * (R - L);
  const Y = (v) => B - ((v - lo) / (hi - lo)) * (B - T);
  c.geom = { L, R, t0, t1 };

  // grid + y labels
  ctx.lineWidth = 1;
  ctx.textAlign = "right"; ctx.textBaseline = "middle"; ctx.fillStyle = C.muted;
  for (const v of yt.ticks) {
    const y = Math.round(Y(v)) + 0.5;
    ctx.strokeStyle = v === lo ? C.axis : C.grid;
    ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(R, y); ctx.stroke();
    ctx.fillText(fmt(v, decY), L - 6, y);
  }
  // x labels
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (const t of timeTicks(t0, t1, R - L)) {
    const x = X(t);
    if (x < L + 14 || x > R - 14) continue;
    ctx.fillText(hhmm(t), x, B + 6);
  }

  // line (broken at gaps)
  const gap = Math.max(3 * hist.bucket_s, 5);
  ctx.strokeStyle = C.series; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.lineCap = "round";
  ctx.beginPath();
  let pen = false, lastT = null, lastI = -1;
  for (let i = 0; i < ts.length; i++) {
    const v = vals[i];
    if (v == null) { pen = false; continue; }
    if (pen && ts[i] - lastT > gap) pen = false;
    const x = X(ts[i]), y = Y(v);
    pen ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    pen = true; lastT = ts[i]; lastI = i;
  }
  ctx.stroke();

  const dot = (x, y) => {
    ctx.beginPath(); ctx.arc(x, y, 6, 0, Math.PI * 2); ctx.fillStyle = C.surface; ctx.fill();
    ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fillStyle = C.series; ctx.fill();
  };
  if (lastI >= 0 && hoverT == null) dot(X(ts[lastI]), Y(vals[lastI]));

  // shared crosshair
  if (hoverT != null && ts.length) {
    let i = nearest(ts, hoverT);
    // walk to nearest non-null value
    let j = i, k = i;
    while (j >= 0 && vals[j] == null) j--;
    while (k < ts.length && vals[k] == null) k++;
    i = j < 0 ? k : k >= ts.length ? j : (hoverT - ts[j] <= ts[k] - hoverT ? j : k);
    const xh = Math.round(X(hoverT)) + 0.5;
    ctx.strokeStyle = C.axis; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(xh, T); ctx.lineTo(xh, B); ctx.stroke();
    if (i >= 0 && i < ts.length && vals[i] != null && Math.abs(ts[i] - hoverT) <= gap) {
      const x = X(ts[i]), y = Y(vals[i]);
      dot(x, y);
      c.tip.innerHTML = `<b>${fmt(vals[i], m.dec)} ${m.unit()}</b> · ${hist.bucket_s > 1 ? hhmm(ts[i]) : hhmmss(ts[i])}`;
      c.tip.hidden = false;
      const off = canvas.offsetTop, tipW = c.tip.offsetWidth;
      const left = Math.min(Math.max(x + canvas.offsetLeft, tipW / 2 + 4), c.el.clientWidth - tipW / 2 - 4);
      c.tip.style.left = `${left}px`;
      c.tip.style.top = `${off + Math.max(y - 12, 18)}px`;
    }
  }
}

function nearest(arr, t) {
  let lo = 0, hi = arr.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] < t) lo = mid + 1; else hi = mid;
  }
  return lo > 0 && t - arr[lo - 1] < arr[lo] - t ? lo - 1 : lo;
}

// ------------------------------------------------------------------ controls
function setPressed(group, attr, value) {
  document.querySelectorAll(`${group} button`).forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset[attr] === value)));
}

document.querySelectorAll("[data-unit]").forEach((b) => b.addEventListener("click", () => {
  unit = b.dataset.unit; store.set("envmon.unit", unit);
  setPressed(".top-right .seg", "unit", unit);
  renderTiles(); drawAll();
}));
document.querySelectorAll("[data-range]").forEach((b) => b.addEventListener("click", () => {
  range = b.dataset.range; store.set("envmon.range", range);
  setPressed("#ranges", "range", range);
  hist = null; drawAll(); loadHistory();
}));
// Theme: follows the OS until the toggle is used, then the choice is remembered.
const darkMQ = window.matchMedia("(prefers-color-scheme: dark)");
const themeBtn = document.getElementById("theme-btn");
const isDark = () => (document.documentElement.dataset.theme || (darkMQ.matches ? "dark" : "light")) === "dark";
function syncThemeButton() {
  const dark = isDark();
  themeBtn.classList.toggle("is-dark", dark);
  const label = dark ? "Switch to light mode" : "Switch to dark mode";
  themeBtn.setAttribute("aria-label", label);
  themeBtn.title = label;
}
themeBtn.addEventListener("click", () => {
  const next = isDark() ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  store.set("envmon.theme", next);
  syncThemeButton();
  drawAll();
});
darkMQ.addEventListener("change", () => { syncThemeButton(); drawAll(); });
syncThemeButton();

// ------------------------------------------------------------------ boot
if (!REFRESH[range]) range = "1h";
setPressed(".top-right .seg", "unit", unit);
setPressed("#ranges", "range", range);
buildCharts();
pollStatus();
loadHistory();
setInterval(pollStatus, 1000);
setInterval(loadLogs, 30000);
