"""
Paper Trading Engine.
Simulates real trading with ₹10,000 virtual capital.
Tracks positions, P&L, brokerage, slippage — everything.
"""

import logging
import json
from datetime import datetime
from typing import Optional

import config

logger = logging.getLogger(__name__)


class Position:
    """A single open position in the paper portfolio."""

    def __init__(self, trade_id, symbol, option_type, strike, side,
                 qty, lot_size, entry_price, strategy, entry_time=None,
                 stoploss_pct=30, signal_data=None):
        self.trade_id = trade_id
        self.symbol = symbol
        self.option_type = option_type
        self.strike = strike
        self.side = side
        self.qty = qty
        self.lot_size = lot_size
        self.entry_price = max(entry_price, config.MIN_OPTION_PREMIUM)
        self.current_price = self.entry_price
        self.strategy = strategy
        self.entry_time = entry_time or datetime.now()
        self.stoploss_pct = stoploss_pct
        self.signal_data = signal_data or {}

        # Risk management
        if side == "BUY":
            self.stoploss_price = self.entry_price * (1 - stoploss_pct / 100)
        else:
            self.stoploss_price = self.entry_price * (1 + stoploss_pct / 100)
        self.trailing_sl = self.stoploss_price
        self.highest_price = self.entry_price
        self.max_pnl_seen = 0

        # Expiry & theta tracking
        self.days_to_expiry = config.DEFAULT_DAYS_TO_EXPIRY
        self.entry_dte = config.DEFAULT_DAYS_TO_EXPIRY

        # Greeks snapshot
        self.entry_greeks = {}
        self.current_greeks = {}

    @property
    def unrealized_pnl(self):
        multiplier = self.qty * self.lot_size
        if self.side == "BUY":
            return (self.current_price - self.entry_price) * multiplier
        else:
            return (self.entry_price - self.current_price) * multiplier

    @property
    def pnl_pct(self):
        if self.entry_price <= 0:
            return 0
        if self.side == "BUY":
            return (self.current_price - self.entry_price) / self.entry_price * 100
        return (self.entry_price - self.current_price) / self.entry_price * 100

    @property
    def days_held(self):
        delta = datetime.now() - self.entry_time
        return delta.total_seconds() / 86400

    def update_price(self, price: float):
        """Update current price with theta decay simulation."""
        # Apply theta decay to the incoming price
        # Options lose time value — simulate this even in live paper trading
        if self.days_held > 0 and self.days_to_expiry > 0:
            remaining_dte = max(0.1, self.entry_dte - self.days_held)
            self.days_to_expiry = remaining_dte

            # If near expiry, accelerate decay
            if remaining_dte < 2:
                decay_mult = 0.7  # Rapid decay in last 2 days
                price = max(config.MIN_OPTION_PREMIUM, price * decay_mult)

        self.current_price = max(config.MIN_OPTION_PREMIUM, price)

        if self.current_price > self.highest_price:
            self.highest_price = self.current_price
            # Update trailing stop for BUY positions (trail at half stoploss width)
            if self.side == "BUY":
                trail_pct = self.stoploss_pct / 2  # Tighter trail than initial stop
                new_sl = self.current_price * (1 - trail_pct / 100)
                self.trailing_sl = max(self.trailing_sl, new_sl)

        # Track max P&L for trailing profit protection
        current_pnl = self.unrealized_pnl
        if current_pnl > self.max_pnl_seen:
            self.max_pnl_seen = current_pnl

    def should_exit(self) -> tuple[bool, str]:
        """Check if position should be exited — institutional-grade exits."""
        # 1. Stoploss (hard stop + trailing)
        if self.side == "BUY":
            if self.current_price <= self.trailing_sl:
                return True, "TRAILING_STOPLOSS"
            if self.current_price <= self.stoploss_price:
                return True, "STOPLOSS"
        else:
            if self.current_price >= self.stoploss_price:
                return True, "STOPLOSS"

        # 2. Quick profit target for SELL (credit strategies)
        # Take 25% of premium — high win rate, consistent profits
        if self.side == "SELL" and self.pnl_pct >= 25:
            return True, "TARGET_REACHED"

        # 3. Debit target: 40% return on premium (realistic for weeklies)
        if self.side == "BUY" and self.pnl_pct >= 40:
            return True, "TARGET_REACHED"

        # 4. Trailing profit: activate at 10% gain, lock in 60% of peak
        notional = self.entry_price * self.lot_size
        if self.max_pnl_seen > notional * 0.10:
            if self.unrealized_pnl < self.max_pnl_seen * 0.60:
                return True, "TRAILING_PROFIT_STOP"

        # 5. Time-based: credit strategies hold longer (theta is friend)
        if self.side == "SELL":
            if self.days_held > self.entry_dte * 0.65:
                return True, "TIME_DECAY_EXIT"
        else:
            # Debit: exit early, theta is the enemy
            if self.days_held > self.entry_dte * 0.40:
                return True, "TIME_DECAY_EXIT"

        # 6. Near-worthless: close if premium dropped below Rs.1
        if self.side == "BUY" and self.current_price < 1.0:
            return True, "NEAR_WORTHLESS"

        # 7. Breakeven exit: stuck too long with no movement
        if self.days_held > self.entry_dte * 0.30:
            if abs(self.pnl_pct) < 3:  # within 3% of breakeven
                return True, "BREAKEVEN_EXIT"

        return False, ""

    def to_dict(self):
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "option_type": self.option_type,
            "strike": self.strike,
            "side": self.side,
            "qty": self.qty,
            "lot_size": self.lot_size,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "pnl_pct": round(self.pnl_pct, 2),
            "strategy": self.strategy,
            "entry_time": self.entry_time.isoformat(),
            "stoploss": round(self.trailing_sl, 2),
            "days_held": round(self.days_held, 1),
            "days_to_expiry": round(self.days_to_expiry, 1),
        }


