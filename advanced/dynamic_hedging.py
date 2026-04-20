"""
Greeks-Based Dynamic Hedging — Auto-hedge portfolio risk.
Monitors portfolio Delta/Gamma exposure and suggests/executes
hedge trades to maintain Delta neutrality.
"""

import logging
from datetime import datetime
from typing import Optional

import numpy as np

import config
from analysis.greeks import BlackScholes

logger = logging.getLogger(__name__)


class DynamicHedger:
    """
    Greeks-based dynamic hedging system.
    
    Monitors:
    1. Portfolio Delta → If too directional, hedge with opposite
    2. Portfolio Gamma → If too high, reduce with spreads
    3. Portfolio Theta → Track daily time decay
    4. Portfolio Vega → Track volatility exposure
    
    When exposure exceeds thresholds, automatically suggests
    or executes hedge trades.
    """

    DELTA_THRESHOLD = 30      # Hedge when abs(delta) > 30
    GAMMA_THRESHOLD = 3       # Alert when abs(gamma) > 3
    VEGA_THRESHOLD = 100      # Alert when abs(vega) > 100
    HEDGE_RATIO = 0.7         # Hedge 70% of excess exposure

    def __init__(self):
        self.bs = BlackScholes()
        self.hedge_history: list[dict] = []

    def calculate_portfolio_greeks(self, positions: list,
                                   underlying_price: float,
                                   risk_free_rate: float = 0.065,
                                   time_to_expiry: float = 7/365) -> dict:
        """
        Calculate aggregate portfolio Greeks from all open positions.
        
        Each position should have:
            - option_type: CE/PE
            - strike: float
            - side: BUY/SELL
            - qty: int
            - lot_size: int
            - current_price: float
        """
        portfolio = {
            "delta": 0, "gamma": 0, "theta": 0, "vega": 0, "rho": 0,
            "positions": [],
        }

        for pos in positions:
            try:
                opt_type = getattr(pos, "option_type", "CE")
                strike = getattr(pos, "strike", underlying_price)
                side = getattr(pos, "side", "BUY")
                qty = getattr(pos, "qty", 1)
                lot_size = getattr(pos, "lot_size", 25)
                multiplier = qty * lot_size * (1 if side == "BUY" else -1)

                # Calculate IV from current price
                current_price = getattr(pos, "current_price", 100)
                try:
                    iv = self.bs.implied_volatility(
                        current_price, underlying_price, strike,
                        time_to_expiry, risk_free_rate,
                        "call" if opt_type == "CE" else "put"
                    )
                except Exception:
                    iv = 0.2  # Default 20% IV

                # Calculate Greeks
                greeks = self.bs.calculate(
                    underlying_price, strike, time_to_expiry,
                    risk_free_rate, iv,
                    "call" if opt_type == "CE" else "put"
                )

                pos_greeks = {
                    "trade_id": getattr(pos, "trade_id", 0),
                    "type": opt_type,
                    "strike": strike,
                    "side": side,
                    "delta": round(greeks["delta"] * multiplier, 4),
                    "gamma": round(greeks["gamma"] * multiplier, 6),
                    "theta": round(greeks["theta"] * multiplier, 2),
                    "vega": round(greeks["vega"] * multiplier, 2),
                    "iv": round(iv * 100, 2),
                }

                portfolio["delta"] += pos_greeks["delta"]
                portfolio["gamma"] += pos_greeks["gamma"]
                portfolio["theta"] += pos_greeks["theta"]
                portfolio["vega"] += pos_greeks["vega"]
                portfolio["positions"].append(pos_greeks)

            except Exception as e:
                logger.debug("Greeks calc failed for position: %s", e)

        # Round totals
        for key in ["delta", "gamma", "theta", "vega"]:
            portfolio[key] = round(portfolio[key], 4)

        return portfolio

    def check_hedge_needed(self, portfolio_greeks: dict) -> dict:
        """
        Check if the portfolio needs hedging.
        Returns hedge recommendations.
        """
        delta = portfolio_greeks.get("delta", 0)
        gamma = portfolio_greeks.get("gamma", 0)
        vega = portfolio_greeks.get("vega", 0)
        theta = portfolio_greeks.get("theta", 0)

        recommendations = []
        hedge_urgency = "NONE"

        # ── Delta Hedge ───────────────────────────────────
        if abs(delta) > self.DELTA_THRESHOLD:
            excess_delta = delta - np.sign(delta) * self.DELTA_THRESHOLD
            hedge_delta = -excess_delta * self.HEDGE_RATIO

            if delta > 0:
                # Long Delta → buy ATM puts or sell ATM calls
                recommendations.append({
                    "type": "DELTA_HEDGE",
                    "urgency": "HIGH",
                    "current_delta": round(delta, 2),
                    "target_delta": round(delta + hedge_delta, 2),
                    "action": f"Buy {abs(int(hedge_delta/0.5))} ATM PE or Sell {abs(int(hedge_delta/0.5))} ATM CE",
                    "reason": f"Portfolio too long (Delta={delta:.1f}). Needs {hedge_delta:.1f} Delta reduction.",
                })
            else:
                # Short Delta → buy ATM calls or sell ATM puts
                recommendations.append({
                    "type": "DELTA_HEDGE",
                    "urgency": "HIGH",
                    "current_delta": round(delta, 2),
                    "target_delta": round(delta + hedge_delta, 2),
                    "action": f"Buy {abs(int(hedge_delta/0.5))} ATM CE or Sell {abs(int(hedge_delta/0.5))} ATM PE",
                    "reason": f"Portfolio too short (Delta={delta:.1f}). Needs +{abs(hedge_delta):.1f} Delta.",
                })

            hedge_urgency = "HIGH"

        # ── Gamma Alert ───────────────────────────────────
        if abs(gamma) > self.GAMMA_THRESHOLD:
            recommendations.append({
                "type": "GAMMA_ALERT",
                "urgency": "MEDIUM",
                "current_gamma": round(gamma, 4),
                "action": "Reduce gamma by closing short-dated options or adding spreads",
                "reason": f"High Gamma exposure ({gamma:.4f}). P&L will swing rapidly.",
            })
            if hedge_urgency == "NONE":
                hedge_urgency = "MEDIUM"

        # ── Vega Alert ────────────────────────────────────
        if abs(vega) > self.VEGA_THRESHOLD:
            direction = "long" if vega > 0 else "short"
            recommendations.append({
                "type": "VEGA_ALERT",
                "urgency": "LOW",
                "current_vega": round(vega, 2),
                "action": f"You are {direction} volatility. {'Sell' if vega > 0 else 'Buy'} options to reduce Vega.",
                "reason": f"Vega={vega:.1f}. A 1% IV change will impact P&L by Rs.{abs(vega):.0f}.",
            })

        # ── Theta Summary ────────────────────────────────
        daily_decay = theta
        recommendations.append({
            "type": "THETA_INFO",
            "urgency": "INFO",
            "daily_decay": round(daily_decay, 2),
            "message": f"Daily time decay: Rs.{daily_decay:+.2f}",
        })

        result = {
            "hedge_needed": hedge_urgency != "NONE",
            "urgency": hedge_urgency,
            "recommendations": recommendations,
            "portfolio_greeks": {
                "delta": round(delta, 2),
                "gamma": round(gamma, 4),
                "theta": round(theta, 2),
                "vega": round(vega, 2),
            },
            "risk_level": "HIGH" if hedge_urgency == "HIGH" else "MEDIUM" if hedge_urgency == "MEDIUM" else "LOW",
            "timestamp": datetime.now().isoformat(),
        }

        if hedge_urgency != "NONE":
            self.hedge_history.append(result)
            if len(self.hedge_history) > 100:
                self.hedge_history = self.hedge_history[-100:]

        return result

    def generate_hedge_order(self, portfolio_greeks: dict,
                             chain_df, underlying_price: float,
                             lot_size: int = 25) -> Optional[dict]:
        """
        Generate an actual hedge order to flatten Delta.
        Returns a strategy order dict that can be passed to the paper engine.
        """
        delta = portfolio_greeks.get("delta", 0)

        if abs(delta) <= self.DELTA_THRESHOLD:
            return None  # No hedge needed

        # Find ATM strike
        if chain_df is not None and not chain_df.empty:
            strikes = chain_df["strike"].unique()
            atm = float(min(strikes, key=lambda s: abs(s - underlying_price)))
        else:
            atm = round(underlying_price / 50) * 50  # Round to nearest 50

        if delta > 0:
            # Buy puts to reduce long delta
            lots_needed = max(1, abs(int(delta * self.HEDGE_RATIO / 0.5)))
            hedge = {
                "strategy_name": "DELTA_HEDGE",
                "symbol": "NIFTY",
                "legs": [{"option_type": "PE", "strike": atm, "side": "BUY",
                          "qty": lots_needed, "premium": 100}],
                "hedge_type": "DELTA_REDUCTION",
                "target_delta_change": round(-delta * self.HEDGE_RATIO, 2),
            }
        else:
            # Buy calls to increase delta
            lots_needed = max(1, abs(int(delta * self.HEDGE_RATIO / 0.5)))
            hedge = {
                "strategy_name": "DELTA_HEDGE",
                "symbol": "NIFTY",
                "legs": [{"option_type": "CE", "strike": atm, "side": "BUY",
                          "qty": lots_needed, "premium": 100}],
                "hedge_type": "DELTA_ADDITION",
                "target_delta_change": round(-delta * self.HEDGE_RATIO, 2),
            }

        return hedge

    def get_hedge_history(self, limit: int = 20) -> list[dict]:
        return self.hedge_history[-limit:]
