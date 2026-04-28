"""
NSE Option Chain Analyzer — replicates and extends the logic from
github.com/VarunS2002/Python-NSE-Option-Chain-Analyzer.

Calculates: Call/Put Sum, Difference, OI Boundaries, PCR,
Call/Put Exits, ITM signals, Max Pain, OI buildup classification.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)


@dataclass
class OISnapshot:
    """Single point-in-time OI analysis result."""
    timestamp: datetime = field(default_factory=datetime.now)
    symbol: str = ""
    strike_price: float = 0
    underlying_price: float = 0

    # Core OCA signals
    call_sum: float = 0
    put_sum: float = 0
    difference: float = 0  # call_sum - put_sum
    call_boundary: float = 0
    put_boundary: float = 0
    pcr: float = 0

    # Trend signals
    oi_trend: str = "SIDEWAYS"  # BULLISH / BEARISH / SIDEWAYS
    call_exits: bool = False
    put_exits: bool = False
    call_itm: bool = False
    put_itm: bool = False

    # OI boundaries (support / resistance)
    max_call_oi_strike: float = 0
    max_put_oi_strike: float = 0
    max_call_oi: int = 0
    max_put_oi: int = 0
    second_call_oi_strike: float = 0
    second_put_oi_strike: float = 0

    # Max pain
    max_pain_strike: float = 0

    # Classification
    buildup: str = ""  # LONG_BUILDUP, SHORT_BUILDUP, LONG_COVERING, SHORT_COVERING


class OptionChainAnalyzer:
    """
    Continuous NSE Option Chain analysis engine.
    Maintains history of OI snapshots for trend detection.
    """

    def __init__(self, symbol: str = "NIFTY"):
        self.symbol = symbol
        self.history: list[OISnapshot] = []
        self.latest: Optional[OISnapshot] = None

        idx_cfg = config.INDICES.get(symbol, {})
        self.lot_size = idx_cfg.get("lot_size", 25)
        self.strike_interval = idx_cfg.get("strike_interval", 50)
        self._divisor = 1000 if symbol in config.INDICES else 10

    def analyze(self, chain_df: pd.DataFrame, underlying_price: float,
                strike_price: float = 0) -> OISnapshot:
        """
        Run full OI analysis on an option chain DataFrame.
        strike_price: ATM strike to focus analysis around.
        """
        if chain_df.empty:
            return OISnapshot(symbol=self.symbol)

        if strike_price <= 0:
            strike_price = self._find_atm_strike(chain_df, underlying_price)

        snap = OISnapshot(
            symbol=self.symbol,
            strike_price=strike_price,
            underlying_price=underlying_price,
        )

        strikes = sorted(chain_df["strike"].unique())
        try:
            atm_idx = strikes.index(strike_price)
        except ValueError:
            atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - strike_price))

        # ── Call Sum & Put Sum (ATM + 2 above) ────────────
        target_strikes = strikes[atm_idx: atm_idx + 3]
        ce_chg = chain_df[chain_df["strike"].isin(target_strikes)]["ce_chg_oi"].sum()
        pe_chg = chain_df[chain_df["strike"].isin(target_strikes)]["pe_chg_oi"].sum()
        snap.call_sum = round(ce_chg / self._divisor, 2)
        snap.put_sum = round(pe_chg / self._divisor, 2)
        snap.difference = round(snap.call_sum - snap.put_sum, 2)

        # ── Call Boundary (2 strikes above) ───────────────
        if atm_idx + 2 < len(strikes):
            boundary_strike = strikes[atm_idx + 2]
            row = chain_df[chain_df["strike"] == boundary_strike]
            if not row.empty:
                snap.call_boundary = round(row.iloc[0]["ce_chg_oi"] / self._divisor, 2)

        # ── Put Boundary (at ATM strike) ──────────────────
        atm_row = chain_df[chain_df["strike"] == strike_price]
        if not atm_row.empty:
            snap.put_boundary = round(atm_row.iloc[0]["pe_chg_oi"] / self._divisor, 2)

        # ── PCR ───────────────────────────────────────────
        total_ce_oi = chain_df["ce_oi"].sum()
        total_pe_oi = chain_df["pe_oi"].sum()
        snap.pcr = round(total_pe_oi / total_ce_oi, 4) if total_ce_oi > 0 else 0

        # ── OI Trend ──────────────────────────────────────
        if snap.put_sum > snap.call_sum:
            snap.oi_trend = "BULLISH"
        elif snap.call_sum > snap.put_sum:
            snap.oi_trend = "BEARISH"
        else:
            snap.oi_trend = "SIDEWAYS"

        # ── Call Exits ────────────────────────────────────
        snap.call_exits = snap.call_sum < 0 or snap.call_boundary < 0

        # ── Put Exits ─────────────────────────────────────
        snap.put_exits = snap.put_sum < 0 or snap.put_boundary < 0

        # ── Call ITM (4 strikes above) ────────────────────
        if atm_idx + 4 < len(strikes):
            itm_strike = strikes[atm_idx + 4]
            itm_row = chain_df[chain_df["strike"] == itm_strike]
            if not itm_row.empty:
                ce_w = itm_row.iloc[0]["ce_chg_oi"]
                pe_w = itm_row.iloc[0]["pe_chg_oi"]
                if ce_w != 0:
                    ratio = abs(pe_w / ce_w)
                    snap.call_itm = ratio > 1.5 or ce_w < 0

        # ── Put ITM (2 strikes below) ─────────────────────
        if atm_idx - 2 >= 0:
            itm_strike = strikes[atm_idx - 2]
            itm_row = chain_df[chain_df["strike"] == itm_strike]
            if not itm_row.empty:
                pe_w = itm_row.iloc[0]["pe_chg_oi"]
                ce_w = itm_row.iloc[0]["ce_chg_oi"]
                if pe_w != 0:
                    ratio = abs(ce_w / pe_w)
                    snap.put_itm = ratio > 1.5 or pe_w < 0

        # ── OI Boundaries (Resistance / Support) ─────────
        snap.max_call_oi = int(chain_df["ce_oi"].max())
        snap.max_put_oi = int(chain_df["pe_oi"].max())
        snap.max_call_oi_strike = float(chain_df.loc[chain_df["ce_oi"].idxmax(), "strike"])
        snap.max_put_oi_strike = float(chain_df.loc[chain_df["pe_oi"].idxmax(), "strike"])

        # Second highest
        ce_sorted = chain_df.nlargest(2, "ce_oi")
        pe_sorted = chain_df.nlargest(2, "pe_oi")
        if len(ce_sorted) >= 2:
            snap.second_call_oi_strike = float(ce_sorted.iloc[1]["strike"])
        if len(pe_sorted) >= 2:
            snap.second_put_oi_strike = float(pe_sorted.iloc[1]["strike"])

        # ── Max Pain ──────────────────────────────────────
        snap.max_pain_strike = self._calculate_max_pain(chain_df)

        # ── OI Buildup Classification ─────────────────────
        snap.buildup = self._classify_buildup(chain_df, underlying_price)

        # Store in history
        self.latest = snap
        self.history.append(snap)
        if len(self.history) > 500:
            self.history = self.history[-500:]

        return snap

    def _find_atm_strike(self, df: pd.DataFrame, price: float) -> float:
        strikes = df["strike"].unique()
        return float(min(strikes, key=lambda s: abs(s - price)))

    def _calculate_max_pain(self, df: pd.DataFrame) -> float:
        """Max Pain = strike where total loss for option writers is minimum."""
        strikes = sorted(df["strike"].unique())
        min_pain = float("inf")
        max_pain_strike = strikes[len(strikes) // 2]

        for s in strikes:
            # Call writers' pain: calls are ITM when settlement (s) > strike (K)
            # Pain = sum of max(0, s - K) * ce_oi for all strikes K
            ce_pain = sum(
                max(0, s - row["strike"]) * row["ce_oi"]
                for _, row in df.iterrows()
            )
            # Put writers' pain: puts are ITM when strike (K) > settlement (s)
            # Pain = sum of max(0, K - s) * pe_oi for all strikes K
            pe_pain = sum(
                max(0, row["strike"] - s) * row["pe_oi"]
                for _, row in df.iterrows()
            )
            total = ce_pain + pe_pain
            if total < min_pain:
                min_pain = total
                max_pain_strike = s

        return float(max_pain_strike)

    def _classify_buildup(self, df: pd.DataFrame, price: float) -> str:
        """Classify overall OI buildup pattern."""
        atm = self._find_atm_strike(df, price)
        nearby = df[abs(df["strike"] - atm) <= self.strike_interval * 3]

        total_ce_chg = nearby["ce_chg_oi"].sum()
        total_pe_chg = nearby["pe_chg_oi"].sum()

        if total_pe_chg > 0 and total_ce_chg > 0:
            if total_pe_chg > total_ce_chg:
                return "LONG_BUILDUP"
            return "SHORT_BUILDUP"
        elif total_ce_chg < 0:
            return "SHORT_COVERING"
        elif total_pe_chg < 0:
            return "LONG_COVERING"
        return "NEUTRAL"

    def get_trend_summary(self) -> dict:
        """Return a summary dict of current OI-based trends."""
        if not self.latest:
            return {"trend": "NO_DATA"}
        s = self.latest

        # Detect trend change from history
        trend_shift = "STABLE"
        if len(self.history) >= 3:
            prev = self.history[-3]
            if prev.oi_trend != s.oi_trend:
                trend_shift = f"{prev.oi_trend} → {s.oi_trend}"

        return {
            "trend": s.oi_trend,
            "pcr": s.pcr,
            "call_sum": s.call_sum,
            "put_sum": s.put_sum,
            "difference": s.difference,
            "call_exits": bool(s.call_exits),
            "put_exits": bool(s.put_exits),
            "call_itm": bool(s.call_itm),
            "put_itm": bool(s.put_itm),
            "resistance": s.max_call_oi_strike,
            "support": s.max_put_oi_strike,
            "max_pain": s.max_pain_strike,
            "buildup": s.buildup,
            "trend_shift": trend_shift,
        }
