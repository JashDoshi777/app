"""
Open Interest Analysis Engine.
Tracks OI changes, buildup classification, Short Covering / Long Unwinding
detection, and support/resistance levels.

4 Key OI-Price Patterns (institutional standard):
1. LONG_BUILDUP    → Price ↑ + OI ↑ → Fresh buying (BULLISH)
2. SHORT_BUILDUP   → Price ↓ + OI ↑ → Fresh shorting (BEARISH)
3. SHORT_COVERING  → Price ↑ + OI ↓ → Shorts closing (BULLISH, can cause sharp rallies)
4. LONG_UNWINDING  → Price ↓ + OI ↓ → Longs exiting (BEARISH, selling pressure)
"""

import logging
from datetime import datetime
import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)


class OIAnalyzer:
    """Advanced Open Interest analysis with Short Covering / Long Unwinding detection."""

    def __init__(self):
        self.history: list[dict] = []
        self._prev_chain: pd.DataFrame = pd.DataFrame()
        self._prev_underlying: float = 0

    def analyze(self, chain_df: pd.DataFrame, underlying_price: float) -> dict:
        """Run comprehensive OI analysis with buildup classification."""
        if chain_df.empty:
            return {"status": "NO_DATA"}

        result = {
            "timestamp": datetime.now().isoformat(),
            "underlying": underlying_price,
        }

        # ── PCR Analysis ──────────────────────────────────
        total_ce_oi = chain_df["ce_oi"].sum()
        total_pe_oi = chain_df["pe_oi"].sum()
        pcr = round(total_pe_oi / max(total_ce_oi, 1), 4)
        result["pcr"] = pcr
        result["pcr_signal"] = (
            "BULLISH" if pcr > config.PCR_BULLISH_THRESHOLD
            else "BEARISH" if pcr < config.PCR_BEARISH_THRESHOLD
            else "NEUTRAL"
        )

        # ── Volume PCR ────────────────────────────────────
        total_ce_vol = chain_df["ce_volume"].sum()
        total_pe_vol = chain_df["pe_volume"].sum()
        result["volume_pcr"] = round(total_pe_vol / max(total_ce_vol, 1), 4)

        # ── Max Pain ──────────────────────────────────────
        result["max_pain"] = self._max_pain(chain_df)

        # ── OI Concentration (Support / Resistance) ──────
        result["resistance"] = float(chain_df.loc[chain_df["ce_oi"].idxmax(), "strike"])
        result["support"] = float(chain_df.loc[chain_df["pe_oi"].idxmax(), "strike"])

        top_ce = chain_df.nlargest(3, "ce_oi")[["strike", "ce_oi"]]
        result["resistance_levels"] = top_ce.to_dict("records")

        top_pe = chain_df.nlargest(3, "pe_oi")[["strike", "pe_oi"]]
        result["support_levels"] = top_pe.to_dict("records")

        # ══════════════════════════════════════════════════
        #  STRIKE-LEVEL OI-PRICE BUILDUP CLASSIFICATION
        # ══════════════════════════════════════════════════
        price_change = underlying_price - self._prev_underlying if self._prev_underlying > 0 else 0
        price_direction = "UP" if price_change > 0 else "DOWN" if price_change < 0 else "FLAT"

        strike_analysis = []
        for _, row in chain_df.iterrows():
            strike = row["strike"]
            ce_chg_oi = row.get("ce_chg_oi", 0)
            pe_chg_oi = row.get("pe_chg_oi", 0)

            # Classify CE buildup
            ce_buildup = self._classify_oi_price(ce_chg_oi, price_change, "CE")
            # Classify PE buildup
            pe_buildup = self._classify_oi_price(pe_chg_oi, price_change, "PE")

            strike_analysis.append({
                "strike": strike,
                "ce_chg_oi": int(ce_chg_oi),
                "pe_chg_oi": int(pe_chg_oi),
                "ce_buildup": ce_buildup,
                "pe_buildup": pe_buildup,
            })

        result["strike_buildups"] = strike_analysis

        # ══════════════════════════════════════════════════
        #  MARKET-LEVEL SHORT COVERING / LONG UNWINDING
        # ══════════════════════════════════════════════════
        sc_lw = self._detect_short_covering_long_unwinding(
            chain_df, underlying_price, price_change
        )
        result.update(sc_lw)

        # ── OI Change Heatmap Data ────────────────────────
        result["ce_oi_changes"] = chain_df[["strike", "ce_chg_oi"]].to_dict("records")
        result["pe_oi_changes"] = chain_df[["strike", "pe_chg_oi"]].to_dict("records")

        # ── Unusual OI Activity ───────────────────────────
        ce_mean = chain_df["ce_chg_oi"].abs().mean()
        pe_mean = chain_df["pe_chg_oi"].abs().mean()
        unusual = chain_df[
            (chain_df["ce_chg_oi"].abs() > ce_mean * 2) |
            (chain_df["pe_chg_oi"].abs() > pe_mean * 2)
        ]
        result["unusual_oi_strikes"] = unusual["strike"].tolist()

        # ── Overall Signal ────────────────────────────────
        # Factor in short covering / long unwinding
        bullish_count = sum(1 for b in strike_analysis if b["pe_buildup"] == "LONG_BUILDUP")
        bearish_count = sum(1 for b in strike_analysis if b["ce_buildup"] == "SHORT_BUILDUP")
        sc_count = sum(1 for b in strike_analysis if b["ce_buildup"] == "SHORT_COVERING")
        lu_count = sum(1 for b in strike_analysis if b["pe_buildup"] == "LONG_UNWINDING")

        # Short covering adds to bullish signal, long unwinding adds to bearish
        net_bullish = bullish_count + sc_count * 0.7
        net_bearish = bearish_count + lu_count * 0.7

        if net_bullish > net_bearish * 1.5:
            result["oi_signal"] = "BULLISH"
        elif net_bearish > net_bullish * 1.5:
            result["oi_signal"] = "BEARISH"
        else:
            result["oi_signal"] = "NEUTRAL"

        result["oi_signal_score"] = round(
            (net_bullish - net_bearish) / max(net_bullish + net_bearish, 1), 2
        )

        result["price_direction"] = price_direction
        result["price_change"] = round(price_change, 2)

        # Save state for next comparison
        self._prev_chain = chain_df.copy()
        self._prev_underlying = underlying_price

        self.history.append(result)
        if len(self.history) > 200:
            self.history = self.history[-200:]

        return result

    def _detect_short_covering_long_unwinding(
        self, chain_df: pd.DataFrame, price: float, price_change: float
    ) -> dict:
        """
        Detect aggregate Short Covering and Long Unwinding events.
        
        SHORT COVERING (Bullish):
        - Call writers closing positions: CE OI drops significantly
        - Price moves UP while CE OI drops → shorts are panicking
        - Can trigger sharp upside rallies
        
        LONG UNWINDING (Bearish):
        - Put buyers/CE holders exiting: PE OI drops or CE OI drops with price
        - Price moves DOWN while OI drops → longs are giving up
        - Leads to sustained downside
        """
        atm = self._find_atm(chain_df, price)
        nearby_mask = abs(chain_df["strike"] - atm) <= 500  # ~10 strikes around ATM
        nearby = chain_df[nearby_mask]

        total_ce_chg = nearby["ce_chg_oi"].sum()
        total_pe_chg = nearby["pe_chg_oi"].sum()

        # Count strikes showing each pattern
        sc_strikes = []  # Short covering strikes
        lu_strikes = []  # Long unwinding strikes
        lb_strikes = []  # Long buildup strikes
        sb_strikes = []  # Short buildup strikes

        for _, row in nearby.iterrows():
            strike = row["strike"]
            ce_chg = row.get("ce_chg_oi", 0)
            pe_chg = row.get("pe_chg_oi", 0)

            # Call side: OI dropping + price rising = short covering
            if ce_chg < 0 and price_change > 0:
                sc_strikes.append({"strike": strike, "ce_oi_drop": int(abs(ce_chg))})
            # Call side: OI rising + price falling = short buildup
            if ce_chg > 0 and price_change < 0:
                sb_strikes.append({"strike": strike, "ce_oi_add": int(ce_chg)})
            # Put side: OI dropping + price falling = long unwinding
            if pe_chg < 0 and price_change < 0:
                lu_strikes.append({"strike": strike, "pe_oi_drop": int(abs(pe_chg))})
            # Put side: OI rising + price rising = long buildup
            if pe_chg > 0 and price_change > 0:
                lb_strikes.append({"strike": strike, "pe_oi_add": int(pe_chg)})

        # Determine dominant pattern
        patterns = {
            "SHORT_COVERING": len(sc_strikes),
            "LONG_UNWINDING": len(lu_strikes),
            "LONG_BUILDUP": len(lb_strikes),
            "SHORT_BUILDUP": len(sb_strikes),
        }
        dominant_pattern = max(patterns, key=patterns.get)
        pattern_strength = patterns[dominant_pattern] / max(len(nearby), 1)

        # Short covering intensity (total OI dropped on call side with price up)
        sc_intensity = sum(s["ce_oi_drop"] for s in sc_strikes) if sc_strikes else 0
        lu_intensity = sum(s["pe_oi_drop"] for s in lu_strikes) if lu_strikes else 0

        return {
            "market_buildup": dominant_pattern if pattern_strength > 0.3 else "MIXED",
            "buildup_strength": round(pattern_strength * 100, 1),

            # Short Covering details
            "short_covering": {
                "active": len(sc_strikes) > 2 and price_change > 0,
                "strike_count": len(sc_strikes),
                "strikes": sc_strikes[:5],  # Top 5
                "total_ce_oi_dropped": int(sc_intensity),
                "signal": "BULLISH_RALLY" if len(sc_strikes) > 3 and sc_intensity > 50000 else "MILD" if sc_strikes else "NONE",
            },

            # Long Unwinding details
            "long_unwinding": {
                "active": len(lu_strikes) > 2 and price_change < 0,
                "strike_count": len(lu_strikes),
                "strikes": lu_strikes[:5],
                "total_pe_oi_dropped": int(lu_intensity),
                "signal": "BEARISH_PRESSURE" if len(lu_strikes) > 3 and lu_intensity > 50000 else "MILD" if lu_strikes else "NONE",
            },

            # Long Buildup details
            "long_buildup": {
                "active": len(lb_strikes) > 2 and price_change > 0,
                "strike_count": len(lb_strikes),
                "strikes": lb_strikes[:5],
            },

            # Short Buildup details
            "short_buildup": {
                "active": len(sb_strikes) > 2 and price_change < 0,
                "strike_count": len(sb_strikes),
                "strikes": sb_strikes[:5],
            },

            "pattern_summary": patterns,
            "total_ce_oi_change": int(total_ce_chg),
            "total_pe_oi_change": int(total_pe_chg),
        }

    def _classify_oi_price(self, chg_oi: float, price_change: float, opt_type: str) -> str:
        """
        Classify OI-Price action pattern for a single strike.
        
        For CE options:
          OI ↑ + Price ↑ → LONG_BUILDUP (buying calls — bullish)
          OI ↑ + Price ↓ → SHORT_BUILDUP (writing calls — bearish)
          OI ↓ + Price ↑ → SHORT_COVERING (call writers exiting — bullish)
          OI ↓ + Price ↓ → LONG_UNWINDING (call buyers exiting — bearish)
          
        For PE options:
          OI ↑ + Price ↓ → LONG_BUILDUP (buying puts — bearish)
          OI ↑ + Price ↑ → SHORT_BUILDUP (writing puts — bullish)
          OI ↓ + Price ↓ → SHORT_COVERING (put writers exiting — bearish)
          OI ↓ + Price ↑ → LONG_UNWINDING (put buyers exiting — bullish)
        """
        if opt_type == "CE":
            if chg_oi > 0 and price_change > 0:
                return "LONG_BUILDUP"
            elif chg_oi > 0 and price_change <= 0:
                return "SHORT_BUILDUP"
            elif chg_oi < 0 and price_change > 0:
                return "SHORT_COVERING"
            elif chg_oi < 0 and price_change <= 0:
                return "LONG_UNWINDING"
        else:  # PE
            if chg_oi > 0 and price_change < 0:
                return "LONG_BUILDUP"
            elif chg_oi > 0 and price_change >= 0:
                return "SHORT_BUILDUP"
            elif chg_oi < 0 and price_change < 0:
                return "SHORT_COVERING"
            elif chg_oi < 0 and price_change >= 0:
                return "LONG_UNWINDING"
        return "NEUTRAL"

    def _max_pain(self, df: pd.DataFrame) -> float:
        strikes = sorted(df["strike"].unique())
        min_pain = float("inf")
        mp_strike = strikes[len(strikes) // 2] if strikes else 0

        for s in strikes:
            ce_pain = sum(max(0, s - r["strike"]) * r["ce_oi"] for _, r in df.iterrows())
            pe_pain = sum(max(0, r["strike"] - s) * r["pe_oi"] for _, r in df.iterrows())
            total = ce_pain + pe_pain
            if total < min_pain:
                min_pain = total
                mp_strike = s

        return float(mp_strike)

    def _find_atm(self, df: pd.DataFrame, price: float) -> float:
        strikes = df["strike"].unique()
        return float(min(strikes, key=lambda s: abs(s - price)))
