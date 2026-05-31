"""Small local web server for the BeatOdds operator GUI."""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import duckdb
from loguru import logger

from beatodds.common.config import get_settings
from beatodds.data.clob_client import ClobReadClient

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "gui" / "web"


@dataclass
class GuiState:
    tracked_ids: list[str]
    selected_id: str | None
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
                return GuiState([], None, [], [], [], [], [], [])
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return GuiState(
                tracked_ids=list(data.get("tracked_ids", [])),
                selected_id=data.get("selected_id"),
                notes=list(data.get("notes", [])),
                actions=list(data.get("actions", [])),
                deals=list(data.get("deals", [])),
                followups=list(data.get("followups", [])),
                reviews=list(data.get("reviews", [])),
                messages=list(data.get("messages", [])),
            )

    def save(self, state: GuiState) -> None:
        with self._lock:
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

    def state_payload(self) -> dict:
        state = self.store.load()
        markets = self.markets()
        selected_id = state.selected_id or (markets[0]["condition_id"] if markets else None)
        selected = self.market_detail(selected_id, markets=markets) if selected_id else None
        return {
            "state": asdict(state),
            "markets": markets,
            "selected": selected,
            "stats": self.stats(markets),
            "tracked_report": self.tracked_report(markets),
            "history": state.actions[:40],
            "notes": state.notes[:60],
        }

    def markets(self) -> list[dict]:
        if not self.market_db.exists():
            return []
        with duckdb.connect(str(self.market_db), read_only=True) as conn:
            rows = conn.execute(
                """
                SELECT condition_id, question, category, neg_risk, token_yes_id, token_no_id,
                       close_time, volume_24h, liquidity, active, slug
                FROM markets
                ORDER BY volume_24h DESC NULLS LAST
                LIMIT 120
                """
            ).fetchall()
        state = self.store.load()
        tracked = set(state.tracked_ids)
        return [
            {
                "condition_id": row[0],
                "question": row[1],
                "category": row[2] or "Uncategorized",
                "neg_risk": bool(row[3]),
                "token_yes_id": row[4] or "",
                "token_no_id": row[5] or "",
                "close_time": _iso(row[6]),
                "volume_24h": float(row[7] or 0),
                "liquidity": float(row[8] or 0),
                "active": bool(row[9]),
                "slug": row[10] or "",
                "tracked": row[0] in tracked,
            }
            for row in rows
        ]

    def market_detail(
        self, condition_id: str | None, markets: list[dict] | None = None
    ) -> dict | None:
        if not condition_id:
            return None
        market_list = markets if markets is not None else self.markets()
        market = next((m for m in market_list if m["condition_id"] == condition_id), None)
        if market is None:
            return None

        snapshot, snapshot_status = self._live_snapshot(market)
        forecast = self._latest_forecast(condition_id)
        chart = self._chart_points(condition_id, snapshot, forecast)
        return {
            "market": market,
            "snapshot": snapshot,
            "snapshot_status": snapshot_status,
            "forecast": forecast,
            "analysis": self._analysis(market, snapshot, forecast),
            "chart": chart,
            "evidence": self._latest_evidence(forecast.get("run_id") if forecast else None),
            "topic_logs": self.topic_logs(condition_id),
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

    def stats(self, markets: list[dict]) -> dict:
        state = self.store.load()
        categories: dict[str, int] = {}
        for market in markets:
            categories[market["category"]] = categories.get(market["category"], 0) + 1
        edges = self._forecast_edges()
        return {
            "market_count": len(markets),
            "tracked_count": len(state.tracked_ids),
            "note_count": len(state.notes),
            "action_count": len(state.actions),
            "deal_count": len(state.deals),
            "followup_count": len(state.followups),
            "review_count": len(state.reviews),
            "total_volume_24h": sum(m["volume_24h"] for m in markets),
            "total_liquidity": sum(m["liquidity"] for m in markets),
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

    def track(self, condition_id: str, track: bool = True) -> dict:
        state = self.store.load()
        ids = set(state.tracked_ids)
        if track:
            ids.add(condition_id)
            state.selected_id = condition_id
        else:
            ids.discard(condition_id)
        state.tracked_ids = list(ids)
        self.store.save(state)
        self.store.append_action("track" if track else "untrack", condition_id)
        return self.state_payload()

    def select(self, condition_id: str) -> dict:
        state = self.store.load()
        state.selected_id = condition_id
        self.store.save(state)
        return self.state_payload()

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
        detail = self.market_detail(condition_id)
        market = detail.get("market") if detail else None
        snapshot = detail.get("snapshot") if detail else None
        analysis = detail.get("analysis") if detail else None
        if action == "paper_deal":
            net_edge = analysis.get("net_edge_estimate") if analysis else None
            size = 10
            estimated_pnl = float(net_edge or 0) * size
            human_report = self._deal_report(market, snapshot, analysis, size, estimated_pnl)
            special_report = self._special_report_for_deal(
                market, analysis, estimated_pnl, size
            )
            deal = {
                "at": _now(),
                "condition_id": condition_id,
                "question": market.get("question") if market else "",
                "side": "YES",
                "size": size,
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
        size: int,
        estimated_pnl: float,
    ) -> str:
        question = market.get("question") if market else "selected market"
        ask = snapshot.get("best_ask") if snapshot else None
        net_edge = analysis.get("net_edge_estimate") if analysis else None
        if estimated_pnl >= 0:
            result = f"projected gain about ${estimated_pnl:.2f}"
        else:
            result = f"projected loss about ${abs(estimated_pnl):.2f}"
        return (
            f"Simulated YES {size} on '{question}'"
            f"{f' at {ask:.1%}' if ask is not None else ''}. "
            f"Estimated net edge is {float(net_edge or 0):+.1%}; {result} "
            "before final settlement and execution uncertainty."
        )

    def _special_report_for_deal(
        self,
        market: dict | None,
        analysis: dict | None,
        estimated_pnl: float,
        size: int,
    ) -> dict | None:
        if not analysis:
            return None
        net_edge = float(analysis.get("net_edge_estimate") or 0)
        if net_edge >= 0.20:
            severity = "gain"
            title = "Possible gain above 20%"
            body = (
                f"YES paper deal shows {net_edge:.1%} estimated net edge; "
                f"rough gain is ${max(0, estimated_pnl):.2f} on {size} shares."
            )
        elif net_edge <= -0.05:
            severity = "loss"
            title = "Possible loss warning"
            body = (
                f"YES paper deal shows {net_edge:.1%} estimated net edge; "
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

    def _live_snapshot(self, market: dict) -> tuple[dict | None, dict]:
        token_id = market.get("token_yes_id") or market.get("token_no_id")
        if not token_id:
            return None, {
                "available": False,
                "reason": "No CLOB token id is stored for this market.",
            }
        try:
            snapshot = ClobReadClient().get_snapshot(market["condition_id"], token_id)
        except Exception as exc:
            return None, {
                "available": False,
                "reason": f"CLOB order-book request failed: {exc}",
            }
        if snapshot is None:
            return None, {
                "available": False,
                "reason": (
                    "No live order book was returned. This usually means the market "
                    "is closed, inactive, not CLOB-enabled, or temporarily unavailable."
                ),
            }
        return (
            {
                "snapshot_time": _iso(snapshot.snapshot_time),
                "midpoint": snapshot.midpoint,
                "best_bid": snapshot.best_bid,
                "best_ask": snapshot.best_ask,
                "spread": snapshot.spread,
                "last_trade_price": snapshot.last_trade_price,
            },
            {"available": True, "reason": "Live CLOB order book available."},
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
                    SELECT run_id, snapshot_time, evidence_cutoff, p_m, p_f, confidence,
                           edge, model, reasoning
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
        self, condition_id: str, snapshot: dict | None, forecast: dict | None
    ) -> list[dict]:
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
                            {"at": _iso(row[0]), "market": float(row[1] or 0), "fair": None}
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
            for point in points:
                point["fair"] = forecast["p_f"]
        return points

    def _analysis(self, market: dict, snapshot: dict | None, forecast: dict | None) -> dict:
        p_m = snapshot["midpoint"] if snapshot else (forecast["p_m"] if forecast else 0.0)
        p_f = forecast["p_f"] if forecast else p_m
        edge = p_f - p_m
        spread = snapshot["spread"] if snapshot else 0.0
        net_edge = abs(edge) - spread / 2 - 0.015
        lean_threshold = 0.005
        action_threshold = 0.02
        if not snapshot:
            stance = "Observe"
            advice = "No live order book snapshot is available; keep this market on watch."
        elif forecast and edge > lean_threshold:
            stance = "Tend YES"
            if net_edge > action_threshold:
                advice = "Forecast exceeds market after spread and fee buffer. Size cautiously."
            else:
                advice = (
                    "Forecast leans above market, but the edge is not strong enough "
                    "after spread and fee buffer."
                )
        elif forecast and edge < -lean_threshold:
            stance = "Tend NO"
            if net_edge > action_threshold:
                advice = "Forecast is below market after costs. Do not buy YES at current ask."
            else:
                advice = (
                    "Forecast leans below market, but costs absorb most of the signal."
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
            return self._json({"selected": self.data.market_detail(condition_id)})
        if parsed.path.startswith("/assets/"):
            return self._file(parsed.path.removeprefix("/assets/"))
        return self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        payload = self._read_json()
        condition_id = str(payload.get("condition_id") or "")
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
