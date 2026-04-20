"""
Order Manager — Full order lifecycle management.
Signal → Validation → Risk Check → Execute → Monitor → Exit

Tracks every order state transition and logs to database.
"""

import logging
from datetime import datetime
from enum import Enum
from typing import Optional

import config

logger = logging.getLogger(__name__)


class OrderState(str, Enum):
    PENDING = "PENDING"
    VALIDATED = "VALIDATED"
    RISK_APPROVED = "RISK_APPROVED"
    RISK_REJECTED = "RISK_REJECTED"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ManagedOrder:
    """Wraps a strategy order with lifecycle tracking."""

    def __init__(self, strategy_order, order_id: int = 0):
        self.order_id = order_id
        self.strategy_order = strategy_order
        self.state = OrderState.PENDING
        self.state_history: list[dict] = []
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
        self.fill_price: Optional[float] = None
        self.rejection_reason: str = ""
        self.positions = []

        self._transition(OrderState.PENDING, "Order created")

    def _transition(self, new_state: OrderState, reason: str = ""):
        self.state_history.append({
            "from": self.state,
            "to": new_state,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
        })
        self.state = new_state
        self.updated_at = datetime.now()
        logger.debug("Order #%d: %s → %s (%s)", self.order_id, 
                      self.state_history[-1]["from"], new_state, reason)

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "state": self.state,
            "strategy": self.strategy_order.strategy_name,
            "symbol": self.strategy_order.symbol,
            "legs": len(self.strategy_order.legs),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "rejection_reason": self.rejection_reason,
            "state_history": self.state_history,
        }


class OrderManager:
    """
    Manages the full lifecycle of trading orders.
    Acts as the bridge between signal generation and trade execution.
    """

    def __init__(self, risk_manager, paper_engine):
        self.risk_manager = risk_manager
        self.paper_engine = paper_engine
        self.order_counter = 0
        self.orders: list[ManagedOrder] = []
        self.active_orders: list[ManagedOrder] = []

    def submit_order(self, strategy_order) -> ManagedOrder:
        """
        Submit a strategy order through the full lifecycle.
        Returns the managed order with final state.
        """
        self.order_counter += 1
        managed = ManagedOrder(strategy_order, self.order_counter)

        # ── Step 1: Validate ──────────────────────────────
        valid, reason = self._validate(strategy_order)
        if not valid:
            managed._transition(OrderState.RISK_REJECTED, reason)
            managed.rejection_reason = reason
            self.orders.append(managed)
            logger.warning("Order #%d REJECTED (validation): %s", managed.order_id, reason)
            return managed

        managed._transition(OrderState.VALIDATED, "Passed validation")

        # ── Step 2: Risk Check ────────────────────────────
        approved, risk_reason = self.risk_manager.validate_entry(
            strategy_order,
            self.paper_engine.positions,
            self.paper_engine.available_capital,
        )
        if not approved:
            managed._transition(OrderState.RISK_REJECTED, risk_reason)
            managed.rejection_reason = risk_reason
            self.orders.append(managed)
            logger.warning("Order #%d REJECTED (risk): %s", managed.order_id, risk_reason)
            return managed

        managed._transition(OrderState.RISK_APPROVED, "Passed risk check")

        # ── Step 3: Execute ───────────────────────────────
        managed._transition(OrderState.SUBMITTED, "Submitting to paper engine")

        positions = self.paper_engine.open_position(strategy_order)
        if positions:
            managed._transition(OrderState.FILLED, f"Filled {len(positions)} legs")
            managed.positions = positions
            self.active_orders.append(managed)
            logger.info("Order #%d FILLED: %s | %d legs",
                        managed.order_id, strategy_order.strategy_name, len(positions))
        else:
            managed._transition(OrderState.REJECTED, "Paper engine rejected")
            managed.rejection_reason = "Paper engine rejected — check capital/limits"

        self.orders.append(managed)
        return managed

    def _validate(self, order) -> tuple[bool, str]:
        """Basic order validation."""
        if not hasattr(order, "legs") or not order.legs:
            return False, "No legs in order"

        if not hasattr(order, "strategy_name") or not order.strategy_name:
            return False, "No strategy name"

        for leg in order.legs:
            if leg.strike <= 0:
                return False, f"Invalid strike price: {leg.strike}"
            if leg.premium < config.MIN_OPTION_PREMIUM:
                return False, f"Premium too low: {leg.premium} < min {config.MIN_OPTION_PREMIUM}"
            if leg.option_type not in ("CE", "PE"):
                return False, f"Invalid option type: {leg.option_type}"
            if leg.side not in ("BUY", "SELL"):
                return False, f"Invalid side: {leg.side}"

        return True, "OK"

    def cancel_order(self, order_id: int) -> bool:
        """Cancel a pending order."""
        for order in self.active_orders:
            if order.order_id == order_id and order.state in (OrderState.PENDING, OrderState.VALIDATED):
                order._transition(OrderState.CANCELLED, "Manual cancellation")
                self.active_orders.remove(order)
                return True
        return False

    def get_order_history(self, limit: int = 100) -> list[dict]:
        """Get recent order history."""
        return [o.to_dict() for o in self.orders[-limit:]]

    def get_active_orders(self) -> list[dict]:
        """Get currently active (filled) orders."""
        return [o.to_dict() for o in self.active_orders if o.state == OrderState.FILLED]

    def get_stats(self) -> dict:
        """Order execution statistics."""
        total = len(self.orders)
        filled = sum(1 for o in self.orders if o.state == OrderState.FILLED)
        rejected = sum(1 for o in self.orders if o.state in (OrderState.RISK_REJECTED, OrderState.REJECTED))
        cancelled = sum(1 for o in self.orders if o.state == OrderState.CANCELLED)

        return {
            "total_orders": total,
            "filled": filled,
            "rejected": rejected,
            "cancelled": cancelled,
            "fill_rate": round(filled / max(total, 1) * 100, 1),
            "active": len(self.active_orders),
        }
