"""
Adaptive Signal Weights — Self-learning weight optimizer.
Tracks which analysis layer has been most predictive and
auto-adjusts confluence weights every N trades.

The system that makes JP Morgan jealous: it gets smarter with every trade.
"""

import logging
import json
from datetime import datetime
from collections import defaultdict
from pathlib import Path

import numpy as np

import config

logger = logging.getLogger(__name__)


class AdaptiveWeightOptimizer:
    """
    Learns optimal signal weights from trade outcomes.
    
    After each closed trade, it correlates each layer's signal
    with the actual P&L outcome. Layers that predicted correctly
    get more weight; layers that predicted wrong get less.
    
    Uses an exponential moving average so recent performance
    matters more than old performance.
    """

    LAYERS = ["technical", "greeks", "oi", "sentiment", "regime"]
    MIN_WEIGHT = 0.05    # No layer goes below 5%
    MAX_WEIGHT = 0.45    # No layer goes above 45%
    EMA_ALPHA = 0.15     # How fast weights adapt (higher = faster)
    MIN_TRADES_TO_ADAPT = 5  # Need at least this many trades before adapting

    def __init__(self):
        # Start with config defaults
        self.weights = dict(config.SIGNAL_WEIGHTS)
        self.layer_accuracy: dict[str, list[float]] = {l: [] for l in self.LAYERS}
        self.trade_log: list[dict] = []
        self.adaptation_history: list[dict] = []
        self._save_path = config.DATA_DIR / "adaptive_weights.json"
        self._load_state()

    def get_weights(self) -> dict:
        """Return current adaptive weights."""
        return dict(self.weights)

    def record_trade_outcome(self, signal_data: dict, trade_pnl: float):
        """
        Record a trade outcome and update layer accuracy.
        
        signal_data should contain:
            - technical_score, greeks_score, oi_score, sentiment_score, regime_score
            - direction (BUY/SELL)
            - score (final confluence score)
        """
        was_profitable = trade_pnl > 0
        direction = signal_data.get("direction", "NEUTRAL")

        for layer in self.LAYERS:
            layer_score = signal_data.get(f"{layer}_score", 0)

            # Did this layer agree with the trade direction?
            if direction in ("STRONG_BUY", "BUY"):
                layer_correct = layer_score > 0 and was_profitable
                layer_wrong = layer_score > 0 and not was_profitable
                layer_contrary = layer_score < 0 and was_profitable
            elif direction in ("STRONG_SELL", "SELL"):
                layer_correct = layer_score < 0 and was_profitable
                layer_wrong = layer_score < 0 and not was_profitable
                layer_contrary = layer_score > 0 and was_profitable
            else:
                continue

            # Score: +1 for correct, -1 for wrong, +0.5 for contrary (it was right to disagree)
            if layer_correct:
                accuracy = 1.0
            elif layer_contrary:
                accuracy = -0.5  # Layer disagreed but trade was profitable — layer was wrong
            elif layer_wrong:
                accuracy = -1.0
            else:
                accuracy = 0.0

            self.layer_accuracy[layer].append(accuracy)

            # Keep last 100 outcomes per layer
            if len(self.layer_accuracy[layer]) > 100:
                self.layer_accuracy[layer] = self.layer_accuracy[layer][-100:]

        self.trade_log.append({
            "timestamp": datetime.now().isoformat(),
            "pnl": trade_pnl,
            "profitable": was_profitable,
            "signal": {l: signal_data.get(f"{l}_score", 0) for l in self.LAYERS},
        })

        # Recalculate weights if we have enough data
        if len(self.trade_log) >= self.MIN_TRADES_TO_ADAPT:
            self._recalculate_weights()

    def _recalculate_weights(self):
        """Recalculate weights based on recent layer accuracy."""
        raw_scores = {}

        for layer in self.LAYERS:
            history = self.layer_accuracy[layer]
            if not history:
                raw_scores[layer] = 0.5  # Neutral if no data
                continue

            # Exponentially weighted mean — recent outcomes matter more
            weights = np.array([self.EMA_ALPHA * (1 - self.EMA_ALPHA) ** i
                               for i in range(len(history) - 1, -1, -1)])
            weights /= weights.sum()

            ema_accuracy = np.dot(weights, history)

            # Map from [-1, 1] to [0, 1] range
            raw_scores[layer] = (ema_accuracy + 1) / 2

        # Normalize to sum to 1.0
        total = sum(raw_scores.values())
        if total <= 0:
            return  # Don't update if all layers are terrible

        new_weights = {}
        for layer in self.LAYERS:
            w = raw_scores[layer] / total
            # Clamp to min/max
            w = max(self.MIN_WEIGHT, min(self.MAX_WEIGHT, w))
            new_weights[layer] = w

        # Re-normalize after clamping
        total = sum(new_weights.values())
        new_weights = {k: round(v / total, 4) for k, v in new_weights.items()}

        # Log the change
        old_weights = dict(self.weights)
        self.weights = new_weights

        self.adaptation_history.append({
            "timestamp": datetime.now().isoformat(),
            "old_weights": old_weights,
            "new_weights": new_weights,
            "layer_accuracy": {l: round(np.mean(self.layer_accuracy[l][-20:]), 4)
                              if self.layer_accuracy[l] else 0
                              for l in self.LAYERS},
            "trade_count": len(self.trade_log),
        })

        logger.info("Adaptive weights updated: %s", new_weights)
        self._save_state()

    def get_layer_performance(self) -> dict:
        """Return current accuracy metrics for each layer."""
        result = {}
        for layer in self.LAYERS:
            history = self.layer_accuracy[layer]
            if history:
                recent = history[-20:]
                result[layer] = {
                    "weight": self.weights.get(layer, 0),
                    "accuracy_all": round(np.mean(history), 4),
                    "accuracy_recent_20": round(np.mean(recent), 4),
                    "sample_size": len(history),
                    "correct_rate": round(sum(1 for h in recent if h > 0) / max(len(recent), 1) * 100, 1),
                }
            else:
                result[layer] = {
                    "weight": self.weights.get(layer, 0),
                    "accuracy_all": 0,
                    "accuracy_recent_20": 0,
                    "sample_size": 0,
                    "correct_rate": 0,
                }
        return result

    def get_adaptation_history(self, limit: int = 20) -> list[dict]:
        return self.adaptation_history[-limit:]

    def _save_state(self):
        """Persist weights to disk."""
        try:
            state = {
                "weights": self.weights,
                "layer_accuracy": self.layer_accuracy,
                "adaptation_count": len(self.adaptation_history),
            }
            self._save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._save_path, "w") as f:
                json.dump(state, f)
        except Exception as e:
            logger.warning("Failed to save adaptive weights: %s", e)

    def _load_state(self):
        """Load persisted weights."""
        try:
            if self._save_path.exists():
                with open(self._save_path, "r") as f:
                    state = json.load(f)
                self.weights = state.get("weights", self.weights)
                self.layer_accuracy = state.get("layer_accuracy", self.layer_accuracy)
                logger.info("Loaded adaptive weights from disk: %s", self.weights)
        except Exception as e:
            logger.warning("Failed to load adaptive weights: %s", e)
