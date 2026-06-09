const app = {
  state: null,
  selectedEventId: null,
  selectedMarketId: null,
  selectedSide: "YES",
  infoTab: "rules",
  filter: "all",
  search: "",
  topicAddResult: null,
  topicFetchResult: null,
  updating: false,
  pendingUpdateAll: false,
  dragPanel: null,
  theme: localStorage.getItem("beatodds-theme") || "light",
  page: localStorage.getItem("beatodds-page") || "markets",
  userSection: localStorage.getItem("beatodds-user-section") || "overview",
  expandedPositionEvents: new Set(),
  expandedTradeEvents: new Set(),
  selectedManualSell: new Set(),
  maintainerRunning: false,
  maintainerPoll: null,
};

const $ = (id) => document.getElementById(id);

function logControl(action, detail = {}) {
  console.info(`[BeatOdds] ${action}`, {
    at: new Date().toISOString(),
    selectedEventId: app.selectedEventId,
    selectedMarketId: app.selectedMarketId,
    selectedSide: app.selectedSide,
    page: app.page,
    userSection: app.userSection,
    ...detail,
  });
}

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

function fmtMaybeSignedMoney(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return fmtSignedMoney(value);
}

function pnlClass(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "muted";
  return Number(value) >= 0 ? "positive" : "negative";
}

