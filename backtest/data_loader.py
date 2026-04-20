"""
Backtest Data Loader — Loads historical data from various sources.
Supports yfinance (free), CSV files, and generates synthetic data for testing.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)


class BacktestDataLoader:
    """Load and prepare historical data for backtesting."""

    def __init__(self, market_data=None):
        self.md = market_data
        self.cache_dir = config.CACHE_DIR

    def load(self, symbol: str = "NIFTY", days: int = 90,
             interval: str = "5min", source: str = "auto") -> pd.DataFrame:
        """
        Load historical data for backtesting.
        
        Args:
            symbol: Index or stock name
            days: Number of days of history
            interval: Candle interval (1min, 5min, 15min, 1h, daily)
            source: 'api', 'csv', 'synthetic', or 'auto'
        
        Returns:
            DataFrame with columns: open, high, low, close, volume (datetime index)
        """
        df = pd.DataFrame()

        if source == "auto":
            # Try API first, then CSV, then synthetic
            df = self._load_from_api(symbol, days, interval)
            if df.empty:
                df = self._load_from_csv(symbol)
            if df.empty:
                logger.info("Using synthetic data for backtest.")
                df = self._generate_synthetic(symbol, days, interval)
        elif source == "api":
            df = self._load_from_api(symbol, days, interval)
        elif source == "csv":
            df = self._load_from_csv(symbol)
        elif source == "synthetic":
            df = self._generate_synthetic(symbol, days, interval)

        if not df.empty:
            df = self._clean_data(df)
            logger.info("Loaded %d candles for %s backtest.", len(df), symbol)

        return df

    def _load_from_api(self, symbol: str, days: int, interval: str = "5min") -> pd.DataFrame:
        """Load from yfinance via market data service (FREE)."""
        if self.md is None or not self.md.is_connected:
            return pd.DataFrame()

        try:
            # Map interval format
            interval_map = {
                "1min": "1m", "5min": "5m", "15min": "15m",
                "1h": "60m", "daily": "1d",
            }
            yf_interval = interval_map.get(interval, "5m")

            # Map days to yfinance period
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

            df = self.md.get_historical(symbol, period=period, interval=yf_interval)
            return df
        except Exception as e:
            logger.error("API data load failed: %s", e)
            return pd.DataFrame()

    def _load_from_csv(self, symbol: str) -> pd.DataFrame:
        """Load from local CSV file."""
        csv_patterns = [
            self.cache_dir / f"{symbol.lower()}_historical.csv",
            config.DATA_DIR / f"{symbol.lower()}.csv",
            config.BASE_DIR / "data" / f"{symbol.lower()}_data.csv",
        ]

        for csv_path in csv_patterns:
            if csv_path.exists():
                try:
                    df = pd.read_csv(csv_path, parse_dates=True, index_col=0)
                    # Standardize column names
                    col_map = {}
                    for col in df.columns:
                        lower = col.lower()
                        if "open" in lower:
                            col_map[col] = "open"
                        elif "high" in lower:
                            col_map[col] = "high"
                        elif "low" in lower:
                            col_map[col] = "low"
                        elif "close" in lower:
                            col_map[col] = "close"
                        elif "vol" in lower:
                            col_map[col] = "volume"
                    df = df.rename(columns=col_map)
                    logger.info("Loaded CSV: %s (%d rows)", csv_path.name, len(df))
                    return df
                except Exception as e:
                    logger.warning("CSV load failed for %s: %s", csv_path, e)

        return pd.DataFrame()

    def _generate_synthetic(self, symbol: str, days: int = 90,
                            interval: str = "5min") -> pd.DataFrame:
        """
        Generate realistic synthetic OHLCV data for backtesting.
        Uses geometric Brownian motion with realistic Indian market characteristics.
        """
        # Starting prices for known indices
        start_prices = {
            "NIFTY": 22500, "BANKNIFTY": 48000,
            "FINNIFTY": 22000, "MIDCPNIFTY": 10500,
        }
        price = start_prices.get(symbol.upper(), 22500)

        # Interval mapping
        freq_map = {
            "1min": "1min", "5min": "5min", "15min": "15min",
            "1h": "1h", "daily": "1D",
        }
        freq = freq_map.get(interval, "5min")

        # Generate timestamps (market hours only)
        end = datetime.now()
        start = end - timedelta(days=days)
        all_dates = pd.date_range(start, end, freq=freq)

        # Filter to market hours (9:15 - 15:30 IST)
        market_dates = all_dates[
            (all_dates.time >= datetime.strptime("09:15", "%H:%M").time()) &
            (all_dates.time <= datetime.strptime("15:30", "%H:%M").time()) &
            (all_dates.weekday < 5)
        ]

        if len(market_dates) == 0:
            market_dates = pd.date_range(end - timedelta(days=days), end, periods=5000, freq="5min")

        # GBM parameters
        mu = 0.0001      # Slight upward drift (Indian market long-term trend)
        sigma = 0.001     # Volatility per candle
        dt = 1

        rows = []
        for i, ts in enumerate(market_dates):
            # Add mean-reverting noise for realism
            shock = np.random.normal(mu, sigma)

            # Add occasional trends and reversals
            if i % 100 == 0:
                trend_bias = np.random.choice([-0.0003, 0, 0.0003])
            else:
                trend_bias = 0

            ret = shock + trend_bias
            open_p = price
            close_p = price * (1 + ret)

            # Intrabar volatility
            bar_vol = abs(np.random.normal(0, sigma * 2))
            high_p = max(open_p, close_p) * (1 + bar_vol)
            low_p = min(open_p, close_p) * (1 - bar_vol)

            # Volume — higher during open/close
            hour = ts.hour
            base_vol = 50000
            if hour == 9:
                base_vol = 200000
            elif hour == 15:
                base_vol = 150000
            elif hour in (10, 14):
                base_vol = 100000
            volume = max(1000, int(np.random.normal(base_vol, base_vol * 0.3)))

            rows.append({
                "open": round(open_p, 2),
                "high": round(high_p, 2),
                "low": round(low_p, 2),
                "close": round(close_p, 2),
                "volume": volume,
            })

            price = close_p

        df = pd.DataFrame(rows, index=market_dates[:len(rows)])
        df.index.name = "timestamp"
        return df

    def _clean_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Clean and validate historical data."""
        required = ["open", "high", "low", "close"]
        for col in required:
            if col not in df.columns:
                logger.error("Missing column: %s", col)
                return pd.DataFrame()

        # Drop NaN rows
        df = df.dropna(subset=required)

        # Add volume if missing
        if "volume" not in df.columns:
            df["volume"] = 0

        # Ensure correct types
        for col in required + ["volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Remove zero/negative prices
        df = df[(df["close"] > 0) & (df["open"] > 0)]

        # Ensure high >= low
        df["high"] = df[["open", "high", "low", "close"]].max(axis=1)
        df["low"] = df[["open", "high", "low", "close"]].min(axis=1)

        # Sort by index
        df = df.sort_index()

        return df

    def save_to_csv(self, df: pd.DataFrame, symbol: str):
        """Save data to CSV for future use."""
        path = self.cache_dir / f"{symbol.lower()}_historical.csv"
        df.to_csv(path)
        logger.info("Saved %d rows to %s", len(df), path.name)
