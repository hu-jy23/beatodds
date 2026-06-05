"""Small local web server for the BeatOdds operator GUI."""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
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
from beatodds.data.clob_client import ClobReadClient
from beatodds.evaluation.paper_store import (
    create_paper_account,
    deposit_cash,
    ensure_default_paper_account,
    load_account_transactions,
    load_paper_account,
    load_paper_accounts,
    update_account_profile,
    update_risk_params,
    withdraw_cash,
)

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


class GuiStore:
    def __init__(self):
        self.cfg = get_settings()
        self.path = self.cfg.data_dir / "gui_state.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def load(self) -> GuiState:
        with self._lock:
            if not self.path.exists():
                return GuiState([], None, None, None, "YES", None, [], [], [], [], [], [])
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
        state.actions = state.actions[:80]
        self.save(state)


class GuiData:
    def __init__(self):
        self.cfg = get_settings()
        self.store = GuiStore()

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
            "events": events,
            "markets": markets[:120],
            "selected_event": selected_event,
            "selected": selected,
            "account_context": account_context,
            "stats": self.stats(events, markets),
            "tracked_report": self.tracked_report(markets),
            "history": state.actions[:40],
            "notes": state.notes[:60],
        }

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

    def _account_activity(self, account_id: str) -> dict:
        state = self.store.load()
        transactions = [
            _paper_transaction_dict(item)
            for item in load_account_transactions(account_id, limit=80)
        ]
        nav_points = [
            {
                "at": item["created_at"],
                "nav": round(item["cash_after"] + item["reserved_after"], 8),
            }
            for item in reversed(transactions)
        ]
        deals = [
            item for item in state.deals
            if item.get("account_id") in {account_id, None, ""}
        ]
        trade_records = [
            self._enrich_trade_record(item)
            for item in sorted(
                deals,
                key=lambda item: item.get("at") or "",
                reverse=True,
            )[:80]
        ]
        positions_by_key: dict[tuple[str, str, str], dict] = {}
        for deal in trade_records:
            key = (
                deal.get("event_id") or deal.get("condition_id") or "",
                deal.get("condition_id") or "",
                deal.get("side") or "YES",
            )
            if not key[1]:
                continue
            row = positions_by_key.setdefault(
                key,
                {
                    "event_id": key[0],
                    "event_title": deal.get("event_title") or deal.get("question") or "",
                    "condition_id": key[1],
                    "side": key[2],
                    "question": deal.get("question") or "",
                    "shares": 0.0,
                    "notional": 0.0,
                    "estimated_pnl": 0.0,
                    "trade_count": 0,
                },
            )
            row["shares"] += float(deal.get("size") or 0)
            row["notional"] += float(deal.get("notional") or 0)
            row["estimated_pnl"] += float(deal.get("estimated_pnl") or 0)
            row["trade_count"] += 1
        positions = sorted(
            positions_by_key.values(),
            key=lambda item: (item["event_title"], -abs(item["notional"])),
        )
        event_position_groups = _event_groups(positions)
        event_trade_groups = _event_groups(trade_records)
        estimated_pnl = sum(float(item.get("estimated_pnl") or 0) for item in trade_records)
        user_stats = {
            "trade_count": len(trade_records),
            "position_count": len(positions),
            "estimated_pnl": round(estimated_pnl, 8),
            "transaction_count": len(transactions),
            "latest_nav": nav_points[-1]["nav"] if nav_points else 0.0,
        }
        return {
            "transactions": transactions,
            "nav_points": nav_points,
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
            if "fetched_at" in columns:
                filters.append("fetched_at = (SELECT MAX(fetched_at) FROM markets)")
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
        grouped: dict[str, dict] = {}
        for market in markets:
            event_id = market["event_id"] or market["condition_id"]
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
                    "active": True,
                    "markets": [],
                },
            )
            event["markets"].append(market)
            event["market_count"] += 1
            event["market_volume_24h"] += market["volume_24h"]
            event["market_liquidity"] += market["liquidity"]
            event["neg_risk_count"] += 1 if market["neg_risk"] else 0
            event["tracked_count"] += 1 if market["condition_id"] in tracked else 0
            event["active"] = event["active"] and market["active"]

        for event in grouped.values():
            if not event["volume_24h"]:
                event["volume_24h"] = event["market_volume_24h"]
            if not event["liquidity"]:
                event["liquidity"] = event["market_liquidity"]
            event["top_markets"] = event["markets"][:4]
            event.pop("markets")
        return sorted(
            grouped.values(),
            key=lambda item: item["volume_24h"],
            reverse=True,
        )[:120]

    def event_detail(
        self,
        event_id: str | None,
        markets: list[dict] | None = None,
        events: list[dict] | None = None,
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

        edge_by_market = {item["condition_id"]: item for item in self._forecast_edges()}
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
                })
            enriched_markets.append(enriched)

        edges = [m["edge"] for m in enriched_markets if "edge" in m]
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

    def topic_logs(self, condition_id: str) -> dict:
        state = self.store.load()
        return {
            "notes": self._topic_filter(state.notes, condition_id)[:40],
            "actions": self._topic_filter(state.actions, condition_id)[:40],
            "deals": self._topic_filter(state.deals, condition_id)[:20],
            "followups": self._topic_filter(state.followups, condition_id)[:20],
            "reviews": self._topic_filter(state.reviews, condition_id)[:20],
            "messages": self._topic_filter(state.messages, condition_id)[:20],
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

    def clear_topic(self, condition_id: str) -> dict:
        state = self.store.load()
        state.notes = self._without_topic(state.notes, condition_id)
        state.actions = self._without_topic(state.actions, condition_id)
        state.deals = self._without_topic(state.deals, condition_id)
        state.followups = self._without_topic(state.followups, condition_id)
        state.reviews = self._without_topic(state.reviews, condition_id)
        state.messages = self._without_topic(state.messages, condition_id)
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
                row = conn.execute(
                    """
                    SELECT run_id, snapshot_time, evidence_frozen_at, p_m, p_f, confidence,
                           edge, model_version, reasoning
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
                rows = conn.execute(
                    """
                    SELECT condition_id, p_m, p_f, edge, confidence, snapshot_time
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
            }
            for row in rows
        ]

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
            return self._json({
                "selected": self.data.market_detail(
                    condition_id,
                    include_live=True,
                    side=side,
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
        if parsed.path == "/api/select":
            return self._json(self.data.select(condition_id))
        if parsed.path == "/api/track":
            return self._json(self.data.track(condition_id, bool(payload.get("track", True))))
        if parsed.path == "/api/note":
            return self._json(self.data.add_note(condition_id, str(payload.get("text") or "")))
        if parsed.path == "/api/action":
            return self._json(self.data.action(condition_id, str(payload.get("action") or "")))
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


def _side(value: object) -> str:
    text = str(value or "YES").upper()
    return "NO" if text == "NO" else "YES"


def _side_probability(yes_probability: float, side: str) -> float:
    probability = max(0.0, min(1.0, float(yes_probability)))
    return 1 - probability if _side(side) == "NO" else probability


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
                "estimated_pnl": 0.0,
                "rows": [],
            },
        )
        group["rows"].append(row)
        group["row_count"] += 1
        group["trade_count"] += int(row.get("trade_count") or 1)
        group["shares"] += float(row.get("shares") or row.get("size") or 0)
        group["notional"] += float(row.get("notional") or 0)
        group["estimated_pnl"] += float(row.get("estimated_pnl") or 0)
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
