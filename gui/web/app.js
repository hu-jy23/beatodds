const app = {
  state: null,
  selectedEventId: null,
  selectedMarketId: null,
  selectedSide: "YES",
  infoTab: "rules",
  filter: "all",
  search: "",
  theme: localStorage.getItem("beatodds-theme") || "light",
};

const $ = (id) => document.getElementById(id);

function fmtPct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function fmtToken(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const cents = Number(value) * 100;
  const digits = cents < 10 && cents !== 0 ? 1 : 0;
  return `${cents.toFixed(digits)}¢`;
}

function fmtMoney(value) {
  const n = Number(value || 0);
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}m`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}k`;
  return `$${n.toFixed(0)}`;
}

function fmtSize(value) {
  const n = Number(value || 0);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}m`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toFixed(n >= 10 ? 0 : 2);
}

function fmtDate(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  return date.toLocaleDateString();
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
  app.selectedEventId = app.state.selected_event?.event_id || app.state.state.selected_event_id;
  app.selectedMarketId = app.state.selected?.market?.condition_id || app.state.state.selected_market_id;
  app.selectedSide = app.state.selected?.side || app.state.state.selected_side || app.selectedSide || "YES";
  render();
  refreshSelectedMarketDetail();
}

async function post(path, payload) {
  app.state = await api(path, { method: "POST", body: JSON.stringify(payload) });
  app.selectedEventId = app.state.selected_event?.event_id || app.selectedEventId;
  app.selectedMarketId = app.state.selected?.market?.condition_id || app.selectedMarketId;
  app.selectedSide = app.state.selected?.side || app.state.state.selected_side || app.selectedSide || "YES";
  render();
  refreshSelectedMarketDetail();
}

async function refreshSelectedMarketDetail() {
  const marketId = app.selectedMarketId;
  if (!marketId) return;
  try {
    const side = app.selectedSide || "YES";
    const payload = await api(`/api/market/${encodeURIComponent(marketId)}?side=${encodeURIComponent(side)}`);
    if (marketId !== app.selectedMarketId || !payload.selected) return;
    app.state.selected = payload.selected;
    app.selectedSide = payload.selected.side || side;
    app.state.state.selected_side = app.selectedSide;
    renderSelected();
    renderTimeline();
    renderCharts();
  } catch (error) {
    console.warn("Live market detail failed", error);
  }
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
    ["Events", stats.event_count || 0],
    ["Markets", stats.market_count || 0],
    ["Tracked", stats.tracked_count || 0],
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
  let events = app.state.events || [];
  const query = app.search.trim().toLowerCase();
  if (query) {
    events = events.filter((e) => {
      const haystack = [e.title, e.category, ...(e.tags || [])].join(" ").toLowerCase();
      return haystack.includes(query);
    });
  }
  if (app.filter === "tracked") events = events.filter((e) => Number(e.tracked_count || 0) > 0);
  if (app.filter === "neg") events = events.filter((e) => Number(e.neg_risk_count || 0) > 0);

  $("marketList").innerHTML = events
    .map((event) => {
      const active = event.event_id === app.selectedEventId ? "active" : "";
      const stance = eventStanceClass(event);
      const tag = event.neg_risk_count ? `<span class="tag green">neg-risk</span>` : `<span class="tag">event</span>`;
      const mark = event.tracked_count ? `<span class="tag amber">tracked</span>` : "";
      return `
        <button class="market-item ${active} ${stance}" data-id="${event.event_id}">
          <span class="market-title">${escapeHtml(event.title)}</span>
          <span class="market-meta">
            <span>${fmtMoney(event.volume_24h)} vol</span>
            <span>${tag}${mark}</span>
          </span>
          <span class="mini-row">
            <span>${escapeHtml(event.category || "Event")}</span>
            <span>${event.market_count || 0} markets</span>
          </span>
        </button>
      `;
    })
    .join("");

  document.querySelectorAll(".market-item").forEach((el) => {
    el.addEventListener("click", async () => {
      animateClick(el);
      await post("/api/select-event", { event_id: el.dataset.id });
      if (window.innerWidth < 980) {
        $("appShell").classList.add("left-collapsed");
      }
    });
  });
}

function renderSelected() {
  const event = app.state.selected_event;
  const selected = app.state.selected;
  if (!event) return;
  const market = selected?.market || {};
  const analysis = selected?.analysis || {};
  const snapshot = selected?.snapshot;
  const snapshotStatus = selected?.snapshot_status || {};
  const forecast = selected?.forecast;
  const tracked = new Set(app.state.state.tracked_ids || []);
  const side = selected?.side || app.selectedSide || "YES";
  app.selectedSide = side;

  $("selectedTitle").textContent = event.title;
  $("stance").textContent = event.category || "Event";
  $("edgePill").textContent = `${event.edge_count || 0} forecasts`;
  $("edgePill").className = `pill ${(event.max_abs_edge || 0) > 0.02 ? "good" : ""}`;
  document.querySelector(".analysis-panel").className = `analysis-panel ${eventStanceClass(event)}`;
  renderEventHero(event);
  $("eventMarketCount").textContent = `${event.markets?.length || 0} markets`;
  $("trackBtn").textContent = tracked.has(market.condition_id) ? "Untrack" : "Track";
  $("selectedMarketTitle").textContent = market.question || "No market selected";
  $("selectedMarketMeta").textContent = market.condition_id
    ? `${side} book · ${market.category || "Market"} · ${shortId(market.condition_id)}`
    : "--";
  $("snapshotTime").textContent = snapshot?.snapshot_time ? new Date(snapshot.snapshot_time).toLocaleString() : "--";
  $("snapshotStatus").textContent = snapshotStatus.reason || "Live price status unavailable.";
  $("snapshotStatus").className = `data-status ${snapshotStatus.available ? "good" : "warn"}`;
  renderEventInfo(event);

  renderEventMarkets(event);

  $("quoteGrid").innerHTML = [
    [`${side} Bid`, snapshot ? fmtPct(snapshot.best_bid) : "--"],
    [`${side} Ask`, snapshot ? fmtPct(snapshot.best_ask) : "--"],
    [`${side} Market`, fmtPct(analysis.p_m)],
    [`${side} Fair`, forecast ? fmtPct(analysis.p_f) : "--"],
    ["Spread", snapshot ? fmtPct(snapshot.spread) : "--"],
    ["Net edge", fmtPct(analysis.net_edge_estimate)],
  ]
    .map(([label, value]) => `<div class="quote-cell"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
  renderOrderBook(snapshot, side);

  const evidence = selected?.evidence || [];
  $("evidenceList").innerHTML =
    evidence.length === 0
      ? `<div class="evidence-item"><p>No stored evidence for this market yet.</p></div>`
      : evidence
          .map(
            (item) => `
              <div class="evidence-item">
                <a href="${escapeAttr(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title || item.source || "Evidence")}</a>
                <p>${escapeHtml(item.summary || item.query || "")}</p>
              </div>
            `,
          )
          .join("");

  renderTopicBrief(event, selected);
}

