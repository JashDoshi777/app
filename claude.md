# Options Trading Engine — Complete Technical Documentation

> **Institutional-grade AI-powered options trading system for Indian F&O markets (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY)**

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Data Pipeline](#2-data-pipeline)
3. [Technical Analysis Engine](#3-technical-analysis-engine)
4. [Options Greeks & Black-Scholes](#4-options-greeks--black-scholes)
5. [Open Interest Analysis](#5-open-interest-analysis)
6. [Sentiment Analysis](#6-sentiment-analysis)
7. [Signal Aggregation & Confluence Scoring](#7-signal-aggregation--confluence-scoring)
8. [Market Regime Detection](#8-market-regime-detection)
9. [Strategy Selection & Execution](#9-strategy-selection--execution)
10. [Risk Management](#10-risk-management)
11. [Paper Trading Engine](#11-paper-trading-engine)
12. [Backtesting Engine](#12-backtesting-engine)
13. [Advanced Modules](#13-advanced-modules)
14. [Web Dashboard](#14-web-dashboard)
15. [Configuration Reference](#15-configuration-reference)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    DATA LAYER (Tier System)                  │
│  Tier 1: Angel One SmartAPI (Real-time WebSocket <50ms)     │
│  Tier 2: yfinance (15-min delayed, free)                    │
│  Tier 3: Mock data (fallback)                               │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│               ANALYSIS LAYER (5 Parallel Engines)           │
│  ┌──────────┐ ┌────────┐ ┌──────┐ ┌──────────┐ ┌────────┐ │
│  │Technical │ │Greeks/ │ │  OI  │ │Sentiment │ │Regime  │ │
│  │Analysis  │ │  IV    │ │      │ │          │ │Detect  │ │
│  │(15 ind.) │ │(BSM)   │ │(PCR) │ │(NLP+RSS) │ │(5 mode)│ │
│  └────┬─────┘ └───┬────┘ └──┬───┘ └────┬─────┘ └───┬────┘ │
│       │           │         │          │            │      │
│  Score: [-1,+1]  [-1,+1]  [-1,+1]   [-1,+1]     [-1,+1]  │
└──────────────────────┬──────────────────────────────────────┘
                       │ Weighted Sum
┌──────────────────────▼──────────────────────────────────────┐
│              SIGNAL AGGREGATOR (Confluence Score)            │
│  Final Score = Σ(layer_score × weight)  →  [-1, +1]        │
│  Direction: STRONG_BUY | BUY | NEUTRAL | SELL | STRONG_SELL │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│              STRATEGY SELECTOR (Rule-Based Regime Mapping)    │
│  Iron Condor │ Vertical Spreads │ Straddle │ Naked Options  │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│              EXECUTION + RISK MANAGEMENT                     │
│  Paper Trading │ Position Sizing │ Stop-Loss │ Auto Exit    │
└─────────────────────────────────────────────────────────────┘
```

### File Structure

| Directory | Purpose |
|-----------|---------|
| `core/` | Market data fetching, option chain, instruments |
| `analysis/` | Technical, Greeks, OI, IV, Sentiment, Signal aggregation |
| `strategy/` | Strategy definitions and AI selector |
| `trading/` | Paper engine, risk manager, order manager, portfolio |
| `backtest/` | Historical backtesting engine, data loader, reports |
| `advanced/` | Adaptive weights, VIX, hedging, walk-forward, UOA |
| `web/` | FastAPI server, API routes, HTML/CSS/JS dashboard |
| `database/` | PostgreSQL (NeonDB) models and async connection |

---

## 2. Data Pipeline

### Tiered Data Architecture

| Tier | Source | Latency | Auth Required | Use Case |
|------|--------|---------|---------------|----------|
| **Tier 1** | Angel One SmartAPI | <50ms (WebSocket) | Yes (API key + TOTP) | Real-time live trading |
| **Tier 2** | yfinance | ~15 min delayed | No | Historical data, backtesting |
| **Tier 3** | Mock | N/A | No | Development fallback |

### Data Flow

1. `MarketDataService` auto-detects available tier at startup
2. `get_ltp(symbol)` → Returns last traded price
3. `get_option_chain(symbol)` → Returns DataFrame with columns:

| Column | Description |
|--------|-------------|
| `strike` | Strike price (e.g., 24500, 24550, 24600) |
| `ce_ltp` | Call option last traded price |
| `pe_ltp` | Put option last traded price |
| `ce_oi` | Call open interest (number of contracts) |
| `pe_oi` | Put open interest |
| `ce_volume` | Call traded volume |
| `pe_volume` | Put traded volume |
| `ce_chg_oi` | Change in call OI from previous session |
| `pe_chg_oi` | Change in put OI from previous session |
| `ce_iv` | Call implied volatility |
| `pe_iv` | Put implied volatility |

---

## 3. Technical Analysis Engine

**File:** `analysis/technical.py`

### 3.1 Indicators Computed

| Indicator | Formula | Parameters | Signal |
|-----------|---------|------------|--------|
| **RSI** | `100 - 100/(1 + RS)` where `RS = avg_gain / avg_loss` | Period: 14 | <30 = Oversold (Bullish), >70 = Overbought (Bearish) |
| **MACD** | `EMA(12) - EMA(26)`, Signal = `EMA(9) of MACD` | Fast:12, Slow:26, Signal:9 | Histogram > 0 = Bullish |
| **Bollinger Bands** | `SMA(20) ± 2 × StdDev(20)` | Period: 20, StdDev: 2 | Near lower = Bullish, Near upper = Bearish |
| **EMA** | `Close × k + EMA_prev × (1-k)` where `k = 2/(period+1)` | 9, 21, 50 | Price > EMA9 > EMA21 = Bullish |
| **SuperTrend** | `HL2 ± multiplier × ATR(10)` | Period: 10, Mult: 3 | Direction +1 = Bullish, -1 = Bearish |
| **Stochastic RSI** | `(RSI - RSI_min) / (RSI_max - RSI_min)` over 14 periods | 14, K:3, D:3 | K crossing above D = Bullish |
| **ATR** | `max(H-L, |H-Prev_C|, |L-Prev_C|)` rolling mean | Period: 14 | Volatility measure |
| **VWAP** | `Σ(TP × Vol) / Σ(Vol)` where `TP = (H+L+C)/3` | Cumulative | Price > VWAP = Bullish |
| **OBV** | `Cumulative sum of (sign(ΔClose) × Volume)` | N/A | Rising = Accumulation |
| **Pivot Points** | `PP = (H+L+C)/3`, `R1 = 2PP-L`, `S1 = 2PP-H` | Previous bar | Support/Resistance levels |
| **Fibonacci** | Retracement at 0.236, 0.382, 0.5, 0.618, 0.786 | Lookback: 50 bars | Key reversal levels |

### 3.2 Candlestick Patterns (20+ Patterns)

#### Single Candle

| Pattern | Detection Rule | Signal |
|---------|---------------|--------|
| **Doji** | `|body| < range × 0.1` | Indecision |
| **Hammer** | `lower_shadow > |body| × 2`, `upper_shadow < |body| × 0.3` | Bullish reversal |
| **Shooting Star** | `upper_shadow > |body| × 2`, bearish body | Bearish reversal |
| **Hanging Man** | Same shape as hammer but in uptrend, bearish body | Bearish reversal |
| **Marubozu** | Shadows < 5% of range, `|body| > avg_body × 1.5` | Strong trend continuation |

#### Double Candle

| Pattern | Detection Rule | Signal |
|---------|---------------|--------|
| **Bullish Engulfing** | Previous red, current green body engulfs previous | Strong bullish |
| **Bearish Engulfing** | Previous green, current red body engulfs previous | Strong bearish |
| **Bullish/Bearish Harami** | Small body inside previous large opposite body (<60%) | Reversal |
| **Piercing Line** | Opens below prior low, closes above prior midpoint | Bullish |
| **Dark Cloud Cover** | Opens above prior high, closes below prior midpoint | Bearish |
| **Tweezer Bottom/Top** | Equal lows/highs within 5% of range | Reversal |

#### Triple Candle

| Pattern | Detection Rule | Signal |
|---------|---------------|--------|
| **Morning Star** | Large red → small body → large green closing above midpoint | Strong bullish |
| **Evening Star** | Large green → small body → large red closing below midpoint | Strong bearish |
| **Three White Soldiers** | Three consecutive green candles with rising opens and closes | Strong bullish |
| **Three Black Crows** | Three consecutive red candles with falling opens and closes | Strong bearish |
| **Three Inside Up/Down** | Harami pattern confirmed by third candle breaking out | Confirmed reversal |

### 3.3 Technical Score Calculation

Each sub-indicator contributes to a normalized score in `[-1, +1]`:

```
tech_score = (
    RSI_score      × 1     # ±0.8 for extreme, ±0.3 for moderate
  + MACD_score     × 1     # ±0.6 capped, scaled by histogram/10
  + SuperTrend     × 1     # ±0.5 based on direction
  + EMA_alignment  × 1     # ±0.6 for C > EMA9 > EMA21
  + BB_position    × 1     # ±0.5 for position in band
  + Pattern_scores × 1     # ±0.4 per detected pattern
) / count × 2

Final: clamped to [-1, +1]
```

---

## 4. Options Greeks & Black-Scholes

**File:** `analysis/greeks.py`

### 4.1 Black-Scholes-Merton Model

The BSM formula for European options (NIFTY/BANKNIFTY are European-style):

**Call Price:**
```
C = S × N(d₁) - K × e^(-rT) × N(d₂)
```

**Put Price:**
```
P = K × e^(-rT) × N(-d₂) - S × N(-d₁)
```

**Where:**
```
d₁ = [ln(S/K) + (r + σ²/2) × T] / (σ × √T)
d₂ = d₁ - σ × √T
```

| Variable | Meaning | Default |
|----------|---------|---------|
| S | Spot price (NIFTY current level) | Live data |
| K | Strike price | From option chain |
| T | Time to expiry in years | `DTE / 365.25` |
| σ (sigma) | Volatility (annualized, decimal) | Implied from market |
| r | Risk-free rate | 6.5% (India 10Y bond) |
| N(x) | Cumulative normal distribution | `scipy.stats.norm.cdf` |

### 4.2 Greeks Formulas

| Greek | Formula | Interpretation |
|-------|---------|----------------|
| **Delta (Δ)** | CE: `N(d₁)`, PE: `N(d₁) - 1` | Price change per ₹1 move in underlying |
| **Gamma (Γ)** | `N'(d₁) / (S × σ × √T)` | Rate of change of delta |
| **Theta (Θ)** | `-(S × N'(d₁) × σ)/(2√T) ∓ rKe^(-rT)N(±d₂)` ÷ 252 | Daily time decay |
| **Vega (ν)** | `S × √T × N'(d₁) / 100` | Price change per 1% IV change |
| **Rho (ρ)** | CE: `KTe^(-rT)N(d₂)/100`, PE: `-KTe^(-rT)N(-d₂)/100` | Sensitivity to interest rate |

### 4.3 Implied Volatility (Newton-Raphson)

IV is solved iteratively — find σ such that `BSM_price(σ) = market_price`:

```
σ_new = σ_old - (BSM_price - Market_price) / Vega
```

- Initial guess: σ = 20%
- Max iterations: 100
- Tolerance: 10⁻⁶
- Clamped to [0.1%, 500%]

### 4.4 Moneyness Classification

| Classification | Condition |
|---------------|-----------|
| **ATM** | `|S - K| / S < 0.5%` |
| **ITM** | CE: `S > K`, PE: `K > S` |
| **OTM** | CE: `S < K`, PE: `K < S` |

---

## 5. Open Interest Analysis

**File:** `analysis/oi_analysis.py`

### 5.1 Core OI Metrics

| Metric | Formula | Signal |
|--------|---------|--------|
| **PCR (Put-Call Ratio)** | `Total_PE_OI / Total_CE_OI` | >1.2 = Bullish, <0.8 = Bearish |
| **Volume PCR** | `Total_PE_Volume / Total_CE_Volume` | Confirms OI PCR |
| **Max Pain** | Strike where `Σ(CE_pain + PE_pain)` is minimized | Price gravitates here at expiry |
| **Resistance** | Strike with highest CE OI | Call writers defend this level |
| **Support** | Strike with highest PE OI | Put writers defend this level |

### 5.2 Max Pain Calculation

For each candidate strike `S`:
```
CE_pain = Σ max(0, S - strike_i) × CE_OI_i    (for all strikes)
PE_pain = Σ max(0, strike_i - S) × PE_OI_i    (for all strikes)
Total_pain = CE_pain + PE_pain
Max_Pain = S that minimizes Total_pain
```

### 5.3 OI-Price Buildup Patterns

The 4 institutional-standard OI-Price patterns:

| Pattern | OI Change | Price Change | Meaning | Signal |
|---------|-----------|-------------|---------|--------|
| **Long Buildup** | OI ↑ | Price ↑ | Fresh buying, new longs entering | 🟢 Bullish |
| **Short Buildup** | OI ↑ | Price ↓ | Fresh shorting, new shorts entering | 🔴 Bearish |
| **Short Covering** | OI ↓ | Price ↑ | Shorts closing, panic buying | 🟢 Bullish (sharp rally) |
| **Long Unwinding** | OI ↓ | Price ↓ | Longs exiting, giving up | 🔴 Bearish (sustained fall) |

### 5.4 OI Signal Score

```
net_bullish = bullish_strikes + short_covering_strikes × 0.7
net_bearish = bearish_strikes + long_unwinding_strikes × 0.7
oi_signal_score = (net_bullish - net_bearish) / (net_bullish + net_bearish)
```

Range: `[-1, +1]`

---

## 6. Sentiment Analysis

**File:** `analysis/sentiment.py`

### Sources

| Source | Method | Refresh Rate |
|--------|--------|-------------|
| **RSS Feeds** | MoneyControl, ET Markets, LiveMint | 5 minutes |
| **Reddit** | r/IndianStockMarket, r/IndianStreetBets, r/DalalStreetBets | 5 minutes |

### NLP Pipeline

1. Fetch headlines from RSS/Reddit
2. Run NLTK VADER sentiment analyzer on each headline
3. Compound score: `[-1, +1]` per headline
4. Aggregate: weighted average (recent = higher weight)
5. Final sentiment score clamped to `[-1, +1]`

---

## 7. Signal Aggregation & Confluence Scoring

**File:** `analysis/signals.py`

### Weighted Confluence Formula

```
Final_Score = (
    tech_score      × w_technical
  + greeks_score    × w_greeks
  + oi_score        × w_oi
  + sentiment_score × w_sentiment
  + regime_score    × w_regime
)
```

### Weight Configurations

| Layer | Live Weights | Backtest Weights |
|-------|-------------|-----------------|
| Technical | 0.25 | 0.70 |
| Greeks/IV | 0.20 | 0.00 |
| Open Interest | 0.25 | 0.00 |
| Sentiment | 0.15 | 0.00 |
| Regime | 0.15 | 0.30 |

### Direction Classification

| Score Range | Direction | Action |
|-------------|-----------|--------|
| ≥ +0.50 | STRONG_BUY | High conviction bullish |
| +0.20 to +0.49 | BUY | Moderate bullish |
| -0.19 to +0.19 | NEUTRAL | No trade |
| -0.49 to -0.20 | SELL | Moderate bearish |
| ≤ -0.50 | STRONG_SELL | High conviction bearish |

### Actionability

- **Minimum entry score:** 0.35 (live), 0.30 (backtest)
- **Confidence:** `min(100, |score| × 150)`

---

## 8. Market Regime Detection

| Regime | Detection Criteria | Preferred Strategy |
|--------|-------------------|-------------------|
| **HIGH_VOLATILITY** | BB width > 4% AND ATR > 100 | Long Straddle |
| **LOW_VOLATILITY** | BB width < 1.5% | Iron Condor |
| **TRENDING_UP** | SuperTrend = +1 AND RSI > 55 | Bull Call Spread |
| **TRENDING_DOWN** | SuperTrend = -1 AND RSI < 45 | Bear Put Spread |
| **SIDEWAYS** | None of the above | Iron Condor |

---

## 9. Strategy Selection & Execution

**File:** `strategy/strategies.py`

The strategy selector is **rule-based** — it maps the detected market regime to the optimal options strategy using a predefined lookup table. There is no machine learning or neural network involved. The logic works as follows:

1. Receive the `AggregatedSignal` (direction + regime)
2. Look up the regime → strategy mapping (see Section 8)
3. Build the option legs using ATM strike + configured offsets
4. Return an `Order` object with all legs, stoploss, and net premium

### 9.1 Iron Condor (Sideways/Low Vol)

```
SELL CE at ATM + 4 × strike_interval (200 pts OTM)
BUY CE at ATM + 5 × strike_interval (protection wing)
SELL PE at ATM - 4 × strike_interval (200 pts OTM)
BUY PE at ATM - 5 × strike_interval (protection wing)
```

| Property | Value |
|----------|-------|
| Max Profit | Net premium received × lot size |
| Max Loss | (strike_interval - net_premium) × lot size |
| Breakeven | Sell_PE - net_credit, Sell_CE + net_credit |
| Stoploss | 25% of margin |
| Ideal Regime | SIDEWAYS, LOW_VOLATILITY |

### 9.2 Bull Call Spread (Bullish)

```
BUY CE at ATM
SELL CE at ATM + 3 × strike_interval (150 pts OTM)
```

| Property | Value |
|----------|-------|
| Max Profit | (spread_width - debit) × lot |
| Max Loss | Debit paid × lot |
| Stoploss | 15% of premium |

### 9.3 Bear Put Spread (Bearish)

```
BUY PE at ATM
SELL PE at ATM - 3 × strike_interval (150 pts OTM)
```

### 9.4 Long Straddle (High Volatility)

```
BUY CE at ATM
BUY PE at ATM
```

### 9.5 Naked CE/PE (Strong Conviction Only)

Only entered on `STRONG_BUY` or `STRONG_SELL` signals. Stoploss: 20%.

---

## 10. Risk Management

### Position-Level Risk

| Parameter | Value | Purpose |
|-----------|-------|---------|
| Max Risk Per Trade | 2% of capital | Never risk >₹10,000 on ₹5L capital |
| Max Daily Drawdown | 3% of capital | Circuit breaker at ₹15,000 daily loss |
| Max Simultaneous Positions | 2 | Focus on quality |
| Slippage | 0.5% per order | Realistic fill simulation |
| Brokerage | ₹20 per F&O order | Flat fee per leg |

### Exit Logic (7-Layer System)

| Priority | Exit Type | Condition | Purpose |
|----------|-----------|-----------|---------|
| 1 | **STOPLOSS** | Credit: loss > 1.5× premium received. Debit: loss > 20% | Cut losses fast |
| 2 | **TARGET_REACHED** | Credit: profit > 50% of premium. Debit: profit > 30-40% | Take profits early |
| 3 | **TRAILING_STOP** | Profit drops below 55% of peak (activates at 10-15% gain) | Lock in profits |
| 4 | **TIME_DECAY_EXIT** | Credit: held > 65% DTE. Debit: held > 40% DTE | Don't let theta destroy position |
| 5 | **SIGNAL_REVERSAL** | Opposite signal score > 0.25 | Market direction flipped |
| 6 | **BREAKEVEN_EXIT** | Held > 40% DTE AND |PnL| < ₹150 | Free up capital from stuck trades |
| 7 | **AUTO_SQUAREOFF** | 3:25 PM IST | Mandatory market close exit |

---

## 11. Paper Trading Engine

**File:** `trading/paper_engine.py`

- Capital: ₹5,00,000
- Tracks: entry/exit prices, slippage, brokerage, P&L per leg
- Theta decay simulation: premium decays using sqrt model near expiry
- Trailing stop: trails at `stoploss_pct / 2` (7.5% for 15% stoploss)
- Daily reset at market open (9:15 AM IST)

---

## 12. Backtesting Engine

**File:** `backtest/engine.py`

### Data

- Source: yfinance historical (59 days, 5-min candles)
- ~4,280 bars per backtest run

### Synthetic Option Chain

Since historical option chain data isn't available, the engine generates synthetic premiums using Black-Scholes with estimated IV.

### P&L Simulation

For each bar:
1. Compute all technical indicators
2. Generate signal with backtest weights (70% technical, 30% regime)
3. Check exit conditions on open positions
4. Enter new positions if signal > 0.30 AND cooldown elapsed (30 bars)

Option premium updates use delta-based pricing + accelerated theta decay:
```
remaining_life = max(0.01, 1 - days_elapsed / DTE)
decay_factor = 1 - √(1 - remaining_life)     # Accelerated near expiry
current_premium = intrinsic + time_value × decay_factor
```

### Key Metrics Reported

| Metric | Formula |
|--------|---------|
| Win Rate | `wins / total_trades × 100` |
| Profit Factor | `Σ(winning_trades) / |Σ(losing_trades)|` |
| Sharpe Ratio | `mean(daily_returns) / std(daily_returns) × √252` |
| Max Drawdown | `max(peak - trough) / peak × 100` |
| Sortino Ratio | `mean(returns) / downside_deviation × √252` |

---

## 13. Advanced Modules

### 13.1 Adaptive Weight Optimizer
Self-learning system that adjusts signal weights based on trade outcomes using exponential moving average of P&L per regime.

### 13.2 Multi-Timeframe Engine
Resamples 5-min data to 15-min, 1-hour, and daily. Only trades when all timeframes agree on direction.

### 13.3 VIX Analyzer
Monitors India VIX levels. Regimes: CALM (<13), NORMAL (13-20), ELEVATED (20-25), HIGH (25-35), PANIC (>35). Adjusts position size and blocks trades in PANIC.

### 13.4 Dynamic Hedging
Monitors portfolio-level Greeks and recommends hedges when delta/gamma exposure exceeds thresholds.

### 13.5 Walk-Forward Optimizer
Out-of-sample optimization: trains on 60% of data, validates on remaining 40%.

### 13.6 UOA Detector (Unusual Options Activity)
Flags strikes with OI change > 2× average — indicates smart money positioning.

### 13.7 Feedback Loop
Records trade outcomes by regime/strategy and skips patterns that historically lose money.

---

## 14. Web Dashboard

**Stack:** FastAPI + Jinja2 + Chart.js + Vanilla CSS

### Sections

| Section | Features |
|---------|----------|
| **Dashboard** | P&L, equity curve, signal distribution doughnut, active positions |
| **Option Chain** | Live CE/PE prices, OI, volume, IV with market-hours detection |
| **Signals** | Real-time signal table with direction, score, confidence, regime |
| **Greeks** | Portfolio-level Delta/Gamma/Theta/Vega radar chart |
| **Sentiment** | Aggregate score gauge, RSS/Reddit feed with NLP scores |
| **Trade Journal** | All closed trades with entry/exit, P&L, exit reason |
| **Backtest** | Run backtests, equity curve, trade-by-trade breakdown |
| **Settings** | Capital, risk params, API status |

All charts are **hover-interactive** with custom dark-theme tooltips showing formatted values.

---

## 15. Configuration Reference

**File:** `config.py` + `.env`

### Market Constants

| Setting | Value | Description |
|---------|-------|-------------|
| Market Open | 09:15 IST | NSE F&O session start |
| Market Close | 15:30 IST | NSE session end |
| Auto Squareoff | 15:25 IST | Close all positions before close |
| Risk-Free Rate | 6.5% | India 10-year government bond yield |
| Trading Days/Year | 252 | Standard for annualization |

### Supported Indices

| Index | Lot Size | Strike Interval |
|-------|----------|----------------|
| NIFTY 50 | 25 | 50 |
| BANKNIFTY | 15 | 100 |
| FINNIFTY | 25 | 50 |
| MIDCPNIFTY | 50 | 25 |

### Trading Parameters

| Parameter | Value | Purpose |
|-----------|-------|---------|
| Paper Capital | ₹5,00,000 | Realistic F&O margin |
| Risk Per Trade | 2% | Max ₹10,000 risk per trade |
| Daily Drawdown Limit | 3% | Max ₹15,000 daily loss |
| Max Positions | 2 | Quality over quantity |
| Default DTE | 7 days | Weekly expiry |
| Min Premium | ₹2 | Don't trade illiquid options |

---

## Deployment

### Local Development
```bash
python -m venv venv
venv\Scripts\activate      # Windows
pip install -r requirements.txt
python run.py              # Starts on port 7860
```

### HuggingFace Spaces (24/7)
- SDK: Docker
- Port: 7860
- All API keys set as Repository Secrets
- Auto-starts trading loop on container boot
- Market-aware: only executes trades during 9:15-15:30 IST

---

*Built with Python 3.11 | FastAPI | Chart.js | Black-Scholes-Merton | NLTK VADER*