class PaperTradingEngine:
    """Paper trading simulation engine with full P&L tracking."""

    def __init__(self, initial_capital: float = None):
        self.initial_capital = initial_capital or config.PAPER_TRADING_CAPITAL
        self.available_capital = self.initial_capital
        self.used_margin = 0
        self.positions: list[Position] = []
        self.closed_trades: list[dict] = []
        self.trade_counter = 0
        self.daily_pnl = 0
        self.daily_trades = 0
        self.daily_wins = 0
        self.daily_losses = 0
        self._trading_halted = False

    @property
    def total_unrealized_pnl(self):
        return sum(p.unrealized_pnl for p in self.positions)

    @property
    def total_realized_pnl(self):
        return sum(t.get("net_pnl", 0) for t in self.closed_trades)

    @property
    def total_pnl(self):
        return self.total_realized_pnl + self.total_unrealized_pnl

    @property
    def equity(self):
        return self.initial_capital + self.total_pnl

    @property
    def win_rate(self):
        wins = sum(1 for t in self.closed_trades if t.get("net_pnl", 0) > 0)
        total = len(self.closed_trades)
        return round(wins / total * 100, 1) if total > 0 else 0

    def can_trade(self) -> tuple[bool, str]:
        """Check if we can take a new trade."""
        if self._trading_halted:
            return False, "Trading halted for the day"
        if len(self.positions) >= config.MAX_SIMULTANEOUS_POSITIONS:
            return False, f"Max {config.MAX_SIMULTANEOUS_POSITIONS} positions reached"
        if self.daily_pnl <= -config.MAX_DAILY_LOSS:
            self._trading_halted = True
            return False, f"Daily loss limit hit: ₹{config.MAX_DAILY_LOSS}"
        if self.available_capital <= 0:
            return False, "No capital available"
        return True, "OK"

    def open_position(self, strategy_order) -> Optional[list[Position]]:
        """Open position(s) from a strategy order."""
        can, reason = self.can_trade()
        if not can:
            logger.warning("Cannot trade: %s", reason)
            return None

        positions = []
        total_cost = 0

        for leg in strategy_order.legs:
            self.trade_counter += 1

            # Apply slippage
            slippage = leg.premium * config.SLIPPAGE_PCT / 100
            if leg.side == "BUY":
                entry_price = leg.premium + slippage
                cost = entry_price * strategy_order.__dict__.get("lot_size", 25)
            else:
                entry_price = leg.premium - slippage
                cost = entry_price * strategy_order.__dict__.get("lot_size", 25) * 0.2  # Margin

            total_cost += cost

            pos = Position(
                trade_id=self.trade_counter,
                symbol=strategy_order.symbol,
                option_type=leg.option_type,
                strike=leg.strike,
                side=leg.side,
                qty=leg.qty,
                lot_size=strategy_order.__dict__.get("lot_size", 25),
                entry_price=entry_price,
                strategy=strategy_order.strategy_name,
                stoploss_pct=strategy_order.stoploss_pct,
                signal_data={"score": strategy_order.signal_score,
                             "reason": strategy_order.entry_reason},
            )
            positions.append(pos)
            self.positions.append(pos)

        # Deduct capital + brokerage
        brokerage = config.BROKERAGE_PER_ORDER * len(strategy_order.legs)
        self.available_capital -= (total_cost + brokerage)
        self.used_margin += total_cost
        self.daily_trades += 1

        logger.info("Opened %d legs | Strategy: %s | Cost: ₹%.2f",
                     len(positions), strategy_order.strategy_name, total_cost)
        return positions

    def close_position(self, position: Position, current_price: float,
                       reason: str = "MANUAL") -> dict:
        """Close a single position and record the trade."""
        position.update_price(current_price)

        brokerage = config.BROKERAGE_PER_ORDER
        slippage = current_price * config.SLIPPAGE_PCT / 100

        if position.side == "BUY":
            exit_price = current_price - slippage
        else:
            exit_price = current_price + slippage

        gross_pnl = position.unrealized_pnl
        net_pnl = gross_pnl - brokerage

        trade_record = {
            "trade_id": position.trade_id,
            "symbol": position.symbol,
            "option_type": position.option_type,
            "strike": position.strike,
            "side": position.side,
            "qty": position.qty,
            "lot_size": position.lot_size,
            "entry_price": position.entry_price,
            "exit_price": exit_price,
            "gross_pnl": round(gross_pnl, 2),
            "brokerage": brokerage,
            "net_pnl": round(net_pnl, 2),
            "pnl_pct": round(position.pnl_pct, 2),
            "strategy": position.strategy,
            "exit_reason": reason,
            "entry_time": position.entry_time.isoformat(),
            "exit_time": datetime.now().isoformat(),
            "signal_data": position.signal_data,
        }

        self.closed_trades.append(trade_record)
        self.positions.remove(position)

        # Return capital
        margin = position.entry_price * position.lot_size * position.qty
        if position.side == "SELL":
            margin *= 0.2
        self.available_capital += margin + net_pnl
        self.used_margin -= margin
        self.daily_pnl += net_pnl

        if net_pnl > 0:
            self.daily_wins += 1
        else:
            self.daily_losses += 1

        logger.info("Closed trade #%d | P&L: ₹%.2f | Reason: %s",
                     position.trade_id, net_pnl, reason)
        return trade_record

    def check_exits(self, price_map: dict) -> list[dict]:
        """Check all positions for exit conditions. price_map: {strike: price}"""
        closed = []
        for pos in list(self.positions):
            price = price_map.get(pos.strike, pos.current_price)
            pos.update_price(price)
            should_exit, reason = pos.should_exit()
            if should_exit:
                record = self.close_position(pos, price, reason)
                closed.append(record)
        return closed

    def reset_daily(self):
        """Reset daily counters (call at market open)."""
        self.daily_pnl = 0
        self.daily_trades = 0
        self.daily_wins = 0
        self.daily_losses = 0
        self._trading_halted = False

    def get_portfolio_summary(self) -> dict:
        return {
            "initial_capital": self.initial_capital,
            "available_capital": round(self.available_capital, 2),
            "used_margin": round(self.used_margin, 2),
            "equity": round(self.equity, 2),
            "unrealized_pnl": round(self.total_unrealized_pnl, 2),
            "realized_pnl": round(self.total_realized_pnl, 2),
            "total_pnl": round(self.total_pnl, 2),
            "total_pnl_pct": round(self.total_pnl / max(self.initial_capital, 1) * 100, 2),
            "open_positions": len(self.positions),
            "total_closed_trades": len(self.closed_trades),
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_trades": self.daily_trades,
            "win_rate": self.win_rate,
            "positions": [p.to_dict() for p in self.positions],
        }
