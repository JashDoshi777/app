"""
Market Depth / Order Flow Analysis — Leading price indicator.
Analyzes bid-ask spread, order book imbalance, and depth data
to detect buying/selling pressure before price moves.
"""

import logging
from datetime import datetime
from typing import Optional
from collections import deque

import numpy as np

import config

logger = logging.getLogger(__name__)


class OrderFlowAnalyzer:
    """
    Order flow analysis using market depth data.
    
    While price-based indicators are lagging, order flow is LEADING.
    It shows you where the demand/supply actually is.
    
    Key metrics:
    1. Bid-Ask Spread → Wider = uncertainty/low liquidity
    2. Order Book Imbalance → More buyers vs sellers at each level
    3. Aggressive Orders → Market orders hitting the ask = buying pressure
    4. Absorption → Price doesn't move despite heavy volume = accumulation/distribution
    5. Delta → Buy volume - Sell volume (positive = buying pressure)
    """

    SPREAD_NORMAL_BPS = 5   # Normal spread in basis points
    IMBALANCE_THRESHOLD = 0.65  # 65%+ one-sided = strong imbalance

    def __init__(self):
        self.tick_history: deque = deque(maxlen=1000)
        self.delta_history: deque = deque(maxlen=500)
        self.imbalance_history: deque = deque(maxlen=500)

    def analyze_depth(self, depth_data: dict, current_price: float = 0) -> dict:
        """
        Analyze market depth (Level 2 data).
        
        depth_data format (standard Level 2):
        {
            "buy": [{"price": x, "quantity": y}, ...],  # 5 levels
            "sell": [{"price": x, "quantity": y}, ...],  # 5 levels
        }
        """
        bids = depth_data.get("buy", [])
        asks = depth_data.get("sell", [])

        if not bids or not asks:
            return self._mock_analysis(current_price)

        # ── 1. Bid-Ask Spread ─────────────────────────────
        best_bid = bids[0]["price"] if bids else current_price
        best_ask = asks[0]["price"] if asks else current_price
        spread = best_ask - best_bid
        mid_price = (best_bid + best_ask) / 2
        spread_bps = (spread / mid_price * 10000) if mid_price > 0 else 0

        spread_signal = "TIGHT" if spread_bps < 3 else "NORMAL" if spread_bps < 8 else "WIDE"

        # ── 2. Order Book Imbalance ───────────────────────
        total_bid_qty = sum(b.get("quantity", 0) for b in bids)
        total_ask_qty = sum(a.get("quantity", 0) for a in asks)
        total_qty = total_bid_qty + total_ask_qty

        if total_qty > 0:
            bid_pct = total_bid_qty / total_qty
            ask_pct = total_ask_qty / total_qty
        else:
            bid_pct = ask_pct = 0.5

        # Imbalance ratio
        if total_ask_qty > 0:
            imbalance_ratio = total_bid_qty / total_ask_qty
        else:
            imbalance_ratio = 1.0

        if bid_pct > self.IMBALANCE_THRESHOLD:
            imbalance_signal = "BUY_PRESSURE"
        elif ask_pct > self.IMBALANCE_THRESHOLD:
            imbalance_signal = "SELL_PRESSURE"
        else:
            imbalance_signal = "BALANCED"

        self.imbalance_history.append({
            "timestamp": datetime.now().isoformat(),
            "bid_pct": bid_pct,
            "imbalance_ratio": imbalance_ratio,
        })

        # ── 3. Depth Pressure Analysis ────────────────────
        # Weighted by distance from best price
        weighted_bid_pressure = sum(
            b.get("quantity", 0) * (1 / max(1, i + 1))
            for i, b in enumerate(bids)
        )
        weighted_ask_pressure = sum(
            a.get("quantity", 0) * (1 / max(1, i + 1))
            for i, a in enumerate(asks)
        )
        total_pressure = weighted_bid_pressure + weighted_ask_pressure
        pressure_ratio = (weighted_bid_pressure / total_pressure) if total_pressure > 0 else 0.5

        # ── 4. Wall Detection ─────────────────────────────
        # A "wall" is a very large order at a specific price level
        avg_bid_qty = total_bid_qty / max(len(bids), 1)
        avg_ask_qty = total_ask_qty / max(len(asks), 1)

        bid_walls = [b for b in bids if b.get("quantity", 0) > avg_bid_qty * 3]
        ask_walls = [a for a in asks if a.get("quantity", 0) > avg_ask_qty * 3]

        # ── 5. Absorption Detection ───────────────────────
        # If price isn't moving despite high volume, large players are absorbing
        absorption = "NONE"
        if len(self.imbalance_history) >= 5:
            recent_imb = list(self.imbalance_history)[-5:]
            avg_imb = np.mean([r["imbalance_ratio"] for r in recent_imb])
            if avg_imb > 1.5 and spread_signal == "TIGHT":
                absorption = "BUY_ABSORPTION"
            elif avg_imb < 0.67 and spread_signal == "TIGHT":
                absorption = "SELL_ABSORPTION"

        # ── Composite Score ───────────────────────────────
        score = 0
        if imbalance_signal == "BUY_PRESSURE":
            score += 0.4
        elif imbalance_signal == "SELL_PRESSURE":
            score -= 0.4

        score += (pressure_ratio - 0.5) * 1.2  # Scale to [-0.6, 0.6]

        if absorption == "BUY_ABSORPTION":
            score += 0.3
        elif absorption == "SELL_ABSORPTION":
            score -= 0.3

        if bid_walls:
            score += 0.2  # Support from bid walls
        if ask_walls:
            score -= 0.2  # Resistance from ask walls

        score = max(-1, min(1, score))

        result = {
            "spread": round(spread, 2),
            "spread_bps": round(spread_bps, 1),
            "spread_signal": spread_signal,
            "bid_qty": total_bid_qty,
            "ask_qty": total_ask_qty,
            "bid_pct": round(bid_pct * 100, 1),
            "ask_pct": round(ask_pct * 100, 1),
            "imbalance_ratio": round(imbalance_ratio, 2),
            "imbalance_signal": imbalance_signal,
            "pressure_ratio": round(pressure_ratio, 3),
            "bid_walls": len(bid_walls),
            "ask_walls": len(ask_walls),
            "absorption": absorption,
            "order_flow_score": round(score, 4),
            "order_flow_direction": "BUY" if score > 0.15 else "SELL" if score < -0.15 else "NEUTRAL",
            "timestamp": datetime.now().isoformat(),
        }

        return result

    def analyze_tick_delta(self, trades: list[dict]) -> dict:
        """
        Analyze trade-by-trade delta (buy vol - sell vol).
        
        Each trade: {"price": x, "qty": y, "side": "BUY"/"SELL"}
        """
        if not trades:
            return {"cumulative_delta": 0, "delta_signal": "NEUTRAL"}

        buy_vol = sum(t["qty"] for t in trades if t.get("side") == "BUY")
        sell_vol = sum(t["qty"] for t in trades if t.get("side") == "SELL")
        delta = buy_vol - sell_vol

        self.delta_history.append({
            "timestamp": datetime.now().isoformat(),
            "delta": delta,
            "buy_vol": buy_vol,
            "sell_vol": sell_vol,
        })

        # Trend of delta
        if len(self.delta_history) >= 5:
            recent_deltas = [d["delta"] for d in list(self.delta_history)[-5:]]
            delta_trend = "RISING" if recent_deltas[-1] > np.mean(recent_deltas[:-1]) else "FALLING"
        else:
            delta_trend = "STABLE"

        return {
            "cumulative_delta": delta,
            "buy_volume": buy_vol,
            "sell_volume": sell_vol,
            "delta_ratio": round(buy_vol / max(sell_vol, 1), 2),
            "delta_signal": "BUY" if delta > 0 else "SELL" if delta < 0 else "NEUTRAL",
            "delta_trend": delta_trend,
        }

    def _mock_analysis(self, price: float) -> dict:
        """Generate mock depth analysis when real data isn't available."""
        imbalance = np.random.normal(0, 0.15)
        score = max(-1, min(1, imbalance))

        return {
            "spread": round(abs(np.random.normal(0.5, 0.2)), 2),
            "spread_bps": round(abs(np.random.normal(3, 1.5)), 1),
            "spread_signal": "NORMAL",
            "bid_qty": int(np.random.uniform(50000, 200000)),
            "ask_qty": int(np.random.uniform(50000, 200000)),
            "bid_pct": round(50 + imbalance * 50, 1),
            "ask_pct": round(50 - imbalance * 50, 1),
            "imbalance_ratio": round(1 + imbalance, 2),
            "imbalance_signal": "BUY_PRESSURE" if imbalance > 0.15 else "SELL_PRESSURE" if imbalance < -0.15 else "BALANCED",
            "pressure_ratio": round(0.5 + imbalance * 0.3, 3),
            "bid_walls": 0,
            "ask_walls": 0,
            "absorption": "NONE",
            "order_flow_score": round(score, 4),
            "order_flow_direction": "BUY" if score > 0.15 else "SELL" if score < -0.15 else "NEUTRAL",
            "timestamp": datetime.now().isoformat(),
            "status": "MOCK",
        }