function expectedEdgeClass(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "muted";
  return Number(value) >= 0 ? "expected-positive" : "expected-negative";
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

async function loadState(options = {}) {
  const refreshMarks = options.refreshMarks !== false;
  app.state = await api("/api/state");
  app.selectedEventId = app.state.selected_event?.event_id || app.state.state.selected_event_id;
  app.selectedMarketId = app.state.selected?.market?.condition_id || app.state.state.selected_market_id;
  app.selectedSide = app.state.selected?.side || app.state.state.selected_side || app.selectedSide || "YES";
  render();
  if (app.page === "markets") refreshSelectedMarketDetail();
  if (refreshMarks && app.page === "user") refreshAccountMarks().catch((error) => {
    console.warn("[BeatOdds] account mark refresh failed", error);
  });
}

async function refreshAccountPositions(source = "positions section") {
  logControl("account positions refresh clicked", { source });
  app.page = "user";
  app.userSection = "positions";
  localStorage.setItem("beatodds-page", app.page);
  localStorage.setItem("beatodds-user-section", app.userSection);
  await loadState({ refreshMarks: false });
  await refreshAccountMarks();
  render();
  logControl("account positions refresh finished", {
    positions: app.state.account_context?.positions?.length || 0,
    trades: app.state.account_context?.trade_records?.length || 0,
  });
}

async function refreshAccountMarks() {
  if (!app.state || app.page !== "user") return;
  const payload = await api("/api/account-context");
  if (!payload.account_context) return;
  app.state.account_context = payload.account_context;
  render();
}

async function post(path, payload) {
  logControl("POST start", { path, payload });
  app.state = await api(path, { method: "POST", body: JSON.stringify(payload) });
  app.topicAddResult = app.state.topic_add_result || null;
  app.topicFetchResult = app.state.topic_fetch_result || null;
  app.selectedEventId = app.state.selected_event?.event_id || app.selectedEventId;
  app.selectedMarketId = app.state.selected?.market?.condition_id || app.selectedMarketId;
  app.selectedSide = app.state.selected?.side || app.state.state.selected_side || app.selectedSide || "YES";
  logControl("POST complete", {
    path,
    selectedEventForecasts: app.state.selected_event?.edge_count || 0,
    selectedEventDirection: app.state.selected_event?.forecast_direction || "observe",
  });
  render();
  if (app.page === "markets") await refreshSelectedMarketDetail();
}

async function addTopicFromSearch() {
  const query = $("marketSearch").value.trim();
  logControl("add topic clicked", { query });
  app.topicAddResult = { status: "pending", message: "Searching Polymarket online..." };
  renderTopicAddStatus();
  await post("/api/add-topic", { query });
}

async function getNewTopics() {
  const rawCap = Number($("newTopicCap")?.value || 100);
  const cap = Math.max(1, Math.min(Number.isFinite(rawCap) ? Math.floor(rawCap) : 100, 500));
  $("newTopicCap").value = String(cap);
  logControl("get new topics clicked", { cap });
  app.topicFetchResult = { status: "pending", message: `Fetching up to ${cap} fresh topics...` };
  renderTopicFetchStatus();
  await post("/api/get-new-topics", { cap });
}

async function refreshSelectedMarketDetail() {
  const marketId = app.selectedMarketId;
  if (!marketId) return;
  try {
    const side = app.selectedSide || "YES";
    const payload = await api(`/api/market/${encodeURIComponent(marketId)}?side=${encodeURIComponent(side)}`);
    if (marketId !== app.selectedMarketId || !payload.selected) return;
    app.state.selected = payload.selected;
    if (payload.selected_event) {
      app.state.selected_event = payload.selected_event;
      const idx = (app.state.events || []).findIndex((event) => (
        event.event_id === payload.selected_event.event_id
      ));
      if (idx >= 0) app.state.events[idx] = payload.selected_event;
    }
    app.selectedSide = payload.selected.side || side;
    app.state.state.selected_side = app.selectedSide;
    logControl("market detail refreshed", {
      forecasts: app.state.selected_event?.edge_count || 0,
      direction: app.state.selected_event?.forecast_direction || "observe",
    });
    renderMarkets();
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
          <div class="account-row ${item.account_id === account.account_id ? "active" : ""}">
            <button class="account-select-btn" data-account-id="${escapeAttr(item.account_id)}">
              <strong>${escapeHtml(item.name || item.account_id)}</strong>
              <span>${fmtMoney(item.cash_balance)} cash · ${escapeHtml(item.sizing_mode || "all_in")} · fee ${Number(item.fee_rate_bps || 0).toFixed(0)} bps</span>
            </button>
            <button class="icon-btn danger account-delete-btn" title="Delete user" data-account-id="${escapeAttr(item.account_id)}" data-account-name="${escapeAttr(item.name || item.account_id)}">&times;</button>
          </div>
        `)
        .join("")
    : `<div class="empty-panel">No paper users yet.</div>`;
  document.querySelectorAll(".account-select-btn[data-account-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      app.loginOpen = false;
      await post("/api/login", { account_id: button.dataset.accountId });
    });
  });
  document.querySelectorAll(".account-delete-btn[data-account-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      const accountId = button.dataset.accountId;
      const accountName = button.dataset.accountName || accountId;
      const ok = window.confirm(`Delete local paper user "${accountName}"?\n\nThis removes its local account ledger rows, orders, fills, and positions.`);
      if (!ok) return;
      logControl("delete account clicked", { accountId });
      await post("/api/delete-account", { account_id: accountId });
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
  const evalCurves = context.eval_curves || [];
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
    maintainer: "userMaintainerSection",
    agent: "userAgentSection",
  };
  Object.entries(sectionIds).forEach(([key, id]) => {
    $(id).classList.toggle("hidden", key !== app.userSection);
  });

  $("userStatGrid").innerHTML = [
    ["Equity", fmtMoney(account.equity), "cash + reserved"],
    ["Available", fmtMoney(account.available_cash), "after cash buffer"],
    ["Reserved", fmtMoney(account.reserved_cash), "open reserves"],
    ["Trades", String(stats.trade_count || 0), `${stats.position_count || 0} open positions`],
    ["PnL", fmtSignedMoney(stats.open_marked_pnl || 0), "current holds vs cost"],
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

  $("accountMoneyStrip").innerHTML = [
    ["Cash", fmtMoney(stats.cash_balance ?? account.cash_balance), "available paper cash"],
    [
      "Share hold",
      fmtMoney(stats.projected_share_value ?? stats.share_hold_cost),
      `${fmtMoney(stats.share_hold_cost || 0)} cost · ${stats.open_marked_count || 0} marked`,
    ],
    ["Total money", fmtMoney(stats.total_account_money ?? account.equity), "cash + reserved + shares"],
    ["Earn / loss", fmtMaybeSignedMoney(stats.total_earn_loss), `initial ${fmtMoney(stats.initial_cash ?? account.initial_cash)}`],
  ].map(([label, value, detail]) => `
    <div class="account-money-cell ${label === "Earn / loss" ? pnlClass(stats.total_earn_loss) : ""}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(detail)}</small>
    </div>
  `).join("");

  $("positionStatus").textContent = `${positions.length} formal positions`;
  $("positionList").innerHTML = positionGroups.length
    ? renderEventPositionGroups(positionGroups)
    : `<div class="empty-panel">No formal open positions yet.</div>`;

  $("tradeStatus").textContent = `${trades.length} records`;
  $("tradeList").innerHTML = tradeGroups.length
    ? renderEventTradeGroups(tradeGroups)
    : `<div class="empty-panel">No paper trade records yet.</div>`;

  if (app.page === "user") drawUserNavChart($("userNavChart"), context.nav_points || []);
  renderEvalCurves(evalCurves);
  renderMaintainerSection(context);
}

function renderEvalCurves(curves) {
  const grid = $("evalCurveGrid");
  if (!grid) return;
  grid.innerHTML = curves.length
    ? curves.map((curve, idx) => {
      const latest = curve.latest || {};
      const canvasId = `evalCurveCanvas${idx}`;
      return `
        <section class="eval-curve-card">
          <div class="eval-curve-head">
            <div>
              <strong>${escapeHtml(curve.label || curve.account_id || "paper account")}</strong>
              <small>${escapeHtml(curve.status || "")}</small>
            </div>
            <b class="${pnlClass(latest.pnl)}">${fmtMaybeSignedMoney(latest.pnl)}</b>
          </div>
          <canvas id="${canvasId}" width="520" height="180"></canvas>
          <div class="eval-curve-meta">
            <span>${escapeHtml(latest.at ? new Date(latest.at).toLocaleString() : "No reports yet")}</span>
            <span>${fmtMoney(latest.current_value || 0)} value / ${fmtMoney(latest.invested || 0)} invested</span>
            <span>${Number(latest.marked || 0)} marked · ${Number(latest.unmarked || 0)} unmarked</span>
          </div>
          <code>${escapeHtml(curve.command || "")}</code>
        </section>
      `;
    }).join("")
    : `<div class="empty-panel">No paper eval report folders found.</div>`;
  curves.forEach((curve, idx) => {
    const canvas = $(`evalCurveCanvas${idx}`);
    if (canvas) drawEvalReportCurve(canvas, curve.points || [], curve.label || "Eval curve");
  });
}

function renderMaintainerSection(context) {
  const account = context.selected_account || {};
  const stats = context.user_stats || {};
  const maintainer = context.maintainer || {};
  const summary = maintainer.summary || {};
  const params = maintainer.params || {};
  const positions = context.positions || [];
  const decisions = maintainer.recent_decisions || [];
  const consoleLogs = maintainer.console_logs || [];
  $("maintainerStatusPill").textContent = app.maintainerRunning ? "running" : (summary.last_run_id ? "ready" : "not run");
  $("earningStatus").textContent =
    `${fmtSignedMoney(stats.open_marked_pnl || 0)} · ${fmtMoney(stats.open_marked_value || 0)} current holds`;
  $("strategyStatus").textContent = summary.last_finished_at
    ? new Date(summary.last_finished_at).toLocaleString()
    : "no completed run";
  $("strategyGrid").innerHTML = [
    ["Entry edge", `${fmtPct(params.min_edge || 0)} gross / ${fmtPct(params.min_net_edge || 0)} net`],
    ["Confidence", fmtPct(params.min_confidence || 0)],
    ["Spread cap", fmtPct(params.max_spread || 0)],
    ["Order cap", fmtMoney(params.max_order_notional || account.max_order_notional || 0)],
    ["Sell return", fmtPct(params.sell_min_return || 0)],
    ["Stop loss", fmtPct(params.sell_max_loss || 0)],
    ["Last sold", String(summary.last_sold || 0)],
    ["Last buys", String(summary.last_buys || 0)],
  ].map(([label, value]) => `
    <div class="strategy-cell">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `).join("");
  const validKeys = new Set(positions.map((pos) => positionKey(pos)));
  app.selectedManualSell = new Set(
    Array.from(app.selectedManualSell).filter((key) => validKeys.has(key)),
  );
  $("maintainerPositionStatus").textContent =
    `${positions.length} open positions · ${app.selectedManualSell.size} selected`;
  $("maintainerPositionList").innerHTML = positions.length
    ? positions.map((pos) => {
      const key = positionKey(pos);
      const selected = app.selectedManualSell.has(key);
      return `
      <label class="manual-sell-card ${selected ? "selected" : ""}">
        <input class="manual-sell-check" type="checkbox" data-key="${escapeAttr(key)}" ${selected ? "checked" : ""} />
        <span class="manual-sell-main">
          <strong>${escapeHtml(pos.question || shortId(pos.condition_id))}</strong>
          <small>${escapeHtml(pos.side || "YES")} · ${fmtSize(pos.shares)} shares · avg ${fmtToken(pos.avg_price)} · ${fmtMoney(pos.current_value ?? pos.notional)} hold value</small>
        </span>
        <span class="manual-sell-side">${escapeHtml(pos.mark_source === "live_bid" ? `bid ${fmtToken(pos.current_bid)}` : "cost fallback")}</span>
        <b class="${pnlClass(pos.current_pnl)}">${fmtMaybeSignedMoney(pos.current_pnl)}</b>
      </label>
    `;
    }).join("")
    : `<div class="empty-panel">No open shares for this account.</div>`;
  bindManualSellChecks();
  updateManualSellButtons();
  $("maintainerDecisionStatus").textContent = `${decisions.length} recent rows`;
  $("maintainerDecisionList").innerHTML = decisions.length
    ? decisions.map((row) => `
      <div class="trade-row">
        <strong>${escapeHtml(row.action || row.phase || "decision")} · ${escapeHtml(row.side || "")}</strong>
        <span>${row.created_at ? new Date(row.created_at).toLocaleString() : ""}</span>
        <b class="${pnlClass(row.realized_pnl)}">${fmtMaybeSignedMoney(row.realized_pnl)}</b>
        <small>${escapeHtml(row.reason || row.question || shortId(row.condition_id))}</small>
      </div>
    `).join("")
    : `<div class="empty-panel">No maintainer strategy records yet.</div>`;
  renderMaintainerConsole("overviewMaintainerConsole", "overviewMaintainerConsoleStatus", consoleLogs);
  renderMaintainerConsole("maintainerConsole", "maintainerConsoleStatus", consoleLogs);
  if (app.page === "user") drawEarningChart($("earningCurveChart"), context.earning_points || []);
}

function renderMaintainerConsole(listId, statusId, logs) {
  const list = $(listId);
  if (!list) return;
  $(statusId).textContent = `${logs.length} log rows`;
  list.innerHTML = logs.length
    ? logs.slice(0, 220).map((row) => `
      <div class="console-log-row ${row.status === "error" ? "error" : ""}">
        <div>
          <strong>${escapeHtml(row.kind || "maintainer")}</strong>
          <span>${row.at ? new Date(row.at).toLocaleString() : ""}</span>
        </div>
        <p>${escapeHtml(row.summary || "")}</p>
        <pre>${escapeHtml(row.detail || "")}</pre>
      </div>
    `).join("")
    : `<div class="empty-panel">No sell, purchase, or maintain logs yet.</div>`;
}

function positionKey(pos) {
  return `${pos.condition_id || ""}:${pos.side || "YES"}`;
}

function positionFromKey(key) {
  const idx = key.lastIndexOf(":");
  if (idx < 0) return { condition_id: key, side: "YES" };
  return {
    condition_id: key.slice(0, idx),
    side: key.slice(idx + 1) || "YES",
  };
}

function bindManualSellChecks() {
  document.querySelectorAll(".manual-sell-check").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const key = checkbox.dataset.key || "";
      if (!key) return;
      if (checkbox.checked) app.selectedManualSell.add(key);
      else app.selectedManualSell.delete(key);
      renderMaintainerSection(app.state.account_context || {});
    });
  });
}

function updateManualSellButtons() {
  const hasPositions = (app.state.account_context?.positions || []).length > 0;
  $("manualSellSelectedBtn").disabled = app.maintainerRunning || app.selectedManualSell.size === 0;
  $("manualSellAllBtn").disabled = app.maintainerRunning || !hasPositions;
  $("manualSellSelectAllBtn").disabled = app.maintainerRunning || !hasPositions;
  $("manualSellClearBtn").disabled = app.maintainerRunning || app.selectedManualSell.size === 0;
}

function selectAllManualSell() {
  const positions = app.state.account_context?.positions || [];
  positions.forEach((pos) => app.selectedManualSell.add(positionKey(pos)));
  renderMaintainerSection(app.state.account_context || {});
}

function clearManualSellSelection() {
  app.selectedManualSell.clear();
  renderMaintainerSection(app.state.account_context || {});
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
          <b class="${pnlClass(group.estimated_pnl)}">${fmtMaybeSignedMoney(group.estimated_pnl)}</b>
          <small>${escapeHtml(group.pnl_label || "current hold PnL")}</small>
          <small>${app.expandedPositionEvents.has(group.event_id) ? "Hide markets" : "Show markets"}</small>
        </span>
      </button>
      <div class="event-exposure-body">
        ${(group.rows || []).map((pos) => {
          const pnl = pos.current_pnl ?? pos.estimated_pnl;
          return `
          <div class="market-exposure-row">
            <span>
              <strong>${escapeHtml(pos.question || shortId(pos.condition_id))}</strong>
              <small>${escapeHtml(pos.side || "YES")} · ${fmtSize(pos.shares)} shares · ${pos.trade_count || 0} trades · ${escapeHtml(pos.mark_source === "live_bid" ? `bid ${fmtToken(pos.current_bid)}` : "cost fallback")}</small>
            </span>
            <span>${fmtMoney(pos.current_value ?? pos.notional)} hold value</span>
            <b class="${pnlClass(pnl)}">${fmtMaybeSignedMoney(pnl)}</b>
          </div>
        `;
        }).join("")}
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
          <b class="${expectedEdgeClass(group.estimated_pnl)}">${fmtMaybeSignedMoney(group.estimated_pnl)}</b>
          <small>${escapeHtml(group.pnl_label || "expected edge")}</small>
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
            <b class="${expectedEdgeClass(trade.estimated_pnl)}">${fmtMaybeSignedMoney(trade.estimated_pnl)} exp.</b>
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

  renderTopicAddStatus();
  renderTopicFetchStatus();
  $("marketList").innerHTML = events.length === 0
    ? `<div class="market-empty">
        <strong>No loaded events match this search.</strong>
        <span>Use Add to search Polymarket online and track a matching topic.</span>
      </div>`
    : events
    .map((event) => {
      const active = event.event_id === app.selectedEventId ? "active" : "";
      const stance = eventStanceClass(event);
      const tag = event.neg_risk_count ? `<span class="tag green">neg-risk</span>` : `<span class="tag">event</span>`;
      const mark = event.tracked_count ? `<span class="tag amber">tracked</span>` : "";
      const forecastTag = event.edge_count
        ? `<span class="tag ${forecastTagClass(event)}">${forecastDirectionLabel(event.forecast_direction)}</span>`
        : "";
      return `
        <button class="market-item ${active} ${stance}" data-id="${event.event_id}">
          <span class="market-title">${escapeHtml(event.title)}</span>
          <span class="market-meta">
            <span>${fmtMoney(event.volume_24h)} vol</span>
            <span>${tag}${mark}${forecastTag}</span>
          </span>
          <span class="mini-row">
            <span>${escapeHtml(event.category || "Event")}</span>
            <span>${event.market_count || 0} markets · ${event.edge_count || 0} forecasts</span>
          </span>
        </button>
      `;
    })
    .join("");

  document.querySelectorAll(".market-item").forEach((el) => {
    el.addEventListener("click", async () => {
      animateClick(el);
      logControl("event selected", { eventId: el.dataset.id });
      await post("/api/select-event", { event_id: el.dataset.id });
      if (window.innerWidth < 980) {
        $("appShell").classList.add("left-collapsed");
      }
    });
  });
}

function renderTopicAddStatus() {
  const result = app.topicAddResult;
  const el = $("topicAddStatus");
  if (!el) return;
  if (!result?.message) {
    el.textContent = "";
    el.className = "topic-add-status";
    return;
  }
  el.textContent = result.message;
  el.className = `topic-add-status ${result.status || ""}`;
}

function renderTopicFetchStatus() {
  const result = app.topicFetchResult;
  const el = $("topicFetchStatus");
  if (!el) return;
  if (!result?.message) {
    el.textContent = "";
    el.className = "topic-add-status";
    return;
  }
  el.textContent = result.message;
  el.className = `topic-add-status ${result.status || ""}`;
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
  $("edgePill").textContent = event.edge_count
    ? `${event.edge_count} forecasts · ${forecastDirectionLabel(event.forecast_direction)}`
    : "0 forecasts";
  $("edgePill").className = `pill ${forecastPillClass(event)}`;
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
                <a href="${escapeAttr(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title || item.source || "News")}</a>
                <p>${escapeHtml(item.summary || item.query || "")}</p>
                <span>${escapeHtml(item.source || "")}${item.published_at ? ` · ${new Date(item.published_at).toLocaleDateString()}` : ""}</span>
              </div>
            `,
          )
          .join("");

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
                <span>${escapeHtml(item.source || "")}${item.published_at ? ` · ${new Date(item.published_at).toLocaleDateString()}` : ""}</span>
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
            const forecast = market.p_f === undefined
              ? "no forecast"
              : `${forecastDirectionLabel(market.forecast_direction)} · fair ${fmtPct(market.p_f)}`;
            const yesSource = market.yes_price_source === "live_ask" ? "live ask" : "stored";
            const noSource = market.no_price_source === "live_ask" ? "live ask" : "stored";
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
                    <small>${escapeHtml(yesSource)}</small>
                  </button>
                  <button class="token-btn no ${noActive}" data-id="${market.condition_id}" data-side="NO">
                    <span>${escapeHtml(market.no_label || "NO")}</span>
                    <strong>${fmtToken(market.no_price)}</strong>
                    <small>${escapeHtml(noSource)}</small>
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
      logControl("market side selected", {
        conditionId: el.dataset.id,
        side: app.selectedSide,
      });
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
  if ((event.edge_count || 0) > 0) {
    if (event.forecast_direction === "tend_no") return "stance-no";
    if (event.forecast_direction === "tend_yes") return "stance-yes";
    if ((event.forecast_edge || 0) < -0.02) return "stance-no";
    if ((event.forecast_edge || 0) > 0.02) return "stance-yes";
    return "stance-observe";
  }
  if ((event.neg_risk_count || 0) > 0) return "stance-track";
  return "stance-observe";
}

function forecastDirectionLabel(direction) {
  if (direction === "tend_yes") return "tend yes";
  if (direction === "tend_no") return "tend no";
  return "observe";
}

function forecastTagClass(event) {
  if (event.forecast_direction === "tend_yes") return "green";
  if (event.forecast_direction === "tend_no") return "red";
  return "amber";
}

function forecastPillClass(event) {
  if (!event.edge_count) return "";
  if (event.forecast_direction === "tend_yes") return "good";
  if (event.forecast_direction === "tend_no") return "bad";
  if ((event.forecast_edge || 0) > 0.02) return "good";
  if ((event.forecast_edge || 0) < -0.02) return "bad";
  return "";
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
  const pad = { left: 58, right: 66, top: 48, bottom: 58 };
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

function drawEarningChart(canvas, points) {
  const ctx = setupCanvas(canvas);
  clearChart(ctx, canvas, "Earning curve");
  const values = (points || []).map((point) => Number(point.pnl || 0));
  if (!values.length) {
    ctx.fillStyle = cssVar("--muted");
    ctx.font = "12px system-ui";
    ctx.fillText("No earning history yet", 12, 54);
    return;
  }
  const pad = 34;
  const min = Math.min(-1, ...values) * 1.08;
  const max = Math.max(1, ...values) * 1.08;
  drawLine(ctx, values, min, max, pad, ctx.logicalWidth, ctx.logicalHeight, "#2f6df6");
  const zeroY = ctx.logicalHeight - pad - ((0 - min) / Math.max(0.001, max - min)) * (ctx.logicalHeight - pad * 2);
  ctx.strokeStyle = cssVar("--line");
  ctx.beginPath();
  ctx.moveTo(pad, zeroY);
  ctx.lineTo(ctx.logicalWidth - pad, zeroY);
  ctx.stroke();
  ctx.fillStyle = values[values.length - 1] >= 0 ? cssVar("--green") : cssVar("--red");
  ctx.font = "11px system-ui";
  ctx.fillText(fmtSignedMoney(values[values.length - 1]), 12, ctx.logicalHeight - 12);
}

function drawEvalReportCurve(canvas, points, title) {
  const ctx = setupCanvas(canvas);
  clearChart(ctx, canvas, title);
  const values = (points || []).map((point) => Number(point.pnl || 0));
  if (!values.length) {
    ctx.fillStyle = cssVar("--muted");
    ctx.font = "12px system-ui";
    ctx.fillText("No eval report history yet", 12, 54);
    return;
  }
  const pad = 32;
  const min = Math.min(-1, ...values) * 1.08;
  const max = Math.max(1, ...values) * 1.08;
  drawLine(ctx, values, min, max, pad, ctx.logicalWidth, ctx.logicalHeight, "#1b8f62");
  const zeroY = ctx.logicalHeight - pad - ((0 - min) / Math.max(0.001, max - min)) * (ctx.logicalHeight - pad * 2);
  ctx.strokeStyle = cssVar("--line");
  ctx.beginPath();
  ctx.moveTo(pad, zeroY);
  ctx.lineTo(ctx.logicalWidth - pad, zeroY);
  ctx.stroke();
  ctx.fillStyle = values[values.length - 1] >= 0 ? cssVar("--green") : cssVar("--red");
  ctx.font = "11px system-ui";
  ctx.fillText(fmtSignedMoney(values[values.length - 1]), 12, ctx.logicalHeight - 12);
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

  ctx.textBaseline = "top";
  const first = points[0]?.at || "start";
  const last = points[points.length - 1]?.at || "now";
  ctx.textAlign = "left";
  ctx.fillText(String(first).slice(0, 12), x0, y0 + 8);
  ctx.textAlign = "right";
  ctx.fillText(String(last).slice(0, 12), x1, y0 + 8);
  ctx.textAlign = "center";
  ctx.fillText("time", (x0 + x1) / 2, y0 + 30);

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
  const itemWidth = 66;
  const start = Math.max(150, w - items.length * itemWidth - 18);
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  items.forEach(([label, color], idx) => {
    const x = start + idx * itemWidth;
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
  logControl("page switch", { targetPage: page });
  app.page = page === "user" ? "user" : "markets";
  localStorage.setItem("beatodds-page", app.page);
  render();
  if (app.page === "markets") refreshSelectedMarketDetail();
}

function setUserSection(section) {
  logControl("user section switch", { section });
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
  $("refreshBtn").addEventListener("click", () => {
    if (app.page === "user" && app.userSection === "positions") {
      refreshAccountPositions("global refresh button");
      return;
    }
    logControl("manual refresh clicked");
    loadState();
  });
  $("marketsPageBtn").addEventListener("click", () => setPage("markets"));
  $("userPageBtn").addEventListener("click", () => setPage("user"));
  document.querySelectorAll(".user-nav-btn").forEach((button) => {
    button.addEventListener("click", () => setUserSection(button.dataset.userSection));
  });
  $("createAccountBtn").addEventListener("click", async () => {
    const name = $("newAccountName").value.trim();
    if (!name) return;
    logControl("create account clicked", { name });
    $("newAccountName").value = "";
    await post("/api/create-account", { name });
  });
  $("saveProfileBtn").addEventListener("click", async () => {
    logControl("save profile clicked");
    await post("/api/account-profile", {
      name: $("profileName").value,
      icon_url: $("profileIconUrl").value,
      notes: $("profileNotes").value,
    });
  });
  $("depositBtn").addEventListener("click", async () => {
    logControl("deposit clicked", { amount: Number($("fundAmount").value || 0) });
    await post("/api/account-funds", {
      action: "deposit",
      amount: Number($("fundAmount").value || 0),
      memo: $("fundMemo").value || "GUI deposit",
    });
    $("fundAmount").value = "";
  });
  $("withdrawBtn").addEventListener("click", async () => {
    logControl("withdraw clicked", { amount: Number($("fundAmount").value || 0) });
    await post("/api/account-funds", {
      action: "withdraw",
      amount: Number($("fundAmount").value || 0),
      memo: $("fundMemo").value || "GUI withdraw",
    });
    $("fundAmount").value = "";
  });
  $("saveConfigBtn").addEventListener("click", async () => {
    logControl("save account config clicked");
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
  $("refreshPositionsBtn").addEventListener("click", () => {
    refreshAccountPositions("positions panel button");
  });
  $("maintainerUpdateBtn").addEventListener("click", () => runMaintainerAction("update"));
  $("maintainerSellBtn").addEventListener("click", () => runMaintainerAction("sell"));
  $("maintainerBuyBtn").addEventListener("click", () => runMaintainerAction("purchase"));
  $("maintainerRunBtn").addEventListener("click", () => runMaintainerAction("maintain"));
  $("manualSellSelectAllBtn").addEventListener("click", selectAllManualSell);
  $("manualSellClearBtn").addEventListener("click", clearManualSellSelection);
  $("manualSellSelectedBtn").addEventListener("click", runManualSellSelected);
  $("manualSellAllBtn").addEventListener("click", runManualSellAll);
  $("themeToggle").addEventListener("click", () => {
    applyTheme(app.theme === "dark" ? "light" : "dark");
  });
  $("marketSearch").addEventListener("input", (event) => {
    app.search = event.target.value;
    app.topicAddResult = null;
    renderMarkets();
  });
  $("marketSearch").addEventListener("keydown", async (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    await addTopicFromSearch();
  });
  $("addTopicBtn").addEventListener("click", addTopicFromSearch);
  $("getNewTopicsBtn").addEventListener("click", getNewTopics);
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
    logControl("track toggle clicked", {
      nextTrack: !tracked.has(app.selectedMarketId),
    });
    post("/api/track", {
      condition_id: app.selectedMarketId,
      track: !tracked.has(app.selectedMarketId),
    });
  });
  $("dealBtn").addEventListener("click", () => {
    logControl("paper deal clicked");
    post("/api/action", { condition_id: app.selectedMarketId, action: "paper_deal" });
  });
  $("followBtn").addEventListener("click", () => {
    logControl("follow up clicked");
    post("/api/action", { condition_id: app.selectedMarketId, action: "follow_up" });
  });
  $("reviewBtn").addEventListener("click", () => {
    logControl("review clicked");
    post("/api/action", { condition_id: app.selectedMarketId, action: "reviewed" });
  });
  $("updateCurrentBtn").addEventListener("click", async () => {
    logControl("update+forecast topic clicked");
    await runUpdate("topic");
  });
  $("updateTrackedBtn").addEventListener("click", async () => {
    logControl("update+forecast tracked clicked");
    await runUpdate("tracked");
  });
  $("updateAllBtn").addEventListener("click", async () => {
    logControl("update+forecast all dialog opened");
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
    logControl("update+forecast all confirmed", { mode, count });
    closeUpdateDialog();
    await runUpdate("all", { updateAll: mode === "all", maxTopics: count });
  });
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
  initPanelInteractions();
}

function initPanelInteractions() {
  restorePanelOrder();
  document.querySelectorAll(".draggable-panel").forEach((panel) => {
    panel.setAttribute("draggable", "true");
    panel.addEventListener("dragstart", (event) => {
      if (isInteractiveTarget(event.target)) {
        event.preventDefault();
        return;
      }
      app.dragPanel = panel;
      panel.classList.add("dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", panel.dataset.panelId || "");
    });
    panel.addEventListener("dragend", () => {
      panel.classList.remove("dragging");
      app.dragPanel = null;
      savePanelOrder();
      renderCharts();
    });
  });

  document.querySelectorAll(".panel-zone").forEach((zone) => {
    zone.addEventListener("dragover", (event) => {
      if (!app.dragPanel || app.dragPanel.parentElement !== zone) return;
      event.preventDefault();
      const after = panelAfterPointer(zone, event.clientY);
      if (after) zone.insertBefore(app.dragPanel, after);
      else zone.appendChild(app.dragPanel);
    });
  });

  if ("ResizeObserver" in window) {
    const resizeObserver = new ResizeObserver(() => renderCharts());
    document.querySelectorAll(".draggable-panel").forEach((panel) => resizeObserver.observe(panel));
  }
}

function isInteractiveTarget(target) {
  return !!target.closest("button, input, textarea, a, canvas, label");
}

function panelAfterPointer(zone, y) {
  const candidates = [...zone.querySelectorAll(".draggable-panel:not(.dragging)")];
  return candidates.reduce(
    (closest, child) => {
      const box = child.getBoundingClientRect();
      const offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > closest.offset) return { offset, element: child };
      return closest;
    },
    { offset: Number.NEGATIVE_INFINITY, element: null },
  ).element;
}

function savePanelOrder() {
  const order = {};
  document.querySelectorAll(".panel-zone").forEach((zone) => {
    order[zone.dataset.zone] = [...zone.querySelectorAll(".draggable-panel")]
      .map((panel) => panel.dataset.panelId)
      .filter(Boolean);
  });
  localStorage.setItem("beatodds.panelOrder", JSON.stringify(order));
}

function restorePanelOrder() {
  let order = {};
  try {
    order = JSON.parse(localStorage.getItem("beatodds.panelOrder") || "{}");
  } catch {
    order = {};
  }
  document.querySelectorAll(".panel-zone").forEach((zone) => {
    const ids = order[zone.dataset.zone] || [];
    ids.forEach((id) => {
      const panel = zone.querySelector(`.draggable-panel[data-panel-id="${CSS.escape(id)}"]`);
      if (panel) zone.appendChild(panel);
    });
  });
}

async function runUpdate(scope, options = {}) {
  if (app.updating) return;
  app.updating = true;
  logControl("update+forecast started", {
    scope,
    options,
    beforeForecasts: app.state.selected_event?.edge_count || 0,
  });
  const labels = {
    topic: "Forecasting topic...",
    tracked: "Forecasting tracked...",
    all: options.updateAll ? "Forecasting all..." : `Forecasting ${options.maxTopics || 8}...`,
  };
  setUpdateButtons(true, labels[scope] || "Updating...");
  try {
    if (scope === "all") {
      await post("/api/update-all", {
        condition_id: app.selectedMarketId,
        max_topics: options.updateAll ? (app.state.events || []).length : options.maxTopics || 8,
      });
    } else if (scope === "tracked") {
      await post("/api/update-tracked", { condition_id: app.selectedMarketId });
    } else {
      await post("/api/update-current", { condition_id: app.selectedMarketId });
    }
    logControl("update+forecast finished", {
      scope,
      afterForecasts: app.state.selected_event?.edge_count || 0,
      direction: app.state.selected_event?.forecast_direction || "observe",
    });
  } finally {
    app.updating = false;
    setUpdateButtons(false, "");
  }
}

function setUpdateButtons(disabled, label) {
  $("updateCurrentBtn").disabled = disabled;
  $("updateTrackedBtn").disabled = disabled;
  $("updateAllBtn").disabled = disabled;
  $("updateCurrentBtn").textContent = disabled ? label : "Update + forecast topic";
  $("updateTrackedBtn").textContent = disabled ? "Please wait" : "Update + forecast tracked";
  $("updateAllBtn").textContent = disabled ? "Please wait" : "Update + forecast all";
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
    countInput.value = (app.state.events || []).length || countInput.value;
  }
}

async function runMaintainerAction(action, extraPayload = {}) {
  if (app.maintainerRunning) return;
  app.maintainerRunning = true;
  logControl("maintainer action started", {
    action,
    dryRun: $("maintainerDryRun").checked,
  });
  setMaintainerButtons(true, action);
  app.maintainerPoll = window.setInterval(() => {
    loadState({ refreshMarks: false }).catch(() => {});
  }, 1500);
  try {
    await post("/api/maintainer-action", {
      action,
      dry_run: $("maintainerDryRun").checked,
      ...extraPayload,
    });
    const maintainer = app.state.account_context?.maintainer || {};
    logControl("maintainer action finished", {
      action,
      lastSold: maintainer.summary?.last_sold || 0,
      lastBuys: maintainer.summary?.last_buys || 0,
      consoleRows: maintainer.console_logs?.length || 0,
    });
  } finally {
    if (app.maintainerPoll) {
      window.clearInterval(app.maintainerPoll);
      app.maintainerPoll = null;
    }
    app.maintainerRunning = false;
    setMaintainerButtons(false, "");
    if (action === "manual_sell" && !$("maintainerDryRun").checked) {
      app.selectedManualSell.clear();
    }
    loadState({ refreshMarks: false })
      .then(() => refreshAccountMarks())
      .catch(() => {});
  }
}

function runManualSellSelected() {
  const positions = Array.from(app.selectedManualSell).map(positionFromKey);
  return runMaintainerAction("manual_sell", {
    positions,
    all_positions: false,
    sell_fraction: 1.0,
  });
}

function runManualSellAll() {
  return runMaintainerAction("manual_sell", {
    positions: [],
    all_positions: true,
    sell_fraction: 1.0,
  });
}

function setMaintainerButtons(disabled, action) {
  [
    "maintainerUpdateBtn",
    "maintainerSellBtn",
    "maintainerBuyBtn",
    "maintainerRunBtn",
    "manualSellSelectedBtn",
    "manualSellAllBtn",
    "manualSellSelectAllBtn",
    "manualSellClearBtn",
  ].forEach((id) => {
    if ($(id)) $(id).disabled = disabled;
  });
  $("maintainerStatusPill").textContent = disabled ? `${action}...` : "ready";
  if (!disabled) updateManualSellButtons();
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
