"""
Unusual Options Activity (UOA) Detection — Smart money tracker.
Detects large block trades, volume spikes, and unusual OI changes
that indicate institutional/informed trading activity.
"""

import logging
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)


class UOADetector:
    """
    Unusual Options Activity detector.
    
    Institutional traders can't hide — they leave footprints:
    1. Volume spikes: 3x+ normal volume at a strike
    2. OI jumps: Large sudden OI build at specific strikes
    3. Volume-to-OI ratio: High V/OI = new positions being opened
    4. Block trades: Large single orders that move the market
    5. Sweep detection: Aggressive buying across multiple exchanges
    """

    VOLUME_SPIKE_MULTIPLIER = 3.0    # 3x average = unusual
    OI_SPIKE_MULTIPLIER = 2.5        # 2.5x average = unusual
    VOI_RATIO_THRESHOLD = 0.5        # Volume > 50% of OI = very unusual
    MIN_VOLUME_THRESHOLD = 1000      # Ignore low-volume strikes

    def __init__(self):
        self.alerts: list[dict] = []
        self.historical_volume: dict[float, list[float]] = {}  # strike → volume history
        self.historical_oi: dict[float, list[float]] = {}

    def detect(self, chain_df: pd.DataFrame, underlying_price: float) -> dict:
        """
        Scan the option chain for unusual activity.
        Returns alerts and aggregate smart money signal.
        """
        if chain_df.empty:
            return {"alerts": [], "smart_money_signal": "NEUTRAL", "score": 0}

        new_alerts = []

        for _, row in chain_df.iterrows():
            strike = row["strike"]

            # ── CE side ───────────────────────────────────
            ce_vol = row.get("ce_volume", 0) or 0
            ce_oi = row.get("ce_oi", 0) or 0
            ce_chg_oi = row.get("ce_chg_oi", 0) or 0
            ce_ltp = row.get("ce_ltp", 0) or 0

            ce_alerts = self._check_strike(strike, "CE", ce_vol, ce_oi, ce_chg_oi,
                                           ce_ltp, underlying_price)
            new_alerts.extend(ce_alerts)

            # ── PE side ───────────────────────────────────
            pe_vol = row.get("pe_volume", 0) or 0
            pe_oi = row.get("pe_oi", 0) or 0
            pe_chg_oi = row.get("pe_chg_oi", 0) or 0
            pe_ltp = row.get("pe_ltp", 0) or 0

            pe_alerts = self._check_strike(strike, "PE", pe_vol, pe_oi, pe_chg_oi,
                                           pe_ltp, underlying_price)
            new_alerts.extend(pe_alerts)

            # Update historical data
            self._update_history(strike, ce_vol, pe_vol, ce_oi, pe_oi)

        # Sort by severity
        new_alerts.sort(key=lambda a: a["severity"], reverse=True)

        # Aggregate smart money signal
        signal = self._aggregate_signal(new_alerts, underlying_price)

        self.alerts = new_alerts
        return {
            "alerts": new_alerts[:20],  # Top 20 alerts
            "smart_money_signal": signal["direction"],
            "score": signal["score"],
            "bullish_flow": signal["bullish_flow"],
            "bearish_flow": signal["bearish_flow"],
            "total_unusual_strikes": len(new_alerts),
            "timestamp": datetime.now().isoformat(),
        }

    def _check_strike(self, strike: float, opt_type: str, volume: float,
                      oi: float, chg_oi: float, ltp: float,
                      underlying: float) -> list[dict]:
        """Check a single strike for unusual activity."""
        alerts = []

        if volume < self.MIN_VOLUME_THRESHOLD:
            return alerts

        # ── 1. Volume Spike ───────────────────────────────
        hist_vols = self.historical_volume.get(strike, [])
        avg_vol = np.mean(hist_vols) if hist_vols else volume
        if avg_vol > 0 and volume > avg_vol * self.VOLUME_SPIKE_MULTIPLIER:
            severity = min(10, volume / avg_vol)
            alerts.append({
                "strike": strike,
                "type": opt_type,
                "alert_type": "VOLUME_SPIKE",
                "volume": volume,
                "avg_volume": round(avg_vol),
                "multiplier": round(volume / avg_vol, 1),
                "severity": round(severity, 1),
                "message": f"{opt_type} {strike}: Volume {volume:,.0f} = {volume/avg_vol:.1f}x average",
            })

        # ── 2. Volume-to-OI Ratio ─────────────────────────
        if oi > 0:
            voi = volume / oi
            if voi > self.VOI_RATIO_THRESHOLD:
                alerts.append({
                    "strike": strike,
                    "type": opt_type,
                    "alert_type": "HIGH_VOI_RATIO",
                    "voi_ratio": round(voi, 2),
                    "volume": volume,
                    "oi": oi,
                    "severity": round(min(10, voi * 5), 1),
                    "message": f"{opt_type} {strike}: V/OI ratio = {voi:.2f} (new positions opening)",
                })

        # ── 3. OI Change Spike ─────────────────────────────
        hist_ois = self.historical_oi.get(strike, [])
        avg_oi_change = np.mean([abs(o) for o in hist_ois]) if hist_ois else abs(chg_oi)
        if avg_oi_change > 0 and abs(chg_oi) > avg_oi_change * self.OI_SPIKE_MULTIPLIER:
            direction = "BUILDUP" if chg_oi > 0 else "UNWINDING"
            alerts.append({
                "strike": strike,
                "type": opt_type,
                "alert_type": f"OI_{direction}",
                "oi_change": chg_oi,
                "avg_oi_change": round(avg_oi_change),
                "severity": round(min(10, abs(chg_oi) / avg_oi_change), 1),
                "message": f"{opt_type} {strike}: OI {direction} of {chg_oi:,.0f} ({abs(chg_oi)/avg_oi_change:.1f}x avg)",
            })

        # ── 4. Large premium trade ─────────────────────────
        notional = volume * ltp
        if notional > 500000:  # Rs.5L+ notional
            alerts.append({
                "strike": strike,
                "type": opt_type,
                "alert_type": "BLOCK_TRADE",
                "notional": round(notional),
                "severity": round(min(10, notional / 1000000 * 2), 1),
                "message": f"{opt_type} {strike}: Block trade Rs.{notional:,.0f} notional",
            })

        return alerts

    def _aggregate_signal(self, alerts: list[dict], underlying: float) -> dict:
        """Aggregate all alerts into a directional smart money signal."""
        bullish_flow = 0
        bearish_flow = 0

        for alert in alerts:
            sev = alert.get("severity", 1)
            strike = alert.get("strike", underlying)
            opt_type = alert.get("type", "CE")
            alert_type = alert.get("alert_type", "")

            # CE volume spike at strikes above underlying → usually bearish (selling calls)
            # PE volume spike at strikes below underlying → usually bullish (selling puts for support)
            # CE OI buildup above underlying → resistance being written → bearish
            # PE OI buildup below underlying → support being written → bullish
            # High V/OI on CE below underlying → aggressive buying → bullish
            # High V/OI on PE above underlying → aggressive buying → bearish

            if opt_type == "CE":
                if strike > underlying:
                    if "BUILDUP" in alert_type:
                        bearish_flow += sev  # Call writing at higher strikes = bearish
                    elif alert_type in ("VOLUME_SPIKE", "HIGH_VOI_RATIO"):
                        bearish_flow += sev * 0.7
                else:
                    if alert_type in ("VOLUME_SPIKE", "HIGH_VOI_RATIO"):
                        bullish_flow += sev  # ITM call buying = bullish
            elif opt_type == "PE":
                if strike < underlying:
                    if "BUILDUP" in alert_type:
                        bullish_flow += sev  # Put writing at lower strikes = bullish
                    elif alert_type in ("VOLUME_SPIKE", "HIGH_VOI_RATIO"):
                        bullish_flow += sev * 0.7
                else:
                    if alert_type in ("VOLUME_SPIKE", "HIGH_VOI_RATIO"):
                        bearish_flow += sev  # ITM put buying = bearish

        total = bullish_flow + bearish_flow
        if total > 0:
            score = (bullish_flow - bearish_flow) / total
        else:
            score = 0

        direction = "BULLISH" if score > 0.2 else "BEARISH" if score < -0.2 else "NEUTRAL"

        return {
            "direction": direction,
            "score": round(score, 4),
            "bullish_flow": round(bullish_flow, 1),
            "bearish_flow": round(bearish_flow, 1),
        }

    def _update_history(self, strike: float, ce_vol: float, pe_vol: float,
                        ce_oi: float, pe_oi: float):
        """Update historical data for a strike."""
        if strike not in self.historical_volume:
            self.historical_volume[strike] = []
        self.historical_volume[strike].append(ce_vol + pe_vol)
        if len(self.historical_volume[strike]) > 50:
            self.historical_volume[strike] = self.historical_volume[strike][-50:]

        if strike not in self.historical_oi:
            self.historical_oi[strike] = []
        self.historical_oi[strike].append(ce_oi + pe_oi)
        if len(self.historical_oi[strike]) > 50:
            self.historical_oi[strike] = self.historical_oi[strike][-50:]
