// Cohen-Bot Dashboard – pollt die API und rendert alles live.
const REFRESH_MS = 5000;
let equityChart = null;

const fmt = (n, d = 2) =>
  (n === null || n === undefined || isNaN(n)) ? "–"
  : Number(n).toLocaleString("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d });
const usd = (n) => "$" + fmt(n);
const sign = (n) => (n > 0 ? "green" : n < 0 ? "red" : "");

async function getJSON(url) {
  try { const r = await fetch(url); return await r.json(); }
  catch (e) { return null; }
}

function renderKpis(s) {
  if (!s) return;
  const cards = [
    { label: "Equity", value: usd(s.equity) },
    { label: "Gesamt-P&L", value: usd(s.pnl_total), cls: sign(s.pnl_total) },
    { label: "Rendite", value: fmt(s.pnl_pct) + " %", cls: sign(s.pnl_pct) },
    { label: "Cash", value: usd(s.cash), cls: s.cash < 0 ? "red" : "" },
    { label: "Hebel", value: fmt(s.leverage, 1) + "×" },
    { label: "Exposure", value: usd(s.gross_exposure) + " (" + fmt(s.exposure_pct, 0) + "%)" },
    { label: "Offene Pos.", value: s.open_positions },
    { label: "Trefferquote", value: fmt(s.win_rate, 1) + " %" },
    { label: "Gebühren gesamt", value: usd(s.total_fees), cls: "red" },
    { label: "Ø Gewinn", value: usd(s.avg_win), cls: "green" },
    { label: "Ø Verlust", value: usd(s.avg_loss), cls: "red" },
    { label: "Trades", value: s.closed_trades },
  ];
  document.getElementById("kpis").innerHTML = cards.map(c =>
    `<div class="kpi"><div class="label">${c.label}</div>
     <div class="value ${c.cls || ""}">${c.value}</div></div>`).join("");

  document.getElementById("phase").textContent = s.phase || "–";
  const m = document.getElementById("marketState");
  m.textContent = s.market_open ? "Markt OFFEN" : "Markt zu";
  m.className = "badge " + (s.market_open ? "open" : "closed");
  document.getElementById("lastCycle").textContent =
    s.last_cycle ? "Letzter Zyklus: " + s.last_cycle + " UTC" : "";
  const dot = document.getElementById("liveDot");
  dot.className = "dot " + (s.market_open ? "on" : "off");
  if (s.halted_today) m.textContent += " · STOPP (Tageslimit)";
}

function renderPositions(rows) {
  const tb = document.querySelector("#positions tbody");
  if (!rows || !rows.length) {
    tb.innerHTML = `<tr><td colspan="10" class="empty">Keine offenen Positionen</td></tr>`;
    return;
  }
  tb.innerHTML = rows.map(p => `
    <tr>
      <td><b>${p.symbol}</b></td>
      <td><span class="pill ${p.side}">${p.side.toUpperCase()}</span></td>
      <td>${fmt(p.qty, 0)}</td>
      <td>${usd(p.avg_price)}</td>
      <td>${usd(p.mark)}</td>
      <td>${usd(p.stop)}</td>
      <td>${usd(p.target)}</td>
      <td class="${sign(p.r_now)}">${fmt(p.r_now, 1)}R</td>
      <td class="${sign(p.upnl)}">${usd(p.upnl)} <span class="muted">(${fmt(p.upnl_pct,1)}%)</span></td>
      <td class="muted">${p.thesis || ""}</td>
    </tr>`).join("");
}

function renderTrades(rows) {
  const tb = document.querySelector("#trades tbody");
  if (!rows || !rows.length) {
    tb.innerHTML = `<tr><td colspan="6" class="empty">Noch keine Trades</td></tr>`;
    return;
  }
  tb.innerHTML = rows.slice(0, 60).map(t => `
    <tr>
      <td class="muted">${t.ts}</td>
      <td><b>${t.action}</b></td>
      <td>${t.symbol}</td>
      <td>${fmt(t.qty, 0)}</td>
      <td>${usd(t.price)}</td>
      <td class="${sign(t.pnl)}">${t.pnl ? usd(t.pnl) : "–"}</td>
    </tr>`).join("");
}

function renderCatalysts(d) {
  const tb = document.querySelector("#catalysts tbody");
  const rows = (d && d.catalysts) || [];
  if (!rows.length) {
    tb.innerHTML = `<tr><td colspan="4" class="empty">Keine Katalysatoren heute</td></tr>`;
    return;
  }
  tb.innerHTML = rows.map(c => `
    <tr><td><b>${c.symbol}</b></td><td>${c.kind}</td>
        <td class="muted">${c.detail || ""}</td><td>${c.date || ""}</td></tr>`).join("");
}

function renderRegime(d) {
  if (!d) return;
  const el = document.getElementById("regime");
  const tr = (t) => `<span class="trend ${t}">${(t||"flat").toUpperCase()}</span>`;
  let html = `<div class="row"><span><b>Gesamtmarkt (SPY)</b></span>${tr(d.market)}</div>`;
  const secs = d.sectors || {};
  html += Object.keys(secs).map(s =>
    `<div class="row"><span>${s}</span>${tr(secs[s])}</div>`).join("");
  el.innerHTML = html;
}

function renderLogs(rows) {
  const el = document.getElementById("logs");
  if (!rows || !rows.length) { el.innerHTML = `<div class="empty">–</div>`; return; }
  el.innerHTML = rows.map(l =>
    `<div class="line"><span class="lvl ${l.level}">${l.level}</span>
     <span class="muted">${l.ts}</span> ${escapeHtml(l.msg)}</div>`).join("");
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function renderAnalyst(d) {
  const tb = document.querySelector("#books tbody");
  const crit = document.getElementById("analystCriteria");
  if (!d || !d.enabled) {
    crit.textContent = "(deaktiviert)";
    tb.innerHTML = `<tr><td colspan="10" class="empty">Analyst aus</td></tr>`;
    return;
  }
  const c = d.criteria || {};
  crit.textContent = `· Promotion: ≥${c.min_days}T, ≥${c.min_trades} Trades, +${c.improvement_pct}% Score, ${c.streak_days}T am Stück`
    + (d.ai_enabled ? " · KI aktiv" : " · KI aus (kein Key)");
  const rows = d.books || [];
  if (!rows.length) {
    tb.innerHTML = `<tr><td colspan="10" class="empty">Initialisiere…</td></tr>`;
  } else {
    tb.innerHTML = rows.map(b => {
      const isChamp = b.role === "champion";
      const tag = isChamp ? `<span class="pill long">CHAMPION</span>`
        : `<span class="pill ${b.kind === 'ai' ? 'ai' : 'short'}">${(b.kind || '').toUpperCase()}</span>`;
      return `<tr class="${isChamp ? 'champ-row' : ''}">
        <td><b>${b.name}</b></td>
        <td>${tag}</td>
        <td class="${sign(b.total_return_pct)}">${fmt(b.total_return_pct, 1)}%</td>
        <td class="red">${fmt(b.max_dd_pct, 1)}%</td>
        <td>${b.trades}</td>
        <td>${fmt(b.win_rate, 0)}%</td>
        <td><b>${fmt(b.score, 2)}</b></td>
        <td>${b.days}</td>
        <td>${b.beat_streak > 0 ? "🔥" + b.beat_streak : "–"}</td>
        <td class="muted small">${escapeHtml(b.summary || "")}</td>
      </tr>`;
    }).join("");
  }
  const pel = document.getElementById("promotions");
  const proms = d.promotions || [];
  pel.innerHTML = proms.length
    ? proms.map(p => `<div class="line"><span class="lvl PROMO">PROMO</span>
        <span class="muted">${p.ts}</span> ${escapeHtml(p.detail || "")}</div>`).join("")
    : `<div class="empty">Noch keine Beförderung – Challenger sammeln Historie.</div>`;
}

function renderEquity(rows) {
  if (!rows) return;
  const labels = rows.map(r => r.ts.slice(5, 16));
  const data = rows.map(r => r.value);
  const ctx = document.getElementById("equityChart");
  if (!equityChart) {
    equityChart = new Chart(ctx, {
      type: "line",
      data: { labels, datasets: [{
        data, borderColor: "#4c8dff", backgroundColor: "rgba(76,141,255,.12)",
        fill: true, tension: .25, pointRadius: 0, borderWidth: 2 }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: "#8a94a6", maxTicksLimit: 8 }, grid: { color: "rgba(255,255,255,.04)" } },
          y: { ticks: { color: "#8a94a6" }, grid: { color: "rgba(255,255,255,.04)" } }
        }
      }
    });
  } else {
    equityChart.data.labels = labels;
    equityChart.data.datasets[0].data = data;
    equityChart.update("none");
  }
}

