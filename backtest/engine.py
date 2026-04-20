"""
Backtesting Engine v2 — Options-aware event-driven backtest.
Uses actual multi-leg strategy evaluation with proper options P&L,
theta decay modeling, and realistic position management.
"""

import logging
import math
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

import config
from analysis.technical import TechnicalAnalysis
from analysis.signals import SignalAggregator, AggregatedSignal
from strategy.strategies import StrategySelector, StrategyOrder

logger = logging.getLogger(__name__)


class BacktestEngine:
    """
    Event-driven backtesting framework v2.
    
    Key improvements over v1:
    1. Uses actual strategy evaluation (Iron Condor, Spreads, etc.)
    2. Proper options P&L with theta decay and delta-based movement
    3. Realistic position sizing based on actual premium costs
    4. Multi-leg position tracking
    5. No magic multipliers — all P&L is premium * lot_size * qty
    """

    def __init__(self, initial_capital: float = None):
        self.initial_capital = initial_capital or config.BACKTEST_START_CAPITAL
        self.capital = self.initial_capital
        self.equity_curve: list[dict] = []
        self.trades: list[dict] = []
        self.ta = TechnicalAnalysis()
        self.signal_agg = SignalAggregator()
        self.strategy_sel = StrategySelector()
        self._positions: list[dict] = []
        self._trade_counter = 0
        self._daily_pnl = 0

    def run(self, df: pd.DataFrame, strategy_name: str = "MULTI_STRATEGY",
            symbol: str = "NIFTY") -> dict:
        """
        Run backtest on historical OHLCV DataFrame.
        Uses the actual strategy selector (not a simple momentum proxy).
        """
        if df is None or df.empty or len(df) < 50:
            return {"error": "Insufficient data for backtest"}

        self.capital = self.initial_capital
        self.equity_curve = []
        self.trades = []
        self._positions = []
        self._trade_counter = 0
        self._daily_pnl = 0

        lot_size = config.INDICES.get(symbol, {}).get("lot_size", 25)
        strike_interval = config.INDICES.get(symbol, {}).get("strike_interval", 50)

        self.ta.set_data(df)

        # Pre-compute indicators
        rsi = self.ta.rsi().values
        ema9 = self.ta.ema(9).values
        ema21 = self.ta.ema(21).values
        macd_l, macd_s, macd_h = self.ta.macd()
        macd_hist = macd_h.values
        st, st_dir = self.ta.supertrend()
        st_direction = st_dir.values
        bb_upper, bb_mid, bb_lower = self.ta.bollinger_bands()

        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        volumes = df["volume"].values

        # Track bars-in-trade for theta decay
        last_entry_bar = 0
        last_day = None
        bars_per_day = 0
        day_bar_count = 0
        max_positions = config.MAX_SIMULTANEOUS_POSITIONS

        # Walk through each bar
        for i in range(50, len(df)):
            price = closes[i]
            high = highs[i]
            low = lows[i]
            timestamp = df.index[i]

            # Track daily bars for theta calculation
            current_day = timestamp.date() if hasattr(timestamp, 'date') else None
            if current_day != last_day:
                if day_bar_count > 0:
                    bars_per_day = day_bar_count
                day_bar_count = 0
                last_day = current_day
                self._daily_pnl = 0  # Reset daily P&L
            day_bar_count += 1

            # Generate signal from indicators
            tech_data = {
                "close": price,
                "rsi": rsi[i] if not np.isnan(rsi[i]) else 50,
                "macd_histogram": macd_hist[i] if not np.isnan(macd_hist[i]) else 0,
                "supertrend_dir": st_direction[i],
                "ema_9": ema9[i] if not np.isnan(ema9[i]) else price,
                "ema_21": ema21[i] if not np.isnan(ema21[i]) else price,
                "bb_upper": float(bb_upper.iloc[i]) if not np.isnan(bb_upper.iloc[i]) else price + 100,
                "bb_lower": float(bb_lower.iloc[i]) if not np.isnan(bb_lower.iloc[i]) else price - 100,
                "atr": abs(high - low),
                "patterns": {},
            }

            # In backtest, only technical data is available, so rescale weights
            # to give technical analysis full influence
            backtest_weights = {
                "technical": 0.70,
                "greeks": 0.0,
                "oi": 0.0,
                "sentiment": 0.0,
                "regime": 0.30,
            }
            signal = self.signal_agg.generate(
                symbol, technical=tech_data,
                adaptive_weights=backtest_weights,
            )

            # ── Update existing positions ─────────────────
            for pos in list(self._positions):
                self._update_position(pos, price, high, low, i, bars_per_day)

                should_exit, reason = self._check_position_exit(pos, price, signal, i)
                if should_exit:
                    self._close_position(pos, price, timestamp, reason)

            # Cooldown: wait at least 30 bars (2.5 hrs) between new entries
            bars_since_last_entry = i - last_entry_bar
            if (len(self._positions) < max_positions
                    and abs(signal.score) >= 0.30  # Need strong conviction
                    and self._daily_pnl > -config.MAX_DAILY_LOSS
                    and bars_since_last_entry >= 30  # No overtrading
                    and day_bar_count > 5           # Skip first 25min (opening noise)
                    and day_bar_count < 60):         # Skip last hour

                # Build a synthetic option chain from current price
                chain_df = self._synthetic_chain(price, strike_interval)

                order = self.strategy_sel.select_and_evaluate(
                    signal, chain_df, price,
                    symbol=symbol, lot_size=lot_size,
                    strike_interval=strike_interval,
                )

                if order:
                    self._enter_position(order, price, timestamp, i, lot_size)
                    last_entry_bar = i

            # Record equity
            unrealized = sum(p["unrealized_pnl"] for p in self._positions)
            equity = self.capital + unrealized
            self.equity_curve.append({
                "timestamp": str(timestamp),
                "equity": round(equity, 2),
                "capital": round(self.capital, 2),
                "positions": len(self._positions),
            })

        # Force close any open positions
        for pos in list(self._positions):
            self._close_position(pos, closes[-1], df.index[-1], "END_OF_BACKTEST")

        return self._generate_report(symbol, strategy_name, df)

    def _synthetic_chain(self, price: float, strike_interval: int) -> pd.DataFrame:
        """Build a synthetic option chain for backtest entry evaluation."""
        import math as _math

        atm = round(price / strike_interval) * strike_interval
        strikes = [atm + j * strike_interval for j in range(-10, 11)]

        rows = []
        dte = config.DEFAULT_DAYS_TO_EXPIRY
        t = max(dte / 365, 0.001)
        r = config.RISK_FREE_RATE
        iv = 0.15

        for s in strikes:
            moneyness = (s - price) / price

            # IV smile
            strike_iv = iv + abs(moneyness) * 0.25
            strike_iv = max(0.08, strike_iv)

            # Simplified BS pricing
            d1_num = _math.log(price / s) + (r + 0.5 * strike_iv**2) * t
            d1_den = strike_iv * _math.sqrt(t)
            d1 = d1_num / d1_den if d1_den > 0 else 0

            def _ncdf(x):
                x = max(-6, min(6, x))
                return 1 / (1 + _math.exp(-1.7 * x - 0.73 * x**3))

            nd1 = _ncdf(d1)
            d2 = d1 - strike_iv * _math.sqrt(t)
            nd2 = _ncdf(d2)

            ce_p = max(config.MIN_OPTION_PREMIUM, price * nd1 - s * _math.exp(-r * t) * nd2)
            pe_p = max(config.MIN_OPTION_PREMIUM, s * _math.exp(-r * t) * _ncdf(-d2) - price * _ncdf(-d1))

            rows.append({
                "strike": s, "expiry": "",
                "ce_ltp": round(ce_p, 2), "pe_ltp": round(pe_p, 2),
                "ce_oi": 100000, "pe_oi": 100000,
                "ce_chg_oi": 0, "pe_chg_oi": 0,
                "ce_volume": 50000, "pe_volume": 50000,
                "ce_iv": round(strike_iv * 100, 2),
                "pe_iv": round(strike_iv * 100, 2),
                "ce_delta": round(nd1, 4),
                "pe_delta": round(nd1 - 1, 4),
                "ce_gamma": 0.005, "pe_gamma": 0.005,
                "ce_theta": round(-ce_p * 0.04, 2),
                "pe_theta": round(-pe_p * 0.04, 2),
                "ce_vega": 10, "pe_vega": 10,
            })
        return pd.DataFrame(rows)

    def _enter_position(self, order: StrategyOrder, underlying: float,
                        timestamp, bar_idx: int, lot_size: int):
        """Enter a multi-leg options position."""
        self._trade_counter += 1

        # Calculate actual cost
        total_cost = 0
        legs_data = []
        for leg in order.legs:
            # Apply slippage
            slippage = leg.premium * config.SLIPPAGE_PCT / 100
            if leg.side == "BUY":
                fill_price = leg.premium + slippage
                leg_cost = fill_price * lot_size * leg.qty
            else:
                fill_price = max(config.MIN_OPTION_PREMIUM, leg.premium - slippage)
                leg_cost = fill_price * lot_size * leg.qty * 0.2  # Margin for sells

            total_cost += leg_cost

            legs_data.append({
                "option_type": leg.option_type,
                "strike": leg.strike,
                "side": leg.side,
                "qty": leg.qty,
                "entry_premium": fill_price,
                "current_premium": fill_price,
                "delta": leg.delta,
            })

        # Check if we have enough capital (allow up to 80% per trade in backtest)
        brokerage = config.BROKERAGE_PER_ORDER * len(order.legs)
        if total_cost + brokerage > self.capital * 0.8:
            return  # Not enough capital for this trade

        self.capital -= (total_cost + brokerage)

        self._positions.append({
            "trade_id": self._trade_counter,
            "strategy": order.strategy_name,
            "legs": legs_data,
            "entry_underlying": underlying,
            "entry_bar": bar_idx,
            "entry_time": str(timestamp),
            "lot_size": lot_size,
            "total_cost": total_cost,
            "net_premium": order.net_premium * lot_size,  # +ve=credit received, -ve=debit paid
            "brokerage_entry": brokerage,
            "signal_score": order.signal_score,
            "stoploss_pct": order.stoploss_pct,
            "max_profit_seen": 0,
            "unrealized_pnl": 0,
            "bars_held": 0,
        })

    def _update_position(self, pos: dict, underlying: float,
                         high: float, low: float, bar_idx: int,
                         bars_per_day: int):
        """Update a position's P&L with delta movement and theta decay."""
        bars_held = bar_idx - pos["entry_bar"]
        pos["bars_held"] = bars_held

        total_pnl = 0
        lot_size = pos["lot_size"]

        for leg in pos["legs"]:
            entry_p = leg["entry_premium"]
            strike = leg["strike"]
            opt_type = leg["option_type"]

            # Price change of underlying since entry
            price_change = underlying - pos["entry_underlying"]

            # Delta-based premium change (simplified but correct direction)
            if opt_type == "CE":
                moneyness = underlying - strike
                # Deep ITM delta ~1, ATM ~0.5, deep OTM ~0
                intrinsic = max(0, moneyness)
                time_value = max(0, entry_p - max(0, pos["entry_underlying"] - strike))
            else:
                moneyness = strike - underlying
                intrinsic = max(0, moneyness)
                time_value = max(0, entry_p - max(0, strike - pos["entry_underlying"]))

            # Theta decay: accelerated near expiry
            dte = config.DEFAULT_DAYS_TO_EXPIRY
            bpd = max(bars_per_day, 75)  # ~75 five-min bars per day
            days_elapsed = bars_held / bpd

            if config.THETA_DECAY_MODEL == "accelerated":
                # sqrt model: decay accelerates as expiry approaches
                remaining_life = max(0.01, 1 - days_elapsed / dte)
                decay_factor = 1 - math.sqrt(1 - remaining_life)
            else:
                # Linear decay
                decay_factor = max(0, 1 - days_elapsed / dte)

            time_value_now = time_value * decay_factor

            # Current premium = intrinsic + remaining time value
            current_p = max(config.MIN_OPTION_PREMIUM, intrinsic + time_value_now)

            # Add some noise (market microstructure)
            current_p *= (1 + np.random.uniform(-0.02, 0.02))
            current_p = max(config.MIN_OPTION_PREMIUM, round(current_p, 2))

            leg["current_premium"] = current_p

            # P&L for this leg
            if leg["side"] == "BUY":
                leg_pnl = (current_p - entry_p) * lot_size * leg["qty"]
            else:
                leg_pnl = (entry_p - current_p) * lot_size * leg["qty"]

            total_pnl += leg_pnl

        pos["unrealized_pnl"] = round(total_pnl, 2)
        if total_pnl > pos["max_profit_seen"]:
            pos["max_profit_seen"] = total_pnl

    def _check_position_exit(self, pos: dict, underlying: float,
                             signal: AggregatedSignal, bar_idx: int) -> tuple:
        """Check if position should be exited — institutional-grade risk management."""
        pnl = pos["unrealized_pnl"]
        cost = pos["total_cost"]
        bars_held = pos["bars_held"]
        strategy = pos["strategy"]
        net_premium = pos.get("net_premium", 0)  # +ve for credit, -ve for debit

        is_credit = strategy in ("IRON_CONDOR", "SHORT_STRANGLE")

        # ── 1. Hard stop-loss ─────────────────────────────
        if pnl < 0:
            if is_credit and net_premium > 0:
                # Credit strategy: stop at 1.5× the premium received
                # e.g., received ₹1000 credit → stop at ₹1500 loss
                if abs(pnl) > net_premium * 1.5:
                    return True, "STOPLOSS"
            elif cost > 0:
                # Debit strategy: stop at 20% of premium paid
                loss_pct = abs(pnl) / cost * 100
                if loss_pct > 20:
                    return True, "STOPLOSS"

        # ── 2. Profit targets ─────────────────────────────
        if is_credit and net_premium > 0:
            # Credit: take 50% of max profit (premium received)
            if pnl > net_premium * 0.50:
                return True, "TARGET_REACHED"
        elif strategy in ("BULL_CALL_SPREAD", "BEAR_PUT_SPREAD"):
            # Debit spreads: take 30% return on premium paid
            if cost > 0 and pnl > cost * 0.30:
                return True, "TARGET_REACHED"
        else:
            # Naked: take 40% return
            if cost > 0 and pnl > cost * 0.40:
                return True, "TARGET_REACHED"

        # ── 3. Trailing stop: lock in profits ─────────────
        min_profit = net_premium * 0.15 if (is_credit and net_premium > 0) else cost * 0.10
        if pos["max_profit_seen"] > min_profit:
            if pnl < pos["max_profit_seen"] * 0.55:
                return True, "TRAILING_STOP"

        # ── 4. Time-based exit ────────────────────────────
        bpd = 75
        days_held = bars_held / bpd
        if is_credit:
            if days_held > config.DEFAULT_DAYS_TO_EXPIRY * 0.65:
                return True, "TIME_DECAY_EXIT"
        else:
            if days_held > config.DEFAULT_DAYS_TO_EXPIRY * 0.40:
                return True, "TIME_DECAY_EXIT"

        # ── 5. Signal reversal ────────────────────────────
        if strategy in ("BULL_CALL_SPREAD", "NAKED_CE") and signal.score < -0.25:
            return True, "SIGNAL_REVERSAL"
        elif strategy in ("BEAR_PUT_SPREAD", "NAKED_PE") and signal.score > 0.25:
            return True, "SIGNAL_REVERSAL"

        # ── 6. Breakeven exit: truly stuck ────────────────
        if days_held > config.DEFAULT_DAYS_TO_EXPIRY * 0.40:
            if abs(pnl) < 150:
                return True, "BREAKEVEN_EXIT"

        return False, ""

    def _close_position(self, pos: dict, underlying: float, timestamp, reason: str):
        """Close a position and record the trade."""
        brokerage = config.BROKERAGE_PER_ORDER * len(pos["legs"])
        gross_pnl = pos["unrealized_pnl"]
        net_pnl = gross_pnl - brokerage

        self.capital += pos["total_cost"] + net_pnl
        self._daily_pnl += net_pnl

        self.trades.append({
            "id": pos["trade_id"],
            "strategy": pos["strategy"],
            "legs": len(pos["legs"]),
            "entry_underlying": pos["entry_underlying"],
            "exit_underlying": underlying,
            "entry_time": pos["entry_time"],
            "exit_time": str(timestamp),
            "cost": round(pos["total_cost"], 2),
            "gross_pnl": round(gross_pnl, 2),
            "brokerage": round(pos["brokerage_entry"] + brokerage, 2),
            "net_pnl": round(net_pnl, 2),
            "pnl_pct": round(net_pnl / max(pos["total_cost"], 1) * 100, 2),
            "exit_reason": reason,
            "signal_score": pos["signal_score"],
            "bars_held": pos["bars_held"],
        })

        self._positions.remove(pos)

    def _generate_report(self, symbol: str, strategy: str, df: pd.DataFrame) -> dict:
        """Generate comprehensive backtest report."""
        if not self.trades:
            return {"error": "No trades executed", "total_trades": 0}

        pnls = [t["net_pnl"] for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        equities = [e["equity"] for e in self.equity_curve]

        # Max drawdown
        peak = equities[0] if equities else self.initial_capital
        max_dd = 0
        max_dd_duration = 0
        dd_start = 0
        for idx, eq in enumerate(equities):
            if eq > peak:
                peak = eq
                dd_start = idx
            dd = (peak - eq) / peak * 100
            if dd > max_dd:
                max_dd = dd
                max_dd_duration = idx - dd_start

        # Sharpe ratio (annualized, using per-trade returns)
        if len(pnls) > 1:
            returns = np.array(pnls) / self.initial_capital
            mean_ret = np.mean(returns)
            std_ret = np.std(returns)
            sharpe = mean_ret / max(std_ret, 0.0001) * np.sqrt(min(len(pnls), 252))
        else:
            sharpe = 0

        # Sortino ratio (downside deviation only)
        if losses:
            downside_returns = np.array(losses) / self.initial_capital
            downside_std = np.std(downside_returns)
            sortino = np.mean(np.array(pnls) / self.initial_capital) / max(downside_std, 0.0001) * np.sqrt(252)
        else:
            sortino = float("inf")

        # Calmar ratio
        total_pnl = sum(pnls)
        calmar = (total_pnl / self.initial_capital * 100) / max(max_dd, 0.01)

        profit_factor = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else float("inf")

        # Strategy breakdown
        strategy_stats = {}
        for t in self.trades:
            strat = t["strategy"]
            if strat not in strategy_stats:
                strategy_stats[strat] = {"trades": 0, "wins": 0, "total_pnl": 0}
            strategy_stats[strat]["trades"] += 1
            if t["net_pnl"] > 0:
                strategy_stats[strat]["wins"] += 1
            strategy_stats[strat]["total_pnl"] += t["net_pnl"]

        for strat in strategy_stats:
            s = strategy_stats[strat]
            s["win_rate"] = round(s["wins"] / max(s["trades"], 1) * 100, 1)
            s["total_pnl"] = round(s["total_pnl"], 2)

        # Exit reason breakdown
        exit_reasons = {}
        for t in self.trades:
            r = t["exit_reason"]
            if r not in exit_reasons:
                exit_reasons[r] = {"count": 0, "total_pnl": 0}
            exit_reasons[r]["count"] += 1
            exit_reasons[r]["total_pnl"] = round(exit_reasons[r]["total_pnl"] + t["net_pnl"], 2)

        # Average bars held
        avg_bars = np.mean([t.get("bars_held", 0) for t in self.trades])

        return {
            "symbol": symbol,
            "strategy": strategy,
            "start_date": str(df.index[0]),
            "end_date": str(df.index[-1]),
            "initial_capital": self.initial_capital,
            "final_capital": round(self.capital, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl / self.initial_capital * 100, 2),
            "total_trades": len(self.trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": round(len(wins) / max(len(self.trades), 1) * 100, 1),
            "profit_factor": round(min(profit_factor, 99.99), 2),
            "avg_win": round(np.mean(wins), 2) if wins else 0,
            "avg_loss": round(np.mean(losses), 2) if losses else 0,
            "largest_win": round(max(wins), 2) if wins else 0,
            "largest_loss": round(min(losses), 2) if losses else 0,
            "max_drawdown_pct": round(max_dd, 2),
            "max_drawdown_bars": max_dd_duration,
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(min(sortino, 99.99), 2),
            "calmar_ratio": round(min(calmar, 99.99), 2),
            "avg_bars_held": round(avg_bars, 0),
            "strategy_breakdown": strategy_stats,
            "exit_reasons": exit_reasons,
            "equity_curve": self.equity_curve[-200:],  # Last 200 points
            "trades": self.trades,
        }