function renderEventHero(event) {
  const image = event.icon || event.image || "";
  $("eventIcon").innerHTML = image
    ? `<img src="${escapeAttr(image)}" alt="" loading="lazy" />`
    : `<span>${escapeHtml(initials(event.title))}</span>`;
  const tags = [event.category, ...(event.tags || [])].filter(Boolean).slice(0, 6);
  $("eventTags").innerHTML = tags.length
    ? tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")
    : `<span class="tag">event</span>`;
  const meta = [
    ["24h Volume", fmtMoney(event.volume_24h)],
    ["Liquidity", fmtMoney(event.liquidity)],
    ["Markets", String(event.market_count || 0)],
    ["End", fmtDate(event.end_time)],
  ];
  $("eventMeta").innerHTML = meta
    .map(([label, value]) => `<span><strong>${escapeHtml(value)}</strong>${escapeHtml(label)}</span>`)
    .join("");
}

function renderOrderBook(snapshot, side) {
  const book = snapshot?.order_book || {};
  const asks = book.asks || [];
  const bids = book.bids || [];
  if (!asks.length && !bids.length) {
    $("orderBook").innerHTML = `
      <div class="book-empty">No live order book levels returned for ${escapeHtml(side)}.</div>
    `;
    return;
  }
  $("orderBook").innerHTML = `
    <div class="book-title-row">
      <strong>${escapeHtml(side)} Order Book</strong>
      <span>${asks.length} asks · ${bids.length} bids</span>
    </div>
    <div class="order-book-grid">
      ${renderBookSide("Asks", asks, "ask")}
      ${renderBookSide("Bids", bids, "bid")}
    </div>
  `;
}

