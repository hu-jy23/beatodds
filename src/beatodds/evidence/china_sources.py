"""China source registry used by the evidence router."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field


class ChinaSource(BaseModel):
    name: str
    domain: str
    source_type: str
    jurisdiction: str = ""
    topics: list[str] = Field(default_factory=list)
    reliability_prior: float = 0.0
    access_method: str = "site_search"
    notes: str = ""


def _default_registry_path() -> Path:
    return Path(__file__).resolve().parents[3] / "configs" / "china_sources.json"


@lru_cache(maxsize=4)
def load_china_sources(path: str | None = None) -> tuple[ChinaSource, ...]:
    registry_path = Path(path) if path else _default_registry_path()
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    return tuple(ChinaSource(**item) for item in data.get("sources", []))


def domain_from_url(url_or_domain: str) -> str:
    value = (url_or_domain or "").strip().lower()
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    domain = parsed.netloc or parsed.path
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def source_for_domain(
    url_or_domain: str,
    sources: tuple[ChinaSource, ...] | None = None,
) -> ChinaSource | None:
    domain = domain_from_url(url_or_domain)
    if not domain:
        return None
    for source in sources or load_china_sources():
        source_domain = domain_from_url(source.domain)
        if domain == source_domain or domain.endswith(f".{source_domain}"):
            return source
    return None
