"""
Walk-Forward Optimization — Prevents overfitting in backtests.
Uses rolling train/test windows for realistic performance estimation.
"""

import logging
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

import config
from backtest.engine import BacktestEngine

logger = logging.getLogger(__name__)


class WalkForwardOptimizer:
    """
    Walk-Forward Analysis (WFA).
    
    Instead of testing a strategy on the entire dataset at once
    (which leads to overfitting), WFA:
    
    1. Divides data into windows
    2. Trains (optimizes parameters) on each in-sample window
    3. Tests on the subsequent out-of-sample window
    4. Slides forward and repeats
    
    This gives a realistic estimate of future performance.
    
    Example with 90 days of data, 60/20 split:
        Window 1: Train on days 1-60,  Test on days 61-80
        Window 2: Train on days 21-80, Test on days 81-100
        Window 3: Train on days 41-100, Test on days 101-120
        ... and so on
    """

    def __init__(self, train_days: int = 60, test_days: int = 20,
                 step_days: int = 10):
        self.train_days = train_days
        self.test_days = test_days
        self.step_days = step_days
        self.results: list[dict] = []

    def run(self, df: pd.DataFrame, symbol: str = "NIFTY") -> dict:
        """
        Run walk-forward analysis on historical data.
        
        Args:
            df: Full historical DataFrame (OHLCV)
            symbol: Trading symbol
            
        Returns:
            Comprehensive WFA results.
        """
        if df is None or df.empty or len(df) < self.train_days + self.test_days:
            return {"error": f"Need at least {self.train_days + self.test_days} candles"}

        total_rows = len(df)
        windows = []
        start = 0

        while start + self.train_days + self.test_days <= total_rows:
            train_end = start + self.train_days
            test_end = train_end + self.test_days

            train_df = df.iloc[start:train_end].copy()
            test_df = df.iloc[train_end:test_end].copy()

            windows.append({
                "window_id": len(windows) + 1,
                "train_start": start,
                "train_end": train_end,
                "test_start": train_end,
                "test_end": test_end,
                "train_df": train_df,
                "test_df": test_df,
            })

            start += self.step_days

        logger.info("Walk-forward: %d windows of %d/%d",
                     len(windows), self.train_days, self.test_days)

        # Run backtest on each window
        window_results = []
        for w in windows:
            try:
                # In-sample (train) backtest
                train_engine = BacktestEngine(config.BACKTEST_START_CAPITAL)
                train_result = train_engine.run(w["train_df"], "WFA_TRAIN", symbol)

                # Out-of-sample (test) backtest
                test_engine = BacktestEngine(config.BACKTEST_START_CAPITAL)
                test_result = test_engine.run(w["test_df"], "WFA_TEST", symbol)

                window_results.append({
                    "window_id": w["window_id"],
                    "train": {
                        "pnl": train_result.get("total_pnl", 0),
                        "pnl_pct": train_result.get("total_pnl_pct", 0),
                        "trades": train_result.get("total_trades", 0),
                        "win_rate": train_result.get("win_rate", 0),
                        "sharpe": train_result.get("sharpe_ratio", 0),
                        "max_dd": train_result.get("max_drawdown_pct", 0),
                    },
                    "test": {
                        "pnl": test_result.get("total_pnl", 0),
                        "pnl_pct": test_result.get("total_pnl_pct", 0),
                        "trades": test_result.get("total_trades", 0),
                        "win_rate": test_result.get("win_rate", 0),
                        "sharpe": test_result.get("sharpe_ratio", 0),
                        "max_dd": test_result.get("max_drawdown_pct", 0),
                    },
                })
            except Exception as e:
                logger.warning("Window %d failed: %s", w["window_id"], e)

        if not window_results:
            return {"error": "All windows failed"}

        # Aggregate results
        return self._aggregate(window_results, symbol)

    def _aggregate(self, window_results: list, symbol: str) -> dict:
        """Aggregate walk-forward window results."""
        train_pnls = [w["train"]["pnl"] for w in window_results]
        test_pnls = [w["test"]["pnl"] for w in window_results]
        train_wrs = [w["train"]["win_rate"] for w in window_results]
        test_wrs = [w["test"]["win_rate"] for w in window_results]

        # Walk-forward efficiency = OOS performance / IS performance
        avg_train_pnl = np.mean(train_pnls) if train_pnls else 0
        avg_test_pnl = np.mean(test_pnls) if test_pnls else 0
        wfe = (avg_test_pnl / avg_train_pnl * 100) if avg_train_pnl != 0 else 0

        # Consistency: % of OOS windows that were profitable
        profitable_windows = sum(1 for p in test_pnls if p > 0)
        consistency = profitable_windows / max(len(test_pnls), 1) * 100

        # Robustness: correlation between IS and OOS performance
        if len(train_pnls) >= 3 and len(test_pnls) >= 3:
            try:
                correlation = np.corrcoef(train_pnls[:len(test_pnls)],
                                          test_pnls[:len(train_pnls)])[0, 1]
            except Exception:
                correlation = 0
        else:
            correlation = 0

        # Overall assessment
        if wfe > 60 and consistency > 60:
            assessment = "ROBUST — Strategy performs well out-of-sample"
        elif wfe > 40 and consistency > 50:
            assessment = "ACCEPTABLE — Some degradation but viable"
        elif wfe > 20:
            assessment = "MARGINAL — Significant overfitting detected"
        else:
            assessment = "OVERFIT — Strategy does not generalize. Do NOT trade live."

        # Degradation factor: how much worse is OOS vs IS
        avg_train_wr = np.mean(train_wrs) if train_wrs else 0
        avg_test_wr = np.mean(test_wrs) if test_wrs else 0
        degradation = avg_train_wr - avg_test_wr

        result = {
            "symbol": symbol,
            "total_windows": len(window_results),
            "train_days": self.train_days,
            "test_days": self.test_days,
            "step_days": self.step_days,

            # In-sample
            "is_avg_pnl": round(avg_train_pnl, 2),
            "is_avg_win_rate": round(avg_train_wr, 1),
            "is_total_pnl": round(sum(train_pnls), 2),

            # Out-of-sample
            "oos_avg_pnl": round(avg_test_pnl, 2),
            "oos_avg_win_rate": round(avg_test_wr, 1),
            "oos_total_pnl": round(sum(test_pnls), 2),
            "oos_profitable_windows": profitable_windows,

            # Quality metrics
            "walk_forward_efficiency": round(wfe, 1),
            "consistency_pct": round(consistency, 1),
            "is_oos_correlation": round(correlation, 3),
            "win_rate_degradation": round(degradation, 1),

            # Assessment
            "assessment": assessment,
            "is_robust": wfe > 50 and consistency > 55,

            # Window details
            "windows": window_results,
        }

        self.results.append(result)
        return result
