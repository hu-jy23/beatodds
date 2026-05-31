const app = {
  state: null,
  selectedId: null,
  filter: "all",
  search: "",
  updating: false,
  pendingUpdateAll: false,
};

const $ = (id) => document.getElementById(id);

function fmtPct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function fmtMoney(value) {
  const n = Number(value || 0);
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}m`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}k`;
  return `$${n.toFixed(0)}`;
}

function fmtSignedMoney(value) {
  const n = Number(value || 0);
  const sign = n >= 0 ? "+" : "-";
  return `${sign}$${Math.abs(n).toFixed(2)}`;
}

function shortId(id) {
  return id ? `${id.slice(0, 10)}...${id.slice(-5)}` : "--";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function loadState() {
  app.state = await api("/api/state");
  app.selectedId = app.state.selected?.market?.condition_id || app.state.state.selected_id;
  render();
}

async function post(path, payload) {
  app.state = await api(path, { method: "POST", body: JSON.stringify(payload) });
  app.selectedId = app.state.selected?.market?.condition_id || app.selectedId;
  render();
}

function render() {
  renderMetrics();
  renderMarkets();
  renderSelected();
  renderConsoles();
  renderTimeline();
  renderCharts();
}

function renderMetrics() {
  const stats = app.state.stats || {};
  const items = [
    ["Markets", stats.market_count || 0],
    ["Tracked", stats.tracked_count || 0],
    ["24h volume", fmtMoney(stats.total_volume_24h)],
    ["Liquidity", fmtMoney(stats.total_liquidity)],
  ];
  $("metricStrip").innerHTML = items
    .map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
  $("consoleSummary").innerHTML = [
    ["Notes", stats.note_count || 0],
    ["Actions", stats.action_count || 0],
    ["Paper deals", stats.deal_count || 0],
  ]
    .map(
      ([label, value]) => `
        <div class="mini-stat">
          <span>${label}</span>
          <strong>${value}</strong>
        </div>
      `,
    )
    .join("");
}

function renderMarkets() {
  const tracked = new Set(app.state.state.tracked_ids || []);
  let markets = app.state.markets || [];
  const query = app.search.trim().toLowerCase();
  if (query) {
    markets = markets.filter((m) => m.question.toLowerCase().includes(query));
  }
  if (app.filter === "tracked") markets = markets.filter((m) => tracked.has(m.condition_id));
  if (app.filter === "neg") markets = markets.filter((m) => m.neg_risk);

  $("marketList").innerHTML = markets
    .map((m) => {
      const active = m.condition_id === app.selectedId ? "active" : "";
      const stance = marketStanceClass(m);
      const tag = m.neg_risk ? `<span class="tag green">neg-risk</span>` : `<span class="tag">binary</span>`;
      const mark = tracked.has(m.condition_id) ? `<span class="tag amber">tracked</span>` : "";
      return `
        <button class="market-item ${active} ${stance}" data-id="${m.condition_id}">
          <span class="market-title">${escapeHtml(m.question)}</span>
          <span class="market-meta">
            <span>${fmtMoney(m.volume_24h)} vol</span>
            <span>${tag}${mark}</span>
          </span>
          <span class="mini-row"><span>${escapeHtml(m.category || "Market")}</span><span>${shortId(m.condition_id)}</span></span>
        </button>
      `;
    })
    .join("");

  document.querySelectorAll(".market-item").forEach((el) => {
    el.addEventListener("click", async () => {
      animateClick(el);
      await post("/api/select", { condition_id: el.dataset.id });
      if (window.innerWidth < 980) {
        $("appShell").classList.add("left-collapsed");
      }
    });
  });
}

function renderSelected() {
  const selected = app.state.selected;
  if (!selected) return;
  const market = selected.market;
  const analysis = selected.analysis || {};
  const snapshot = selected.snapshot;
  const snapshotStatus = selected.snapshot_status || {};
  const forecast = selected.forecast;
  const tracked = new Set(app.state.state.tracked_ids || []);

  $("selectedTitle").textContent = market.question;
  $("stance").textContent = analysis.stance || "Observe";
  $("advice").textContent = analysis.advice || "No advice available.";
  $("edgePill").textContent = `edge ${fmtPct(analysis.edge || 0)}`;
  $("edgePill").className = `pill ${(analysis.edge || 0) > 0.02 ? "good" : (analysis.edge || 0) < -0.02 ? "bad" : ""}`;
  document.querySelector(".analysis-panel").className = `analysis-panel ${stanceClass(analysis.stance)}`;
  $("trackBtn").textContent = tracked.has(market.condition_id) ? "Untrack" : "Track";
  $("snapshotTime").textContent = snapshot?.snapshot_time ? new Date(snapshot.snapshot_time).toLocaleString() : "--";
  $("snapshotStatus").textContent = snapshotStatus.reason || "Live price status unavailable.";
  $("snapshotStatus").className = `data-status ${snapshotStatus.available ? "good" : "warn"}`;

  $("quoteGrid").innerHTML = [
    ["Bid", snapshot ? fmtPct(snapshot.best_bid) : "--"],
    ["Ask", snapshot ? fmtPct(snapshot.best_ask) : "--"],
    ["Market", fmtPct(analysis.p_m)],
    ["Fair", forecast ? fmtPct(forecast.p_f) : "--"],
    ["Spread", snapshot ? fmtPct(snapshot.spread) : "--"],
    ["Net edge", fmtPct(analysis.net_edge_estimate)],
  ]
    .map(([label, value]) => `<div class="quote-cell"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");

  const news = selected.related_news || selected.evidence || [];
  $("newsStatus").textContent =
    news.length > 0 ? `${news.length} related items` : "manual update fills this list";
  $("newsList").innerHTML =
    news.length === 0
      ? `<div class="evidence-item"><p>No related news stored for this topic yet. Use Update topic to fetch news and refresh the forecast.</p></div>`
      : news
          .map(
            (item) => `
              <div class="evidence-item">
                <a href="${escapeAttr(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title || item.source || "Evidence")}</a>
                <p>${escapeHtml(item.summary || item.query || "")}</p>
                <span>${escapeHtml(item.source || "")}${item.published_at ? ` · ${new Date(item.published_at).toLocaleDateString()}` : ""}</span>
              </div>
            `,
          )
          .join("");

  renderTopicBrief(selected);
}

function renderTimeline() {
  const logs = app.state.selected?.topic_logs || {};
  const notes = logs.notes || [];
  const history = (logs.actions || []).filter((item) => item.kind !== "note");
  const combined = [
    ...notes.map((n) => ({ ...n, type: "note" })),
    ...history.map((h) => ({
      at: h.at,
      condition_id: h.condition_id,
      text: actionLabel(h.kind),
      type: "action",
    })),
  ]
    .sort((a, b) => new Date(b.at) - new Date(a.at))
    .slice(0, 18);

  $("timeline").innerHTML =
    combined.length === 0
      ? `<div class="timeline-item"><span class="timeline-meta">No history yet</span><p>Actions and notes will appear here.</p></div>`
      : combined
          .map(
            (item) => `
              <div class="timeline-item">
                <span class="timeline-meta">
                  <span>${item.type}</span>
                  <span>${item.at ? new Date(item.at).toLocaleString() : ""}</span>
                </span>
                <p>${escapeHtml(item.text || "")}</p>
              </div>
            `,
          )
          .join("");
}

function renderTopicBrief(selected) {
  const market = selected.market || {};
  const snapshot = selected.snapshot || {};
  const forecast = selected.forecast || {};
  const analysis = selected.analysis || {};
  const logs = selected.topic_logs || {};
  $("topicStatus").textContent =
    `${(logs.actions || []).length} actions · ${(logs.notes || []).length} notes`;
  const cells = [
    ["Market", fmtPct(analysis.p_m), `${fmtMoney(market.volume_24h)} 24h volume`],
    ["Fair", forecast.p_f !== undefined ? fmtPct(forecast.p_f) : "--", forecast.model || "No stored forecast"],
    ["Spread", snapshot.spread !== undefined ? fmtPct(snapshot.spread) : "--", "current order book"],
    ["Net edge", fmtPct(analysis.net_edge_estimate), analysis.stance || "Observe"],
    ["Category", market.category || "Market", market.neg_risk ? "neg-risk structure" : "binary market"],
    ["Topic logs", `${(logs.deals || []).length} deals`, `${(logs.followups || []).length} follow-ups · ${(logs.reviews || []).length} reviews`],
  ];
  $("topicBrief").innerHTML = cells
    .map(
      ([label, value, detail]) => `
        <div class="topic-cell">
          <span>${label}</span>
          <strong>${escapeHtml(value)}</strong>
          <p>${escapeHtml(detail || "")}</p>
        </div>
      `,
    )
    .join("");

  const messages = logs.messages || [];
  $("topicMessages").innerHTML =
    messages.length === 0
      ? `<div class="message-item"><span>No topic drafts</span><strong>Follow-up creates a topic-specific message.</strong></div>`
      : messages
          .slice(0, 3)
          .map(
            (message) => `
              <div class="message-item">
                <span>${message.kind} · ${message.at ? new Date(message.at).toLocaleString() : ""}</span>
                <strong>${escapeHtml(message.title || "Generated brief")}</strong>
                <pre>${escapeHtml(message.body || "")}</pre>
              </div>
            `,
          )
          .join("");
}

function renderConsoles() {
  const report = app.state.tracked_report || {};
  const stats = [
    ["Tracked", report.tracked_count || 0],
    ["Neg-risk", report.neg_risk_count || 0],
    ["Track vol", fmtMoney(report.total_volume_24h)],
  ];
  $("trackingStatus").textContent = `${report.open_followups?.length || 0} open follow-ups`;
  $("trackingStats").innerHTML = stats
    .map(
      ([label, value]) => `
        <div class="mini-stat">
          <span>${label}</span>
          <strong>${value}</strong>
        </div>
      `,
    )
    .join("");

  const deals = report.recent_deals || [];
  const followups = report.open_followups || [];
  const reviews = app.state.state.reviews || [];
  const rows = [
    ...deals.map((deal) => ({
      label: "paper deal",
      title: deal.question || shortId(deal.condition_id),
      body:
        deal.human_report ||
        `YES ${deal.size} @ ${fmtPct(deal.limit_price)} · ${deal.status}`,
      pnl: deal.estimated_pnl,
      at: deal.at,
    })),
    ...followups.map((item) => ({
      label: "follow-up",
      title: item.question || shortId(item.condition_id),
      body: item.prompt,
      at: item.at,
    })),
    ...reviews.slice(0, 8).map((item) => ({
      label: "review",
      title: item.question || shortId(item.condition_id),
      body: `${item.stance || "Reviewed"} · ${item.summary || ""}`,
      at: item.at,
    })),
  ]
    .sort((a, b) => new Date(b.at) - new Date(a.at))
    .slice(0, 8);

  $("actionStatus").textContent =
    `${deals.length} deals · ${followups.length} follow-ups · ${reviews.length} reviews`;
  $("actionReport").innerHTML =
    rows.length === 0
      ? `<div class="report-item"><span>No action records</span><strong>Use the action buttons to create reports.</strong></div>`
      : rows
          .map(
            (row) => `
              <div class="report-item ${row.pnl !== undefined ? (Number(row.pnl) >= 0 ? "gain" : "loss") : ""}">
                <span>${row.label} · ${row.at ? new Date(row.at).toLocaleString() : ""}</span>
                <strong>${escapeHtml(row.title)}</strong>
                ${row.pnl !== undefined ? `<span>estimated PnL ${fmtSignedMoney(row.pnl)}</span>` : ""}
                <p>${escapeHtml(row.body || "")}</p>
              </div>
            `,
          )
          .join("");

  const specials = report.special_reports || [];
  $("specialReport").innerHTML =
    specials.length === 0
      ? `<div class="special-item"><span>No priority alert</span><strong>Reserved for material opportunities or loss warnings.</strong><p>High estimated gains, possible losses, and execution warnings will appear here.</p></div>`
      : specials
          .map(
            (item) => `
              <div class="special-item ${escapeAttr(item.severity || "")}">
                <span>${item.at ? new Date(item.at).toLocaleString() : ""}</span>
                <strong>${escapeHtml(item.title || "Special report")}</strong>
                <p>${escapeHtml(item.body || "")}</p>
              </div>
            `,
          )
          .join("");

  const messages = report.generated_messages || [];
  $("messageList").innerHTML =
    messages.length === 0
      ? `<div class="message-item"><span>No generated messages</span><strong>Follow-up creates a draft brief.</strong></div>`
      : messages
          .map(
            (message) => `
              <div class="message-item">
                <span>${message.kind} · ${message.at ? new Date(message.at).toLocaleString() : ""}</span>
                <strong>${escapeHtml(message.title || "Generated brief")}</strong>
                <pre>${escapeHtml(message.body || "")}</pre>
              </div>
            `,
          )
          .join("");
}

function stanceClass(stance) {
  const text = String(stance || "").toLowerCase();
  if (text.includes("yes")) return "stance-yes";
  if (text.includes("no") || text.includes("avoid")) return "stance-no";
  if (text.includes("track")) return "stance-track";
  return "stance-observe";
}

function marketStanceClass(market) {
  if (market.condition_id === app.selectedId && app.state.selected?.analysis) {
    return stanceClass(app.state.selected.analysis.stance);
  }
  const edge = (app.state.stats?.forecast_edges || []).find(
    (item) => item.condition_id === market.condition_id,
  )?.edge;
  if (edge > 0.005) return "stance-yes";
  if (edge < -0.005) return "stance-no";
  if (market.neg_risk) return "stance-track";
  return "stance-observe";
}

function animateClick(button) {
  if (!button) return;
  button.classList.remove("clicked");
  void button.offsetWidth;
  button.classList.add("clicked");
  window.setTimeout(() => button.classList.remove("clicked"), 320);
}

function actionLabel(kind) {
  const labels = {
    track: "Started tracking",
    untrack: "Stopped tracking",
    paper_deal: "Simulated a paper deal",
    follow_up: "Queued follow-up",
    reviewed: "Marked reviewed",
  };
  return labels[kind] || kind;
}

function renderCharts() {
  const selected = app.state.selected;
  drawPriceChart($("priceChart"), selected?.chart || []);
  drawStatsChart($("statsChart"), app.state.stats?.category_counts || []);
  drawEdgeChart($("edgeChart"), app.state.stats?.forecast_edges || []);
  drawTrackedChart($("trackedChart"), app.state.tracked_report?.category_counts || []);
}

function drawPriceChart(canvas, points) {
  const ctx = setupCanvas(canvas);
  clearChart(ctx, canvas, "Market vs fair probability");
  if (!points.length) return;
  const pad = { left: 48, right: 18, top: 34, bottom: 38 };
  const w = ctx.logicalWidth;
  const h = ctx.logicalHeight;
  const values = points.flatMap((p) => [p.market, p.fair]).filter((v) => v !== null && v !== undefined);
  const min = Math.max(0, Math.min(...values) - 0.02);
  const max = Math.min(1, Math.max(...values) + 0.02);
  drawAxes(ctx, points, min, max, pad, w, h);
  drawLine(ctx, points.map((p) => p.market), min, max, pad, w, h, "#2f6df6");
  if (points.some((p) => p.fair !== null)) {
    drawLine(ctx, points.map((p) => p.fair), min, max, pad, w, h, "#1b8f62");
  }
  drawLegend(ctx, [["Market", "#2f6df6"], ["Fair", "#1b8f62"]], w);
}

function drawStatsChart(canvas, categories) {
  const ctx = setupCanvas(canvas);
  clearChart(ctx, canvas, "Market category mix");
  const max = Math.max(1, ...categories.map(([, count]) => count));
  categories.slice(0, 6).forEach(([name, count], idx) => {
    const y = 40 + idx * 21;
    const width = (ctx.logicalWidth - 150) * (count / max);
    ctx.fillStyle = idx % 2 ? "#1b8f62" : "#2f6df6";
    ctx.fillRect(92, y, width, 10);
    ctx.fillStyle = "#61707d";
    ctx.font = "11px system-ui";
    ctx.fillText(String(name).slice(0, 12), 10, y + 9);
    ctx.fillText(String(count), 100 + width, y + 9);
  });
}

function drawEdgeChart(canvas, edges) {
  const ctx = setupCanvas(canvas);
  clearChart(ctx, canvas, "Stored forecast edges");
  const values = edges.slice(0, 10).map((e) => e.edge);
  if (!values.length) return;
  const max = Math.max(0.02, ...values.map((v) => Math.abs(v)));
  const center = ctx.logicalWidth / 2;
  ctx.strokeStyle = "#dce2e7";
  ctx.beginPath();
  ctx.moveTo(center, 32);
  ctx.lineTo(center, ctx.logicalHeight - 18);
  ctx.stroke();
  values.forEach((value, idx) => {
    const y = 42 + idx * 13;
    const width = (ctx.logicalWidth / 2 - 30) * (Math.abs(value) / max);
    ctx.fillStyle = value >= 0 ? "#1b8f62" : "#b42318";
    ctx.fillRect(value >= 0 ? center : center - width, y, width, 8);
  });
}

function drawTrackedChart(canvas, categories) {
  const ctx = setupCanvas(canvas);
  clearChart(ctx, canvas, "Tracked event mix");
  if (!categories.length) {
    ctx.fillStyle = "#61707d";
    ctx.font = "12px system-ui";
    ctx.fillText("No tracked markets yet", 12, 54);
    return;
  }
  const total = categories.reduce((sum, [, count]) => sum + count, 0);
  let start = -Math.PI / 2;
  const colors = ["#2f6df6", "#1b8f62", "#b46b00", "#61707d", "#b42318"];
  categories.slice(0, 5).forEach(([name, count], idx) => {
    const angle = (count / Math.max(1, total)) * Math.PI * 2;
    ctx.beginPath();
    ctx.moveTo(70, 88);
    ctx.fillStyle = colors[idx % colors.length];
    ctx.arc(70, 88, 42, start, start + angle);
    ctx.fill();
    ctx.fillRect(135, 48 + idx * 18, 8, 8);
    ctx.fillStyle = "#33434f";
    ctx.font = "11px system-ui";
    ctx.fillText(`${String(name).slice(0, 16)} (${count})`, 148, 56 + idx * 18);
    start += angle;
  });
}

function setupCanvas(canvas) {
  const scale = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const parentWidth = canvas.parentElement ? canvas.parentElement.clientWidth : 0;
  const cssWidth = Math.max(240, Math.floor(rect.width || parentWidth || 320));
  if (!canvas.dataset.logicalHeight) {
    canvas.dataset.logicalHeight = canvas.getAttribute("height") || String(canvas.clientHeight || 180);
  }
  const cssHeight = Number(canvas.dataset.logicalHeight);
  canvas.width = Math.floor(cssWidth * scale);
  canvas.height = Math.floor(cssHeight * scale);
  canvas.style.width = "100%";
  canvas.style.height = `${cssHeight}px`;
  const ctx = canvas.getContext("2d");
  ctx.scale(scale, scale);
  ctx.logicalWidth = cssWidth;
  ctx.logicalHeight = cssHeight;
  return ctx;
}

function clearChart(ctx, canvas, title) {
  const width = ctx.logicalWidth || canvas.clientWidth;
  const height = ctx.logicalHeight || Number(canvas.getAttribute("height"));
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#fbfcfd";
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "#172027";
  ctx.font = "600 13px system-ui";
  ctx.fillText(title, 12, 22);
}

function drawAxes(ctx, points, min, max, pad, w, h) {
  const x0 = pad.left;
  const x1 = w - pad.right;
  const y0 = h - pad.bottom;
  const y1 = pad.top;
  ctx.strokeStyle = "#dce2e7";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x0, y1);
  ctx.lineTo(x0, y0);
  ctx.lineTo(x1, y0);
  ctx.stroke();

  ctx.fillStyle = "#61707d";
  ctx.font = "11px system-ui";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  const ticks = [max, (max + min) / 2, min];
  ticks.forEach((value) => {
    const y = y0 - ((value - min) / Math.max(0.001, max - min)) * (y0 - y1);
    ctx.strokeStyle = "#edf1f4";
    ctx.beginPath();
    ctx.moveTo(x0, y);
    ctx.lineTo(x1, y);
    ctx.stroke();
    ctx.fillText(`${(value * 100).toFixed(1)}%`, x0 - 6, y);
  });

  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const first = points[0]?.at || "start";
  const last = points[points.length - 1]?.at || "now";
  ctx.fillText(String(first).slice(0, 12), x0, y0 + 8);
  ctx.fillText(String(last).slice(0, 12), x1, y0 + 8);
  ctx.fillText("time", (x0 + x1) / 2, y0 + 22);

  ctx.save();
  ctx.translate(13, (y0 + y1) / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("probability", 0, 0);
  ctx.restore();
}

function drawLine(ctx, values, min, max, pad, w, h, color) {
  const left = typeof pad === "number" ? pad : pad.left;
  const right = typeof pad === "number" ? pad : pad.right;
  const top = typeof pad === "number" ? pad : pad.top;
  const bottom = typeof pad === "number" ? pad : pad.bottom;
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  values.forEach((value, idx) => {
    if (value === null || value === undefined) return;
    const x = left + (idx / Math.max(1, values.length - 1)) * (w - left - right);
    const y = h - bottom - ((value - min) / Math.max(0.001, max - min)) * (h - top - bottom);
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function drawLegend(ctx, items, w) {
  items.forEach(([label, color], idx) => {
    const x = w - 130 + idx * 62;
    ctx.fillStyle = color;
    ctx.fillRect(x, 14, 8, 8);
    ctx.fillStyle = "#61707d";
    ctx.font = "11px system-ui";
    ctx.fillText(label, x + 12, 22);
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value || "#");
}

function bindEvents() {
  if (window.innerWidth < 980) {
    $("appShell").classList.add("right-collapsed");
  }
  if (window.innerWidth < 720) {
    $("appShell").classList.add("left-collapsed");
  }
  $("hideLeft").addEventListener("click", () => $("appShell").classList.add("left-collapsed"));
  $("showLeft").addEventListener("click", () => $("appShell").classList.remove("left-collapsed"));
  $("hideRight").addEventListener("click", () => $("appShell").classList.add("right-collapsed"));
  $("showRight").addEventListener("click", () => $("appShell").classList.remove("right-collapsed"));
  document.querySelectorAll("button").forEach((button) => {
    button.addEventListener("pointerdown", () => animateClick(button));
  });
  $("refreshBtn").addEventListener("click", loadState);
  $("marketSearch").addEventListener("input", (event) => {
    app.search = event.target.value;
    renderMarkets();
  });
  document.querySelectorAll(".segment").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".segment").forEach((b) => b.classList.remove("active"));
      button.classList.add("active");
      app.filter = button.dataset.filter;
      renderMarkets();
    });
  });
  $("trackBtn").addEventListener("click", () => {
    const tracked = new Set(app.state.state.tracked_ids || []);
    post("/api/track", { condition_id: app.selectedId, track: !tracked.has(app.selectedId) });
  });
  $("dealBtn").addEventListener("click", () => post("/api/action", { condition_id: app.selectedId, action: "paper_deal" }));
  $("followBtn").addEventListener("click", () => post("/api/action", { condition_id: app.selectedId, action: "follow_up" }));
  $("reviewBtn").addEventListener("click", () => post("/api/action", { condition_id: app.selectedId, action: "reviewed" }));
  $("updateCurrentBtn").addEventListener("click", async () => {
    await runUpdate("topic");
  });
  $("updateTrackedBtn").addEventListener("click", async () => {
    await runUpdate("tracked");
  });
  $("updateAllBtn").addEventListener("click", async () => {
    openUpdateDialog();
  });
  $("closeUpdateDialog").addEventListener("click", closeUpdateDialog);
  $("cancelUpdateAll").addEventListener("click", closeUpdateDialog);
  $("updateAllDialog").addEventListener("click", (event) => {
    if (event.target.id === "updateAllDialog") closeUpdateDialog();
  });
  document.querySelectorAll("input[name='updateScope']").forEach((input) => {
    input.addEventListener("change", updateDialogState);
  });
  $("confirmUpdateAll").addEventListener("click", async () => {
    const mode = document.querySelector("input[name='updateScope']:checked")?.value || "limited";
    const count = Number($("updateTopicCount").value || 8);
    closeUpdateDialog();
    await runUpdate("all", { updateAll: mode === "all", maxTopics: count });
  });
  $("clearTopicBtn").addEventListener("click", () => post("/api/clear-topic", { condition_id: app.selectedId }));
  $("clearAllBtn").addEventListener("click", () => post("/api/clear-all", {}));
  $("saveNoteBtn").addEventListener("click", async () => {
    const text = $("noteInput").value;
    $("noteInput").value = "";
    await post("/api/note", { condition_id: app.selectedId, text });
  });
  window.addEventListener("resize", renderCharts);
}

async function runUpdate(scope, options = {}) {
  if (app.updating) return;
  app.updating = true;
  const labels = {
    topic: "Updating topic...",
    tracked: "Updating tracked...",
    all: options.updateAll ? "Updating all..." : `Updating ${options.maxTopics || 8}...`,
  };
  setUpdateButtons(true, labels[scope] || "Updating...");
  try {
    if (scope === "all") {
      await post("/api/update-all", {
        condition_id: app.selectedId,
        max_topics: options.updateAll ? (app.state.markets || []).length : options.maxTopics || 8,
      });
    } else if (scope === "tracked") {
      await post("/api/update-tracked", { condition_id: app.selectedId });
    } else {
      await post("/api/update-current", { condition_id: app.selectedId });
    }
  } finally {
    app.updating = false;
    setUpdateButtons(false, "");
  }
}

function setUpdateButtons(disabled, label) {
  $("updateCurrentBtn").disabled = disabled;
  $("updateTrackedBtn").disabled = disabled;
  $("updateAllBtn").disabled = disabled;
  $("updateCurrentBtn").textContent = disabled ? label : "Update topic";
  $("updateTrackedBtn").textContent = disabled ? "Please wait" : "Update tracked";
  $("updateAllBtn").textContent = disabled ? "Please wait" : "Update all";
}

function openUpdateDialog() {
  $("updateAllDialog").classList.add("open");
  $("updateAllDialog").setAttribute("aria-hidden", "false");
  updateDialogState();
}

function closeUpdateDialog() {
  $("updateAllDialog").classList.remove("open");
  $("updateAllDialog").setAttribute("aria-hidden", "true");
}

function updateDialogState() {
  const mode = document.querySelector("input[name='updateScope']:checked")?.value || "limited";
  const countInput = $("updateTopicCount");
  countInput.disabled = mode === "all";
  if (mode === "all") {
    countInput.value = (app.state.markets || []).length || countInput.value;
  }
}

bindEvents();
loadState().catch((error) => {
  $("selectedTitle").textContent = "GUI data failed to load";
  $("advice").textContent = error.message;
});
