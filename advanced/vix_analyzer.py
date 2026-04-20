"""
VIX Analysis — India VIX tracking, term structure, and vol regime.
Uses India VIX to determine optimal strategy selection.
"""

import logging
from datetime import datetime
from collections import deque

import numpy as np

import config

logger = logging.getLogger(__name__)

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False


class VIXAnalyzer:
    """
    India VIX analysis for volatility regime detection.
    
    VIX Levels Guide:
    - <12:  Extreme complacency → sell strangles, iron condors
    - 12-15: Low vol → premium selling strategies
    - 15-20: Normal → directional + neutral strategies
    - 20-25: Elevated → widen stops, reduce position size
    - 25-35: High fear → long straddles, protect portfolio
    - >35:  Panic → only hedging, no new positions
    """

    # VIX regime boundaries
    REGIME_THRESHOLDS = {
        "ULTRA_LOW": 12,
        "LOW": 15,
        "NORMAL": 20,
        "ELEVATED": 25,
        "HIGH": 35,
        "PANIC": 100,
    }

    def __init__(self):
        self.current_vix = 0
        self.vix_history: deque = deque(maxlen=1000)
        self.regime_history: deque = deque(maxlen=200)

    def update(self, vix_value: float = 0):
        """Update current VIX value."""
        if vix_value > 0:
            self.current_vix = vix_value
        else:
            self.current_vix = self._fetch_vix()

        self.vix_history.append({
            "timestamp": datetime.now().isoformat(),
            "vix": self.current_vix,
        })

    def analyze(self) -> dict:
        """Full VIX analysis."""
        vix = self.current_vix if self.current_vix > 0 else self._fetch_vix()

        # Regime classification
        regime = self._classify_regime(vix)

        # VIX percentile (where is current VIX vs history)
        hist_values = [h["vix"] for h in self.vix_history if h["vix"] > 0]
        if len(hist_values) >= 10:
            vix_percentile = sum(1 for v in hist_values if v < vix) / len(hist_values) * 100
            vix_mean = np.mean(hist_values)
            vix_std = np.std(hist_values)
            z_score = (vix - vix_mean) / max(vix_std, 0.1)
        else:
            vix_percentile = 50
            vix_mean = vix
            vix_std = 0
            z_score = 0

        # VIX trend (rising/falling)
        if len(hist_values) >= 5:
            recent = hist_values[-5:]
            older = hist_values[-10:-5] if len(hist_values) >= 10 else recent
            vix_trend = "RISING" if np.mean(recent) > np.mean(older) * 1.05 else \
                        "FALLING" if np.mean(recent) < np.mean(older) * 0.95 else "STABLE"
        else:
            vix_trend = "STABLE"

        # Strategy recommendations based on VIX regime
        strategy_rec = self._strategy_recommendation(regime, vix_trend)

        # Position sizing adjustment
        size_multiplier = self._position_size_multiplier(regime)

        # Stop-loss adjustment
        sl_multiplier = self._stoploss_multiplier(regime)

        result = {
            "vix": round(vix, 2),
            "regime": regime,
            "vix_percentile": round(vix_percentile, 1),
            "vix_mean": round(vix_mean, 2),
            "vix_z_score": round(z_score, 2),
            "vix_trend": vix_trend,
            "strategy_recommendation": strategy_rec,
            "position_size_multiplier": size_multiplier,
            "stoploss_multiplier": sl_multiplier,
            "timestamp": datetime.now().isoformat(),
        }

        self.regime_history.append({
            "timestamp": result["timestamp"],
            "regime": regime,
            "vix": vix,
        })

        return result

    def _classify_regime(self, vix: float) -> str:
        if vix < self.REGIME_THRESHOLDS["ULTRA_LOW"]:
            return "ULTRA_LOW_VOL"
        elif vix < self.REGIME_THRESHOLDS["LOW"]:
            return "LOW_VOL"
        elif vix < self.REGIME_THRESHOLDS["NORMAL"]:
            return "NORMAL_VOL"
        elif vix < self.REGIME_THRESHOLDS["ELEVATED"]:
            return "ELEVATED_VOL"
        elif vix < self.REGIME_THRESHOLDS["HIGH"]:
            return "HIGH_VOL"
        else:
            return "PANIC"

    def _strategy_recommendation(self, regime: str, trend: str) -> dict:
        """Recommend strategies based on VIX regime."""
        recs = {
            "ULTRA_LOW_VOL": {
                "primary": ["SHORT_STRANGLE", "IRON_CONDOR"],
                "avoid": ["LONG_STRADDLE", "LONG_STRANGLE"],
                "note": "Premium is cheap — sell it. But watch for vol expansion.",
            },
            "LOW_VOL": {
                "primary": ["IRON_CONDOR", "SHORT_STRANGLE", "VERTICAL_SPREAD"],
                "avoid": ["LONG_STRADDLE"],
                "note": "Good environment for premium selling.",
            },
            "NORMAL_VOL": {
                "primary": ["VERTICAL_SPREAD", "IRON_CONDOR", "CALENDAR_SPREAD"],
                "avoid": [],
                "note": "All strategies viable. Use signal direction.",
            },
            "ELEVATED_VOL": {
                "primary": ["VERTICAL_SPREAD", "LONG_STRADDLE"],
                "avoid": ["NAKED_OPTION", "SHORT_STRANGLE"],
                "note": "Reduce position size. Wider stops needed.",
            },
            "HIGH_VOL": {
                "primary": ["LONG_STRADDLE", "LONG_STRANGLE"],
                "avoid": ["SHORT_STRANGLE", "NAKED_OPTION", "IRON_CONDOR"],
                "note": "Buy premium. Big moves expected.",
            },
            "PANIC": {
                "primary": ["HEDGING_ONLY"],
                "avoid": ["ALL_NEW_POSITIONS"],
                "note": "No new trades. Hedge existing positions only.",
            },
        }
        rec = recs.get(regime, recs["NORMAL_VOL"])

        # Adjust for trend
        if trend == "RISING" and regime not in ("PANIC", "HIGH_VOL"):
            rec["note"] += " VIX rising — consider adding long premium hedges."
        elif trend == "FALLING" and regime in ("HIGH_VOL", "ELEVATED_VOL"):
            rec["note"] += " VIX falling — vol crush imminent, close long premium."

        return rec

    def _position_size_multiplier(self, regime: str) -> float:
        """Reduce position size in high vol."""
        multipliers = {
            "ULTRA_LOW_VOL": 1.0,
            "LOW_VOL": 1.0,
            "NORMAL_VOL": 1.0,
            "ELEVATED_VOL": 0.7,
            "HIGH_VOL": 0.5,
            "PANIC": 0.0,  # No new positions in panic
        }
        return multipliers.get(regime, 1.0)

    def _stoploss_multiplier(self, regime: str) -> float:
        """Wider stops in high vol (options move more)."""
        multipliers = {
            "ULTRA_LOW_VOL": 0.8,
            "LOW_VOL": 0.9,
            "NORMAL_VOL": 1.0,
            "ELEVATED_VOL": 1.3,
            "HIGH_VOL": 1.6,
            "PANIC": 2.0,
        }
        return multipliers.get(regime, 1.0)

    def _fetch_vix(self) -> float:
        """Fetch India VIX from NSE or use mock."""
        if not REQUESTS_OK:
            return self._mock_vix()

        try:
            url = "https://www.nseindia.com/api/allIndices"
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            }
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                for idx in data.get("data", []):
                    if idx.get("index") == "INDIA VIX":
                        return float(idx.get("last", 15))
        except Exception:
            pass

        return self._mock_vix()

    def _mock_vix(self) -> float:
        """Generate realistic mock VIX."""
        base = 14.5
        if self.vix_history:
            base = list(self.vix_history)[-1]["vix"]
        noise = np.random.normal(0, 0.3)
        mean_revert = (15 - base) * 0.05  # Mean revert to 15
        return round(max(8, base + noise + mean_revert), 2)
