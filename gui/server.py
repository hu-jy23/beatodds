"""Small local web server for the BeatOdds operator GUI."""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import subprocess
import sys
import threading
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import duckdb
from loguru import logger

from beatodds.common.config import get_settings
from beatodds.common.db import ensure_schema
from beatodds.common.types import CandidateMarket, EventMeta, MarketMeta, PriceSnapshot
from beatodds.data.clob_client import ClobReadClient
from beatodds.data.gamma_client import GammaClient
from beatodds.evaluation.paper_store import (
    create_paper_account,
    deposit_cash,
    ensure_default_paper_account,
    load_account_transactions,
    load_paper_account,
    load_paper_accounts,
    load_paper_orders,
    load_paper_positions,
    update_account_profile,
    update_risk_params,
    withdraw_cash,
)
from beatodds.evaluation.workflow_store import save_forecast_run
from beatodds.evidence.forecaster import LLMForecaster
from beatodds.evidence.retriever import EvidenceRetriever
from beatodds.resolution_parser.parser import ResolutionParser

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "gui" / "web"


@dataclass
class GuiState:
    tracked_ids: list[str]
    selected_id: str | None
    selected_event_id: str | None
    selected_market_id: str | None
    selected_side: str
    selected_account_id: str | None
    notes: list[dict]
    actions: list[dict]
    deals: list[dict]
    followups: list[dict]
    reviews: list[dict]
    messages: list[dict]
    news: list[dict]
    topic_feed_offset: int = 0


class GuiStore:
    def __init__(self):
        self.cfg = get_settings()
        self.path = self.cfg.data_dir / "gui_state.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def load(self) -> GuiState:
        with self._lock:
            if not self.path.exists():
                return GuiState([], None, None, None, "YES", None, [], [], [], [], [], [], 0)
            data = json.loads(self.path.read_text(encoding="utf-8"))
            selected_market_id = data.get("selected_market_id") or data.get("selected_id")
            return GuiState(
                tracked_ids=list(data.get("tracked_ids", [])),
                selected_id=selected_market_id,
                selected_event_id=data.get("selected_event_id"),
                selected_market_id=selected_market_id,
                selected_side=_side(data.get("selected_side")),
                selected_account_id=data.get("selected_account_id"),
                notes=list(data.get("notes", [])),
                actions=list(data.get("actions", [])),
                deals=list(data.get("deals", [])),
                followups=list(data.get("followups", [])),
                reviews=list(data.get("reviews", [])),
                messages=list(data.get("messages", [])),
                news=list(data.get("news", [])),
                topic_feed_offset=int(data.get("topic_feed_offset") or 0),
            )

    def save(self, state: GuiState) -> None:
        with self._lock:
            state.selected_id = state.selected_market_id
            state.selected_side = _side(state.selected_side)
            self.path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")

    def append_action(self, kind: str, condition_id: str, payload: dict | None = None) -> None:
        state = self.load()
        state.actions.insert(
            0,
            {
                "at": _now(),
                "kind": kind,
                "condition_id": condition_id,
                "payload": payload or {},
            },
        )
        state.actions = state.actions[:600]
        self.save(state)


