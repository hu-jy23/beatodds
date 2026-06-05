const app = {
  state: null,
  selectedEventId: null,
  selectedMarketId: null,
  selectedSide: "YES",
  infoTab: "rules",
  filter: "all",
  search: "",
  theme: localStorage.getItem("beatodds-theme") || "light",
  page: localStorage.getItem("beatodds-page") || "markets",
  userSection: localStorage.getItem("beatodds-user-section") || "overview",
  expandedPositionEvents: new Set(),
  expandedTradeEvents: new Set(),
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
  if (app.page === "markets") refreshSelectedMarketDetail();
}

async function post(path, payload) {
  app.state = await api(path, { method: "POST", body: JSON.stringify(payload) });
  app.selectedEventId = app.state.selected_event?.event_id || app.selectedEventId;
  app.selectedMarketId = app.state.selected?.market?.condition_id || app.selectedMarketId;
  app.selectedSide = app.state.selected?.side || app.state.state.selected_side || app.selectedSide || "YES";
  render();
  if (app.page === "markets") refreshSelectedMarketDetail();
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
  renderPage();
  renderAccountControls();
  renderMetrics();
  renderMarkets();
  renderSelected();
  renderConsoles();
  renderTimeline();
  renderUserPage();
  renderCharts();
}

function renderPage() {
  const isUser = app.page === "user";
  $("appShell").classList.toggle("hidden", isUser);
  $("userPage").classList.toggle("hidden", !isUser);
  $("marketsPageBtn").classList.toggle("active", !isUser);
  $("userPageBtn").classList.toggle("active", isUser);
  if (isUser) {
    const account = app.state?.account_context?.selected_account || {};
    $("selectedTitle").textContent = account.name ? `User: ${account.name}` : "User";
  } else if (app.state?.selected_event) {
    $("selectedTitle").textContent = app.state.selected_event.title;
  }
}

function renderMetrics() {
  const stats = app.state.stats || {};
  const account = app.state.account_context?.selected_account || {};
  const items = [
    ["Events", stats.event_count || 0],
    ["Markets", stats.market_count || 0],
    ["Tracked", stats.tracked_count || 0],
  ];
  $("metricStrip").innerHTML = items
    .map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
  $("consoleSummary").innerHTML = [
    ["Cash", fmtMoney(account.cash_balance)],
    ["Reserved", fmtMoney(account.reserved_cash)],
    ["Fee", `${Number(account.fee_rate_bps || 0).toFixed(0)} bps`],
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

function renderAccountControls() {
  const context = app.state.account_context || {};
  const account = context.selected_account || {};
  const accounts = context.accounts || [];
  const activeLabel = account.name || account.account_id || "No user";
  $("userPageBtn").textContent = account.name ? activeLabel : "User";
  $("configAccountLabel").textContent = account.account_id
    ? `${activeLabel} · ${fmtMoney(account.available_cash)} available`
    : "--";

  $("accountList").innerHTML = accounts.length
    ? accounts
        .map((item) => `
          <button class="account-row ${item.account_id === account.account_id ? "active" : ""}" data-account-id="${escapeAttr(item.account_id)}">
            <strong>${escapeHtml(item.name || item.account_id)}</strong>
            <span>${fmtMoney(item.cash_balance)} cash · ${escapeHtml(item.sizing_mode || "all_in")} · fee ${Number(item.fee_rate_bps || 0).toFixed(0)} bps</span>
          </button>
        `)
        .join("")
    : `<div class="empty-panel">No paper users yet.</div>`;
  document.querySelectorAll(".account-row[data-account-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      app.loginOpen = false;
      await post("/api/login", { account_id: button.dataset.accountId });
    });
  });

  $("sizingMode").value = account.sizing_mode || "all_in";
  $("orderFraction").value = account.order_fraction ?? 1;
  $("maxOrderNotional").value = account.max_order_notional ?? account.initial_cash ?? 0;
  $("minCashBuffer").value = account.min_cash_buffer ?? 0;
  $("feeRateBps").value = account.fee_rate_bps ?? 0;
  $("slippageBps").value = account.slippage_bps ?? 0;
  $("maxTotalExposure").value = account.max_total_exposure ?? account.initial_cash ?? 0;
  $("autoTradeEnabled").checked = Boolean(account.auto_trade_enabled);
}

