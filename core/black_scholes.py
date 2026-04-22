"""
Black-Scholes Option Pricing — IV Solver + Greeks Calculator.

Computes Implied Volatility using Newton-Raphson method,
then derives all Greeks (Delta, Gamma, Theta, Vega) from IV.

Used to fill in IV/Greeks columns that Angel One API doesn't provide.
"""

import math
from typing import Tuple, Optional

# Standard normal CDF and PDF
def _norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def _norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "CE") -> float:
    """
    Black-Scholes option price.
    
    Args:
        S: Underlying price (spot)
        K: Strike price
        T: Time to expiry in years (e.g., 7 days = 7/365)
        r: Risk-free rate (e.g., 0.07 for 7%)
        sigma: Volatility (e.g., 0.15 for 15%)
        option_type: "CE" for call, "PE" for put
    
    Returns:
        Theoretical option price
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    
    if option_type == "CE":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def implied_volatility(
    market_price: float, S: float, K: float, T: float, r: float,
    option_type: str = "CE", max_iter: int = 50, tol: float = 1e-5
) -> float:
    """
    Compute Implied Volatility using Newton-Raphson method.
    
    Returns IV as a decimal (e.g., 0.15 for 15%). Returns 0 if cannot converge.
    """
    if market_price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return 0.0
    
    # Initial guess: use Brenner-Subrahmanyam approximation
    sigma = math.sqrt(2.0 * math.pi / T) * market_price / S
    sigma = max(0.01, min(sigma, 5.0))  # Clamp between 1% and 500%
    
    for _ in range(max_iter):
        price = bs_price(S, K, T, r, sigma, option_type)
        vega = _vega_raw(S, K, T, r, sigma)
        
        if vega < 1e-10:
            break
        
        diff = price - market_price
        if abs(diff) < tol:
            return sigma
        
        sigma -= diff / vega
        sigma = max(0.001, min(sigma, 10.0))  # Keep within bounds
    
    return max(sigma, 0.0)


def _vega_raw(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Raw vega for Newton-Raphson (not annualized)."""
    if T <= 0 or sigma <= 0:
        return 0.0
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    return S * _norm_pdf(d1) * sqrt_T


def greeks(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "CE") -> dict:
    """
    Compute all Greeks for an option.
    
    Returns dict with: delta, gamma, theta (per day), vega (per 1% vol change)
    """
    result = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return result
    
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    
    nd1 = _norm_pdf(d1)
    exp_rT = math.exp(-r * T)
    
    # Delta
    if option_type == "CE":
        result["delta"] = round(_norm_cdf(d1), 4)
    else:
        result["delta"] = round(_norm_cdf(d1) - 1, 4)
    
    # Gamma (same for call and put)
    result["gamma"] = round(nd1 / (S * sigma * sqrt_T), 6)
    
    # Theta (per calendar day, negative = time decay)
    common_theta = -(S * nd1 * sigma) / (2 * sqrt_T)
    if option_type == "CE":
        result["theta"] = round((common_theta - r * K * exp_rT * _norm_cdf(d2)) / 365, 4)
    else:
        result["theta"] = round((common_theta + r * K * exp_rT * _norm_cdf(-d2)) / 365, 4)
    
    # Vega (per 1% change in volatility)
    result["vega"] = round(S * nd1 * sqrt_T / 100, 4)
    
    return result


def compute_iv_and_greeks(
    S: float, K: float, T: float, r: float, market_price: float, option_type: str = "CE"
) -> dict:
    """
    All-in-one: compute IV first, then derive all Greeks.
    
    Args:
        S: Spot price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free rate
        market_price: Current market LTP of the option
        option_type: "CE" or "PE"
    
    Returns:
        dict with iv, delta, gamma, theta, vega
    """
    iv = implied_volatility(market_price, S, K, T, r, option_type)
    
    if iv <= 0:
        return {"iv": 0.0, "delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    
    g = greeks(S, K, T, r, iv, option_type)
    g["iv"] = round(iv * 100, 2)  # Convert to percentage
    
    return g