class GuiData:
    def __init__(self):
        self.cfg = get_settings()
        self.store = GuiStore()
        self._parser: ResolutionParser | None = None
        self._retriever: EvidenceRetriever | None = None
        self._forecaster: LLMForecaster | None = None

    @property
    def market_db(self) -> Path:
        return self.cfg.data_dir / "beatodds.duckdb"

    @property
    def eval_db(self) -> Path:
        return self.cfg.data_dir / "eval.duckdb"

    def _columns(self, conn, table: str) -> set[str]:
        try:
            return {row[1] for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
        except Exception:
            return set()

    def _event_meta(self) -> dict[str, dict]:
        if not self.market_db.exists():
            return {}
        try:
            with duckdb.connect(str(self.market_db), read_only=True) as conn:
                tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
                if "events" not in tables:
                    return {}
                columns = self._columns(conn, "events")
                image_select = "image" if "image" in columns else "'' AS image"
                icon_select = "icon" if "icon" in columns else "'' AS icon"
                rows = conn.execute(
                    f"""
                    SELECT event_id, title, slug, category, tags_json, end_time,
                           volume_24h, liquidity, active, description,
                           {image_select}, {icon_select}
                    FROM events
                    """
                ).fetchall()
        except Exception:
            return {}
        meta: dict[str, dict] = {}
        for row in rows:
            try:
                tags = json.loads(row[4] or "[]")
            except json.JSONDecodeError:
                tags = []
            meta[row[0]] = {
                "event_id": row[0],
                "title": row[1] or "",
                "slug": row[2] or "",
                "category": row[3] or "Uncategorized",
                "tags": tags if isinstance(tags, list) else [],
                "end_time": _iso(row[5]),
                "volume_24h": float(row[6] or 0),
                "liquidity": float(row[7] or 0),
                "active": bool(row[8]),
                "description": row[9] or "",
                "image": row[10] or "",
                "icon": row[11] or "",
            }
        return meta

    def _market_by_id(self, condition_id: str) -> dict | None:
        if not condition_id:
            return None
        if not self.market_db.exists():
            return None
        with duckdb.connect(str(self.market_db), read_only=True) as conn:
            columns = self._columns(conn, "markets")
            event_select = "event_id" if "event_id" in columns else "'' AS event_id"
            price_select = (
                "outcome_prices_json"
                if "outcome_prices_json" in columns else
                "'[]' AS outcome_prices_json"
            )
            row = conn.execute(
                f"""
                SELECT condition_id, {event_select}, question, category, neg_risk,
                       token_yes_id, token_no_id, close_time, volume_24h, liquidity,
                       active, slug, description, resolution_text, created_time,
                       outcomes_json, {price_select}
                FROM markets
                WHERE condition_id = ?
                LIMIT 1
                """,
                [condition_id],
            ).fetchone()
        if not row:
            return None
        return {
            "condition_id": row[0],
            "event_id": row[1] or row[0],
            "question": row[2],
            "category": row[3] or "Uncategorized",
            "neg_risk": bool(row[4]),
            "token_yes_id": row[5] or "",
            "token_no_id": row[6] or "",
            "close_time": _iso(row[7]),
            "volume_24h": float(row[8] or 0),
            "liquidity": float(row[9] or 0),
            "active": bool(row[10]),
            "slug": row[11] or "",
            "description": row[12] or "",
            "resolution_text": row[13] or "",
            "created_time": _iso(row[14]),
            "outcomes": _json_list(row[15]),
            "outcome_prices": _json_float_list(row[16]),
            "tracked": row[0] in set(self.store.load().tracked_ids),
        }

    def state_payload(self, include_live: bool = False) -> dict:
        state = self.store.load()
        markets = self.markets(limit=800)
        events = self.events(markets)

        selected_event_id = state.selected_event_id
        if not selected_event_id and state.selected_market_id:
            selected_market = next(
                (m for m in markets if m["condition_id"] == state.selected_market_id),
                None,
            )
            if selected_market:
                selected_event_id = selected_market["event_id"]
        if not selected_event_id and events:
            selected_event_id = events[0]["event_id"]

        selected_event = (
            self.event_detail(selected_event_id, markets=markets, events=events)
            if selected_event_id else None
        )
        if selected_event is None and events:
            selected_event_id = events[0]["event_id"]
            selected_event = self.event_detail(selected_event_id, markets=markets, events=events)
        event_markets = selected_event.get("markets", []) if selected_event else []
        selected_market_id = state.selected_market_id
        if event_markets and selected_market_id not in {m["condition_id"] for m in event_markets}:
            selected_market_id = event_markets[0]["condition_id"]
        selected = (
            self.market_detail(
                selected_market_id,
                markets=event_markets or markets,
                include_live=include_live,
            )
            if selected_market_id else None
        )

        if (
            state.selected_event_id != selected_event_id
            or state.selected_market_id != selected_market_id
        ):
            state.selected_event_id = selected_event_id
            state.selected_market_id = selected_market_id
            state.selected_id = selected_market_id
            self.store.save(state)

        account_context = self.account_context()
        state = self.store.load()
        return {
            "state": asdict(state),
            "events": self._ensure_event_visible(events, selected_event),
            "markets": markets[:120],
            "selected_event": selected_event,
            "selected": selected,
            "account_context": account_context,
            "stats": self.stats(events, markets),
            "tracked_report": self.tracked_report(markets),
            "history": state.actions[:40],
            "notes": state.notes[:60],
        }

    def _ensure_event_visible(
        self,
        events: list[dict],
        selected_event: dict | None,
    ) -> list[dict]:
        if not selected_event:
            return events
        selected_event_id = selected_event.get("event_id")
        if not selected_event_id:
            return events
        if any(event.get("event_id") == selected_event_id for event in events):
            return events
        return [selected_event, *events]

    def account_context(self) -> dict:
        state = self.store.load()
        account = self._selected_account(state)
        accounts = load_paper_accounts(limit=30)
        if account.account_id not in {item.account_id for item in accounts}:
            accounts.insert(0, account)
        activity = self._account_activity(account.account_id)
        return {
            "selected_account_id": account.account_id,
            "selected_account": _paper_account_dict(account),
            "accounts": [_paper_account_dict(item) for item in accounts],
            "transactions": activity["transactions"],
            "nav_points": activity["nav_points"],
            "positions": activity["positions"],
            "position_event_groups": activity["position_event_groups"],
            "trade_records": activity["trade_records"],
            "trade_event_groups": activity["trade_event_groups"],
            "earning_points": activity["earning_points"],
            "maintainer": self.maintainer_context(account.account_id),
            "user_stats": activity["user_stats"],
        }

    def create_account(self, name: str) -> dict:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("name is required")
        account_id = self._unique_account_id(clean_name)
        create_paper_account(
            account_id=account_id,
            name=clean_name,
            risk_profile="research",
            sizing_mode="all_in",
            order_fraction=1.0,
            fee_rate_bps=0.0,
            slippage_bps=0.0,
        )
        state = self.store.load()
        state.selected_account_id = account_id
        self.store.save(state)
        return self.state_payload()

    def login_account(self, account_id: str) -> dict:
        if not load_paper_account(account_id):
            raise ValueError(f"paper account not found: {account_id}")
        state = self.store.load()
        state.selected_account_id = account_id
        self.store.save(state)
        return self.state_payload()

    def update_account_config(self, payload: dict) -> dict:
        state = self.store.load()
        account = self._selected_account(state)
        update_risk_params(
            account.account_id,
            risk_profile=str(payload.get("risk_profile") or account.risk_profile),
            sizing_mode=str(payload.get("sizing_mode") or account.sizing_mode),
            order_fraction=_payload_float(
                payload, "order_fraction", account.order_fraction
            ),
            auto_trade_enabled=bool(payload.get("auto_trade_enabled", False)),
            max_order_notional=_payload_float(
                payload, "max_order_notional", account.max_order_notional
            ),
            max_market_exposure=_payload_float(
                payload, "max_market_exposure", account.max_market_exposure
            ),
            max_event_exposure=_payload_float(
                payload, "max_event_exposure", account.max_event_exposure
            ),
            max_category_exposure=_payload_float(
                payload, "max_category_exposure", account.max_category_exposure
            ),
            max_total_exposure=_payload_float(
                payload, "max_total_exposure", account.max_total_exposure
            ),
            min_cash_buffer=_payload_float(
                payload, "min_cash_buffer", account.min_cash_buffer
            ),
            fee_rate_bps=_payload_float(payload, "fee_rate_bps", account.fee_rate_bps),
            slippage_bps=_payload_float(payload, "slippage_bps", account.slippage_bps),
            status=str(payload.get("status") or account.status),
            notes=str(payload.get("notes") or account.notes),
        )
        return self.state_payload()

    def update_account_profile(self, payload: dict) -> dict:
        state = self.store.load()
        account = self._selected_account(state)
        update_account_profile(
            account.account_id,
            name=str(payload.get("name") or account.name),
            icon_url=str(payload.get("icon_url") or ""),
            notes=str(payload.get("notes") or ""),
        )
        return self.state_payload()

    def account_funds(self, payload: dict) -> dict:
        state = self.store.load()
        account = self._selected_account(state)
        amount = _payload_float(payload, "amount", 0.0)
        memo = str(payload.get("memo") or "GUI funding action")
        action = str(payload.get("action") or "deposit")
        if action == "withdraw":
            withdraw_cash(account.account_id, amount, memo=memo)
        else:
            deposit_cash(account.account_id, amount, memo=memo)
        return self.state_payload()

    def maintainer_context(self, account_id: str) -> dict:
        state = self.store.load()
        log_rows = _read_strategy_log(self.cfg.data_dir / "paper_strategy_runs.jsonl", account_id)
        recent = log_rows[:40]
        latest_start = next((row for row in recent if row.get("type") == "strategy_run_start"), {})
        latest_end = next((row for row in recent if row.get("type") == "strategy_run_end"), {})
        decisions = [row for row in recent if row.get("type") == "strategy_decision"]
        action_logs = [
            action for action in state.actions
            if action.get("kind") in {
                "maintainer_action",
                "maintainer_log",
                "maintainer_start",
                "maintainer_timeout",
            }
            and (action.get("payload") or {}).get("account_id") == account_id
        ][:300]
        params = latest_start.get("params") or {}
        summary = {
            "last_run_id": latest_start.get("run_id") or latest_end.get("run_id") or "",
            "last_started_at": latest_start.get("created_at") or "",
            "last_finished_at": latest_end.get("created_at") or "",
            "last_sold": int(latest_end.get("sold") or 0),
            "last_buys": int(latest_end.get("buys") or 0),
            "last_cash_earned": float(latest_end.get("cash_earned") or 0),
            "last_realized_pnl": float(latest_end.get("realized_pnl") or 0),
            "decision_count": len(decisions),
        }
        return {
            "summary": summary,
            "params": params,
            "recent_decisions": decisions[:18],
            "console_logs": _maintainer_console_logs(action_logs, recent),
            "log_path": str(self.cfg.data_dir / "paper_strategy_runs.jsonl"),
        }

    def run_maintainer_action(self, payload: dict) -> dict:
        state = self.store.load()
        account = self._selected_account(state)
        action = str(payload.get("action") or "update")
        dry_run = bool(payload.get("dry_run", False))
        if action == "update":
            return self.state_payload()
        if action not in {"sell", "purchase", "maintain"}:
            raise ValueError("action must be update, sell, purchase, or maintain")
        cmd = [
            sys.executable,
            "-u",
            str(ROOT / "scripts" / "run_paper_maintainer.py"),
            "--account-id",
            account.account_id,
        ]
        if action == "sell":
            cmd.append("--sell-only")
        elif action == "purchase":
            cmd.append("--buy-only")
        if dry_run:
            cmd.append("--dry-run")
        started = datetime.now(timezone.utc)
        self.store.append_action(
            "maintainer_start",
            "",
            {
                "account_id": account.account_id,
                "action": action,
                "dry_run": dry_run,
                "started_at": started.isoformat(),
                "command": " ".join(cmd),
            },
        )
        output_lines: list[str] = []
        try:
            process = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                clean = line.rstrip()
                if not clean:
                    continue
                output_lines.append(clean)
                self.store.append_action(
                    "maintainer_log",
                    "",
                    {
                        "account_id": account.account_id,
                        "action": action,
                        "dry_run": dry_run,
                        "started_at": started.isoformat(),
                        "line": clean,
                    },
                )
            returncode = process.wait(timeout=900)
        except subprocess.TimeoutExpired as exc:
            self.store.append_action(
                "maintainer_timeout",
                "",
                {"account_id": account.account_id, "action": action, "timeout": exc.timeout},
            )
            raise RuntimeError("maintainer action timed out") from exc
        self.store.append_action(
            "maintainer_action",
            "",
            {
                "account_id": account.account_id,
                "action": action,
                "dry_run": dry_run,
                "returncode": returncode,
                "started_at": started.isoformat(),
                "stdout": "\n".join(output_lines),
                "stderr": "",
            },
        )
        if returncode != 0:
            message = "\n".join(output_lines).strip() or "maintainer failed"
            raise RuntimeError(message)
        return self.state_payload()

    def _account_activity(self, account_id: str) -> dict:
        account = load_paper_account(account_id)
        transactions = [
            _paper_transaction_dict(item)
            for item in load_account_transactions(account_id, limit=80)
        ]
        orders = load_paper_orders(account_id, limit=120)
        projected_by_position: dict[tuple[str, str], float] = {}
        for order in orders:
            if order.action != "buy" or not order.net_edge:
                continue
            key = (order.condition_id, order.side)
            projected_by_position[key] = (
                projected_by_position.get(key, 0.0)
                + order.filled_shares * order.net_edge
            )
        positions = [
            _paper_position_dict(
                item,
                projected_by_position.get((item.condition_id, item.side)),
            )
            for item in load_paper_positions(account_id)
        ]
        trade_records = [
            _paper_order_dict(item)
            for item in orders
        ]
        open_cost = sum(float(item.get("notional") or 0) for item in positions)
        initial_cash = float(account.initial_cash if account else 0)
        nav_points = [
            {
                "at": item["created_at"],
                "nav": round(item["cash_after"] + item["reserved_after"] + open_cost, 8),
                "pnl": round(
                    item["cash_after"] + item["reserved_after"] + open_cost - initial_cash,
                    8,
                ),
            }
            for item in reversed(transactions)
        ]
        earning_points = nav_points
        event_position_groups = _event_groups(positions)
        event_trade_groups = _event_groups(trade_records)
        projected_open_pnl = sum(
            float(item.get("estimated_pnl") or 0)
            for item in positions
            if item.get("estimated_pnl") is not None
        )
        share_hold_cost = open_cost
        projected_share_value = share_hold_cost + projected_open_pnl
        cash_balance = float(account.cash_balance if account else 0)
        reserved_cash = float(account.reserved_cash if account else 0)
        total_account_money = cash_balance + reserved_cash + projected_share_value
        total_earn_loss = total_account_money - initial_cash
        realized_pnl = sum(
            float(item.get("cash_delta") or 0)
            for item in transactions
            if item.get("transaction_type") == "trade" and float(item.get("cash_delta") or 0) > 0
        )
        latest_nav = (
            nav_points[-1]["nav"]
            if nav_points else
            (account.cash_balance if account else 0.0)
        )
        user_stats = {
            "trade_count": len(trade_records),
            "position_count": len(positions),
            "estimated_pnl": round(projected_open_pnl, 8),
            "realized_pnl": round(realized_pnl, 8),
            "open_cost_basis": round(open_cost, 8),
            "cash_balance": round(cash_balance, 8),
            "reserved_cash": round(reserved_cash, 8),
            "share_hold_cost": round(share_hold_cost, 8),
            "projected_share_value": round(projected_share_value, 8),
            "total_account_money": round(total_account_money, 8),
            "total_earn_loss": round(total_earn_loss, 8),
            "initial_cash": round(initial_cash, 8),
            "transaction_count": len(transactions),
            "latest_nav": latest_nav,
        }
        return {
            "transactions": transactions,
            "nav_points": nav_points,
            "earning_points": earning_points,
            "positions": positions,
            "position_event_groups": event_position_groups,
            "trade_records": trade_records,
            "trade_event_groups": event_trade_groups,
            "user_stats": user_stats,
        }

    def _enrich_trade_record(self, deal: dict) -> dict:
        condition_id = deal.get("condition_id") or ""
        market = self._market_by_id(condition_id) if condition_id else None
        event_id = (market or {}).get("event_id") or condition_id
        event_meta = self._event_meta().get(event_id, {}) if event_id else {}
        return {
            **deal,
            "event_id": event_id,
            "event_title": (
                event_meta.get("title")
                or (market or {}).get("question")
                or deal.get("question")
                or ""
            ),
            "event_category": event_meta.get("category") or (market or {}).get("category") or "",
            "question": (market or {}).get("question") or deal.get("question") or "",
        }

    def _selected_account(self, state: GuiState | None = None):
        state = state or self.store.load()
        account = load_paper_account(state.selected_account_id or "")
        if account:
            return account
        account = ensure_default_paper_account()
        state.selected_account_id = account.account_id
        self.store.save(state)
        return account

    def _unique_account_id(self, name: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "user"
        candidate = base
        suffix = 2
        while load_paper_account(candidate):
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def markets(self, limit: int = 120, event_id: str | None = None) -> list[dict]:
        if not self.market_db.exists():
            return []
        with duckdb.connect(str(self.market_db), read_only=True) as conn:
            columns = self._columns(conn, "markets")
            event_select = "event_id" if "event_id" in columns else "'' AS event_id"
            price_select = (
                "outcome_prices_json"
                if "outcome_prices_json" in columns else
                "'[]' AS outcome_prices_json"
            )
            filters: list[str] = []
            params: list[object] = []
            if "active" in columns:
                filters.append("active = TRUE")
            if "close_time" in columns:
                filters.append("(close_time IS NULL OR CAST(close_time AS DATE) >= CURRENT_DATE)")
            if event_id:
                event_key = (
                    "COALESCE(NULLIF(event_id, ''), condition_id)"
                    if "event_id" in columns else "condition_id"
                )
                filters.append(f"{event_key} = ?")
                params.append(event_id)
            where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
            params.append(limit)
            rows = conn.execute(
                f"""
                SELECT condition_id, {event_select}, question, category, neg_risk,
                       token_yes_id, token_no_id, close_time, volume_24h, liquidity,
                       active, slug, description, resolution_text, created_time,
                       outcomes_json, {price_select}
                FROM markets
                {where_clause}
                ORDER BY volume_24h DESC NULLS LAST
                LIMIT ?
                """,
                params,
            ).fetchall()
        state = self.store.load()
        tracked = set(state.tracked_ids)
        return [
            {
                "condition_id": row[0],
                "event_id": row[1] or row[0],
                "question": row[2],
                "category": row[3] or "Uncategorized",
                "neg_risk": bool(row[4]),
                "token_yes_id": row[5] or "",
                "token_no_id": row[6] or "",
                "close_time": _iso(row[7]),
                "volume_24h": float(row[8] or 0),
                "liquidity": float(row[9] or 0),
                "active": bool(row[10]),
                "slug": row[11] or "",
                "description": row[12] or "",
                "resolution_text": row[13] or "",
                "created_time": _iso(row[14]),
                "outcomes": _json_list(row[15]),
                "outcome_prices": _json_float_list(row[16]),
                "tracked": row[0] in tracked,
            }
            for row in rows
        ]

    def events(self, markets: list[dict] | None = None) -> list[dict]:
        markets = markets if markets is not None else self.markets(limit=800)
        meta = self._event_meta()
        state = self.store.load()
        tracked = set(state.tracked_ids)
        forecast_by_market = self._latest_forecast_by_market()
        grouped: dict[str, dict] = {}
        for market in markets:
            event_id = market["event_id"] or market["condition_id"]
            forecast = forecast_by_market.get(market["condition_id"])
            event_meta = meta.get(event_id, {})
            event = grouped.setdefault(
                event_id,
                {
                    "event_id": event_id,
                    "title": event_meta.get("title") or market["question"],
                    "slug": event_meta.get("slug") or market["slug"],
                    "category": event_meta.get("category") or market["category"],
                    "tags": event_meta.get("tags", []),
                    "description": event_meta.get("description") or market["description"],
                    "image": event_meta.get("image") or "",
                    "icon": event_meta.get("icon") or event_meta.get("image") or "",
                    "end_time": event_meta.get("end_time") or market["close_time"],
                    "volume_24h": float(event_meta.get("volume_24h") or 0),
                    "liquidity": float(event_meta.get("liquidity") or 0),
                    "market_volume_24h": 0.0,
                    "market_liquidity": 0.0,
                    "market_count": 0,
                    "neg_risk_count": 0,
                    "tracked_count": 0,
                    "edge_count": 0,
                    "max_abs_edge": 0.0,
                    "avg_abs_edge": 0.0,
                    "forecast_direction": "observe",
                    "forecast_edge": 0.0,
                    "forecast_confidence": 0.0,
                    "active": True,
                    "markets": [],
                    "_abs_edge_sum": 0.0,
                },
            )
            event["markets"].append(market)
            event["market_count"] += 1
            event["market_volume_24h"] += market["volume_24h"]
            event["market_liquidity"] += market["liquidity"]
            event["neg_risk_count"] += 1 if market["neg_risk"] else 0
            event["tracked_count"] += 1 if market["condition_id"] in tracked else 0
            event["active"] = event["active"] and market["active"]
            if forecast:
                abs_edge = abs(forecast["edge"])
                event["edge_count"] += 1
                event["_abs_edge_sum"] += abs_edge
                if abs_edge >= event["max_abs_edge"]:
                    event["max_abs_edge"] = abs_edge
                    event["forecast_direction"] = forecast["forecast_direction"]
                    event["forecast_edge"] = forecast["edge"]
                    event["forecast_confidence"] = forecast["confidence"]

        current_events = []
        for event in grouped.values():
            if _is_past_date(event.get("end_time")):
                continue
            if not event["volume_24h"]:
                event["volume_24h"] = event["market_volume_24h"]
            if not event["liquidity"]:
                event["liquidity"] = event["market_liquidity"]
            if event["edge_count"]:
                event["avg_abs_edge"] = event["_abs_edge_sum"] / event["edge_count"]
            event["top_markets"] = event["markets"][:4]
            event.pop("markets")
            event.pop("_abs_edge_sum", None)
            current_events.append(event)
        return sorted(
            current_events,
            key=lambda item: item["volume_24h"],
            reverse=True,
        )[:120]

    def event_detail(
        self,
        event_id: str | None,
        markets: list[dict] | None = None,
        events: list[dict] | None = None,
        include_live_prices: bool = False,
    ) -> dict | None:
        if not event_id:
            return None
        events = events if events is not None else self.events(markets)
        event = next((e for e in events if e["event_id"] == event_id), None)
        event_markets = self.markets(limit=200, event_id=event_id)
        if event is None and event_markets:
            event = self.events(event_markets)[0]
        if event is None:
            return None

        edge_by_market = self._latest_forecast_by_market()
        enriched_markets = []
        for market in event_markets:
            forecast_edge = edge_by_market.get(market["condition_id"])
            enriched = {**market, **self._market_prices(market)}
            if forecast_edge:
                enriched.update({
                    "p_f": forecast_edge["p_f"],
                    "edge": forecast_edge["edge"],
                    "confidence": forecast_edge["confidence"],
                    "forecast_snapshot_time": forecast_edge["snapshot_time"],
                    "forecast_direction": forecast_edge["forecast_direction"],
                })
            enriched_markets.append(enriched)
        if include_live_prices:
            self._attach_live_button_prices(enriched_markets)

        edges = [m["edge"] for m in enriched_markets if "edge" in m]
        dominant = max(
            (m for m in enriched_markets if "edge" in m),
            key=lambda item: abs(item["edge"]),
            default=None,
        )
        primary_market = enriched_markets[0] if enriched_markets else {}
        rules = primary_market.get("resolution_text") or primary_market.get("description") or ""
        background = event.get("description") or primary_market.get("description") or ""
        return {
            **event,
            "markets": enriched_markets,
            "rules": rules,
            "background": background,
            "start_time": primary_market.get("created_time"),
            "edge_count": len(edges),
            "max_abs_edge": max((abs(edge) for edge in edges), default=0.0),
            "avg_abs_edge": sum(abs(edge) for edge in edges) / len(edges) if edges else 0.0,
            "forecast_direction": (
                dominant.get("forecast_direction")
                if dominant else event.get("forecast_direction", "observe")
            ),
            "forecast_edge": dominant.get("edge") if dominant else event.get("forecast_edge", 0.0),
            "forecast_confidence": (
                dominant.get("confidence") if dominant else event.get("forecast_confidence", 0.0)
            ),
        }

    def market_detail(
        self,
        condition_id: str | None,
        markets: list[dict] | None = None,
        include_live: bool = False,
        side: str | None = None,
    ) -> dict | None:
        if not condition_id:
            return None
        market_list = markets if markets is not None else self.markets()
        market = next((m for m in market_list if m["condition_id"] == condition_id), None)
        if market is None:
            market = self._market_by_id(condition_id)
        if market is None:
            return None
        side = _side(side or self.store.load().selected_side)
        market = {**market, **self._market_prices(market)}

        if include_live:
            snapshot, snapshot_status = self._live_snapshot(market, side)
        else:
            snapshot, snapshot_status = None, {
                "available": False,
                "reason": "Live quote is loaded separately after the event shell renders.",
            }
        forecast = self._latest_forecast(condition_id)
        chart = self._chart_points(condition_id, snapshot, forecast, side)
        return {
            "market": market,
            "side": side,
            "snapshot": snapshot,
            "snapshot_status": snapshot_status,
            "forecast": forecast,
            "analysis": self._analysis(market, snapshot, forecast, side),
            "chart": chart,
            "evidence": self._latest_evidence(forecast.get("run_id") if forecast else None),
            "related_news": self._related_news(condition_id),
            "topic_logs": self.topic_logs(condition_id),
        }

    def _market_prices(self, market: dict) -> dict:
        prices = market.get("outcome_prices") or []
        yes_price = prices[0] if prices else None
        no_price = prices[1] if len(prices) > 1 else (
            1 - yes_price if yes_price is not None else None
        )
        return {
            "yes_price": yes_price,
            "no_price": no_price,
            "yes_label": (market.get("outcomes") or ["YES"])[0]
            if market.get("outcomes") else "YES",
            "no_label": (market.get("outcomes") or ["YES", "NO"])[1]
            if len(market.get("outcomes") or []) > 1 else "NO",
        }

    def _attach_live_button_prices(self, markets: list[dict], limit: int = 30) -> None:
        if not markets:
            return
        client = ClobReadClient()
        for market in markets[:limit]:
            yes_live = self._live_button_price(client, market.get("token_yes_id"))
            no_live = self._live_button_price(client, market.get("token_no_id"))
            if yes_live is not None:
                market["yes_price"] = yes_live
                market["yes_price_source"] = "live_ask"
            else:
                market["yes_price_source"] = "stored"
            if no_live is not None:
                market["no_price"] = no_live
                market["no_price_source"] = "live_ask"
            else:
                market["no_price_source"] = "stored"
        for market in markets[limit:]:
            market.setdefault("yes_price_source", "stored")
            market.setdefault("no_price_source", "stored")

    def _live_button_price(self, client: ClobReadClient, token_id: object) -> float | None:
        token = str(token_id or "")
        if not token:
            return None
        try:
            book = client.get_order_book(token)
        except Exception as exc:
            logger.debug(f"Could not fetch live button price for token {token}: {exc}")
            return None
        asks = _book_levels((book or {}).get("asks") or [])
        if not asks:
            return None
        return asks[0]["price"]

    def topic_logs(self, condition_id: str) -> dict:
        state = self.store.load()
        return {
            "notes": self._topic_filter(state.notes, condition_id)[:40],
            "actions": self._topic_filter(state.actions, condition_id)[:40],
            "deals": self._topic_filter(state.deals, condition_id)[:20],
            "followups": self._topic_filter(state.followups, condition_id)[:20],
            "reviews": self._topic_filter(state.reviews, condition_id)[:20],
            "messages": self._topic_filter(state.messages, condition_id)[:20],
            "news": self._topic_filter(state.news, condition_id)[:20],
            "special_reports": self._special_reports(condition_id=condition_id),
        }

    def stats(self, events: list[dict], markets: list[dict]) -> dict:
        state = self.store.load()
        categories: dict[str, int] = {}
        for event in events:
            categories[event["category"]] = categories.get(event["category"], 0) + 1
        edges = self._forecast_edges()
        return {
            "event_count": len(events),
            "market_count": len(markets),
            "tracked_count": len(state.tracked_ids),
            "note_count": len(state.notes),
            "action_count": len(state.actions),
            "deal_count": len(state.deals),
            "followup_count": len(state.followups),
            "review_count": len(state.reviews),
            "total_volume_24h": sum(e["volume_24h"] for e in events),
            "total_liquidity": sum(e["liquidity"] for e in events),
            "category_counts": sorted(
                categories.items(), key=lambda item: item[1], reverse=True
            )[:8],
            "forecast_edges": edges,
        }

    def tracked_report(self, markets: list[dict]) -> dict:
        state = self.store.load()
        tracked_ids = set(state.tracked_ids)
        tracked = [market for market in markets if market["condition_id"] in tracked_ids]
        category_counts: dict[str, int] = {}
        for market in tracked:
            category_counts[market["category"]] = category_counts.get(market["category"], 0) + 1
        open_followups = [
            item for item in state.followups if item.get("status", "open") == "open"
        ]
        recent_deals = state.deals[:8]
        reviewed_ids = {item["condition_id"] for item in state.reviews}
        return {
            "tracked": tracked,
            "tracked_count": len(tracked),
            "neg_risk_count": sum(1 for market in tracked if market["neg_risk"]),
            "total_volume_24h": sum(market["volume_24h"] for market in tracked),
            "category_counts": sorted(
                category_counts.items(), key=lambda item: item[1], reverse=True
            )[:8],
            "open_followups": open_followups[:8],
            "recent_deals": recent_deals,
            "reviewed_count": len(reviewed_ids),
            "generated_messages": state.messages[:6],
            "special_reports": self._special_reports(),
        }

    def select_event(self, event_id: str) -> dict:
        state = self.store.load()
        state.selected_event_id = event_id
        detail = self.event_detail(event_id)
        markets = detail.get("markets", []) if detail else []
        state.selected_market_id = markets[0]["condition_id"] if markets else None
        state.selected_id = state.selected_market_id
        state.selected_side = "YES"
        self.store.save(state)
        return self.state_payload()

    def select_market(self, condition_id: str, side: str | None = None) -> dict:
        state = self.store.load()
        market = self._market_by_id(condition_id)
        state.selected_market_id = condition_id
        state.selected_id = condition_id
        state.selected_side = _side(side or state.selected_side)
        if market:
            state.selected_event_id = market["event_id"]
        self.store.save(state)
        return self.state_payload()

    def add_topic(self, query: str) -> dict:
        query = query.strip()
        if not query:
            payload = self.state_payload()
            payload["topic_add_result"] = {
                "status": "empty",
                "message": "Enter a Polymarket question, slug, or condition id.",
                "query": query,
            }
            return payload

        matches = self.search_topics_online(query, limit=1)
        if not matches:
            payload = self.state_payload()
            payload["topic_add_result"] = {
                "status": "not_found",
                "message": f"No online Polymarket topic matched: {query}",
                "query": query,
            }
            return payload

        market = matches[0]
        state = self.store.load()
        tracked_ids = set(state.tracked_ids)
        tracked_ids.add(market["condition_id"])
        state.tracked_ids = list(tracked_ids)
        state.selected_market_id = market["condition_id"]
        state.selected_id = market["condition_id"]
        state.selected_event_id = market["event_id"]
        state.selected_side = _side(state.selected_side)
        self.store.save(state)
        self.store.append_action("add_topic", market["condition_id"], {
            "query": query,
            "question": market["question"],
        })

        payload = self.state_payload()
        payload["topic_add_result"] = {
            "status": "added",
            "message": f"Added online topic: {market['question']}",
            "query": query,
            "condition_id": market["condition_id"],
            "event_id": market["event_id"],
            "question": market["question"],
        }
        return payload

    def get_new_topics(self, cap: int = 100) -> dict:
        cap = max(1, min(int(cap or 100), 500))
        state = self.store.load()
        offset = max(0, int(state.topic_feed_offset or 0))
        existing = self._existing_condition_ids()
        raw_new: list[dict] = []
        pages_read = 0
        exhausted = False

        try:
            with GammaClient() as gamma:
                while len(raw_new) < cap and pages_read < 25:
                    page_limit = max(
                        1,
                        min(cap - len(raw_new), self.cfg.scanner_gamma_page_limit, 100),
                    )
                    batch = gamma.get_liquid_markets_page(
                        limit=page_limit,
                        offset=offset,
                        min_volume_24h=0.0,
                    )
                    pages_read += 1
                    offset += len(batch)
                    if not batch:
                        exhausted = True
                        break
                    for raw in batch:
                        condition_id = str(raw.get("conditionId") or raw.get("condition_id") or "")
                        if not condition_id or condition_id in existing:
                            continue
                        try:
                            market = gamma.parse_market(raw)
                        except Exception:
                            continue
                        if not self._is_current_market(market):
                            existing.add(condition_id)
                            continue
                        raw_new.append(raw)
                        existing.add(condition_id)
                        if len(raw_new) >= cap:
                            break
                markets, events = self._parse_online_topic_batch(gamma, raw_new)
        except Exception as exc:
            logger.warning(f"Fresh topic fetch failed: {exc}")
            payload = self.state_payload()
            payload["topic_fetch_result"] = {
                "status": "error",
                "message": f"Could not fetch new online topics: {exc}",
                "cap": cap,
                "added": 0,
                "offset": offset,
            }
            return payload

        state = self.store.load()
        state.topic_feed_offset = offset
        self.store.save(state)

        before_count = len(self._existing_condition_ids())
        self._write_online_topics(markets, events)
        after_count = len(self._existing_condition_ids())
        added = max(0, after_count - before_count)

        payload = self.state_payload()
        if added:
            status = "added"
            message = f"Added {added} fresh online topics."
        elif exhausted:
            status = "not_found"
            message = "No new online topics were available from Gamma."
        else:
            status = "not_found"
            message = "No unseen current topics found in the next Gamma pages."
        payload["topic_fetch_result"] = {
            "status": status,
            "message": message,
            "cap": cap,
            "added": added,
            "offset": offset,
        }
        return payload

    def _existing_condition_ids(self) -> set[str]:
        if not self.market_db.exists():
            return set()
        try:
            with duckdb.connect(str(self.market_db), read_only=True) as conn:
                tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
                if "markets" not in tables:
                    return set()
                rows = conn.execute("SELECT condition_id FROM markets").fetchall()
        except Exception:
            return set()
        return {str(row[0]) for row in rows if row[0]}

    def _is_current_market(self, market: MarketMeta) -> bool:
        if not market.active:
            return False
        if market.close_time is None:
            return True
        return market.close_time.date() >= datetime.now().date()

    def _parse_online_topic_batch(
        self,
        gamma: GammaClient,
        raw_markets: list[dict],
    ) -> tuple[list[MarketMeta], list[EventMeta]]:
        markets: list[MarketMeta] = []
        events_by_id: dict[str, EventMeta] = {}
        for raw in raw_markets:
            try:
                market = gamma.parse_market(raw)
            except Exception:
                continue
            if not market.condition_id or not self._is_current_market(market):
                continue
            markets.append(market)
            raw_events = raw.get("events") or []
            if isinstance(raw_events, list) and raw_events:
                raw_event = raw_events[0]
                if isinstance(raw_event, dict):
                    event = gamma.parse_event(raw_event)
                    if event.event_id:
                        events_by_id[event.event_id] = event
                        continue
            if market.event_id and market.event_id not in events_by_id:
                try:
                    raw_event = gamma.get_event(market.event_id)
                except Exception:
                    raw_event = None
                if raw_event:
                    event = gamma.parse_event(raw_event)
                    if event.event_id:
                        events_by_id[event.event_id] = event
                        continue
                events_by_id[market.event_id] = EventMeta(
                    event_id=market.event_id,
                    title=market.question,
                    slug=market.slug,
                    category=market.category,
                    end_time=market.close_time,
                    volume_24h=market.volume_24h,
                    liquidity=market.liquidity,
                    active=market.active,
                    market_count=1,
                )
        return markets, list(events_by_id.values())

    def search_topics_online(self, query: str, limit: int = 8) -> list[dict]:
        query = query.strip()
        if not query:
            return []
        scan_limit = max(self.cfg.scanner_market_limit, 1000)
        try:
            with GammaClient() as gamma:
                raw_markets = gamma.search_markets(query, limit=limit, scan_limit=scan_limit)
                if not raw_markets:
                    return []
                markets, events = self._parse_online_topics(gamma, raw_markets)
        except Exception as exc:
            logger.warning(f"Online topic search failed for {query!r}: {exc}")
            return []
        self._write_online_topics(markets, events)
        tracked = set(self.store.load().tracked_ids)
        results: list[dict] = []
        for market in markets[:limit]:
            local = self._market_by_id(market.condition_id)
            if local:
                results.append(local)
            else:
                results.append(_market_meta_row(market, tracked))
        return results

    def _parse_online_topics(
        self,
        gamma: GammaClient,
        raw_markets: list[dict],
    ) -> tuple[list[MarketMeta], list[EventMeta]]:
        markets_by_id: dict[str, MarketMeta] = {}
        events_by_id: dict[str, EventMeta] = {}

        for raw in raw_markets:
            market = gamma.parse_market(raw)
            if market.condition_id:
                markets_by_id[market.condition_id] = market

            raw_events = raw.get("events") or []
            if isinstance(raw_events, list) and raw_events:
                raw_event = raw_events[0]
                if isinstance(raw_event, dict):
                    event = gamma.parse_event(raw_event)
                    if event.event_id:
                        events_by_id[event.event_id] = event

            event_id = market.event_id
            if event_id:
                try:
                    raw_event = gamma.get_event(event_id)
                except Exception as exc:
                    logger.debug(f"Could not fetch online event {event_id}: {exc}")
                    raw_event = None
                if raw_event:
                    event = gamma.parse_event(raw_event)
                    if event.event_id:
                        events_by_id[event.event_id] = event
                    raw_event_markets = raw_event.get("markets") or []
                    if isinstance(raw_event_markets, list):
                        for event_market_raw in raw_event_markets:
                            if not isinstance(event_market_raw, dict):
                                continue
                            try:
                                event_market = gamma.parse_market(event_market_raw, raw_event)
                            except Exception:
                                continue
                            if event_market.condition_id:
                                markets_by_id[event_market.condition_id] = event_market

            if market.event_id and market.event_id not in events_by_id:
                events_by_id[market.event_id] = EventMeta(
                    event_id=market.event_id,
                    title=market.question,
                    slug=market.slug,
                    category=market.category,
                    end_time=market.close_time,
                    volume_24h=market.volume_24h,
                    liquidity=market.liquidity,
                    active=market.active,
                    market_count=1,
                )

        ordered_markets: list[MarketMeta] = []
        for raw in raw_markets:
            condition_id = str(raw.get("conditionId") or raw.get("condition_id") or "")
            market = markets_by_id.get(condition_id)
            if market:
                ordered_markets.append(market)
        return ordered_markets, list(events_by_id.values())

    def _write_online_topics(self, markets: list[MarketMeta], events: list[EventMeta]) -> None:
        if not markets:
            return
        fetched_at = datetime.now(timezone.utc)
        conn = ensure_schema()
        try:
            for event in events:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO events (
                        event_id, title, slug, ticker, description, image, icon,
                        category, tags_json, start_time, end_time, volume_24h,
                        liquidity, active, closed, archived, neg_risk, market_count,
                        fetched_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        event.event_id,
                        event.title,
                        event.slug,
                        event.ticker,
                        event.description,
                        event.image,
                        event.icon,
                        event.category,
                        json.dumps(event.tags),
                        event.start_time,
                        event.end_time,
                        event.volume_24h,
                        event.liquidity,
                        event.active,
                        event.closed,
                        event.archived,
                        event.neg_risk,
                        event.market_count,
                        fetched_at,
                    ],
                )
            for market in markets:
                conn.execute("DELETE FROM markets WHERE condition_id = ?", [market.condition_id])
                conn.execute(
                    """
                    INSERT INTO markets (
                        condition_id, event_id, question, description, resolution_text, category,
                        neg_risk, neg_risk_market_id, token_yes_id, token_no_id,
                        outcome_count, outcomes_json, outcome_prices_json,
                        close_time, created_time,
                        volume_24h, liquidity, active, slug, fetched_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        market.condition_id,
                        market.event_id,
                        market.question,
                        market.description,
                        market.resolution_text,
                        market.category,
                        market.neg_risk,
                        market.neg_risk_market_id,
                        market.token_yes_id,
                        market.token_no_id,
                        market.outcome_count,
                        json.dumps(market.outcomes),
                        json.dumps(market.outcome_prices),
                        market.close_time,
                        market.created_time,
                        market.volume_24h,
                        market.liquidity,
                        market.active,
                        market.slug,
                        fetched_at,
                    ],
                )
            conn.commit()
        finally:
            conn.close()

    def search_topics(self, query: str, limit: int = 8) -> list[dict]:
        query = query.strip()
        if not query or not self.market_db.exists():
            return []
        exact = self._market_by_id(query)
        results: list[dict] = []
        seen: set[str] = set()
        if exact:
            results.append(exact)
            seen.add(exact["condition_id"])
        try:
            with duckdb.connect(str(self.market_db), read_only=True) as conn:
                columns = self._columns(conn, "markets")
                event_select = "event_id" if "event_id" in columns else "'' AS event_id"
                price_select = (
                    "outcome_prices_json"
                    if "outcome_prices_json" in columns else
                    "'[]' AS outcome_prices_json"
                )
                event_tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
                event_matches: list[str] = []
                if "events" in event_tables:
                    event_rows = conn.execute(
                        """
                        SELECT event_id
                        FROM events
                        WHERE LOWER(title) LIKE ?
                           OR LOWER(slug) LIKE ?
                           OR LOWER(category) LIKE ?
                        ORDER BY volume_24h DESC NULLS LAST
                        LIMIT 10
                        """,
                        [f"%{query.lower()}%"] * 3,
                    ).fetchall()
                    event_matches = [str(row[0]) for row in event_rows if row[0]]
                event_clause = ""
                params: list[object] = [f"%{query.lower()}%"] * 4
                if event_matches and "event_id" in columns:
                    placeholders = ", ".join("?" for _ in event_matches)
                    event_clause = f" OR event_id IN ({placeholders})"
                    params.extend(event_matches)
                params.append(limit)
                rows = conn.execute(
                    f"""
                    SELECT condition_id, {event_select}, question, category, neg_risk,
                           token_yes_id, token_no_id, close_time, volume_24h, liquidity,
                           active, slug, description, resolution_text, created_time,
                           outcomes_json, {price_select}
                    FROM markets
                    WHERE LOWER(condition_id) LIKE ?
                       OR LOWER(question) LIKE ?
                       OR LOWER(slug) LIKE ?
                       OR LOWER(category) LIKE ?
                       {event_clause}
                    ORDER BY volume_24h DESC NULLS LAST
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
        except Exception:
            return results[:limit]

        tracked = set(self.store.load().tracked_ids)
        for row in rows:
            if row[0] in seen:
                continue
            seen.add(row[0])
            results.append(_market_row(row, tracked))
            if len(results) >= limit:
                break
        return results

    def track(self, condition_id: str, track: bool = True) -> dict:
        state = self.store.load()
        ids = set(state.tracked_ids)
        if track:
            ids.add(condition_id)
            state.selected_market_id = condition_id
            state.selected_id = condition_id
            state.selected_side = _side(state.selected_side)
            market = self._market_by_id(condition_id)
            if market:
                state.selected_event_id = market["event_id"]
        else:
            ids.discard(condition_id)
        state.tracked_ids = list(ids)
        self.store.save(state)
        self.store.append_action("track" if track else "untrack", condition_id)
        return self.state_payload()

    def select(self, condition_id: str) -> dict:
        return self.select_market(condition_id)

    def add_note(self, condition_id: str, text: str) -> dict:
        state = self.store.load()
        note = {"at": _now(), "condition_id": condition_id, "text": text.strip()}
        if note["text"]:
            state.notes.insert(0, note)
            state.notes = state.notes[:120]
        self.store.save(state)
        self.store.append_action("note", condition_id, {"text": note["text"]})
        return self.state_payload()

    def action(self, condition_id: str, action: str) -> dict:
        payload = {"label": action}
        state = self.store.load()
        detail = self.market_detail(condition_id, include_live=True)
        market = detail.get("market") if detail else None
        snapshot = detail.get("snapshot") if detail else None
        analysis = detail.get("analysis") if detail else None
        side = detail.get("side", "YES") if detail else "YES"
        if action == "paper_deal":
            net_edge = analysis.get("net_edge_estimate") if analysis else None
            account = self._selected_account(state)
            paper_order = _paper_order(account, snapshot)
            size = paper_order["shares"]
            estimated_pnl = float(net_edge or 0) * size - paper_order["fees"]
            human_report = self._deal_report(
                market, snapshot, analysis, size, estimated_pnl, side, paper_order
            )
            special_report = self._special_report_for_deal(
                market, analysis, estimated_pnl, size, side
            )
            deal = {
                "at": _now(),
                "account_id": account.account_id,
                "condition_id": condition_id,
                "question": market.get("question") if market else "",
                "side": side,
                "size": size,
                "notional": paper_order["notional"],
                "fees": paper_order["fees"],
                "limit_price": snapshot.get("best_ask") if snapshot else None,
                "market_mid": snapshot.get("midpoint") if snapshot else None,
                "net_edge_estimate": net_edge,
                "estimated_pnl": estimated_pnl,
                "human_report": human_report,
                "special_report": special_report,
                "status": "simulated",
            }
            state.deals.insert(0, deal)
            state.deals = state.deals[:80]
            payload["deal"] = deal
        elif action == "follow_up":
            followup = {
                "at": _now(),
                "condition_id": condition_id,
                "question": market.get("question") if market else "",
                "status": "open",
                "prompt": self._followup_prompt(market, analysis),
            }
            state.followups.insert(0, followup)
            state.followups = state.followups[:80]
            message = self._generated_message(market, analysis, "follow_up")
            state.messages.insert(0, message)
            state.messages = state.messages[:80]
            payload["followup"] = followup
            payload["message"] = message
        elif action == "reviewed":
            review = {
                "at": _now(),
                "condition_id": condition_id,
                "question": market.get("question") if market else "",
                "stance": analysis.get("stance") if analysis else "",
                "summary": analysis.get("advice") if analysis else "",
            }
            state.reviews.insert(0, review)
            state.reviews = state.reviews[:80]
            payload["review"] = review
        self.store.save(state)
        self.store.append_action(action, condition_id, payload)
        return self.state_payload()

    def update_topic(self, condition_id: str) -> dict:
        markets = self.markets()
        market = next((m for m in markets if m["condition_id"] == condition_id), None)
        if not market:
            return self.state_payload()
        result = self._refresh_market(market)
        self.store.append_action("update_topic", condition_id, result)
        return self.state_payload()

    def update_all_topics(self, max_topics: int = 8) -> dict:
        state = self.store.load()
        markets = self.markets()
        tracked_ids = set(state.tracked_ids)
        selected_id = state.selected_id
        ordered = sorted(
            markets,
            key=lambda m: (
                m["condition_id"] != selected_id,
                m["condition_id"] not in tracked_ids,
                -m["volume_24h"],
            ),
        )
        results = []
        for market in ordered[:max(1, max_topics)]:
            results.append(self._refresh_market(market))
        self.store.append_action(
            "update_all",
            selected_id or "",
            {"updated": len(results), "results": results},
        )
        return self.state_payload()

    def update_tracked_topics(self) -> dict:
        state = self.store.load()
        tracked_ids = set(state.tracked_ids)
        markets = [
            market for market in self.markets()
            if market["condition_id"] in tracked_ids
        ]
        results = [self._refresh_market(market) for market in markets]
        self.store.append_action(
            "update_tracked",
            state.selected_id or "",
            {"updated": len(results), "results": results},
        )
        return self.state_payload()

    def _refresh_market(self, market: dict) -> dict:
        market_meta = self._market_meta(market)
        snapshot, snapshot_status = self._live_snapshot_obj(market)
        candidate = CandidateMarket(
            market=market_meta,
            snapshot=snapshot or self._fallback_snapshot(market),
            scan_flags=["gui_manual_update"],
            priority_score=0.0,
        )
        features = self._parser_client().parse(market_meta)
        evidence, frozen_at = self._retriever_client().retrieve(candidate, features)
        self._store_news(market["condition_id"], evidence)
        result = {
            "condition_id": market["condition_id"],
            "news_count": len(evidence),
            "snapshot_available": snapshot_status["available"],
            "snapshot_reason": snapshot_status["reason"],
            "forecast_saved": False,
            "forecast_snapshot_source": "live" if snapshot is not None else "fallback",
        }
        forecast = self._forecaster_client().forecast(candidate, evidence, frozen_at)
        save_forecast_run(
            candidate=candidate,
            features=features,
            evidence=evidence,
            forecast=forecast,
            evidence_frozen_at=frozen_at,
        )
        result.update({
            "forecast_saved": True,
            "forecast_direction": forecast.forecast_direction,
            "p_f": forecast.p_f,
            "confidence": forecast.confidence,
        })
        event_id = market.get("event_id") or market["condition_id"]
        event = self.event_detail(event_id)
        if event:
            result.update({
                "event_edge_count": event.get("edge_count", 0),
                "event_forecast_direction": event.get("forecast_direction", "observe"),
            })
        if snapshot is None:
            result["forecast_reason"] = (
                "Forecast used stored market price fallback because no live CLOB "
                "snapshot was available."
            )
        return result

    def clear_topic(self, condition_id: str) -> dict:
        state = self.store.load()
        state.notes = self._without_topic(state.notes, condition_id)
        state.actions = self._without_topic(state.actions, condition_id)
        state.deals = self._without_topic(state.deals, condition_id)
        state.followups = self._without_topic(state.followups, condition_id)
        state.reviews = self._without_topic(state.reviews, condition_id)
        state.messages = self._without_topic(state.messages, condition_id)
        state.news = self._without_topic(state.news, condition_id)
        self.store.save(state)
        return self.state_payload()

    def clear_all_logs(self) -> dict:
        state = self.store.load()
        state.notes = []
        state.actions = []
        state.deals = []
        state.followups = []
        state.reviews = []
        state.messages = []
        state.news = []
        self.store.save(state)
        return self.state_payload()

    def _deal_report(
        self,
        market: dict | None,
        snapshot: dict | None,
        analysis: dict | None,
        size: float,
        estimated_pnl: float,
        side: str = "YES",
        paper_order: dict | None = None,
    ) -> str:
        question = market.get("question") if market else "selected market"
        ask = snapshot.get("best_ask") if snapshot else None
        net_edge = analysis.get("net_edge_estimate") if analysis else None
        paper_order = paper_order or {}
        notional = paper_order.get("notional")
        fee_rate_bps = paper_order.get("fee_rate_bps")
        execution = ""
        if notional is not None and fee_rate_bps is not None:
            execution = f"Notional ${notional:.2f}; fee {fee_rate_bps:.1f} bps. "
        if estimated_pnl >= 0:
            result = f"projected gain about ${estimated_pnl:.2f}"
        else:
            result = f"projected loss about ${abs(estimated_pnl):.2f}"
        return (
            f"Simulated {_side(side)} {size} on '{question}'"
            f"{f' at {ask:.1%}' if ask is not None else ''}. "
            f"{execution}"
            f"Estimated net edge is {float(net_edge or 0):+.1%}; {result} "
            "before final settlement and execution uncertainty."
        )

    def _special_report_for_deal(
        self,
        market: dict | None,
        analysis: dict | None,
        estimated_pnl: float,
        size: float,
        side: str = "YES",
    ) -> dict | None:
        if not analysis:
            return None
        net_edge = float(analysis.get("net_edge_estimate") or 0)
        side = _side(side)
        if net_edge >= 0.20:
            severity = "gain"
            title = "Possible gain above 20%"
            body = (
                f"{side} paper deal shows {net_edge:.1%} estimated net edge; "
                f"rough gain is ${max(0, estimated_pnl):.2f} on {size} shares."
            )
        elif net_edge <= -0.05:
            severity = "loss"
            title = "Possible loss warning"
            body = (
                f"{side} paper deal shows {net_edge:.1%} estimated net edge; "
                f"rough loss is ${abs(estimated_pnl):.2f} on {size} shares."
            )
        elif float(analysis.get("spread") or 0) > 0.08:
            severity = "execution"
            title = "Wide spread execution risk"
            body = "The current spread is wide enough that the simulated fill may be fragile."
        else:
            return None
        return {
            "at": _now(),
            "condition_id": market.get("condition_id") if market else "",
            "question": market.get("question") if market else "",
            "severity": severity,
            "title": title,
            "body": body,
        }

    def _special_reports(self, condition_id: str | None = None) -> list[dict]:
        state = self.store.load()
        reports: list[dict] = []
        for deal in state.deals:
            if condition_id and deal.get("condition_id") != condition_id:
                continue
            report = deal.get("special_report")
            if report:
                reports.append(report)
        return reports[:8]

    def _store_news(self, condition_id: str, evidence: list) -> None:
        state = self.store.load()
        state.news = self._without_topic(state.news, condition_id)
        now = _now()
        for item in evidence[:12]:
            state.news.append({
                "at": now,
                "condition_id": condition_id,
                "query": item.query,
                "title": item.title,
                "summary": item.summary,
                "url": item.url,
                "source": item.source,
                "published_at": _iso(item.published_at),
                "relevance_score": item.relevance_score,
            })
        state.news = state.news[-240:]
        self.store.save(state)

    def _related_news(self, condition_id: str) -> list[dict]:
        state = self.store.load()
        news = list(reversed(self._topic_filter(state.news, condition_id)))
        return news[:12]

    def _parser_client(self) -> ResolutionParser:
        if self._parser is None:
            self._parser = ResolutionParser()
        return self._parser

    def _retriever_client(self) -> EvidenceRetriever:
        if self._retriever is None:
            self._retriever = EvidenceRetriever()
        return self._retriever

    def _forecaster_client(self) -> LLMForecaster:
        if self._forecaster is None:
            self._forecaster = LLMForecaster()
        return self._forecaster

    def _market_meta(self, market: dict) -> MarketMeta:
        return MarketMeta(
            condition_id=market["condition_id"],
            question=market["question"],
            category=market.get("category") or "",
            neg_risk=bool(market.get("neg_risk")),
            token_yes_id=market.get("token_yes_id") or "",
            token_no_id=market.get("token_no_id") or "",
            close_time=_parse_iso(market.get("close_time")),
            volume_24h=float(market.get("volume_24h") or 0),
            liquidity=float(market.get("liquidity") or 0),
            active=bool(market.get("active")),
            slug=market.get("slug") or "",
        )

    def _live_snapshot_obj(self, market: dict) -> tuple[PriceSnapshot | None, dict]:
        token_id = market.get("token_yes_id") or market.get("token_no_id")
        snapshot, status = self._live_snapshot(market)
        if snapshot is None or not token_id:
            return None, status
        return (
            PriceSnapshot(
                condition_id=market["condition_id"],
                token_id=token_id,
                snapshot_time=_parse_iso(snapshot["snapshot_time"]) or datetime.now(timezone.utc),
                midpoint=float(snapshot["midpoint"]),
                best_bid=float(snapshot["best_bid"]),
                best_ask=float(snapshot["best_ask"]),
                spread=float(snapshot["spread"]),
                last_trade_price=snapshot.get("last_trade_price"),
                volume_24h=float(market.get("volume_24h") or 0),
                source="triggered",
            ),
            status,
        )

    def _fallback_snapshot(self, market: dict) -> PriceSnapshot:
        token_id = market.get("token_yes_id") or market.get("token_no_id") or ""
        midpoint = _stored_yes_price(market)
        return PriceSnapshot(
            condition_id=market["condition_id"],
            token_id=token_id,
            snapshot_time=datetime.now(timezone.utc),
            midpoint=midpoint,
            best_bid=midpoint,
            best_ask=midpoint,
            spread=0.0,
            volume_24h=float(market.get("volume_24h") or 0),
            source="triggered",
        )

    def _topic_filter(self, items: list[dict], condition_id: str) -> list[dict]:
        return [item for item in items if item.get("condition_id") == condition_id]

    def _without_topic(self, items: list[dict], condition_id: str) -> list[dict]:
        return [item for item in items if item.get("condition_id") != condition_id]

    def _followup_prompt(self, market: dict | None, analysis: dict | None) -> str:
        if not market:
            return "Refresh this market and compare the next order-book snapshot."
        stance = analysis.get("stance") if analysis else "Observe"
        return (
            f"Re-check {market['question']} after the next material price move. "
            f"Current stance: {stance}."
        )

    def _generated_message(
        self, market: dict | None, analysis: dict | None, kind: str
    ) -> dict:
        question = market.get("question") if market else "selected market"
        edge = analysis.get("edge", 0.0) if analysis else 0.0
        net_edge = analysis.get("net_edge_estimate", 0.0) if analysis else 0.0
        advice = analysis.get("advice") if analysis else "Refresh before acting."
        return {
            "at": _now(),
            "kind": kind,
            "condition_id": market.get("condition_id") if market else "",
            "title": f"Follow-up brief: {question}",
            "body": (
                f"Market: {question}\n"
                f"Edge: {edge:+.3f}, estimated net edge: {net_edge:+.3f}.\n"
                f"Advice: {advice}"
            ),
        }

    def _live_snapshot(self, market: dict, side: str = "YES") -> tuple[dict | None, dict]:
        side = _side(side)
        token_id = (
            market.get("token_no_id")
            if side == "NO" else market.get("token_yes_id")
        ) or market.get("token_yes_id") or market.get("token_no_id")
        if not token_id:
            return None, {
                "available": False,
                "reason": "No CLOB token id is stored for this market.",
            }
        try:
            book = ClobReadClient().get_order_book(token_id)
        except Exception as exc:
            return None, {
                "available": False,
                "reason": f"CLOB order-book request failed: {exc}",
            }
        if not book:
            return None, {
                "available": False,
                "reason": (
                    "No live order book was returned. This usually means the market "
                    "is closed, inactive, not CLOB-enabled, or temporarily unavailable."
                ),
            }
        bids = _book_levels(book.get("bids") or [])
        asks = _book_levels(book.get("asks") or [])
        if not bids or not asks:
            return None, {
                "available": False,
                "reason": "Live order book has no bid or ask levels.",
            }
        best_bid = bids[0]["price"]
        best_ask = asks[0]["price"]
        last_trade_price = _level_number(book, "last_trade_price")
        snapshot_time = datetime.now(timezone.utc)
        return (
            {
                "snapshot_time": _iso(snapshot_time),
                "midpoint": (best_bid + best_ask) / 2,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": max(0.0, best_ask - best_bid),
                "last_trade_price": last_trade_price,
                "side": side,
                "token_id": token_id,
                "order_book": {
                    "bids": bids,
                    "asks": asks,
                },
            },
            {"available": True, "reason": f"Live {side} CLOB order book available."},
        )

    def _latest_forecast(self, condition_id: str) -> dict | None:
        if not self.eval_db.exists():
            return None
        try:
            with duckdb.connect(str(self.eval_db), read_only=True) as conn:
                tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
                if "forecast_runs" not in tables:
                    return None
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info('forecast_runs')").fetchall()
                }
                direction_expr = (
                    "forecast_direction"
                    if "forecast_direction" in columns else "'observe'"
                )
                evidence_col = (
                    "evidence_frozen_at"
                    if "evidence_frozen_at" in columns else "evidence_cutoff"
                )
                model_col = "model_version" if "model_version" in columns else "model"
                row = conn.execute(
                    f"""
                    SELECT run_id, snapshot_time, {evidence_col}, p_m, p_f, confidence,
                           edge, {model_col}, reasoning, {direction_expr}
                    FROM forecast_runs
                    WHERE condition_id = ?
                    ORDER BY snapshot_time DESC
                    LIMIT 1
                    """,
                    [condition_id],
                ).fetchone()
        except Exception:
            return None
        if not row:
            return None
        return {
            "run_id": row[0],
            "snapshot_time": _iso(row[1]),
            "evidence_cutoff": _iso(row[2]),
            "p_m": float(row[3] or 0),
            "p_f": float(row[4] or 0),
            "confidence": float(row[5] or 0),
            "edge": float(row[6] or 0),
            "model": row[7] or "",
            "reasoning": row[8] or "",
            "forecast_direction": row[9] or "observe",
        }

    def _latest_evidence(self, run_id: str | None) -> list[dict]:
        if not run_id or not self.eval_db.exists():
            return []
        try:
            with duckdb.connect(str(self.eval_db), read_only=True) as conn:
                tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
                if "workflow_evidence_items" not in tables:
                    return []
                rows = conn.execute(
                    """
                    SELECT query, title, url, source, summary, published_at, relevance_score
                    FROM workflow_evidence_items
                    WHERE run_id = ?
                    ORDER BY relevance_score DESC NULLS LAST
                    LIMIT 8
                    """,
                    [run_id],
                ).fetchall()
        except Exception:
            return []
        return [
            {
                "query": row[0] or "",
                "title": row[1] or "",
                "url": row[2] or "",
                "source": row[3] or "",
                "summary": row[4] or "",
                "published_at": _iso(row[5]),
                "relevance_score": float(row[6] or 0),
            }
            for row in rows
        ]

    def _forecast_edges(self) -> list[dict]:
        if not self.eval_db.exists():
            return []
        try:
            with duckdb.connect(str(self.eval_db), read_only=True) as conn:
                tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
                if "forecast_runs" not in tables:
                    return []
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info('forecast_runs')").fetchall()
                }
                direction_expr = (
                    "forecast_direction"
                    if "forecast_direction" in columns else "'observe'"
                )
                rows = conn.execute(
                    f"""
                    SELECT condition_id, p_m, p_f, edge, confidence, snapshot_time,
                           {direction_expr}
                    FROM forecast_runs
                    ORDER BY ABS(edge) DESC, snapshot_time DESC
                    LIMIT 20
                    """
                ).fetchall()
        except Exception:
            return []
        return [
            {
                "condition_id": row[0],
                "p_m": float(row[1] or 0),
                "p_f": float(row[2] or 0),
                "edge": float(row[3] or 0),
                "confidence": float(row[4] or 0),
                "snapshot_time": _iso(row[5]),
                "forecast_direction": row[6] or "observe",
            }
            for row in rows
        ]

    def _latest_forecast_by_market(self) -> dict[str, dict]:
        if not self.eval_db.exists():
            return {}
        try:
            with duckdb.connect(str(self.eval_db), read_only=True) as conn:
                tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
                if "forecast_runs" not in tables:
                    return {}
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info('forecast_runs')").fetchall()
                }
                direction_expr = (
                    "forecast_direction"
                    if "forecast_direction" in columns else "'observe'"
                )
                rows = conn.execute(
                    f"""
                    SELECT condition_id, p_m, p_f, edge, confidence, snapshot_time,
                           forecast_direction
                    FROM (
                        SELECT condition_id, p_m, p_f, edge, confidence, snapshot_time,
                               {direction_expr} AS forecast_direction,
                               ROW_NUMBER() OVER (
                                   PARTITION BY condition_id
                                   ORDER BY snapshot_time DESC
                               ) AS rn
                        FROM forecast_runs
                    )
                    WHERE rn = 1
                    """
                ).fetchall()
        except Exception:
            return {}
        return {
            str(row[0]): {
                "condition_id": row[0],
                "p_m": float(row[1] or 0),
                "p_f": float(row[2] or 0),
                "edge": float(row[3] or 0),
                "confidence": float(row[4] or 0),
                "snapshot_time": _iso(row[5]),
                "forecast_direction": row[6] or "observe",
            }
            for row in rows
        }

    def _chart_points(
        self,
        condition_id: str,
        snapshot: dict | None,
        forecast: dict | None,
        side: str = "YES",
    ) -> list[dict]:
        side = _side(side)
        points: list[dict] = []
        if self.eval_db.exists():
            try:
                with duckdb.connect(str(self.eval_db), read_only=True) as conn:
                    tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
                    if "market_snapshots" in tables:
                        rows = conn.execute(
                            """
                            SELECT snapshot_time, midpoint
                            FROM market_snapshots
                            WHERE condition_id = ?
                            ORDER BY snapshot_time DESC
                            LIMIT 30
                            """,
                            [condition_id],
                        ).fetchall()
                        points.extend(
                            {
                                "at": _iso(row[0]),
                                "market": _side_probability(float(row[1] or 0), side),
                                "fair": None,
                            }
                            for row in reversed(rows)
                        )
            except Exception:
                points = []

        if not points and snapshot:
            base = snapshot["midpoint"]
            for idx in range(12):
                drift = (idx - 8) * 0.002
                points.append(
                    {
                        "at": f"T-{11 - idx}",
                        "market": max(0.001, min(0.999, base + drift)),
                        "fair": None,
                    }
                )
        if forecast:
            fair = _side_probability(forecast["p_f"], side)
            for point in points:
                point["fair"] = fair
        return points

    def _analysis(
        self,
        market: dict,
        snapshot: dict | None,
        forecast: dict | None,
        side: str = "YES",
    ) -> dict:
        side = _side(side)
        base_market = forecast["p_m"] if forecast else market.get("yes_price") or 0.0
        p_m = snapshot["midpoint"] if snapshot else _side_probability(float(base_market), side)
        p_f = _side_probability(forecast["p_f"], side) if forecast else p_m
        edge = p_f - p_m
        spread = snapshot["spread"] if snapshot else 0.0
        net_edge = abs(edge) - spread / 2 - 0.015
        lean_threshold = 0.005
        action_threshold = 0.02
        if not snapshot:
            stance = "Observe"
            advice = "No live order book snapshot is available; keep this market on watch."
        elif forecast and edge > lean_threshold:
            stance = f"Tend {side}"
            if net_edge > action_threshold:
                advice = (
                    f"Forecast exceeds {side} market after spread and fee buffer. "
                    "Size cautiously."
                )
            else:
                advice = (
                    f"Forecast leans above {side} market, but the edge is not strong enough "
                    "after spread and fee buffer."
                )
        elif forecast and edge < -lean_threshold:
            stance = f"Avoid {side}"
            if net_edge > action_threshold:
                advice = f"Forecast is below {side} market after costs. Do not buy at current ask."
            else:
                advice = (
                    f"Forecast leans below {side} market, but costs absorb most of the signal."
                )
        elif forecast and forecast.get("forecast_direction") in {"tend_yes", "tend_no"}:
            yes_direction = forecast.get("forecast_direction") == "tend_yes"
            stance = f"Tend {side}" if (side == "YES") == yes_direction else f"Avoid {side}"
            advice = "Forecast direction is directional, but current side-adjusted edge is small."
        elif market["neg_risk"]:
            stance = "Track Structure"
            advice = (
                "Neg-risk market is useful for group consistency checks; "
                "watch related outcomes."
            )
        else:
            stance = "Observe"
            advice = "No actionable edge is currently established."
        return {
            "stance": stance,
            "advice": advice,
            "p_m": p_m,
            "p_f": p_f,
            "side": side,
            "edge": edge,
            "spread": spread,
            "net_edge_estimate": net_edge,
        }


