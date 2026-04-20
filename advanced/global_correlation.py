"""
Global Market Correlation — Track international indices for gap prediction.
Monitors SGX Nifty, S&P 500, Dow, Nikkei, Hang Seng before Indian market open.
Correlates global moves with expected NIFTY direction.
"""

import logging
from datetime import datetime
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False


class GlobalCorrelationEngine:
    """
    Track global market indices and their correlation with Indian markets.
    
    Key relationships:
    - SGX Nifty → Direct NIFTY proxy (trades pre-market)
    - S&P 500 / Dow → Overnight sentiment driver
    - US VIX → Global risk appetite
    - DXY (Dollar Index) → FII flow indicator
    - Crude Oil → Macro impact on India
    - Asian markets (Nikkei, Hang Seng) → Regional sentiment
    """

    # Free API endpoints for global market data
    YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=5m"

    GLOBAL_SYMBOLS = {
        "sgx_nifty": {"symbol": "SGX_NIFTY", "yahoo": None, "weight": 0.30,
                      "description": "SGX Nifty Futures"},
        "sp500": {"symbol": "^GSPC", "yahoo": "^GSPC", "weight": 0.20,
                  "description": "S&P 500"},
        "dow": {"symbol": "^DJI", "yahoo": "^DJI", "weight": 0.10,
                "description": "Dow Jones"},
        "nasdaq": {"symbol": "^IXIC", "yahoo": "^IXIC", "weight": 0.10,
                   "description": "Nasdaq Composite"},
        "nikkei": {"symbol": "^N225", "yahoo": "^N225", "weight": 0.08,
                   "description": "Nikkei 225"},
        "hangseng": {"symbol": "^HSI", "yahoo": "^HSI", "weight": 0.07,
                     "description": "Hang Seng"},
        "us_vix": {"symbol": "^VIX", "yahoo": "^VIX", "weight": 0.10,
                   "description": "CBOE VIX"},
        "crude": {"symbol": "CL=F", "yahoo": "CL=F", "weight": 0.05,
                  "description": "Crude Oil WTI"},
    }

    def __init__(self):
        self.latest_data: dict = {}
        self.history: list[dict] = []
        self.correlation_scores: dict[str, float] = {}

    def fetch_global_data(self) -> dict:
        """Fetch current global market data from free APIs."""
        if not REQUESTS_OK:
            return self._mock_global_data()

        data = {}
        for key, info in self.GLOBAL_SYMBOLS.items():
            yahoo = info.get("yahoo")
            if not yahoo:
                # Use mock for unavailable symbols
                data[key] = self._mock_single(key)
                continue

            try:
                url = self.YAHOO_QUOTE_URL.format(symbol=yahoo)
                headers = {"User-Agent": "Mozilla/5.0"}
                resp = requests.get(url, headers=headers, timeout=5)

                if resp.status_code == 200:
                    chart = resp.json().get("chart", {}).get("result", [{}])[0]
                    meta = chart.get("meta", {})
                    price = meta.get("regularMarketPrice", 0)
                    prev_close = meta.get("previousClose", price)
                    change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0

                    data[key] = {
                        "symbol": info["symbol"],
                        "price": round(price, 2),
                        "prev_close": round(prev_close, 2),
                        "change_pct": round(change_pct, 2),
                        "status": "LIVE",
                    }
                else:
                    data[key] = self._mock_single(key)
            except Exception as e:
                logger.debug("Global data fetch failed for %s: %s", key, e)
                data[key] = self._mock_single(key)

        self.latest_data = data
        return data

    def analyze(self) -> dict:
        """Analyze global data and predict NIFTY direction."""
        if not self.latest_data:
            self.fetch_global_data()

        if not self.latest_data:
            return {"global_score": 0, "global_direction": "NEUTRAL", "data": {}}

        # Weighted global sentiment score
        weighted_score = 0
        total_weight = 0

        for key, info in self.GLOBAL_SYMBOLS.items():
            mkt = self.latest_data.get(key, {})
            change = mkt.get("change_pct", 0)
            weight = info["weight"]

            # VIX is inverted — VIX up = bearish for equities
            if key == "us_vix":
                contribution = -change * 0.1  # Scale down VIX impact
            elif key == "crude":
                # High crude is mildly bearish for India (import dependent)
                contribution = -change * 0.05
            else:
                # Positive global indices = bullish for India
                contribution = change * 0.1

            weighted_score += contribution * weight
            total_weight += weight

        if total_weight > 0:
            weighted_score /= total_weight

        # Clamp to [-1, 1]
        global_score = max(-1, min(1, weighted_score))

        # Direction
        if global_score > 0.15:
            direction = "BULLISH"
        elif global_score < -0.15:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"

        # Gap prediction (pre-market)
        now = datetime.now()
        is_premarket = now.hour < 9 or (now.hour == 9 and now.minute < 15)

        # Key signals
        signals = []
        sp = self.latest_data.get("sp500", {}).get("change_pct", 0)
        if abs(sp) > 1.0:
            signals.append(f"S&P500 {'up' if sp > 0 else 'down'} {abs(sp):.1f}% — significant move")
        
        vix = self.latest_data.get("us_vix", {}).get("change_pct", 0)
        if abs(vix) > 5:
            signals.append(f"VIX {'spike' if vix > 0 else 'drop'} {abs(vix):.1f}% — volatility alert")

        crude = self.latest_data.get("crude", {}).get("change_pct", 0)
        if abs(crude) > 2:
            signals.append(f"Crude {'up' if crude > 0 else 'down'} {abs(crude):.1f}%")

        result = {
            "global_score": round(global_score, 4),
            "global_direction": direction,
            "is_premarket": is_premarket,
            "gap_prediction": direction if is_premarket else "N/A",
            "signals": signals,
            "data": self.latest_data,
            "timestamp": datetime.now().isoformat(),
        }

        self.history.append(result)
        if len(self.history) > 200:
            self.history = self.history[-200:]

        return result

    def get_premarket_bias(self) -> dict:
        """Get pre-market direction bias based on overnight global moves."""
        result = self.analyze()
        return {
            "bias": result["global_direction"],
            "score": result["global_score"],
            "key_signals": result.get("signals", []),
        }

    def _mock_single(self, key: str) -> dict:
        change = np.random.normal(0, 0.5)
        return {
            "symbol": self.GLOBAL_SYMBOLS[key]["symbol"],
            "price": 0,
            "prev_close": 0,
            "change_pct": round(change, 2),
            "status": "MOCK",
        }

    def _mock_global_data(self) -> dict:
        return {k: self._mock_single(k) for k in self.GLOBAL_SYMBOLS}