function renderUserPage() {
  const context = app.state.account_context || {};
  const account = context.selected_account || {};
  const stats = context.user_stats || {};
  const transactions = context.transactions || [];
  const positions = context.positions || [];
  const trades = context.trade_records || [];
  const positionGroups = context.position_event_groups || [];
  const tradeGroups = context.trade_event_groups || [];
  const icon = account.icon_url || "";
  $("userAvatar").innerHTML = icon
    ? `<img src="${escapeAttr(icon)}" alt="" loading="lazy" />`
    : `<span>${escapeHtml(initials(account.name || account.account_id))}</span>`;
  $("userName").textContent = account.name || "Paper User";
  $("userAccountId").textContent = account.account_id || "--";
  $("userModePill").textContent =
    `${account.sizing_mode || "all_in"} · fee ${Number(account.fee_rate_bps || 0).toFixed(0)} bps`;
  $("profileName").value = account.name || "";
  $("profileIconUrl").value = account.icon_url || "";
  $("profileNotes").value = account.notes || "";

  document.querySelectorAll(".user-nav-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.userSection === app.userSection);
  });
  const sectionIds = {
    overview: "userOverviewSection",
    profile: "userProfileSection",
    funds: "userFundsSection",
    positions: "userPositionsSection",
    agent: "userAgentSection",
  };
  Object.entries(sectionIds).forEach(([key, id]) => {
    $(id).classList.toggle("hidden", key !== app.userSection);
  });

  $("userStatGrid").innerHTML = [
    ["Equity", fmtMoney(account.equity), "cash + reserved"],
    ["Available", fmtMoney(account.available_cash), "after cash buffer"],
    ["Reserved", fmtMoney(account.reserved_cash), "open reserves"],
    ["Trades", String(stats.trade_count || 0), `${stats.position_count || 0} simulated positions`],
    ["Est. PnL", fmtSignedMoney(stats.estimated_pnl || 0), "from GUI paper deals"],
    ["Ledger rows", String(stats.transaction_count || 0), "account transactions"],
  ]
    .map(([label, value, detail]) => `
      <div class="user-stat">
        <span>${label}</span>
        <strong>${escapeHtml(value)}</strong>
        <p>${escapeHtml(detail)}</p>
      </div>
    `)
    .join("");

  $("ledgerStatus").textContent = `${transactions.length} rows`;
  $("transactionList").innerHTML = transactions.length
    ? transactions.slice(0, 24).map((tx) => `
        <div class="transaction-row">
          <strong>${escapeHtml(tx.transaction_type)}</strong>
          <span>${tx.created_at ? new Date(tx.created_at).toLocaleString() : ""}</span>
          <b>${fmtSignedMoney(tx.cash_delta)}</b>
          <small>cash ${fmtMoney(tx.cash_after)} · reserved ${fmtMoney(tx.reserved_after)} · ${escapeHtml(tx.memo || "")}</small>
        </div>
      `).join("")
    : `<div class="empty-panel">No ledger rows.</div>`;

  $("cashStatus").textContent = `${account.base_currency || "USD"} paper cash`;
  $("cashBreakdown").innerHTML = [
    ["Cash", fmtMoney(account.cash_balance)],
    ["Reserved", fmtMoney(account.reserved_cash)],
    ["Buffer", fmtMoney(account.min_cash_buffer)],
  ]
    .map(([label, value]) => `<div class="mini-stat"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");

  $("positionStatus").textContent = `${positions.length} simulated positions`;
  $("positionList").innerHTML = positionGroups.length
    ? renderEventPositionGroups(positionGroups)
    : `<div class="empty-panel">No formal positions yet. Simulated paper deals will aggregate here.</div>`;

  $("tradeStatus").textContent = `${trades.length} records`;
  $("tradeList").innerHTML = tradeGroups.length
    ? renderEventTradeGroups(tradeGroups)
    : `<div class="empty-panel">No paper trade records yet.</div>`;

  if (app.page === "user") drawUserNavChart($("userNavChart"), context.nav_points || []);
}

function renderEventPositionGroups(groups) {
  return groups.map((group) => `
    <section class="event-exposure-card ${app.expandedPositionEvents.has(group.event_id) ? "expanded" : ""}">
      <button class="event-exposure-head" type="button" data-exposure-kind="position" data-event-id="${escapeAttr(group.event_id)}">
        <span>
          <strong>${escapeHtml(group.event_title || group.event_id)}</strong>
          <small>${escapeHtml(group.event_category || "Event")} · ${group.row_count} markets · ${group.trade_count} trades</small>
        </span>
        <span class="event-exposure-actions">
          <b class="${Number(group.estimated_pnl || 0) >= 0 ? "positive" : "negative"}">${fmtSignedMoney(group.estimated_pnl)}</b>
          <small>${app.expandedPositionEvents.has(group.event_id) ? "Hide markets" : "Show markets"}</small>
        </span>
      </button>
      <div class="event-exposure-body">
        ${(group.rows || []).map((pos) => `
          <div class="market-exposure-row">
            <span>
              <strong>${escapeHtml(pos.question || shortId(pos.condition_id))}</strong>
              <small>${escapeHtml(pos.side || "YES")} · ${fmtSize(pos.shares)} shares · ${pos.trade_count || 0} trades</small>
            </span>
            <span>${fmtMoney(pos.notional)} notional</span>
            <b class="${Number(pos.estimated_pnl || 0) >= 0 ? "positive" : "negative"}">${fmtSignedMoney(pos.estimated_pnl)}</b>
          </div>
        `).join("")}
      </div>
    </section>
  `).join("");
}

function renderEventTradeGroups(groups) {
  return groups.map((group) => `
    <section class="event-exposure-card ${app.expandedTradeEvents.has(group.event_id) ? "expanded" : ""}">
      <button class="event-exposure-head" type="button" data-exposure-kind="trade" data-event-id="${escapeAttr(group.event_id)}">
        <span>
          <strong>${escapeHtml(group.event_title || group.event_id)}</strong>
          <small>${escapeHtml(group.event_category || "Event")} · ${group.trade_count} trade records</small>
        </span>
        <span class="event-exposure-actions">
          <b class="${Number(group.estimated_pnl || 0) >= 0 ? "positive" : "negative"}">${fmtSignedMoney(group.estimated_pnl)}</b>
          <small>${app.expandedTradeEvents.has(group.event_id) ? "Hide trades" : "Show trades"}</small>
        </span>
      </button>
      <div class="event-exposure-body">
        ${(group.rows || []).map((trade) => `
          <div class="market-exposure-row">
            <span>
              <strong>${escapeHtml(trade.question || shortId(trade.condition_id))}</strong>
              <small>${trade.at ? new Date(trade.at).toLocaleString() : ""}</small>
            </span>
            <span>${escapeHtml(trade.side || "YES")} ${fmtSize(trade.size)} · ${fmtMoney(trade.notional)}</span>
            <b class="${Number(trade.estimated_pnl || 0) >= 0 ? "positive" : "negative"}">${fmtSignedMoney(trade.estimated_pnl)}</b>
          </div>
        `).join("")}
      </div>
    </section>
  `).join("");
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

  if (app.page !== "user") $("selectedTitle").textContent = event.title;
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

function drawUserNavChart(canvas, points) {
  const ctx = setupCanvas(canvas);
  clearChart(ctx, canvas, "Account NAV");
  const values = (points || []).map((point) => Number(point.nav || 0));
  if (!values.length) {
    ctx.fillStyle = cssVar("--muted");
    ctx.font = "12px system-ui";
    ctx.fillText("No NAV history yet", 12, 54);
    return;
  }
  const pad = 34;
  const min = Math.min(...values) * 0.98;
  const max = Math.max(...values) * 1.02 || 1;
  drawLine(ctx, values, min, max, pad, ctx.logicalWidth, ctx.logicalHeight, "#25c383");
  ctx.fillStyle = cssVar("--muted");
  ctx.font = "11px system-ui";
  ctx.fillText(fmtMoney(values[values.length - 1]), 12, ctx.logicalHeight - 12);
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

function setPage(page) {
  app.page = page === "user" ? "user" : "markets";
  localStorage.setItem("beatodds-page", app.page);
  render();
  if (app.page === "markets") refreshSelectedMarketDetail();
}

function setUserSection(section) {
  app.userSection = section || "overview";
  localStorage.setItem("beatodds-user-section", app.userSection);
  renderUserPage();
}

function toggleExposureEvent(kind, eventId) {
  const target = kind === "trade" ? app.expandedTradeEvents : app.expandedPositionEvents;
  if (target.has(eventId)) target.delete(eventId);
  else target.add(eventId);
  renderUserPage();
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
  $("marketsPageBtn").addEventListener("click", () => setPage("markets"));
  $("userPageBtn").addEventListener("click", () => setPage("user"));
  document.querySelectorAll(".user-nav-btn").forEach((button) => {
    button.addEventListener("click", () => setUserSection(button.dataset.userSection));
  });
  $("createAccountBtn").addEventListener("click", async () => {
    const name = $("newAccountName").value.trim();
    if (!name) return;
    $("newAccountName").value = "";
    await post("/api/create-account", { name });
  });
  $("saveProfileBtn").addEventListener("click", async () => {
    await post("/api/account-profile", {
      name: $("profileName").value,
      icon_url: $("profileIconUrl").value,
      notes: $("profileNotes").value,
    });
  });
  $("depositBtn").addEventListener("click", async () => {
    await post("/api/account-funds", {
      action: "deposit",
      amount: Number($("fundAmount").value || 0),
      memo: $("fundMemo").value || "GUI deposit",
    });
    $("fundAmount").value = "";
  });
  $("withdrawBtn").addEventListener("click", async () => {
    await post("/api/account-funds", {
      action: "withdraw",
      amount: Number($("fundAmount").value || 0),
      memo: $("fundMemo").value || "GUI withdraw",
    });
    $("fundAmount").value = "";
  });
  $("saveConfigBtn").addEventListener("click", async () => {
    await post("/api/account-config", {
      sizing_mode: $("sizingMode").value,
      order_fraction: Number($("orderFraction").value || 1),
      max_order_notional: Number($("maxOrderNotional").value || 0),
      min_cash_buffer: Number($("minCashBuffer").value || 0),
      fee_rate_bps: Number($("feeRateBps").value || 0),
      slippage_bps: Number($("slippageBps").value || 0),
      max_total_exposure: Number($("maxTotalExposure").value || 0),
      auto_trade_enabled: $("autoTradeEnabled").checked,
    });
  });
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
  $("positionList").addEventListener("click", (event) => {
    const button = event.target.closest(".event-exposure-head[data-event-id]");
    if (!button) return;
    toggleExposureEvent("position", button.dataset.eventId);
  });
  $("tradeList").addEventListener("click", (event) => {
    const button = event.target.closest(".event-exposure-head[data-event-id]");
    if (!button) return;
    toggleExposureEvent("trade", button.dataset.eventId);
  });
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
