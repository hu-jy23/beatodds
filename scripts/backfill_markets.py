#!/usr/bin/env python3
"""One-shot script: backfill all Polymarket markets to Parquet + DuckDB.

Usage:
    uv run scripts/backfill_markets.py
    uv run scripts/backfill_markets.py --incremental
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from beatodds.data.indexers import MarketIndexer
from loguru import logger


def main():
    parser = argparse.ArgumentParser(description="Backfill Polymarket markets")
    parser.add_argument("--incremental", action="store_true",
                        help="Only fetch recent active markets (fast)")
    args = parser.parse_args()

    indexer = MarketIndexer()

    if args.incremental:
        logger.info("Running incremental market update")
        n = indexer.run_incremental()
    else:
        logger.info("Running full market backfill (may take several minutes)")
        n = indexer.run_backfill()

    logger.info(f"Done: {n} new markets stored")


if __name__ == "__main__":
    main()
