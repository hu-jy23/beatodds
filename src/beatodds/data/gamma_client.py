"""Polymarket Gamma API client and market payload parsing."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx

from beatodds.common.config import get_settings
from beatodds.common.types import MarketMeta


def _parse_json_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class GammaClient:
    def __init__(self, timeout_s: float = 30.0):
        self.cfg = get_settings()
        self._client = httpx.Client(
            base_url=self.cfg.gamma_api_url.rstrip("/"),
            timeout=timeout_s,
        )

    def __enter__(self) -> GammaClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get_liquid_markets(
        self,
        limit: int = 500,
        min_volume_24h: float = 100.0,
    ) -> list[dict[str, Any]]:
        """Return active markets ordered by 24h volume."""
        resp = self._client.get(
            "/markets",
            params={
                "active": "true",
                "closed": "false",
                "order": "volume24hr",
                "ascending": "false",
                "limit": limit,
                "volume_num_min": min_volume_24h,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    def get_event_markets(self, event_id: str) -> list[MarketMeta]:
        """Fetch all markets in one Gamma event, used for complete neg-risk groups."""
        if not event_id:
            return []
        resp = self._client.get(f"/events/{event_id}")
        resp.raise_for_status()
        data = resp.json()
        markets = data.get("markets", []) if isinstance(data, dict) else []
        parsed: list[MarketMeta] = []
        for raw in markets:
            try:
                parsed.append(self.parse_market(raw))
            except Exception:
                continue
        return parsed

    def parse_market(self, raw: dict[str, Any]) -> MarketMeta:
        outcomes = _parse_json_list(raw.get("outcomes"))
        token_ids = _parse_json_list(raw.get("clobTokenIds"))
        events = raw.get("events") or []
        event_id = ""
        if isinstance(events, list) and events:
            event_id = str(events[0].get("id", ""))

        neg_risk = bool(raw.get("negRisk") or raw.get("neg_risk"))
        neg_risk_market_id = (
            raw.get("negRiskMarketID")
            or raw.get("negRiskMarketId")
            or raw.get("negRiskRequestID")
            or (event_id if neg_risk else None)
        )

        description = str(raw.get("description") or "")
        return MarketMeta(
            condition_id=str(raw.get("conditionId") or raw.get("condition_id") or ""),
            question=str(raw.get("question") or raw.get("title") or ""),
            description=description,
            resolution_text=description or str(raw.get("resolutionSource") or ""),
            category=str(raw.get("category") or raw.get("groupItemTitle") or ""),
            neg_risk=neg_risk,
            neg_risk_market_id=str(neg_risk_market_id) if neg_risk_market_id else None,
            token_yes_id=token_ids[0] if token_ids else "",
            token_no_id=token_ids[1] if len(token_ids) > 1 else "",
            outcome_count=len(outcomes) if outcomes else len(token_ids),
            outcomes=outcomes,
            close_time=_parse_dt(raw.get("endDate") or raw.get("endDateIso")),
            created_time=_parse_dt(raw.get("createdAt") or raw.get("startDate")),
            volume_24h=_float(raw.get("volume24hr") or raw.get("volume24hrClob")),
            liquidity=_float(
                raw.get("liquidityNum") or raw.get("liquidityClob") or raw.get("liquidity")
            ),
            active=bool(raw.get("active", True)) and not bool(raw.get("closed", False)),
            slug=str(raw.get("slug") or ""),
            event_id=event_id or str(raw.get("eventId") or raw.get("event_id") or ""),
        )
