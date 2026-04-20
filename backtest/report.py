"""
Backtest Report Generator — Creates detailed reports from backtest results.
Outputs formatted console reports and JSON for the web UI.
"""

import logging
from datetime import datetime
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class BacktestReport:
    """Generate and format backtest reports."""

    @staticmethod
    def generate(result: dict) -> dict:
        """
        Enhance a raw backtest result with additional analytics.
        Called after BacktestEngine.run() to add deeper analysis.
        """
        if "error" in result:
            return result

        trades = result.get("trades", [])
        if not trades:
            return result

        # Monthly returns
        monthly = {}
        for t in trades:
            try:
                month_key = t.get("exit_time", "")[:7]  # YYYY-MM
                if month_key:
                    monthly[month_key] = monthly.get(month_key, 0) + t.get("net_pnl", 0)
            except Exception:
                pass
        result["monthly_returns"] = {k: round(v, 2) for k, v in sorted(monthly.items())}

        # Weekly returns
        weekly = {}
        for t in trades:
            try:
                exit_dt = datetime.fromisoformat(t.get("exit_time", ""))
                week_key = exit_dt.strftime("%Y-W%U")
                weekly[week_key] = weekly.get(week_key, 0) + t.get("net_pnl", 0)
            except Exception:
                pass
        result["weekly_returns"] = {k: round(v, 2) for k, v in sorted(weekly.items())}

        # Exit reason analysis
        exit_stats = {}
        for t in trades:
            reason = t.get("exit_reason", "UNKNOWN")
            if reason not in exit_stats:
                exit_stats[reason] = {"count": 0, "total_pnl": 0, "wins": 0}
            exit_stats[reason]["count"] += 1
            exit_stats[reason]["total_pnl"] += t.get("net_pnl", 0)
            if t.get("net_pnl", 0) > 0:
                exit_stats[reason]["wins"] += 1

        for reason, stats in exit_stats.items():
            stats["total_pnl"] = round(stats["total_pnl"], 2)
            stats["win_rate"] = round(stats["wins"] / max(stats["count"], 1) * 100, 1)
        result["exit_analysis"] = exit_stats

        # Strategy-based analysis (v2 engine uses strategy, not direction)
        strategy_pnl = {}
        for t in trades:
            strat = t.get("strategy", t.get("direction", "UNKNOWN"))
            if strat not in strategy_pnl:
                strategy_pnl[strat] = {"count": 0, "total_pnl": 0, "wins": 0}
            strategy_pnl[strat]["count"] += 1
            strategy_pnl[strat]["total_pnl"] += t.get("net_pnl", 0)
            if t.get("net_pnl", 0) > 0:
                strategy_pnl[strat]["wins"] += 1
        for strat, stats in strategy_pnl.items():
            stats["total_pnl"] = round(stats["total_pnl"], 2)
            stats["win_rate"] = round(stats["wins"] / max(stats["count"], 1) * 100, 1)
        result["strategy_analysis"] = strategy_pnl

        # Average holding period
        hold_times = []
        for t in trades:
            try:
                entry = datetime.fromisoformat(t.get("entry_time", ""))
                exit_t = datetime.fromisoformat(t.get("exit_time", ""))
                hold_times.append((exit_t - entry).total_seconds() / 60)
            except Exception:
                pass
        result["avg_holding_minutes"] = round(np.mean(hold_times), 1) if hold_times else 0

        # Risk-adjusted metrics
        pnls = [t.get("net_pnl", 0) for t in trades]
        if len(pnls) > 1:
            returns = np.array(pnls) / result.get("initial_capital", 10000)
            # Sortino ratio (downside deviation only)
            neg_returns = returns[returns < 0]
            downside_std = np.std(neg_returns) if len(neg_returns) > 0 else 1e-6
            result["sortino_ratio"] = round(
                np.mean(returns) / max(downside_std, 1e-6) * np.sqrt(252), 2
            )

            # Payoff ratio
            wins = [p for p in pnls if p > 0]
            losses = [abs(p) for p in pnls if p < 0]
            if losses:
                result["payoff_ratio"] = round(np.mean(wins) / np.mean(losses), 2) if wins else 0
            else:
                result["payoff_ratio"] = float("inf") if wins else 0

            # Expectancy
            win_rate_dec = len(wins) / len(pnls)
            avg_win = np.mean(wins) if wins else 0
            avg_loss = np.mean(losses) if losses else 0
            result["expectancy"] = round(
                win_rate_dec * avg_win - (1 - win_rate_dec) * avg_loss, 2
            )
        else:
            result["sortino_ratio"] = 0
            result["payoff_ratio"] = 0
            result["expectancy"] = 0

        return result

    @staticmethod
    def print_console(result: dict):
        """Print a formatted backtest report to console."""
        if "error" in result:
            print(f"\n  BACKTEST ERROR: {result['error']}\n")
            return

        print("\n" + "=" * 70)
        print("  BACKTEST REPORT")
        print("=" * 70)
        print(f"  Symbol:           {result.get('symbol', '--')}")
        print(f"  Strategy:         {result.get('strategy', '--')}")
        print(f"  Period:           {result.get('start_date', '--')} to {result.get('end_date', '--')}")
        print("-" * 70)
        print(f"  Initial Capital:  Rs.{result.get('initial_capital', 0):,.2f}")
        print(f"  Final Capital:    Rs.{result.get('final_capital', 0):,.2f}")
        print(f"  Total P&L:        Rs.{result.get('total_pnl', 0):,.2f} ({result.get('total_pnl_pct', 0):.2f}%)")
        print("-" * 70)
        print(f"  Total Trades:     {result.get('total_trades', 0)}")
        print(f"  Winning:          {result.get('winning_trades', 0)}")
        print(f"  Losing:           {result.get('losing_trades', 0)}")
        print(f"  Win Rate:         {result.get('win_rate', 0):.1f}%")
        print(f"  Profit Factor:    {result.get('profit_factor', 0):.2f}")
        print("-" * 70)
        print(f"  Avg Win:          Rs.{result.get('avg_win', 0):,.2f}")
        print(f"  Avg Loss:         Rs.{result.get('avg_loss', 0):,.2f}")
        print(f"  Largest Win:      Rs.{result.get('largest_win', 0):,.2f}")
        print(f"  Largest Loss:     Rs.{result.get('largest_loss', 0):,.2f}")
        print("-" * 70)
        print(f"  Max Drawdown:     {result.get('max_drawdown_pct', 0):.2f}%")
        print(f"  Sharpe Ratio:     {result.get('sharpe_ratio', 0):.2f}")
        print(f"  Sortino Ratio:    {result.get('sortino_ratio', 0):.2f}")
        print(f"  Expectancy:       Rs.{result.get('expectancy', 0):,.2f}")
        print("=" * 70 + "\n")
