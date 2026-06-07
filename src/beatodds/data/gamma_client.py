"""Polymarket Gamma API client and market payload parsing."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx

from beatodds.common.config import get_settings
from beatodds.common.types import EventMeta, MarketMeta


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


def _parse_float_list(value: Any) -> list[float]:
    raw_values = _parse_json_list(value)
    parsed: list[float] = []
    for item in raw_values:
        try:
            parsed.append(float(item))
        except (TypeError, ValueError):
            continue
    return parsed


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


def _tag_labels(raw_event: dict[str, Any]) -> list[str]:
    tags = raw_event.get("tags") or []
    if not isinstance(tags, list):
        return []
    labels: list[str] = []
    for tag in tags:
        if isinstance(tag, dict) and tag.get("label"):
            labels.append(str(tag["label"]))
    return labels


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
        page_limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return active markets ordered by 24h volume."""
        if limit <= 0:
            return []
        page_limit = page_limit or self.cfg.scanner_gamma_page_limit
        page_limit = max(1, min(page_limit, limit))
        markets: list[dict[str, Any]] = []
        seen: set[str] = set()
        offset = 0
        while len(markets) < limit:
            batch_limit = min(page_limit, limit - len(markets))
            resp = self._client.get(
                "/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "order": "volume24hr",
                    "ascending": "false",
                    "limit": batch_limit,
                    "offset": offset,
                    "volume_num_min": min_volume_24h,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list) or not data:
                break
            before = len(markets)
            for item in data:
                key = str(item.get("conditionId") or item.get("condition_id") or "")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                markets.append(item)
                if len(markets) >= limit:
                    break
            if len(data) < batch_limit or len(markets) == before:
                break
            offset += len(data)
        return markets

    def get_event_markets(self, event_id: str) -> list[MarketMeta]:
        """Fetch all markets in one Gamma event, used for complete neg-risk groups."""
        data = self.get_event(event_id)
        if not data:
            return []
        markets = data.get("markets", []) if isinstance(data, dict) else []
        parsed: list[MarketMeta] = []
        for raw in markets:
            try:
                parsed.append(self.parse_market(raw, parent_event=data))
            except Exception:
                continue
        return parsed

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        if not event_id:
            return None
        resp = self._client.get(f"/events/{event_id}")
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else None

    def parse_event(self, raw: dict[str, Any]) -> EventMeta:
        tags = _tag_labels(raw)
        return EventMeta(
            event_id=str(raw.get("id") or raw.get("eventId") or raw.get("event_id") or ""),
            title=str(raw.get("title") or raw.get("question") or ""),
            slug=str(raw.get("slug") or ""),
            ticker=str(raw.get("ticker") or ""),
            description=str(raw.get("description") or ""),
            image=str(raw.get("image") or ""),
            icon=str(raw.get("icon") or raw.get("image") or ""),
            category=str(raw.get("category") or (tags[0] if tags else "")),
            tags=tags,
            start_time=_parse_dt(raw.get("startDate") or raw.get("creationDate")),
            end_time=_parse_dt(raw.get("endDate") or raw.get("endDateIso")),
            volume_24h=_float(raw.get("volume24hr") or raw.get("volume24hrClob")),
            liquidity=_float(raw.get("liquidity") or raw.get("liquidityClob")),
            active=bool(raw.get("active", True)),
            closed=bool(raw.get("closed", False)),
            archived=bool(raw.get("archived", False)),
            neg_risk=bool(raw.get("negRisk") or raw.get("enableNegRisk")),
            market_count=len(raw.get("markets") or []),
        )

    def parse_market(
        self,
        raw: dict[str, Any],
        parent_event: dict[str, Any] | None = None,
    ) -> MarketMeta:
        outcomes = _parse_json_list(raw.get("outcomes"))
        outcome_prices = _parse_float_list(raw.get("outcomePrices"))
        token_ids = _parse_json_list(raw.get("clobTokenIds"))
        events = raw.get("events") or []
        event_raw = parent_event or {}
        if not event_raw and isinstance(events, list) and events:
            maybe_event = events[0]
            if isinstance(maybe_event, dict):
                event_raw = maybe_event
        event_id = ""
        event_category = ""
        if event_raw:
            event = self.parse_event(event_raw)
            event_id = event.event_id
            event_category = event.category

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
            category=str(raw.get("category") or event_category or raw.get("groupItemTitle") or ""),
            neg_risk=neg_risk,
            neg_risk_market_id=str(neg_risk_market_id) if neg_risk_market_id else None,
            token_yes_id=token_ids[0] if token_ids else "",
            token_no_id=token_ids[1] if len(token_ids) > 1 else "",
            outcome_count=len(outcomes) if outcomes else len(token_ids),
            outcomes=outcomes,
            outcome_prices=outcome_prices,
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
