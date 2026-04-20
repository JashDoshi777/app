"""
Black-Scholes-Merton Greeks Calculator.
Computes theoretical option prices, all first-order Greeks,
and implied volatility via Newton-Raphson.
"""

import math
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.stats import norm

import config

logger = logging.getLogger(__name__)


@dataclass
class GreeksResult:
    """Container for all Greeks of a single option."""
    price: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0
    iv: float = 0.0
    intrinsic: float = 0.0
    extrinsic: float = 0.0
    moneyness: str = "ATM"  # ITM, ATM, OTM


class BlackScholes:
    """
    Black-Scholes-Merton option pricing model.
    Designed for European-style options (NIFTY/BANKNIFTY are European).
    """

    def __init__(self, risk_free_rate: float = None):
        self.r = risk_free_rate or config.RISK_FREE_RATE

    def price(self, S: float, K: float, T: float, sigma: float,
              option_type: str = "CE") -> float:
        """
        Calculate theoretical option price.
        S: Spot price, K: Strike, T: Time to expiry (years),
        sigma: Volatility (decimal), option_type: CE or PE
        """
        if T <= 0 or sigma <= 0:
            return max(0, (S - K) if option_type == "CE" else (K - S))

        d1 = self._d1(S, K, T, sigma)
        d2 = d1 - sigma * math.sqrt(T)

        if option_type == "CE":
            return S * norm.cdf(d1) - K * math.exp(-self.r * T) * norm.cdf(d2)
        else:
            return K * math.exp(-self.r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)

    def greeks(self, S: float, K: float, T: float, sigma: float,
               option_type: str = "CE", market_price: float = 0) -> GreeksResult:
        """
        Calculate all Greeks for an option.
        If market_price provided and sigma=0, will solve for IV first.
        """
        if sigma <= 0 and market_price > 0:
            sigma = self.implied_volatility(S, K, T, market_price, option_type)

        if T <= 0 or sigma <= 0:
            intrinsic = max(0, (S - K) if option_type == "CE" else (K - S))
            return GreeksResult(
                price=intrinsic, intrinsic=intrinsic,
                moneyness=self._moneyness(S, K, option_type),
            )

        d1 = self._d1(S, K, T, sigma)
        d2 = d1 - sigma * math.sqrt(T)
        sqrt_T = math.sqrt(T)
        exp_rT = math.exp(-self.r * T)
        n_d1 = norm.pdf(d1)

        theo_price = self.price(S, K, T, sigma, option_type)

        # Delta
        delta = norm.cdf(d1) if option_type == "CE" else norm.cdf(d1) - 1

        # Gamma (same for CE and PE)
        gamma = n_d1 / (S * sigma * sqrt_T)

        # Theta (per day)
        common_theta = -(S * n_d1 * sigma) / (2 * sqrt_T)
        if option_type == "CE":
            theta = common_theta - self.r * K * exp_rT * norm.cdf(d2)
        else:
            theta = common_theta + self.r * K * exp_rT * norm.cdf(-d2)
        theta /= config.TRADING_DAYS_PER_YEAR  # Convert to per-day

        # Vega (per 1% move in volatility)
        vega = S * sqrt_T * n_d1 / 100

        # Rho (per 1% move in rate)
        if option_type == "CE":
            rho = K * T * exp_rT * norm.cdf(d2) / 100
        else:
            rho = -K * T * exp_rT * norm.cdf(-d2) / 100

        intrinsic = max(0, (S - K) if option_type == "CE" else (K - S))
        extrinsic = max(0, theo_price - intrinsic)

        return GreeksResult(
            price=round(theo_price, 2),
            delta=round(delta, 4),
            gamma=round(gamma, 6),
            theta=round(theta, 2),
            vega=round(vega, 2),
            rho=round(rho, 4),
            iv=round(sigma * 100, 2),
            intrinsic=round(intrinsic, 2),
            extrinsic=round(extrinsic, 2),
            moneyness=self._moneyness(S, K, option_type),
        )

    def implied_volatility(
        self, S: float, K: float, T: float,
        market_price: float, option_type: str = "CE",
        max_iter: int = 100, tol: float = 1e-6,
    ) -> float:
        """
        Solve for Implied Volatility using Newton-Raphson method.
        Returns IV as a decimal (e.g. 0.15 = 15%).
        """
        if T <= 0 or market_price <= 0:
            return 0.0

        sigma = 0.20  # Initial guess: 20%

        for _ in range(max_iter):
            try:
                price = self.price(S, K, T, sigma, option_type)
                d1 = self._d1(S, K, T, sigma)
                vega = S * math.sqrt(T) * norm.pdf(d1)

                if abs(vega) < 1e-10:
                    break

                sigma -= (price - market_price) / vega

                if abs(price - market_price) < tol:
                    return max(0.001, sigma)

                sigma = max(0.001, min(5.0, sigma))  # Clamp
            except (OverflowError, ValueError):
                break

        return max(0.001, sigma)

    def _d1(self, S: float, K: float, T: float, sigma: float) -> float:
        return (math.log(S / K) + (self.r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))

    def _moneyness(self, S: float, K: float, option_type: str) -> str:
        pct = abs(S - K) / S * 100
        if pct < 0.5:
            return "ATM"
        if option_type == "CE":
            return "ITM" if S > K else "OTM"
        return "ITM" if K > S else "OTM"

    def portfolio_greeks(self, positions: list[dict]) -> dict:
        """
        Calculate aggregate Greeks for a portfolio of options.
        Each position dict: {S, K, T, sigma, option_type, qty, lot_size}
        """
        total = {"delta": 0, "gamma": 0, "theta": 0, "vega": 0, "rho": 0}
        for pos in positions:
            g = self.greeks(
                pos["S"], pos["K"], pos["T"], pos["sigma"], pos["option_type"]
            )
            multiplier = pos.get("qty", 1) * pos.get("lot_size", 1)
            total["delta"] += g.delta * multiplier
            total["gamma"] += g.gamma * multiplier
            total["theta"] += g.theta * multiplier
            total["vega"] += g.vega * multiplier
            total["rho"] += g.rho * multiplier

        return {k: round(v, 4) for k, v in total.items()}

    def time_to_expiry(self, expiry_date: str) -> float:
        """Convert expiry date string to years remaining."""
        from datetime import datetime
        try:
            exp = datetime.strptime(expiry_date, "%Y-%m-%d")
            delta = (exp - datetime.now()).total_seconds()
            return max(0, delta / (365.25 * 24 * 3600))
        except Exception:
            return 0.0
