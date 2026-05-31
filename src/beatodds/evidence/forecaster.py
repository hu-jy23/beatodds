"""LLM Forecaster — generates p_f from evidence using Claude or GPT-4o.

Backend auto-selected from config: Anthropic preferred, OpenAI as fallback.
The temporal contract: ALL evidence must already be frozen before this call.
This module makes NO external API calls beyond the LLM inference.

Reference: ref/agent-benchmark/FutureShow/tools/forecast.py (prompt structure)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from loguru import logger

from beatodds.common.config import get_settings
from beatodds.common.types import CandidateMarket, EvidenceItem, ForecastResult

_SYSTEM_PROMPT = """You are an expert prediction market forecaster. You will be given:
1. A market question and resolution criteria
2. A list of recent news/evidence snippets
3. The current market price (implied probability)

Your task: estimate the TRUE probability of the market resolving YES.

Rules:
- Base your estimate ONLY on the provided evidence — do not use training knowledge beyond context
- Consider what the evidence implies about the resolution criterion
- If evidence is sparse or ambiguous, stay close to the market price
- Output ONLY valid JSON:

{
  "p_f": <0.0-1.0>,
  "forecast_direction": "<tend_yes|tend_no|observe>",
  "confidence": <0.1-1.0>,
  "reasoning": "<1-2 sentences explaining the key factor driving your estimate>"
}

p_f: your probability estimate
forecast_direction: tend_yes when evidence makes YES more likely than the market price,
tend_no when evidence makes YES less likely than the market price, observe when evidence
does not support a directional lean.
confidence: 0.1=very uncertain (stay near market), 1.0=high certainty from strong evidence
"""


class LLMForecaster:
    def __init__(self):
        self.cfg = get_settings()
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        backend = self.cfg.llm_backend
        if backend == "anthropic":
            import anthropic
            self._client = ("anthropic", anthropic.Anthropic(api_key=self.cfg.anthropic_api_key))
        elif backend == "deepseek":
            import openai
            self._client = ("openai", openai.OpenAI(
                api_key=self.cfg.deepseek_api_key,
                base_url=self.cfg.deepseek_base_url,
            ))
        elif backend == "openai":
            import openai
            self._client = ("openai", openai.OpenAI(api_key=self.cfg.openai_api_key))
        else:
            raise RuntimeError(
                "No LLM API key configured. Set DEEPSEEK_API_KEY or "
                "ANTHROPIC_API_KEY in .env"
            )
        return self._client

    def forecast(
        self,
        candidate: CandidateMarket,
        evidence: list[EvidenceItem],
        evidence_frozen_at: datetime,
    ) -> ForecastResult:
        market = candidate.market
        p_m = candidate.snapshot.midpoint
        backend = self.cfg.llm_backend
        model_name = {
            "anthropic": self.cfg.anthropic_model,
            "deepseek": self.cfg.deepseek_model,
            "openai": self.cfg.openai_model,
        }.get(backend, "unknown")

        try:
            result = self._call_llm(market, p_m, evidence)
            result.frozen_at = evidence_frozen_at
            result.model = model_name
            return result
        except Exception as e:
            logger.warning(f"LLMForecaster failed for {market.condition_id}: {e}")
            return ForecastResult(
                condition_id=market.condition_id,
                p_f=p_m,
                confidence=0.1,
                forecast_direction="observe",
                evidence_items=evidence,
                reasoning=f"Forecast failed ({type(e).__name__}); defaulting to market price",
                frozen_at=evidence_frozen_at,
                model="fallback",
            )

    def _call_llm(self, market, p_m: float, evidence: list[EvidenceItem]) -> ForecastResult:
        backend, client = self._get_client()

        evidence_text = "\n\n".join(
            f"[{i+1}] {e.title} ({e.source}, {e.published_at.strftime('%Y-%m-%d')})\n{e.summary}"
            for i, e in enumerate(evidence[:10])
        ) if evidence else "No external evidence available."

        resolution_str = (
            market.resolution_text
            or market.description
            or "No explicit resolution criteria."
        )
        user_text = (
            f"Market: {market.question}\n"
            f"Current market price (p_m): {p_m:.4f} ({p_m*100:.1f}%)\n\n"
            f"Resolution criteria:\n{resolution_str}\n\n"
            f"Evidence ({len(evidence)} items):\n{evidence_text}"
        )

        if backend == "anthropic":
            raw = self._call_anthropic(client, user_text, resolution_str)
        else:  # openai-compatible (openai or deepseek)
            model = (self.cfg.deepseek_model if self.cfg.llm_backend == "deepseek"
                     else self.cfg.openai_model)
            raw = self._call_openai(client, user_text, model)

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())

        p_f = max(0.001, min(0.999, float(data["p_f"])))
        direction = str(data.get("forecast_direction") or "").lower()
        if direction not in {"tend_yes", "tend_no", "observe"}:
            if p_f > p_m + 0.005:
                direction = "tend_yes"
            elif p_f < p_m - 0.005:
                direction = "tend_no"
            else:
                direction = "observe"
        return ForecastResult(
            condition_id=market.condition_id,
            p_f=p_f,
            confidence=float(data.get("confidence", 0.5)),
            forecast_direction=direction,
            evidence_items=evidence,
            reasoning=data.get("reasoning", ""),
            frozen_at=datetime.now(timezone.utc),   # overwritten by caller
            model="",
        )

    def _call_anthropic(self, client, user_text: str, resolution_str: str) -> str:
        response = client.messages.create(
            model=self.cfg.anthropic_model,
            max_tokens=256,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": user_text,
                 "cache_control": {"type": "ephemeral"}},
            ]}],
        )
        return response.content[0].text

    def _call_openai(self, client, user_text: str, model: str | None = None) -> str:
        response = client.chat.completions.create(
            model=model or self.cfg.openai_model,
            max_tokens=256,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content
