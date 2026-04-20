"""
Technical Analysis Engine — 40+ candlestick patterns + indicators.
Pure Python implementation (no TA-Lib dependency).
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class TechnicalAnalysis:
    """Full technical analysis suite for OHLCV data."""

    def __init__(self, df: pd.DataFrame = None):
        self.df = df  # Must have: open, high, low, close, volume

    def set_data(self, df: pd.DataFrame):
        self.df = df.copy()

    # ═══════════════════════════════════════════════════════
    #  CANDLESTICK PATTERNS (Pure Python)
    # ═══════════════════════════════════════════════════════

    def detect_patterns(self) -> dict[str, list[int]]:
        """Detect all candlestick patterns. Returns dict of pattern_name -> list of bar indices."""
        if self.df is None or len(self.df) < 5:
            return {}
        o, h, l, c = self.df["open"].values, self.df["high"].values, self.df["low"].values, self.df["close"].values
        body = c - o
        abs_body = np.abs(body)
        upper_shadow = h - np.maximum(o, c)
        lower_shadow = np.minimum(o, c) - l
        candle_range = h - l
        avg_body = pd.Series(abs_body).rolling(20, min_periods=1).mean().values

        patterns = {}

        # ── Single Candle Patterns ────────────────────────
        # Doji
        doji = abs_body < candle_range * 0.1
        patterns["doji"] = np.where(doji)[0].tolist()

        # Hammer (bullish reversal)
        hammer = (lower_shadow > abs_body * 2) & (upper_shadow < abs_body * 0.3) & (abs_body > 0)
        patterns["hammer"] = np.where(hammer)[0].tolist()

        # Inverted Hammer
        inv_hammer = (upper_shadow > abs_body * 2) & (lower_shadow < abs_body * 0.3) & (abs_body > 0)
        patterns["inverted_hammer"] = np.where(inv_hammer)[0].tolist()

        # Shooting Star (bearish)
        shooting = (upper_shadow > abs_body * 2) & (lower_shadow < abs_body * 0.3) & (body < 0)
        patterns["shooting_star"] = np.where(shooting)[0].tolist()

        # Hanging Man (bearish)
        hanging = (lower_shadow > abs_body * 2) & (upper_shadow < abs_body * 0.3) & (body < 0)
        patterns["hanging_man"] = np.where(hanging)[0].tolist()

        # Marubozu (strong candle, minimal shadows)
        marubozu = (upper_shadow < candle_range * 0.05) & (lower_shadow < candle_range * 0.05) & (abs_body > avg_body * 1.5)
        patterns["marubozu_bullish"] = np.where(marubozu & (body > 0))[0].tolist()
        patterns["marubozu_bearish"] = np.where(marubozu & (body < 0))[0].tolist()

        # Spinning Top
        spinning = (abs_body < candle_range * 0.3) & (upper_shadow > abs_body) & (lower_shadow > abs_body)
        patterns["spinning_top"] = np.where(spinning)[0].tolist()

        # ── Double Candle Patterns ────────────────────────
        for i in range(1, len(o)):
            # Bullish Engulfing
            if body[i - 1] < 0 and body[i] > 0 and o[i] <= c[i - 1] and c[i] >= o[i - 1]:
                patterns.setdefault("bullish_engulfing", []).append(i)

            # Bearish Engulfing
            if body[i - 1] > 0 and body[i] < 0 and o[i] >= c[i - 1] and c[i] <= o[i - 1]:
                patterns.setdefault("bearish_engulfing", []).append(i)

            # Bullish Harami
            if body[i - 1] < 0 and body[i] > 0 and abs_body[i] < abs_body[i - 1] * 0.6:
                if c[i] < o[i - 1] and o[i] > c[i - 1]:
                    patterns.setdefault("bullish_harami", []).append(i)

            # Bearish Harami
            if body[i - 1] > 0 and body[i] < 0 and abs_body[i] < abs_body[i - 1] * 0.6:
                if o[i] < c[i - 1] and c[i] > o[i - 1]:
                    patterns.setdefault("bearish_harami", []).append(i)

            # Piercing Line
            if body[i - 1] < 0 and body[i] > 0 and o[i] < l[i - 1] and c[i] > (o[i - 1] + c[i - 1]) / 2:
                patterns.setdefault("piercing_line", []).append(i)

            # Dark Cloud Cover
            if body[i - 1] > 0 and body[i] < 0 and o[i] > h[i - 1] and c[i] < (o[i - 1] + c[i - 1]) / 2:
                patterns.setdefault("dark_cloud_cover", []).append(i)

            # Tweezer Bottom
            if abs(l[i] - l[i - 1]) < candle_range[i] * 0.05 and body[i - 1] < 0 and body[i] > 0:
                patterns.setdefault("tweezer_bottom", []).append(i)

            # Tweezer Top
            if abs(h[i] - h[i - 1]) < candle_range[i] * 0.05 and body[i - 1] > 0 and body[i] < 0:
                patterns.setdefault("tweezer_top", []).append(i)

        # ── Triple Candle Patterns ────────────────────────
        for i in range(2, len(o)):
            # Morning Star (bullish)
            if (body[i - 2] < 0 and abs_body[i - 2] > avg_body[i - 2]
                    and abs_body[i - 1] < avg_body[i - 1] * 0.4
                    and body[i] > 0 and c[i] > (o[i - 2] + c[i - 2]) / 2):
                patterns.setdefault("morning_star", []).append(i)

            # Evening Star (bearish)
            if (body[i - 2] > 0 and abs_body[i - 2] > avg_body[i - 2]
                    and abs_body[i - 1] < avg_body[i - 1] * 0.4
                    and body[i] < 0 and c[i] < (o[i - 2] + c[i - 2]) / 2):
                patterns.setdefault("evening_star", []).append(i)

            # Three White Soldiers
            if body[i - 2] > 0 and body[i - 1] > 0 and body[i] > 0:
                if c[i] > c[i - 1] > c[i - 2] and o[i - 1] > o[i - 2] and o[i] > o[i - 1]:
                    patterns.setdefault("three_white_soldiers", []).append(i)

            # Three Black Crows
            if body[i - 2] < 0 and body[i - 1] < 0 and body[i] < 0:
                if c[i] < c[i - 1] < c[i - 2] and o[i - 1] < o[i - 2] and o[i] < o[i - 1]:
                    patterns.setdefault("three_black_crows", []).append(i)

            # Three Inside Up
            if body[i - 2] < 0 and body[i - 1] > 0 and abs_body[i - 1] < abs_body[i - 2] * 0.6:
                if body[i] > 0 and c[i] > o[i - 2]:
                    patterns.setdefault("three_inside_up", []).append(i)

            # Three Inside Down
            if body[i - 2] > 0 and body[i - 1] < 0 and abs_body[i - 1] < abs_body[i - 2] * 0.6:
                if body[i] < 0 and c[i] < o[i - 2]:
                    patterns.setdefault("three_inside_down", []).append(i)

        return patterns

    def latest_patterns(self, lookback: int = 5) -> dict:
        """Get patterns detected in the last N candles only."""
        all_p = self.detect_patterns()
        n = len(self.df)
        threshold = n - lookback
        result = {}
        for name, indices in all_p.items():
            recent = [i for i in indices if i >= threshold]
            if recent:
                result[name] = recent
        return result

    # ═══════════════════════════════════════════════════════
    #  TECHNICAL INDICATORS
    # ═══════════════════════════════════════════════════════

    def rsi(self, period: int = 14) -> pd.Series:
        delta = self.df["close"].diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    def macd(self, fast: int = 12, slow: int = 26, signal: int = 9):
        ema_fast = self.df["close"].ewm(span=fast).mean()
        ema_slow = self.df["close"].ewm(span=slow).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    def bollinger_bands(self, period: int = 20, std: float = 2):
        sma = self.df["close"].rolling(period).mean()
        rolling_std = self.df["close"].rolling(period).std()
        upper = sma + std * rolling_std
        lower = sma - std * rolling_std
        return upper, sma, lower

    def ema(self, period: int = 20) -> pd.Series:
        return self.df["close"].ewm(span=period).mean()

    def sma(self, period: int = 20) -> pd.Series:
        return self.df["close"].rolling(period).mean()

    def vwap(self) -> pd.Series:
        tp = (self.df["high"] + self.df["low"] + self.df["close"]) / 3
        cum_tp_vol = (tp * self.df["volume"]).cumsum()
        cum_vol = self.df["volume"].cumsum()
        return cum_tp_vol / cum_vol.replace(0, np.nan)

    def atr(self, period: int = 14) -> pd.Series:
        hl = self.df["high"] - self.df["low"]
        hc = abs(self.df["high"] - self.df["close"].shift())
        lc = abs(self.df["low"] - self.df["close"].shift())
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    def supertrend(self, period: int = 10, multiplier: float = 3):
        atr_val = self.atr(period)
        hl2 = (self.df["high"] + self.df["low"]) / 2
        upper = hl2 + multiplier * atr_val
        lower = hl2 - multiplier * atr_val

        st = pd.Series(0.0, index=self.df.index)
        direction = pd.Series(1, index=self.df.index)

        for i in range(1, len(self.df)):
            if self.df["close"].iloc[i] > upper.iloc[i - 1]:
                direction.iloc[i] = 1
            elif self.df["close"].iloc[i] < lower.iloc[i - 1]:
                direction.iloc[i] = -1
            else:
                direction.iloc[i] = direction.iloc[i - 1]

            st.iloc[i] = lower.iloc[i] if direction.iloc[i] == 1 else upper.iloc[i]

        return st, direction

    def stochastic_rsi(self, rsi_period: int = 14, stoch_period: int = 14,
                        k_smooth: int = 3, d_smooth: int = 3):
        rsi_val = self.rsi(rsi_period)
        stoch_rsi = (rsi_val - rsi_val.rolling(stoch_period).min()) / \
                    (rsi_val.rolling(stoch_period).max() - rsi_val.rolling(stoch_period).min()).replace(0, np.nan)
        k = stoch_rsi.rolling(k_smooth).mean() * 100
        d = k.rolling(d_smooth).mean()
        return k, d

    def obv(self) -> pd.Series:
        sign = np.sign(self.df["close"].diff())
        return (sign * self.df["volume"]).cumsum()

    def pivot_points(self) -> dict:
        h, l, c = self.df["high"].iloc[-1], self.df["low"].iloc[-1], self.df["close"].iloc[-1]
        pp = (h + l + c) / 3
        return {
            "pp": pp,
            "r1": 2 * pp - l, "s1": 2 * pp - h,
            "r2": pp + (h - l), "s2": pp - (h - l),
            "r3": h + 2 * (pp - l), "s3": l - 2 * (h - pp),
        }

    def fibonacci_levels(self, lookback: int = 50) -> dict:
        recent = self.df.tail(lookback)
        high = recent["high"].max()
        low = recent["low"].min()
        diff = high - low
        return {
            "0.0": high, "0.236": high - 0.236 * diff,
            "0.382": high - 0.382 * diff, "0.5": high - 0.5 * diff,
            "0.618": high - 0.618 * diff, "0.786": high - 0.786 * diff,
            "1.0": low,
        }

    def full_analysis(self) -> dict:
        """Run all indicators and return summary dict."""
        if self.df is None or len(self.df) < 20:
            return {}

        rsi_val = self.rsi().iloc[-1]
        macd_l, macd_s, macd_h = self.macd()
        bb_upper, bb_mid, bb_lower = self.bollinger_bands()
        st, st_dir = self.supertrend()
        k, d = self.stochastic_rsi()

        close = self.df["close"].iloc[-1]

        return {
            "close": close,
            "rsi": round(rsi_val, 2) if not np.isnan(rsi_val) else 50,
            "macd": round(macd_l.iloc[-1], 2),
            "macd_signal": round(macd_s.iloc[-1], 2),
            "macd_histogram": round(macd_h.iloc[-1], 2),
            "bb_upper": round(bb_upper.iloc[-1], 2),
            "bb_lower": round(bb_lower.iloc[-1], 2),
            "bb_mid": round(bb_mid.iloc[-1], 2),
            "ema_9": round(self.ema(9).iloc[-1], 2),
            "ema_21": round(self.ema(21).iloc[-1], 2),
            "ema_50": round(self.ema(50).iloc[-1], 2),
            "vwap": round(self.vwap().iloc[-1], 2),
            "supertrend": round(st.iloc[-1], 2),
            "supertrend_dir": int(st_dir.iloc[-1]),
            "stoch_k": round(k.iloc[-1], 2) if not np.isnan(k.iloc[-1]) else 50,
            "stoch_d": round(d.iloc[-1], 2) if not np.isnan(d.iloc[-1]) else 50,
            "atr": round(self.atr().iloc[-1], 2),
            "obv": round(self.obv().iloc[-1], 0),
            "pivot_points": self.pivot_points(),
            "fibonacci": self.fibonacci_levels(),
            "patterns": self.latest_patterns(),
        }