function renderBookSide(label, rows, kind) {
  const body = rows.length
    ? rows.map((row) => `
        <div class="book-row ${kind}">
          <span>${fmtToken(row.price)}</span>
          <span>${fmtSize(row.size)}</span>
          <span>${fmtMoney(row.total)}</span>
        </div>
      `).join("")
    : `<div class="book-row muted"><span>--</span><span>--</span><span>--</span></div>`;
  return `
    <div class="book-side">
      <div class="book-side-head">
        <strong>${label}</strong>
        <span>Price · Shares · Total</span>
      </div>
      <div class="book-scroll">
        ${body}
      </div>
    </div>
  `;
}

function renderEventInfo(event) {
  const tab = app.infoTab === "background" ? "background" : "rules";
  const rules = event.rules || "No explicit resolution rules stored for this event.";
  const background = event.background || event.description || "No market background stored for this event.";
  $("eventInfoContent").textContent = tab === "rules" ? rules : background;
  $("eventInfoStatus").textContent = tab === "rules" ? "resolution criteria" : "market background";
  $("eventRulesTab").classList.toggle("active", tab === "rules");
  $("eventBackgroundTab").classList.toggle("active", tab === "background");
}

function renderEventMarkets(event) {
  const markets = event.markets || [];
  $("eventMarketList").innerHTML =
    markets.length === 0
      ? `<div class="event-market-row empty"><strong>No markets found for this event.</strong></div>`
      : markets
          .map((market) => {
            const active = market.condition_id === app.selectedMarketId ? "active" : "";
            const yesActive = active && app.selectedSide === "YES" ? "active" : "";
            const noActive = active && app.selectedSide === "NO" ? "active" : "";
            const edge = market.edge;
            const edgeText = edge === undefined ? "--" : fmtPct(edge);
            const forecast = market.p_f === undefined ? "no forecast" : `fair ${fmtPct(market.p_f)}`;
            return `
              <div class="event-market-row ${active}" data-id="${market.condition_id}">
                <button class="market-main" data-id="${market.condition_id}" data-side="YES">
                  <strong>${escapeHtml(market.question)}</strong>
                  <small>${escapeHtml(market.neg_risk ? "neg-risk" : "binary")} · ${shortId(market.condition_id)}</small>
                </button>
                <span class="market-stat">${fmtMoney(market.volume_24h)} vol</span>
                <span class="market-stat">${forecast}</span>
                <span class="market-stat ${Number(edge || 0) >= 0 ? "positive" : "negative"}">${edgeText}</span>
                <span class="token-buttons">
                  <button class="token-btn yes ${yesActive}" data-id="${market.condition_id}" data-side="YES">
                    <span>${escapeHtml(market.yes_label || "YES")}</span>
                    <strong>${fmtToken(market.yes_price)}</strong>
                  </button>
                  <button class="token-btn no ${noActive}" data-id="${market.condition_id}" data-side="NO">
                    <span>${escapeHtml(market.no_label || "NO")}</span>
                    <strong>${fmtToken(market.no_price)}</strong>
                  </button>
                </span>
              </div>
            `;
          })
          .join("");

  document.querySelectorAll(".market-main[data-id], .token-btn[data-id]").forEach((el) => {
    el.addEventListener("click", async () => {
      animateClick(el);
      app.selectedSide = el.dataset.side || "YES";
      await post("/api/select-market", { condition_id: el.dataset.id, side: app.selectedSide });
    });
  });
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

function renderTopicBrief(event, selected) {
  const market = selected?.market || {};
  const snapshot = selected?.snapshot || {};
  const forecast = selected?.forecast || {};
  const analysis = selected?.analysis || {};
  const logs = selected?.topic_logs || {};
  $("topicStatus").textContent =
    `${event.market_count || 0} markets · ${(logs.actions || []).length} market actions`;
  const cells = [
    ["Event volume", fmtMoney(event.volume_24h), `${fmtMoney(event.liquidity)} liquidity`],
    ["Markets", String(event.market_count || 0), `${event.neg_risk_count || 0} neg-risk markets`],
    ["Forecasted", String(event.edge_count || 0), `max |edge| ${fmtPct(event.max_abs_edge || 0)}`],
    [`Selected ${selected?.side || app.selectedSide || "YES"}`, fmtPct(analysis.p_m), market.question || "No market selected"],
    ["Fair", analysis.p_f !== undefined ? fmtPct(analysis.p_f) : "--", forecast.model || "No stored forecast"],
    ["Market logs", `${(logs.deals || []).length} deals`, `${(logs.followups || []).length} follow-ups · ${(logs.reviews || []).length} reviews`],
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
  if (market.condition_id === app.selectedMarketId && app.state.selected?.analysis) {
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

function eventStanceClass(event) {
  if ((event.max_abs_edge || 0) > 0.02) return "stance-yes";
  if ((event.neg_risk_count || 0) > 0) return "stance-track";
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
  const pad = 34;
  const w = ctx.logicalWidth;
  const h = ctx.logicalHeight;
  const values = points.flatMap((p) => [p.market, p.fair]).filter((v) => v !== null && v !== undefined);
  const min = Math.max(0, Math.min(...values) - 0.02);
  const max = Math.min(1, Math.max(...values) + 0.02);
  drawLine(ctx, points.map((p) => p.market), min, max, pad, w, h, "#2f6df6");
  if (points.some((p) => p.fair !== null)) {
    drawLine(ctx, points.map((p) => p.fair), min, max, pad, w, h, "#1b8f62");
  }
  drawLegend(ctx, [["Market", "#2f6df6"], ["Fair", "#1b8f62"]], w);
}

function drawStatsChart(canvas, categories) {
  const ctx = setupCanvas(canvas);
  clearChart(ctx, canvas, "Event category mix");
  const max = Math.max(1, ...categories.map(([, count]) => count));
  categories.slice(0, 6).forEach(([name, count], idx) => {
    const y = 40 + idx * 21;
    const width = (ctx.logicalWidth - 150) * (count / max);
    ctx.fillStyle = idx % 2 ? "#1b8f62" : "#2f6df6";
    ctx.fillRect(92, y, width, 10);
    ctx.fillStyle = cssVar("--muted");
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
  ctx.strokeStyle = cssVar("--line");
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
    ctx.fillStyle = cssVar("--muted");
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
    ctx.fillStyle = cssVar("--text");
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
  ctx.fillStyle = cssVar("--panel");
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = cssVar("--text");
  ctx.font = "600 13px system-ui";
  ctx.fillText(title, 12, 22);
}

function drawLine(ctx, values, min, max, pad, w, h, color) {
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  values.forEach((value, idx) => {
    if (value === null || value === undefined) return;
    const x = pad + (idx / Math.max(1, values.length - 1)) * (w - pad * 2);
    const y = h - pad - ((value - min) / Math.max(0.001, max - min)) * (h - pad * 2);
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
    ctx.fillStyle = cssVar("--muted");
    ctx.font = "11px system-ui";
    ctx.fillText(label, x + 12, 22);
  });
}

function cssVar(name) {
  return getComputedStyle(document.body).getPropertyValue(name).trim();
}

function initials(value) {
  return String(value || "BO")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("") || "BO";
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
  applyTheme(app.theme);
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
  $("themeToggle").addEventListener("click", () => {
    applyTheme(app.theme === "dark" ? "light" : "dark");
  });
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
  document.querySelectorAll(".tab-btn[data-info-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      app.infoTab = button.dataset.infoTab || "rules";
      renderEventInfo(app.state.selected_event || {});
    });
  });
  $("trackBtn").addEventListener("click", () => {
    const tracked = new Set(app.state.state.tracked_ids || []);
    post("/api/track", {
      condition_id: app.selectedMarketId,
      track: !tracked.has(app.selectedMarketId),
    });
  });
  $("dealBtn").addEventListener("click", () => post("/api/action", { condition_id: app.selectedMarketId, action: "paper_deal" }));
  $("followBtn").addEventListener("click", () => post("/api/action", { condition_id: app.selectedMarketId, action: "follow_up" }));
  $("reviewBtn").addEventListener("click", () => post("/api/action", { condition_id: app.selectedMarketId, action: "reviewed" }));
  $("clearTopicBtn").addEventListener("click", () => post("/api/clear-topic", { condition_id: app.selectedMarketId }));
  $("clearAllBtn").addEventListener("click", () => post("/api/clear-all", {}));
  $("saveNoteBtn").addEventListener("click", async () => {
    const text = $("noteInput").value;
    $("noteInput").value = "";
    await post("/api/note", { condition_id: app.selectedMarketId, text });
  });
  window.addEventListener("resize", renderCharts);
}

function applyTheme(theme) {
  app.theme = theme === "dark" ? "dark" : "light";
  document.body.dataset.theme = app.theme;
  localStorage.setItem("beatodds-theme", app.theme);
  $("themeToggle").textContent = app.theme === "dark" ? "Light" : "Dark";
  if (app.state) renderCharts();
}

bindEvents();
loadState().catch((error) => {
  $("selectedTitle").textContent = "GUI data failed to load";
  $("stance").textContent = error.message;
});
