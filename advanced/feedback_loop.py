"""
Trade Outcome Feedback Loop — Self-improving trade analysis.
After every trade, dissects what went right/wrong across all layers.
Builds a knowledge base that improves future decision-making.
"""

import logging
import json
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import numpy as np

import config

logger = logging.getLogger(__name__)


class FeedbackLoop:
    """
    Post-trade analysis engine.
    
    For every closed trade, records:
    1. Which signals led to entry
    2. What the market actually did after entry
    3. Which layers were right vs wrong
    4. Optimal entry/exit timing analysis
    5. Strategy-specific win/loss patterns
    
    This data feeds into the AdaptiveWeightOptimizer and helps
    the system avoid repeating mistakes.
    """

    def __init__(self):
        self.feedback_log: list[dict] = []
        self.patterns: dict = {
            "winning_conditions": [],
            "losing_conditions": [],
            "best_entry_hour": defaultdict(lambda: {"wins": 0, "losses": 0, "total_pnl": 0}),
            "strategy_performance": defaultdict(lambda: {"wins": 0, "losses": 0, "total_pnl": 0}),
            "regime_performance": defaultdict(lambda: {"wins": 0, "losses": 0, "total_pnl": 0}),
            "exit_reason_analysis": defaultdict(lambda: {"count": 0, "avg_pnl": 0, "total_pnl": 0}),
        }
        self._save_path = config.DATA_DIR / "feedback_loop.json"
        self._load_state()

    def analyze_trade(self, trade: dict, signal_at_entry: dict = None,
                      signal_at_exit: dict = None) -> dict:
        """
        Perform post-trade analysis on a completed trade.
        
        Args:
            trade: Closed trade record from PaperTradingEngine
            signal_at_entry: The AggregatedSignal data at time of entry
            signal_at_exit: The AggregatedSignal data at time of exit
        """
        pnl = trade.get("net_pnl", 0)
        is_win = pnl > 0
        strategy = trade.get("strategy", "UNKNOWN")
        exit_reason = trade.get("exit_reason", "UNKNOWN")

        # Parse entry time for hour-based analysis
        entry_hour = 10  # default
        try:
            entry_dt = datetime.fromisoformat(trade.get("entry_time", ""))
            entry_hour = entry_dt.hour
        except Exception:
            pass

        # Build feedback record
        feedback = {
            "timestamp": datetime.now().isoformat(),
            "trade_id": trade.get("trade_id", 0),
            "symbol": trade.get("symbol", "NIFTY"),
            "strategy": strategy,
            "direction": trade.get("side", "BUY"),
            "pnl": round(pnl, 2),
            "pnl_pct": trade.get("pnl_pct", 0),
            "is_win": is_win,
            "exit_reason": exit_reason,
            "entry_hour": entry_hour,
        }

        # Signal analysis at entry
        if signal_at_entry:
            feedback["entry_signal"] = {
                "score": signal_at_entry.get("score", 0),
                "direction": signal_at_entry.get("direction", "NEUTRAL"),
                "confidence": signal_at_entry.get("confidence", 0),
                "regime": signal_at_entry.get("regime", "UNKNOWN"),
                "technical_score": signal_at_entry.get("technical_score", 0),
                "greeks_score": signal_at_entry.get("greeks_score", 0),
                "oi_score": signal_at_entry.get("oi_score", 0),
                "sentiment_score": signal_at_entry.get("sentiment_score", 0),
            }

            # Lesson extraction
            lessons = []
            if is_win:
                if signal_at_entry.get("confidence", 0) > 70:
                    lessons.append("HIGH_CONFIDENCE_WIN")
                if signal_at_entry.get("regime") in ("TRENDING_UP", "TRENDING_DOWN"):
                    lessons.append("TRENDING_REGIME_WIN")
            else:
                if signal_at_entry.get("confidence", 0) < 50:
                    lessons.append("LOW_CONFIDENCE_LOSS — avoid trades with confidence < 50")
                if exit_reason == "STOPLOSS":
                    lessons.append("STOPLOSS_HIT — check if stop was too tight")
                if signal_at_entry.get("regime") == "SIDEWAYS":
                    lessons.append("SIDEWAYS_LOSS — directional trade in sideways market")

            feedback["lessons"] = lessons

        # Update pattern database
        hour_key = str(entry_hour)
        if is_win:
            self.patterns["best_entry_hour"][hour_key]["wins"] += 1
            self.patterns["strategy_performance"][strategy]["wins"] += 1
        else:
            self.patterns["best_entry_hour"][hour_key]["losses"] += 1
            self.patterns["strategy_performance"][strategy]["losses"] += 1

        self.patterns["best_entry_hour"][hour_key]["total_pnl"] += pnl
        self.patterns["strategy_performance"][strategy]["total_pnl"] += pnl

        # Regime performance
        regime = signal_at_entry.get("regime", "UNKNOWN") if signal_at_entry else "UNKNOWN"
        if is_win:
            self.patterns["regime_performance"][regime]["wins"] += 1
        else:
            self.patterns["regime_performance"][regime]["losses"] += 1
        self.patterns["regime_performance"][regime]["total_pnl"] += pnl

        # Exit reason
        self.patterns["exit_reason_analysis"][exit_reason]["count"] += 1
        self.patterns["exit_reason_analysis"][exit_reason]["total_pnl"] += pnl
        era = self.patterns["exit_reason_analysis"][exit_reason]
        era["avg_pnl"] = era["total_pnl"] / max(era["count"], 1)

        # Store conditions
        if signal_at_entry:
            condition = {
                "score": signal_at_entry.get("score", 0),
                "confidence": signal_at_entry.get("confidence", 0),
                "regime": regime,
                "strategy": strategy,
                "pnl": pnl,
            }
            if is_win:
                self.patterns["winning_conditions"].append(condition)
                if len(self.patterns["winning_conditions"]) > 100:
                    self.patterns["winning_conditions"] = self.patterns["winning_conditions"][-100:]
            else:
                self.patterns["losing_conditions"].append(condition)
                if len(self.patterns["losing_conditions"]) > 100:
                    self.patterns["losing_conditions"] = self.patterns["losing_conditions"][-100:]

        self.feedback_log.append(feedback)
        if len(self.feedback_log) > 500:
            self.feedback_log = self.feedback_log[-500:]

        self._save_state()
        return feedback

    def get_insights(self) -> dict:
        """Get actionable insights from accumulated trade data."""
        if len(self.feedback_log) < 5:
            return {"status": "INSUFFICIENT_DATA", "min_trades_needed": 5}

        # Best trading hour
        best_hour = None
        best_hour_wr = 0
        for hour, stats in self.patterns["best_entry_hour"].items():
            total = stats["wins"] + stats["losses"]
            if total >= 3:
                wr = stats["wins"] / total * 100
                if wr > best_hour_wr:
                    best_hour_wr = wr
                    best_hour = hour

        # Best strategy
        best_strat = None
        best_strat_pnl = -float("inf")
        for strat, stats in self.patterns["strategy_performance"].items():
            if stats["total_pnl"] > best_strat_pnl and (stats["wins"] + stats["losses"]) >= 2:
                best_strat_pnl = stats["total_pnl"]
                best_strat = strat

        # Worst exit reason
        worst_exit = None
        worst_exit_pnl = 0
        for reason, stats in self.patterns["exit_reason_analysis"].items():
            if stats["avg_pnl"] < worst_exit_pnl:
                worst_exit_pnl = stats["avg_pnl"]
                worst_exit = reason

        # Confidence threshold analysis
        all_trades = self.feedback_log
        if all_trades:
            confidences = [t.get("entry_signal", {}).get("confidence", 50) for t in all_trades if "entry_signal" in t]
            pnls = [t["pnl"] for t in all_trades if "entry_signal" in t]
            high_conf_wins = sum(1 for c, p in zip(confidences, pnls) if c > 60 and p > 0)
            high_conf_total = sum(1 for c in confidences if c > 60)
            low_conf_wins = sum(1 for c, p in zip(confidences, pnls) if c <= 60 and p > 0)
            low_conf_total = sum(1 for c in confidences if c <= 60)
        else:
            high_conf_wins = high_conf_total = low_conf_wins = low_conf_total = 0

        return {
            "total_trades_analyzed": len(self.feedback_log),
            "best_trading_hour": best_hour,
            "best_hour_win_rate": round(best_hour_wr, 1),
            "best_strategy": best_strat,
            "best_strategy_pnl": round(best_strat_pnl, 2) if best_strat_pnl > -float("inf") else 0,
            "worst_exit_reason": worst_exit,
            "high_confidence_win_rate": round(high_conf_wins / max(high_conf_total, 1) * 100, 1),
            "low_confidence_win_rate": round(low_conf_wins / max(low_conf_total, 1) * 100, 1),
            "strategy_breakdown": {k: dict(v) for k, v in self.patterns["strategy_performance"].items()},
            "regime_breakdown": {k: dict(v) for k, v in self.patterns["regime_performance"].items()},
            "recommendation": self._generate_recommendation(),
        }

    def _generate_recommendation(self) -> str:
        """Generate an actionable recommendation based on data."""
        if len(self.feedback_log) < 10:
            return "Need more trades for reliable recommendations."

        recent = self.feedback_log[-20:]
        recent_wr = sum(1 for t in recent if t["is_win"]) / len(recent) * 100

        if recent_wr > 65:
            return "System performing well. Maintain current parameters."
        elif recent_wr < 35:
            return "High loss rate detected. Consider increasing minimum confidence threshold and reducing position size."
        else:
            # Check for patterns
            stoploss_exits = sum(1 for t in recent if t["exit_reason"] == "STOPLOSS")
            if stoploss_exits > len(recent) * 0.5:
                return "Too many stoploss hits. Consider widening stops or improving entry timing."
            return "Moderate performance. Monitor for the next 10 trades."

    def should_skip_trade(self, signal_data: dict) -> tuple[bool, str]:
        """
        Based on learned patterns, recommend whether to skip a trade.
        Returns (should_skip, reason).
        """
        if len(self.feedback_log) < 10:
            return False, ""

        regime = signal_data.get("regime", "UNKNOWN")
        confidence = signal_data.get("confidence", 0)

        # Skip if low confidence has been losing
        if confidence < 50:
            recent_low_conf = [t for t in self.feedback_log[-30:]
                              if t.get("entry_signal", {}).get("confidence", 100) < 50]
            if len(recent_low_conf) >= 3:
                losses = sum(1 for t in recent_low_conf if not t["is_win"])
                if losses / len(recent_low_conf) > 0.7:
                    return True, "Low confidence trades have 70%+ loss rate recently"

        # Skip if this regime has been losing
        regime_data = self.patterns["regime_performance"].get(regime, {})
        if regime_data:
            total = regime_data.get("wins", 0) + regime_data.get("losses", 0)
            if total >= 5:
                wr = regime_data.get("wins", 0) / total * 100
                if wr < 30:
                    return True, f"Poor win rate ({wr:.0f}%) in {regime} regime"

        return False, ""

    def _save_state(self):
        try:
            state = {
                "feedback_count": len(self.feedback_log),
                "patterns": {
                    "best_entry_hour": dict(self.patterns["best_entry_hour"]),
                    "strategy_performance": dict(self.patterns["strategy_performance"]),
                    "regime_performance": dict(self.patterns["regime_performance"]),
                    "exit_reason_analysis": dict(self.patterns["exit_reason_analysis"]),
                },
            }
            self._save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._save_path, "w") as f:
                json.dump(state, f, default=str)
        except Exception as e:
            logger.debug("Feedback save failed: %s", e)

    def _load_state(self):
        try:
            if self._save_path.exists():
                with open(self._save_path, "r") as f:
                    state = json.load(f)
                for key in ["best_entry_hour", "strategy_performance",
                            "regime_performance", "exit_reason_analysis"]:
                    loaded = state.get("patterns", {}).get(key, {})
                    for k, v in loaded.items():
                        self.patterns[key][k].update(v)
                logger.info("Loaded feedback loop state (%d patterns)", state.get("feedback_count", 0))
        except Exception as e:
            logger.debug("Feedback load failed: %s", e)
