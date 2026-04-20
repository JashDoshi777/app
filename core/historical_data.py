"""
Historical data fetcher with local file caching.
Uses yfinance (free) and caches to disk to avoid redundant API calls.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import config

logger = logging.getLogger(__name__)


class HistoricalDataManager:
    """Fetch, cache, and serve historical candle data via yfinance."""

    def __init__(self, market_data):
        self.md = market_data
        self.cache_dir = config.CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_candles(
        self,
        symbol: str = "NIFTY",
        days: int = 90,
        interval: str = "5m",
    ) -> pd.DataFrame:
        """Get candle data, using cache if available and fresh."""
        cache_file = self.cache_dir / f"{symbol.lower()}_{interval}_{days}d.parquet"

        if cache_file.exists():
            age_hours = (datetime.now().timestamp() - cache_file.stat().st_mtime) / 3600
            if age_hours < 4:  # Cache valid for 4 hours
                try:
                    return pd.read_parquet(cache_file)
                except Exception:
                    pass

        # yfinance period mapping
        if days <= 7:
            period = "5d"
        elif days <= 30:
            period = "1mo"
        elif days <= 90:
            period = "3mo"
        elif days <= 180:
            period = "6mo"
        else:
            period = "1y"

        df = self.md.get_historical(symbol, period=period, interval=interval)

        if not df.empty:
            try:
                df.to_parquet(cache_file)
            except Exception as e:
                logger.warning("Cache write failed: %s", e)

        return df

    def get_intraday(self, symbol: str = "NIFTY") -> pd.DataFrame:
        """Get today's intraday 1-min candles."""
        return self.md.get_historical(symbol, period="1d", interval="1m")

    def get_daily(self, symbol: str = "NIFTY", years: int = 2) -> pd.DataFrame:
        """Get daily candles for longer-term analysis."""
        period = f"{years}y"
        return self.md.get_historical(symbol, period=period, interval="1d")

    def clear_cache(self):
        """Remove all cached files."""
        for f in self.cache_dir.glob("*.parquet"):
            f.unlink()
        logger.info("Cache cleared.")
