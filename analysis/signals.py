"""
Signal Aggregator — Multi-layer confluence scoring engine.
Combines technical, Greeks, OI, sentiment, and market regime
into a single actionable signal with confidence score.
"""

import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

import config

logger = logging.getLogger(__name__)


@dataclass
class AggregatedSignal:
    """The final signal output after all layers are combined."""
    timestamp: str = ""
    symbol: str = ""
    direction: str = "NEUTRAL"  # STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL
    score: float = 0.0  # -1 to +1

    # Layer scores (-1 to +1 each)
    technical_score: float = 0.0
    greeks_score: float = 0.0
    oi_score: float = 0.0
    sentiment_score: float = 0.0
    regime_score: float = 0.0

    # Layer details
    technical_detail: dict = field(default_factory=dict)
    greeks_detail: dict = field(default_factory=dict)
    oi_detail: dict = field(default_factory=dict)
    sentiment_detail: dict = field(default_factory=dict)
    regime: str = "UNKNOWN"

    # Actionability
    is_actionable: bool = False
    suggested_strategy: str = ""
    confidence: float = 0.0


class SignalAggregator:
    """Combine all analysis layers into one signal."""

    def __init__(self):
        self.history: list[AggregatedSignal] = []

    def generate(
        self,
        symbol: str,
        technical: dict = None,
        greeks_data: dict = None,
        oi_data: dict = None,
        sentiment_data: dict = None,
        iv_data: dict = None,
        adaptive_weights: dict = None,
    ) -> AggregatedSignal:
        """Generate a confluence signal from all analysis layers."""

        sig = AggregatedSignal(
            timestamp=datetime.now().isoformat(),
            symbol=symbol,
        )

        # ── Layer 1: Technical ────────────────────────────
        if technical:
            sig.technical_score = self._score_technical(technical)
            sig.technical_detail = technical

        # ── Layer 2: Greeks / IV ──────────────────────────
        if greeks_data or iv_data:
            sig.greeks_score = self._score_greeks(greeks_data or {}, iv_data or {})
            sig.greeks_detail = {**(greeks_data or {}), **(iv_data or {})}

        # ── Layer 3: Open Interest ────────────────────────
        if oi_data:
            sig.oi_score = self._score_oi(oi_data)
            sig.oi_detail = oi_data

        # ── Layer 4: Sentiment ────────────────────────────
        if sentiment_data:
            sig.sentiment_score = self._score_sentiment(sentiment_data)
            sig.sentiment_detail = sentiment_data

        # ── Layer 5: Market Regime ────────────────────────
        sig.regime = self._detect_regime(technical, oi_data, iv_data)
        sig.regime_score = self._score_regime(sig.regime)

        # ── Weighted Confluence Score ─────────────────────
        # Use adaptive weights from the self-learning optimizer if available,
        # otherwise fall back to static config weights.
        w = adaptive_weights if adaptive_weights else config.SIGNAL_WEIGHTS
        sig.score = round(
            sig.technical_score * w.get("technical", 0.25)
            + sig.greeks_score * w.get("greeks", 0.20)
            + sig.oi_score * w.get("oi", 0.25)
            + sig.sentiment_score * w.get("sentiment", 0.15)
            + sig.regime_score * w.get("regime", 0.15),
            4,
        )

        # ── Direction Classification ─────────────────────
        if sig.score >= 0.5:
            sig.direction = "STRONG_BUY"
        elif sig.score >= 0.2:
            sig.direction = "BUY"
        elif sig.score <= -0.5:
            sig.direction = "STRONG_SELL"
        elif sig.score <= -0.2:
            sig.direction = "SELL"
        else:
            sig.direction = "NEUTRAL"

        # ── Actionability ─────────────────────────────────
        sig.is_actionable = abs(sig.score) >= config.MIN_ENTRY_SCORE
        sig.confidence = round(min(100, abs(sig.score) * 150), 1)
        sig.suggested_strategy = self._suggest_strategy(sig)

        self.history.append(sig)
        if len(self.history) > 500:
            self.history = self.history[-500:]

        return sig

    # ═══════════════════════════════════════════════════════
    #  LAYER SCORING FUNCTIONS
    # ═══════════════════════════════════════════════════════

    def _score_technical(self, t: dict) -> float:
        """Score technical indicators: -1 (bearish) to +1 (bullish)."""
        score = 0.0
        count = 0

        # RSI
        rsi = t.get("rsi", 50)
        if rsi < 30:
            score += 0.8   # Oversold = bullish
        elif rsi > 70:
            score -= 0.8   # Overbought = bearish
        elif rsi < 45:
            score += 0.3
        elif rsi > 55:
            score -= 0.3
        count += 1

        # MACD
        macd_h = t.get("macd_histogram", 0)
        if macd_h > 0:
            score += min(0.6, macd_h / 10)
        else:
            score -= min(0.6, abs(macd_h) / 10)
        count += 1

        # Supertrend
        if t.get("supertrend_dir", 0) == 1:
            score += 0.5
        else:
            score -= 0.5
        count += 1

        # EMA alignment
        close = t.get("close", 0)
        ema9 = t.get("ema_9", close)
        ema21 = t.get("ema_21", close)
        if close > ema9 > ema21:
            score += 0.6
        elif close < ema9 < ema21:
            score -= 0.6
        count += 1

        # Bollinger position
        bb_upper = t.get("bb_upper", close + 100)
        bb_lower = t.get("bb_lower", close - 100)
        bb_range = bb_upper - bb_lower
        if bb_range > 0:
            pos = (close - bb_lower) / bb_range
            if pos < 0.2:
                score += 0.5  # Near lower band = bullish
            elif pos > 0.8:
                score -= 0.5  # Near upper band = bearish
        count += 1

        # Candlestick patterns
        patterns = t.get("patterns", {})
        bullish_patterns = ["hammer", "bullish_engulfing", "morning_star",
                           "piercing_line", "three_white_soldiers", "tweezer_bottom",
                           "bullish_harami", "three_inside_up"]
        bearish_patterns = ["shooting_star", "bearish_engulfing", "evening_star",
                           "dark_cloud_cover", "three_black_crows", "tweezer_top",
                           "bearish_harami", "three_inside_down", "hanging_man"]

        for p in bullish_patterns:
            if p in patterns:
                score += 0.4
                count += 1
        for p in bearish_patterns:
            if p in patterns:
                score -= 0.4
                count += 1

        return max(-1, min(1, score / max(count, 1) * 2))

    def _score_greeks(self, greeks: dict, iv: dict) -> float:
        """Score Greeks/IV conditions."""
        score = 0.0

        iv_signal = iv.get("iv_signal", "NORMAL_IV")
        if iv_signal == "HIGH_IV":
            score -= 0.3  # High IV = potential sell signal
        elif iv_signal == "LOW_IV":
            score += 0.3

        skew = iv.get("iv_skew", {})
        skew_val = skew.get("skew", 0) if isinstance(skew, dict) else 0
        if skew_val > 5:
            score -= 0.3  # High put skew = fear
        elif skew_val < -3:
            score += 0.3

        return max(-1, min(1, score))

    def _score_oi(self, oi: dict) -> float:
        """Score OI-based signals."""
        signal = oi.get("oi_signal", "NEUTRAL")
        pcr_signal = oi.get("pcr_signal", "NEUTRAL")

        score = 0.0
        if signal == "BULLISH":
            score += 0.5
        elif signal == "BEARISH":
            score -= 0.5

        if pcr_signal == "BULLISH":
            score += 0.3
        elif pcr_signal == "BEARISH":
            score -= 0.3

        oi_score_val = oi.get("oi_signal_score", 0)
        score += oi_score_val * 0.2

        return max(-1, min(1, score))

    def _score_sentiment(self, sent: dict) -> float:
        """Score sentiment data."""
        agg = sent.get("aggregate_score", 0)
        return max(-1, min(1, agg * 2))

    def _detect_regime(self, tech: dict = None, oi: dict = None,
                       iv: dict = None) -> str:
        """Detect current market regime."""
        if not tech:
            return "UNKNOWN"

        rsi = tech.get("rsi", 50)
        atr = tech.get("atr", 0)
        st_dir = tech.get("supertrend_dir", 0)
        bb_upper = tech.get("bb_upper", 0)
        bb_lower = tech.get("bb_lower", 0)
        close = tech.get("close", 0)

        bb_width = (bb_upper - bb_lower) / close * 100 if close > 0 else 0

        if bb_width > 4 and atr > 100:
            return "HIGH_VOLATILITY"
        elif bb_width < 1.5:
            return "LOW_VOLATILITY"

        if st_dir == 1 and rsi > 55:
            return "TRENDING_UP"
        elif st_dir == -1 and rsi < 45:
            return "TRENDING_DOWN"

        return "SIDEWAYS"

    def _score_regime(self, regime: str) -> float:
        """Score based on regime (slight bias toward trending)."""
        mapping = {
            "TRENDING_UP": 0.3,
            "TRENDING_DOWN": -0.3,
            "SIDEWAYS": 0.0,
            "HIGH_VOLATILITY": -0.1,
            "LOW_VOLATILITY": 0.1,
        }
        return mapping.get(regime, 0)

    def _suggest_strategy(self, sig: AggregatedSignal) -> str:
        """Suggest the best strategy based on signal + regime."""
        regime = sig.regime
        direction = sig.direction

        if regime == "SIDEWAYS":
            if abs(sig.score) < 0.3:
                return "IRON_CONDOR"
            return "SHORT_STRANGLE"

        if regime == "HIGH_VOLATILITY":
            return "LONG_STRADDLE"

        if direction in ("STRONG_BUY", "BUY"):
            if regime == "TRENDING_UP":
                return "BULL_CALL_SPREAD" if sig.confidence < 70 else "NAKED_CE"
            return "BULL_PUT_SPREAD"

        if direction in ("STRONG_SELL", "SELL"):
            if regime == "TRENDING_DOWN":
                return "BEAR_PUT_SPREAD" if sig.confidence < 70 else "NAKED_PE"
            return "BEAR_CALL_SPREAD"

        return "IRON_CONDOR"  # Default for neutral
