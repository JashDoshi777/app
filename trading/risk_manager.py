"""
Risk Manager — Position-level and portfolio-level risk controls.
Enforces stop-losses, position sizing, drawdown limits, and Greeks-based risk.
"""

import logging
from datetime import datetime
from typing import Optional

import config

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Multi-layer risk management system.
    
    Position-Level:
      - Max loss per trade: configurable % of capital
      - Stop-loss: Greeks-based (Delta-adjusted) or fixed %
      - Trailing stop-loss on profitable positions
    
    Portfolio-Level:
      - Max simultaneous positions
      - Max daily drawdown → halt trading
      - Portfolio Delta neutrality monitoring
      - Gamma exposure alerts
    """

    def __init__(self, capital: float = None):
        self.capital = capital or config.PAPER_TRADING_CAPITAL
        self.max_risk_pct = config.MAX_RISK_PER_TRADE_PCT
        self.max_daily_dd_pct = config.MAX_DAILY_DRAWDOWN_PCT
        self.max_positions = config.MAX_SIMULTANEOUS_POSITIONS
        self.daily_pnl = 0.0
        self.peak_equity = self.capital
        self._halted = False
        self._risk_events: list[dict] = []

    # ═══════════════════════════════════════════════════════
    #  PRE-TRADE VALIDATION
    # ═══════════════════════════════════════════════════════

    def validate_entry(self, order, current_positions: list, available_capital: float) -> tuple[bool, str]:
        """
        Validate whether a new trade should be allowed.
        Returns (allowed: bool, reason: str).
        """
        # Check halt
        if self._halted:
            return False, "Trading halted — daily drawdown limit reached"

        # Check position count
        if len(current_positions) >= self.max_positions:
            return False, f"Max positions ({self.max_positions}) reached"

        # Check capital
        estimated_cost = self._estimate_cost(order)
        if estimated_cost > available_capital * (self.max_risk_pct / 100):
            return False, f"Cost {estimated_cost:.2f} exceeds {self.max_risk_pct}% risk limit"

        if estimated_cost > available_capital:
            return False, "Insufficient capital"

        # Check daily P&L
        max_daily_loss = self.capital * (self.max_daily_dd_pct / 100)
        if self.daily_pnl <= -max_daily_loss:
            self._halted = True
            self._log_event("DAILY_HALT", f"Daily loss {self.daily_pnl:.2f} >= limit {max_daily_loss:.2f}")
            return False, f"Daily loss limit hit: {self.daily_pnl:.2f}"

        # Check if same strategy already open
        for pos in current_positions:
            if pos.strategy == getattr(order, "strategy_name", ""):
                if pos.symbol == getattr(order, "symbol", ""):
                    return False, f"Already have an open {pos.strategy} on {pos.symbol}"

        return True, "APPROVED"

    def _estimate_cost(self, order) -> float:
        """Estimate the capital required for an order."""
        total = 0
        lot_size = getattr(order, "lot_size", 25) if hasattr(order, "lot_size") else order.__dict__.get("lot_size", 25)
        for leg in getattr(order, "legs", []):
            if leg.side == "BUY":
                total += leg.premium * lot_size * leg.qty
            else:
                # Margin for sell = ~20% of notional
                total += leg.premium * lot_size * leg.qty * 0.2
        return total

    # ═══════════════════════════════════════════════════════
    #  STOP-LOSS CALCULATION
    # ═══════════════════════════════════════════════════════

    def calculate_stoploss(self, entry_price: float, side: str, option_type: str,
                           delta: float = 0, atr: float = 0) -> float:
        """
        Calculate intelligent stop-loss price.
        Uses a combination of fixed %, ATR-based, and Delta-adjusted methods.
        """
        # Method 1: Fixed percentage (30%)
        fixed_sl_pct = 30
        if side == "BUY":
            fixed_sl = entry_price * (1 - fixed_sl_pct / 100)
        else:
            fixed_sl = entry_price * (1 + fixed_sl_pct / 100)

        # Method 2: ATR-based (2x ATR)
        if atr > 0:
            atr_sl = entry_price - (2 * atr) if side == "BUY" else entry_price + (2 * atr)
        else:
            atr_sl = fixed_sl

        # Method 3: Delta-adjusted
        # Higher Delta = tighter stop (more sensitive to price moves)
        if abs(delta) > 0:
            delta_factor = 1 + (1 - abs(delta)) * 0.5  # OTM = wider stop
            delta_sl_pct = fixed_sl_pct * delta_factor
            if side == "BUY":
                delta_sl = entry_price * (1 - delta_sl_pct / 100)
            else:
                delta_sl = entry_price * (1 + delta_sl_pct / 100)
        else:
            delta_sl = fixed_sl

        # Use the most conservative (closest to entry)
        if side == "BUY":
            return max(fixed_sl, atr_sl, delta_sl, 0.05)  # At least 0.05
        else:
            return min(fixed_sl, atr_sl, delta_sl)

    def calculate_trailing_stop(self, current_price: float, highest_price: float,
                                entry_price: float, side: str) -> float:
        """Update trailing stop-loss based on price movement."""
        if side == "BUY":
            profit_pct = (highest_price - entry_price) / entry_price * 100

            # Tighten trailing stop as profit increases
            if profit_pct > 50:
                trail_pct = 10  # Very tight at 50%+ profit
            elif profit_pct > 30:
                trail_pct = 15
            elif profit_pct > 15:
                trail_pct = 20
            else:
                trail_pct = 25

            return highest_price * (1 - trail_pct / 100)
        else:
            return current_price * 1.25  # Fixed 25% for short positions

    # ═══════════════════════════════════════════════════════
    #  POSITION SIZING
    # ═══════════════════════════════════════════════════════

    def calculate_position_size(self, available_capital: float, premium: float,
                                lot_size: int, confidence: float = 50) -> int:
        """
        Calculate number of lots based on:
        - Available capital
        - Risk per trade limit
        - Signal confidence (higher confidence = larger position)
        """
        max_risk = available_capital * (self.max_risk_pct / 100)
        cost_per_lot = premium * lot_size

        if cost_per_lot <= 0:
            return 0

        max_lots = int(max_risk / cost_per_lot)

        # Scale by confidence (50% confidence = half the max lots)
        confidence_factor = min(1.0, confidence / 100)
        scaled_lots = max(1, int(max_lots * confidence_factor))

        return min(scaled_lots, 3)  # Hard cap at 3 lots

    # ═══════════════════════════════════════════════════════
    #  PORTFOLIO RISK MONITORING
    # ═══════════════════════════════════════════════════════

    def assess_portfolio_risk(self, positions: list, portfolio_greeks: dict = None) -> dict:
        """Assess overall portfolio risk and return warnings."""
        warnings = []
        risk_level = "LOW"

        # Position count
        if len(positions) >= self.max_positions:
            warnings.append("Maximum positions reached")
            risk_level = "MEDIUM"

        # Daily P&L
        daily_loss_pct = abs(self.daily_pnl) / self.capital * 100 if self.daily_pnl < 0 else 0
        if daily_loss_pct > self.max_daily_dd_pct * 0.7:
            warnings.append(f"Approaching daily loss limit ({daily_loss_pct:.1f}%)")
            risk_level = "HIGH"

        # Greeks risk
        if portfolio_greeks:
            # High Delta exposure
            total_delta = abs(portfolio_greeks.get("delta", 0))
            if total_delta > 50:
                warnings.append(f"High Delta exposure: {total_delta:.2f}")
                risk_level = "HIGH"

            # Negative Theta check (for net long premium positions)
            total_theta = portfolio_greeks.get("theta", 0)
            if total_theta < -50:
                warnings.append(f"High Theta decay: {total_theta:.2f}/day")

            # Gamma risk
            total_gamma = abs(portfolio_greeks.get("gamma", 0))
            if total_gamma > 5:
                warnings.append(f"High Gamma exposure: {total_gamma:.4f}")

        # Concentration risk
        symbols = [p.symbol for p in positions]
        if len(set(symbols)) == 1 and len(positions) > 1:
            warnings.append("All positions on single underlying")

        return {
            "risk_level": risk_level,
            "warnings": warnings,
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_pnl_pct": round(daily_loss_pct, 2),
            "halted": self._halted,
            "positions_used": len(positions),
            "positions_max": self.max_positions,
        }

    def update_daily_pnl(self, pnl: float):
        """Update daily P&L tracker."""
        self.daily_pnl += pnl
        max_loss = self.capital * (self.max_daily_dd_pct / 100)
        if self.daily_pnl <= -max_loss:
            self._halted = True
            self._log_event("DAILY_HALT", f"Daily loss limit reached: {self.daily_pnl:.2f}")

    def reset_daily(self):
        """Reset at start of trading day."""
        self.daily_pnl = 0
        self._halted = False
        logger.info("Risk manager daily reset.")

    def _log_event(self, event_type: str, message: str):
        self._risk_events.append({
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            "message": message,
        })
        logger.warning("[RISK] %s: %s", event_type, message)

    def get_risk_events(self, limit: int = 50) -> list[dict]:
        return self._risk_events[-limit:]