class Handler(BaseHTTPRequestHandler):
    data = GuiData()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._file("index.html")
        if parsed.path == "/api/state":
            return self._json(self.data.state_payload())
        if parsed.path.startswith("/api/market/"):
            condition_id = unquote(parsed.path.removeprefix("/api/market/"))
            side = parse_qs(parsed.query).get("side", [None])[0]
            selected = self.data.market_detail(
                condition_id,
                include_live=True,
                side=side,
            )
            event_id = (selected or {}).get("market", {}).get("event_id")
            return self._json({
                "selected": selected,
                "selected_event": (
                    self.data.event_detail(event_id, include_live_prices=True)
                    if event_id else None
                ),
            })
        if parsed.path.startswith("/assets/"):
            return self._file(parsed.path.removeprefix("/assets/"))
        return self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        payload = self._read_json()
        condition_id = str(payload.get("condition_id") or "")
        event_id = str(payload.get("event_id") or "")
        side = str(payload.get("side") or "")
        if parsed.path == "/api/select-event":
            return self._json(self.data.select_event(event_id))
        if parsed.path == "/api/select-market":
            return self._json(self.data.select_market(condition_id, side=side))
        if parsed.path == "/api/add-topic":
            return self._json(self.data.add_topic(str(payload.get("query") or "")))
        if parsed.path == "/api/get-new-topics":
            return self._json(self.data.get_new_topics(int(payload.get("cap") or 100)))
        if parsed.path == "/api/create-account":
            return self._json(self.data.create_account(str(payload.get("name") or "")))
        if parsed.path == "/api/login":
            return self._json(self.data.login_account(str(payload.get("account_id") or "")))
        if parsed.path == "/api/account-config":
            return self._json(self.data.update_account_config(payload))
        if parsed.path == "/api/account-profile":
            return self._json(self.data.update_account_profile(payload))
        if parsed.path == "/api/account-funds":
            return self._json(self.data.account_funds(payload))
        if parsed.path == "/api/maintainer-action":
            return self._json(self.data.run_maintainer_action(payload))
        if parsed.path == "/api/select":
            return self._json(self.data.select(condition_id))
        if parsed.path == "/api/track":
            return self._json(self.data.track(condition_id, bool(payload.get("track", True))))
        if parsed.path == "/api/note":
            return self._json(self.data.add_note(condition_id, str(payload.get("text") or "")))
        if parsed.path == "/api/action":
            return self._json(self.data.action(condition_id, str(payload.get("action") or "")))
        if parsed.path == "/api/update-current":
            return self._json(self.data.update_topic(condition_id))
        if parsed.path == "/api/update-tracked":
            return self._json(self.data.update_tracked_topics())
        if parsed.path == "/api/update-all":
            return self._json(
                self.data.update_all_topics(int(payload.get("max_topics") or 8))
            )
        if parsed.path == "/api/clear-topic":
            return self._json(self.data.clear_topic(condition_id))
        if parsed.path == "/api/clear-all":
            return self._json(self.data.clear_all_logs())
        return self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        logger.debug(format, *args)

    def _read_json(self) -> dict:
        length = int(self.headers.get("content-length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name: str) -> None:
        path = (ASSET_DIR / name).resolve()
        if not path.is_file() or ASSET_DIR not in path.parents:
            return self.send_error(HTTPStatus.NOT_FOUND)
        body = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _iso(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _market_row(row: tuple, tracked: set[str]) -> dict:
    return {
        "condition_id": row[0],
        "event_id": row[1] or row[0],
        "question": row[2],
        "category": row[3] or "Uncategorized",
        "neg_risk": bool(row[4]),
        "token_yes_id": row[5] or "",
        "token_no_id": row[6] or "",
        "close_time": _iso(row[7]),
        "volume_24h": float(row[8] or 0),
        "liquidity": float(row[9] or 0),
        "active": bool(row[10]),
        "slug": row[11] or "",
        "description": row[12] or "",
        "resolution_text": row[13] or "",
        "created_time": _iso(row[14]),
        "outcomes": _json_list(row[15]),
        "outcome_prices": _json_float_list(row[16]),
        "tracked": row[0] in tracked,
    }


def _market_meta_row(market: MarketMeta, tracked: set[str]) -> dict:
    return {
        "condition_id": market.condition_id,
        "event_id": market.event_id or market.condition_id,
        "question": market.question,
        "category": market.category or "Uncategorized",
        "neg_risk": bool(market.neg_risk),
        "token_yes_id": market.token_yes_id or "",
        "token_no_id": market.token_no_id or "",
        "close_time": _iso(market.close_time),
        "volume_24h": float(market.volume_24h or 0),
        "liquidity": float(market.liquidity or 0),
        "active": bool(market.active),
        "slug": market.slug or "",
        "description": market.description or "",
        "resolution_text": market.resolution_text or "",
        "created_time": _iso(market.created_time),
        "outcomes": market.outcomes,
        "outcome_prices": market.outcome_prices,
        "tracked": market.condition_id in tracked,
    }


def _parse_iso(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_past_date(value: object) -> bool:
    parsed = _parse_iso(value)
    if parsed is None:
        return False
    return parsed.date() < datetime.now().date()


def _side(value: object) -> str:
    text = str(value or "YES").upper()
    return "NO" if text == "NO" else "YES"


def _side_probability(yes_probability: float, side: str) -> float:
    probability = max(0.0, min(1.0, float(yes_probability)))
    return 1 - probability if _side(side) == "NO" else probability


def _stored_yes_price(market: dict) -> float:
    prices = market.get("outcome_prices") or []
    if prices:
        try:
            value = float(prices[0])
            return max(0.001, min(0.999, value))
        except (TypeError, ValueError):
            pass
    return 0.5


def _paper_account_dict(account) -> dict:
    data = account.model_dump(mode="json")
    data["available_cash"] = round(
        max(0.0, float(account.cash_balance) - float(account.min_cash_buffer)),
        8,
    )
    data["equity"] = round(
        float(account.cash_balance) + float(account.reserved_cash),
        8,
    )
    return data


def _paper_transaction_dict(transaction) -> dict:
    return transaction.model_dump(mode="json")


def _paper_position_dict(position, projected_pnl: float | None = None) -> dict:
    return {
        "event_id": position.event_id or position.condition_id,
        "event_title": position.question or position.condition_id,
        "event_category": position.category or "",
        "condition_id": position.condition_id,
        "token_id": position.token_id,
        "side": position.side,
        "question": position.question,
        "shares": position.shares,
        "notional": round(position.cost_basis + position.fees_paid, 8),
        "avg_price": position.avg_price,
        "fees_paid": position.fees_paid,
        "estimated_pnl": projected_pnl,
        "pnl_label": "projected edge PnL" if projected_pnl is not None else "unmarked",
        "trade_count": 1,
        "opened_at": _iso(position.opened_at),
        "updated_at": _iso(position.updated_at),
    }


def _paper_order_dict(order) -> dict:
    estimated_pnl = None
    pnl_label = "unmarked"
    if order.action == "buy" and order.net_edge:
        estimated_pnl = order.filled_shares * order.net_edge
        pnl_label = "projected edge PnL"
    return {
        "event_id": order.event_id or order.condition_id,
        "event_title": order.question or order.condition_id,
        "event_category": order.category or "",
        "condition_id": order.condition_id,
        "question": order.question,
        "side": order.side,
        "action": order.action,
        "status": order.status,
        "shares": order.filled_shares,
        "size": order.filled_shares,
        "notional": order.filled_notional,
        "avg_price": order.avg_price,
        "fee": order.fee,
        "estimated_pnl": estimated_pnl,
        "pnl_label": pnl_label,
        "trade_count": 1,
        "at": _iso(order.created_at),
        "created_at": _iso(order.created_at),
        "gross_edge": order.gross_edge,
        "net_edge": order.net_edge,
        "confidence": order.confidence,
        "reason": order.decision_reason,
    }


def _read_strategy_log(path: Path, account_id: str, limit: int = 160) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("account_id") == account_id:
                    rows.append(row)
    except OSError:
        return []
    rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return rows[:limit]


def _maintainer_console_logs(actions: list[dict], strategy_rows: list[dict]) -> list[dict]:
    logs: list[dict] = []
    for action in actions:
        payload = action.get("payload") or {}
        if action.get("kind") == "maintainer_log":
            line = str(payload.get("line") or "")
            logs.append({
                "at": action.get("at") or payload.get("started_at") or "",
                "kind": payload.get("action") or "maintainer",
                "status": "cli",
                "summary": line,
                "detail": line,
            })
            continue
        if action.get("kind") == "maintainer_start":
            logs.append({
                "at": action.get("at") or payload.get("started_at") or "",
                "kind": payload.get("action") or "maintainer",
                "status": "start",
                "summary": str(payload.get("command") or "maintainer command started"),
                "detail": str(payload.get("command") or ""),
            })
            continue
        stdout = str(payload.get("stdout") or "").strip()
        stderr = str(payload.get("stderr") or "").strip()
        summary = stdout.splitlines()[-1] if stdout else stderr[-160:]
        logs.append({
            "at": action.get("at") or payload.get("started_at") or "",
            "kind": payload.get("action") or "maintainer",
            "status": "ok" if int(payload.get("returncode") or 0) == 0 else "error",
            "summary": summary,
            "detail": summary,
        })
    for row in strategy_rows[:120]:
        row_type = row.get("type") or "strategy"
        if row_type == "strategy_run_start":
            money = row.get("money") or {}
            summary = (
                f"PAPER MAINTAINER RUN {row.get('run_id') or ''} "
                f"cash=${float(money.get('cash_balance') or 0):.2f} "
                f"open_cost=${float(money.get('open_cost_basis') or 0):.2f}"
            )
            logs.append({
                "at": row.get("created_at") or "",
                "kind": "maintainer",
                "status": "start",
                "summary": summary,
                "detail": summary,
            })
            continue
        if row_type == "strategy_run_end":
            money = row.get("money") or {}
            summary = (
                f"done. sold={int(row.get('sold') or 0)} buys={int(row.get('buys') or 0)} "
                f"cash=${float(money.get('cash_balance') or 0):.2f} "
                f"open_cost=${float(money.get('open_cost_basis') or 0):.2f} "
                f"positions={int(money.get('open_position_count') or 0)}"
            )
            logs.append({
                "at": row.get("created_at") or "",
                "kind": "maintainer",
                "status": "ok",
                "summary": summary,
                "detail": summary,
            })
            continue
        if row_type != "strategy_decision":
            continue
        phase = row.get("phase") or "strategy"
        action = row.get("action") or "decision"
        condition_id = str(row.get("condition_id") or "")
        question = str(row.get("question") or condition_id)
        extras = []
        if row.get("side"):
            extras.append(str(row.get("side")))
        if row.get("gross_edge") is not None:
            extras.append(f"edge={float(row.get('gross_edge') or 0):+.3f}")
        if row.get("confidence") is not None:
            extras.append(f"confidence={float(row.get('confidence') or 0):.2f}")
        if row.get("requested_notional") is not None:
            extras.append(f"notional=${float(row.get('requested_notional') or 0):.2f}")
        suffix = f" ({', '.join(extras)})" if extras else ""
        summary = (
            f"{phase}: {action} {condition_id[:12]} {question[:90]}"
            f"{suffix} - {row.get('reason') or row.get('error') or ''}"
        )
        logs.append({
            "at": row.get("created_at") or "",
            "kind": phase,
            "status": action,
            "summary": summary,
            "detail": summary,
        })
    logs.sort(key=lambda item: str(item.get("at") or ""))
    return logs[-260:]


def _event_groups(rows: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for row in rows:
        event_id = row.get("event_id") or row.get("condition_id") or "unknown"
        group = grouped.setdefault(
            event_id,
            {
                "event_id": event_id,
                "event_title": row.get("event_title") or row.get("question") or event_id,
                "event_category": row.get("event_category") or "",
                "row_count": 0,
                "trade_count": 0,
                "shares": 0.0,
                "notional": 0.0,
                "estimated_pnl": None,
                "_estimated_pnl_sum": 0.0,
                "_estimated_pnl_count": 0,
                "rows": [],
            },
        )
        group["rows"].append(row)
        group["row_count"] += 1
        group["trade_count"] += int(row.get("trade_count") or 1)
        group["shares"] += float(row.get("shares") or row.get("size") or 0)
        group["notional"] += float(row.get("notional") or 0)
        if row.get("estimated_pnl") is not None:
            group["_estimated_pnl_sum"] += float(row.get("estimated_pnl") or 0)
            group["_estimated_pnl_count"] += 1
    for group in grouped.values():
        if group["_estimated_pnl_count"]:
            group["estimated_pnl"] = group["_estimated_pnl_sum"]
        group.pop("_estimated_pnl_sum", None)
        group.pop("_estimated_pnl_count", None)
    return sorted(grouped.values(), key=lambda item: item["event_title"])


def _payload_float(payload: dict, key: str, default: float) -> float:
    value = payload.get(key, default)
    if value is None or value == "":
        return float(default)
    return float(value)


def _paper_order(account, snapshot: dict | None) -> dict:
    available = max(0.0, float(account.cash_balance) - float(account.min_cash_buffer))
    price = float((snapshot or {}).get("best_ask") or 1.0)
    price = max(price, 0.001)
    if account.sizing_mode == "fixed":
        notional = min(available, float(account.max_order_notional))
    elif account.sizing_mode == "fraction":
        notional = available * float(account.order_fraction)
        notional = min(notional, float(account.max_order_notional))
    else:
        notional = available
    fees = notional * float(account.fee_rate_bps) / 10_000
    slippage = notional * float(account.slippage_bps) / 10_000
    executable_notional = max(0.0, notional - fees - slippage)
    shares = executable_notional / price
    return {
        "notional": round(notional, 2),
        "shares": round(shares, 4),
        "fees": round(fees, 4),
        "slippage_cost": round(slippage, 4),
        "fee_rate_bps": float(account.fee_rate_bps),
        "slippage_bps": float(account.slippage_bps),
        "sizing_mode": account.sizing_mode,
    }


def _book_levels(levels: list[object]) -> list[dict]:
    parsed: list[dict] = []
    # CLOB v2 gives best bid/ask as the last level; display best levels first.
    for level in reversed(levels):
        price = _level_number(level, "price")
        size = _level_number(level, "size")
        if price is None or size is None:
            continue
        parsed.append({
            "price": price,
            "size": size,
            "total": price * size,
        })
    return parsed


def _level_number(level: object, key: str) -> float | None:
    value = level.get(key) if isinstance(level, dict) else getattr(level, key, None)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_list(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _json_float_list(value: object) -> list[float]:
    parsed: list[float] = []
    for item in _json_list(value):
        try:
            parsed.append(float(item))
        except (TypeError, ValueError):
            continue
    return parsed


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the BeatOdds local GUI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the GUI in the default browser")
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    logger.info(f"BeatOdds GUI running at {url}")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping BeatOdds GUI")


if __name__ == "__main__":
    main()