async function tick() {
  const [state, positions, trades, equity, catalysts, sectors, logs, analyst] = await Promise.all([
    getJSON("/api/state"), getJSON("/api/positions"), getJSON("/api/trades"),
    getJSON("/api/equity"), getJSON("/api/catalysts"), getJSON("/api/sectors"),
    getJSON("/api/logs"), getJSON("/api/analyst"),
  ]);
  renderKpis(state);
  renderPositions(positions);
  renderTrades(trades);
  renderEquity(equity);
  renderCatalysts(catalysts);
  renderRegime(sectors);
  renderLogs(logs);
  renderAnalyst(analyst);
}

tick();
setInterval(tick, REFRESH_MS);

// --- Snapshot speichern (Download) ---
document.getElementById("btnExport").addEventListener("click", async () => {
  try {
    const r = await fetch("/api/export");
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "cohen_snapshot.json";
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) { alert("Export fehlgeschlagen: " + e); }
});

// --- Snapshot laden (Upload in leeren Bot) ---
const importFile = document.getElementById("importFile");
document.getElementById("btnImport").addEventListener("click", () => importFile.click());
importFile.addEventListener("change", async () => {
  const file = importFile.files[0];
  if (!file) return;
  try {
    const text = await file.text();
    const r = await fetch("/api/import", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: text,
    });
    const res = await r.json();
    alert(res.message || (res.ok ? "Geladen." : "Fehlgeschlagen."));
    if (res.ok) tick();
  } catch (e) { alert("Import fehlgeschlagen: " + e); }
  importFile.value = "";
});
