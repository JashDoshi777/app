"""
Implied Volatility Analysis Engine.
IV Rank, IV Percentile, IV Skew, Volatility Surface, VIX integration.
"""

import logging
from datetime import datetime
import numpy as np
import pandas as pd

import config
from analysis.greeks import BlackScholes

logger = logging.getLogger(__name__)


class IVAnalyzer:
    """Implied Volatility analysis for options positioning."""

    def __init__(self):
        self.bs = BlackScholes()
        self.iv_history: list[dict] = []

    def analyze(self, chain_df: pd.DataFrame, underlying_price: float,
                vix: float = 0) -> dict:
        """Full IV analysis on current option chain."""
        if chain_df.empty:
            return {"status": "NO_DATA"}

        result = {"timestamp": datetime.now().isoformat(), "underlying": underlying_price}

        # ATM IV
        atm_strike = self._find_atm(chain_df, underlying_price)
        atm_row = chain_df[chain_df["strike"] == atm_strike]
        if not atm_row.empty:
            result["atm_ce_iv"] = float(atm_row.iloc[0].get("ce_iv", 0))
            result["atm_pe_iv"] = float(atm_row.iloc[0].get("pe_iv", 0))
            result["atm_avg_iv"] = round((result["atm_ce_iv"] + result["atm_pe_iv"]) / 2, 2)
        else:
            result["atm_ce_iv"] = result["atm_pe_iv"] = result["atm_avg_iv"] = 0

        # VIX
        result["vix"] = vix

        # IV Rank & Percentile (from history)
        iv_rank, iv_pct = self._iv_rank_percentile(result["atm_avg_iv"])
        result["iv_rank"] = iv_rank
        result["iv_percentile"] = iv_pct

        # IV Skew
        result["iv_skew"] = self._iv_skew(chain_df, underlying_price)

        # IV Signal
        if iv_rank > 70:
            result["iv_signal"] = "HIGH_IV"
            result["iv_strategy_hint"] = "SELL_PREMIUM"
        elif iv_rank < 30:
            result["iv_signal"] = "LOW_IV"
            result["iv_strategy_hint"] = "BUY_PREMIUM"
        else:
            result["iv_signal"] = "NORMAL_IV"
            result["iv_strategy_hint"] = "NEUTRAL"

        # Store history
        self.iv_history.append({
            "timestamp": result["timestamp"],
            "atm_iv": result["atm_avg_iv"],
            "vix": vix,
        })
        if len(self.iv_history) > 1000:
            self.iv_history = self.iv_history[-1000:]

        return result

    def _find_atm(self, df: pd.DataFrame, price: float) -> float:
        return float(min(df["strike"].unique(), key=lambda s: abs(s - price)))

    def _iv_rank_percentile(self, current_iv: float) -> tuple[float, float]:
        """
        IV Rank = (Current IV - 52w Low) / (52w High - 52w Low) * 100
        IV Percentile = % of days IV was below current IV
        """
        if len(self.iv_history) < 10:
            return 50.0, 50.0

        ivs = [h["atm_iv"] for h in self.iv_history if h["atm_iv"] > 0]
        if not ivs:
            return 50.0, 50.0

        iv_min, iv_max = min(ivs), max(ivs)
        iv_range = iv_max - iv_min
        rank = ((current_iv - iv_min) / iv_range * 100) if iv_range > 0 else 50
        pct = sum(1 for iv in ivs if iv < current_iv) / len(ivs) * 100

        return round(rank, 2), round(pct, 2)

    def _iv_skew(self, df: pd.DataFrame, price: float) -> dict:
        """
        Calculate IV skew between OTM puts and OTM calls.
        Positive skew = puts are more expensive (fear in market).
        """
        atm = self._find_atm(df, price)
        otm_puts = df[(df["strike"] < atm) & (df["pe_iv"] > 0)].tail(3)
        otm_calls = df[(df["strike"] > atm) & (df["ce_iv"] > 0)].head(3)

        avg_put_iv = otm_puts["pe_iv"].mean() if not otm_puts.empty else 0
        avg_call_iv = otm_calls["ce_iv"].mean() if not otm_calls.empty else 0
        skew = round(avg_put_iv - avg_call_iv, 2)

        return {
            "skew": skew,
            "avg_otm_put_iv": round(avg_put_iv, 2),
            "avg_otm_call_iv": round(avg_call_iv, 2),
            "interpretation": (
                "PUT_SKEW_HIGH" if skew > 3
                else "CALL_SKEW_HIGH" if skew < -3
                else "BALANCED"
            ),
        }
