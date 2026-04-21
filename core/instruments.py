"""
Instrument manager — maps symbols to security IDs.
Downloads the free instrument master CSV from Dhan (no paid API needed)
and caches locally for strike/expiry lookups.
"""

import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

import config

logger = logging.getLogger(__name__)

INSTRUMENT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"


class InstrumentManager:
    """Manage NSE F&O instrument mappings."""

    def __init__(self):
        self._instruments: Optional[pd.DataFrame] = None
        self._cache_file = config.CACHE_DIR / "instruments.csv"
        self._load()

    def _load(self):
        """Load instruments from cache or download."""
        if self._cache_file.exists():
            age = (datetime.now().timestamp() - self._cache_file.stat().st_mtime) / 3600
            if age < 24:
                try:
                    self._instruments = pd.read_csv(self._cache_file)
                    logger.info("Instruments loaded from cache: %d rows", len(self._instruments))
                    return
                except Exception:
                    pass
        self.refresh()

    def refresh(self):
        """Download fresh instrument list (free CSV, no API key needed)."""
        try:
            logger.info("Downloading Dhan instrument master...")
            df = pd.read_csv(INSTRUMENT_URL, low_memory=False)
            fno = df[df["SEM_SEGMENT"].isin(["NSE_FNO", "BSE_FNO"])]
            self._instruments = fno.reset_index(drop=True)
            self._instruments.to_csv(self._cache_file, index=False)
            logger.info("Instruments refreshed: %d F&O instruments", len(self._instruments))
        except Exception as e:
            logger.error("Dhan instrument download failed: %s", e)
            self._instruments = pd.DataFrame()

    def get_security_id(self, symbol: str, option_type: str = "CE",
                        strike: float = 0, expiry: str = "") -> Optional[str]:
        """Look up security ID for an option contract."""
        if self._instruments is None or self._instruments.empty:
            return None
        df = self._instruments
        mask = df["SEM_TRADING_SYMBOL"].str.contains(symbol, case=False, na=False)
        if option_type:
            mask &= df["SEM_OPTION_TYPE"].str.upper() == option_type.upper()
        if strike > 0:
            mask &= df["SEM_STRIKE_PRICE"] == strike
        if expiry:
            mask &= df["SEM_EXPIRY_DATE"].str.contains(expiry, na=False)
        result = df[mask]
        if not result.empty:
            return str(result.iloc[0]["SEM_SMST_SECURITY_ID"])
        return None

    def get_expiry_dates(self, symbol: str) -> list[str]:
        """Get available expiry dates for a symbol, sorted ascending."""
        if self._instruments is None or self._instruments.empty:
            return []
        df = self._instruments
        mask = df["SEM_TRADING_SYMBOL"].str.contains(symbol, case=False, na=False)
        dates = df[mask]["SEM_EXPIRY_DATE"].dropna().unique().tolist()
        return sorted(dates)

    def get_strikes(self, symbol: str, expiry: str = "") -> list[float]:
        """Get available strike prices for a symbol/expiry."""
        if self._instruments is None or self._instruments.empty:
            return []
        df = self._instruments
        mask = df["SEM_TRADING_SYMBOL"].str.contains(symbol, case=False, na=False)
        if expiry:
            mask &= df["SEM_EXPIRY_DATE"].str.contains(expiry, na=False)
        strikes = df[mask]["SEM_STRIKE_PRICE"].dropna().unique().tolist()
        return sorted(strikes)

    def get_underlying_id(self, symbol: str) -> Optional[str]:
        """Get the underlying index/stock security ID."""
        mapping = {
            "NIFTY": "13", "BANKNIFTY": "25",
            "FINNIFTY": "27", "MIDCPNIFTY": "442",
        }
        return mapping.get(symbol.upper())
