"""Pydantic-settings config. All secrets via .env, never hardcoded."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Polymarket
    polymarket_pk: str = ""
    polymarket_api_key: str = ""
    polymarket_api_secret: str = ""
    polymarket_api_passphrase: str = ""
    polymarket_chain_id: int = 137
    polymarket_clob_host: str = "https://clob.polymarket.com"
    gamma_api_url: str = "https://gamma-api.polymarket.com"

    # LLM — supports both Anthropic and OpenAI backends
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_cheap_model: str = "claude-haiku-4-5-20251001"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_cheap_model: str = "gpt-4o-mini"

    # DeepSeek — OpenAI-compatible API, cheaper alternative
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"       # DeepSeek-V3
    deepseek_cheap_model: str = "deepseek-chat"  # same model, cheap enough

    @property
    def llm_backend(self) -> str:
        """Auto-select backend: Anthropic > DeepSeek > OpenAI."""
        if self.anthropic_api_key:
            return "anthropic"
        if self.deepseek_api_key:
            return "deepseek"
        if self.openai_api_key:
            return "openai"
        return "none"

    # Evidence retrieval
    tavily_api_key: str = ""
    newsapi_key: str = ""

    # Storage
    data_dir: Path = Field(default=Path("./data"))
    log_level: str = "INFO"

    # Scanner thresholds
    scanner_min_volume_24h: float = 100.0    # USD
    scanner_min_days_to_close: float = 1.0
    scanner_max_spread: float = 0.10         # 10¢ max spread to be liquid enough

    # Structural detection
    bundle_min_edge: float = 0.01            # 1¢ minimum gross edge
    bundle_taker_fee_bps: float = 150.0      # 1.5% Polymarket taker fee
    bundle_gas_per_order: float = 0.02       # USD gas estimate

    # Scheduling intervals (seconds)
    scanner_interval_s: int = 300            # 5 min
    evidence_interval_s: int = 1800          # 30 min
    score_interval_s: int = 3600             # 1 hour

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def markets_dir(self) -> Path:
        return self.raw_dir / "markets"

    @property
    def snapshots_dir(self) -> Path:
        return self.raw_dir / "snapshots"

    @property
    def price_history_dir(self) -> Path:
        return self.raw_dir / "price_history"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
