"""
Portfolio Tracker — Performance analytics, equity tracking, and reporting.
Calculates Sharpe ratio, max drawdown, win rate, and daily/weekly/monthly returns.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict

import numpy as np

import config

logger = logging.getLogger(__name__)


class PortfolioTracker:
    """
    Comprehensive portfolio performance tracking.
    Maintains equity curve, daily snapshots, and full analytics.
    """

    def __init__(self, initial_capital: float = None):
        self.initial_capital = initial_capital or config.PAPER_TRADING_CAPITAL
        self.equity_history: list[dict] = []
        self.daily_returns: list[dict] = []
        self.peak_equity = self.initial_capital
        self.max_drawdown = 0
        self.max_drawdown_pct = 0

    def record_snapshot(self, equity: float, realized_pnl: float,
                        unrealized_pnl: float, open_positions: int,
                        portfolio_greeks: dict = None):
        """Record a point-in-time portfolio snapshot."""
        now = datetime.now()
        snapshot = {
            "timestamp": now.isoformat(),
            "equity": round(equity, 2),
            "realized_pnl": round(realized_pnl, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "total_pnl": round(realized_pnl + unrealized_pnl, 2),
            "total_pnl_pct": round((equity - self.initial_capital) / self.initial_capital * 100, 2),
            "open_positions": open_positions,
        }

        if portfolio_greeks:
            snapshot["portfolio_delta"] = portfolio_greeks.get("delta", 0)
            snapshot["portfolio_gamma"] = portfolio_greeks.get("gamma", 0)
            snapshot["portfolio_theta"] = portfolio_greeks.get("theta", 0)
            snapshot["portfolio_vega"] = portfolio_greeks.get("vega", 0)

        # Track peak and drawdown
        if equity > self.peak_equity:
            self.peak_equity = equity
        drawdown = self.peak_equity - equity
        drawdown_pct = drawdown / self.peak_equity * 100 if self.peak_equity > 0 else 0
        if drawdown_pct > self.max_drawdown_pct:
            self.max_drawdown_pct = round(drawdown_pct, 2)
            self.max_drawdown = round(drawdown, 2)

        snapshot["drawdown"] = round(drawdown, 2)
        snapshot["drawdown_pct"] = round(drawdown_pct, 2)

        self.equity_history.append(snapshot)
        if len(self.equity_history) > 5000:
            self.equity_history = self.equity_history[-5000:]

    def record_daily_close(self, equity: float, trades_today: int,
                           wins_today: int, losses_today: int, pnl_today: float):
        """Record end-of-day summary."""
        self.daily_returns.append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "equity": round(equity, 2),
            "pnl": round(pnl_today, 2),
            "return_pct": round(pnl_today / self.initial_capital * 100, 2),
            "trades": trades_today,
            "wins": wins_today,
            "losses": losses_today,
            "win_rate": round(wins_today / max(trades_today, 1) * 100, 1),
        })

    def get_performance_metrics(self, closed_trades: list) -> dict:
        """Calculate comprehensive performance metrics from trade history."""
        if not closed_trades:
            return self._empty_metrics()

        pnls = [t.get("net_pnl", 0) for t in closed_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_pnl = sum(pnls)
        win_rate = len(wins) / len(pnls) * 100 if pnls else 0

        # Profit factor
        total_wins = sum(wins) if wins else 0
        total_losses = abs(sum(losses)) if losses else 0
        profit_factor = total_wins / total_losses if total_losses > 0 else float("inf")

        # Sharpe ratio (annualized, assuming daily returns)
        if len(pnls) > 1:
            returns = np.array(pnls) / self.initial_capital
            sharpe = np.mean(returns) / max(np.std(returns), 1e-6) * np.sqrt(252)
        else:
            sharpe = 0

        # Calmar ratio
        calmar = (total_pnl / self.initial_capital * 100) / max(self.max_drawdown_pct, 0.01)

        # Consecutive wins/losses
        max_consec_wins, max_consec_losses = self._max_consecutive(pnls)

        # Average holding time
        hold_times = []
        for t in closed_trades:
            try:
                entry = datetime.fromisoformat(t.get("entry_time", ""))
                exit_t = datetime.fromisoformat(t.get("exit_time", ""))
                hold_times.append((exit_t - entry).total_seconds() / 60)
            except Exception:
                pass
        avg_hold_min = np.mean(hold_times) if hold_times else 0

        # Strategy breakdown
        strategy_pnl = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0})
        for t in closed_trades:
            s = t.get("strategy", "UNKNOWN")
            strategy_pnl[s]["pnl"] += t.get("net_pnl", 0)
            strategy_pnl[s]["trades"] += 1
            if t.get("net_pnl", 0) > 0:
                strategy_pnl[s]["wins"] += 1

        strategy_summary = {}
        for s, data in strategy_pnl.items():
            strategy_summary[s] = {
                "pnl": round(data["pnl"], 2),
                "trades": data["trades"],
                "win_rate": round(data["wins"] / max(data["trades"], 1) * 100, 1),
            }

        # Exit reason breakdown
        exit_reasons = defaultdict(int)
        for t in closed_trades:
            exit_reasons[t.get("exit_reason", "UNKNOWN")] += 1

        return {
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl / self.initial_capital * 100, 2),
            "total_trades": len(pnls),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "sharpe_ratio": round(sharpe, 2),
            "calmar_ratio": round(calmar, 2),
            "max_drawdown": self.max_drawdown,
            "max_drawdown_pct": self.max_drawdown_pct,
            "avg_win": round(np.mean(wins), 2) if wins else 0,
            "avg_loss": round(np.mean(losses), 2) if losses else 0,
            "largest_win": round(max(wins), 2) if wins else 0,
            "largest_loss": round(min(losses), 2) if losses else 0,
            "max_consecutive_wins": max_consec_wins,
            "max_consecutive_losses": max_consec_losses,
            "avg_holding_minutes": round(avg_hold_min, 1),
            "strategy_breakdown": strategy_summary,
            "exit_reasons": dict(exit_reasons),
            "equity_curve": self.equity_history[-200:],
            "daily_returns": self.daily_returns[-30:],
        }

    def get_equity_curve(self, limit: int = 200) -> list[dict]:
        """Get recent equity curve data points."""
        return self.equity_history[-limit:]

    def get_daily_returns(self, limit: int = 30) -> list[dict]:
        """Get recent daily return summaries."""
        return self.daily_returns[-limit:]

    def _max_consecutive(self, pnls: list) -> tuple[int, int]:
        """Calculate max consecutive wins and losses."""
        max_w = max_l = curr_w = curr_l = 0
        for p in pnls:
            if p > 0:
                curr_w += 1
                curr_l = 0
                max_w = max(max_w, curr_w)
            else:
                curr_l += 1
                curr_w = 0
                max_l = max(max_l, curr_l)
        return max_w, max_l

    def _empty_metrics(self) -> dict:
        return {
            "total_pnl": 0, "total_pnl_pct": 0, "total_trades": 0,
            "winning_trades": 0, "losing_trades": 0, "win_rate": 0,
            "profit_factor": 0, "sharpe_ratio": 0, "calmar_ratio": 0,
            "max_drawdown": 0, "max_drawdown_pct": 0,
            "avg_win": 0, "avg_loss": 0, "largest_win": 0, "largest_loss": 0,
            "max_consecutive_wins": 0, "max_consecutive_losses": 0,
            "avg_holding_minutes": 0, "strategy_breakdown": {},
            "exit_reasons": {}, "equity_curve": [], "daily_returns": [],
        }
