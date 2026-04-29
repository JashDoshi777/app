# 📊 NIFTY OI Tracker — Complete Documentation

> **What is this project?**
> This is a **real-time NIFTY Options Open Interest (OI) tracking dashboard**. It connects to Angel One's broker API, fetches live option chain data every 60 seconds during market hours (9:15 AM – 3:30 PM IST), and displays it in a table + charts so you can see where big money (institutions) is placing bets — and trade accordingly.

---

## 🏗️ How the Project Works (Bird's Eye View)

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Angel One API  │────▶│  Python Backend  │────▶│  Web Dashboard  │
│  (Live Data)    │     │  (FastAPI + DB)   │     │  (HTML/JS/CSS)  │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

### Step-by-Step Flow

| Step | What Happens |
|------|-------------|
| **1. Login** | App logs into your Angel One demat account using API key, client ID, password, and TOTP secret |
| **2. WebSocket** | Opens a real-time WebSocket connection for instant NIFTY/BANKNIFTY price ticks (<50ms delay) |
| **3. Every 60s** | A background thread fetches the full option chain (all strikes, CE & PE) via REST API |
| **4. Compute** | Calculates totals — Total CE OI, Total PE OI, PE-CE Diff, PCR, Straddle, Signal, Greeks, IV |
| **5. Store** | Saves snapshot to NeonDB (PostgreSQL) for historical playback |
| **6. Display** | Serves data via FastAPI REST endpoints → rendered in browser as table + charts |

---

## 📋 Data Tiers (Where Does Data Come From?)

| Tier | Source | Speed | Cost | When Used |
|------|--------|-------|------|-----------|
| **Tier 1** | Angel One SmartAPI WebSocket | <50ms real-time | Free (with demat) | Primary — used when credentials are configured |
| **Tier 2** | yfinance (Yahoo Finance) | 1-15s delay | Free | Fallback — if Angel One fails |
| **Tier 3** | Mock/Synthetic data | Instant | Free | Development/testing only |

---

## 📊 The OI Data Table — Every Column Explained

The main table has **17 columns** organized into groups. Here is every single one:

---

### 🕐 Column 1: `Time`

| Detail | Value |
|--------|-------|
| **What it shows** | The time (HH:MM) when this data snapshot was captured |
| **How calculated** | `datetime.now(IST).strftime("%H:%M")` — current Indian Standard Time |
| **Why it matters** | Each row = one snapshot taken every 60 seconds during market hours |

---

### 🟢 Columns 2-4: `Put OI` Group (3 sub-columns)

#### 2. Put OI → Total
| Detail | Value |
|--------|-------|
| **What it shows** | Sum of Open Interest across ALL Put (PE) option contracts within selected strike range |
| **How calculated** | `total_pe_oi = sum of pe_oi for all strikes within ±(range × 50) of ATM` |
| **Example** | If ATM is 24000, Range=10 → sums PE OI from strike 23500 to 24500 |
| **Displayed as** | Lakh notation: `45.2 L` = 45,20,000 contracts |

#### 3. Put OI → Chg (Day)
| Detail | Value |
|--------|-------|
| **What it shows** | How much PE OI has changed **since previous day's close** |
| **How calculated** | `pe_chg_oi_day = current_pe_oi - previous_day_closing_pe_oi` |
| **Positive (+)** | New put positions added today → **Bullish** (writers selling puts = expecting support) |
| **Negative (-)** | Put positions closed today → **Bearish** (support being removed) |

#### 4. Put OI → Change
| Detail | Value |
|--------|-------|
| **What it shows** | How much PE OI changed **since the last snapshot** (minute-to-minute) |
| **How calculated** | `pe_oi_change = current_total_pe_oi - previous_minute_total_pe_oi` |
| **Why it matters** | Shows real-time flow — are traders adding or removing puts RIGHT NOW? |

---

### 🔴 Columns 5-7: `Call OI` Group (3 sub-columns)

#### 5. Call OI → Total
| Detail | Value |
|--------|-------|
| **What it shows** | Sum of Open Interest across ALL Call (CE) option contracts within selected strike range |
| **How calculated** | `total_ce_oi = sum of ce_oi for all strikes within range` |

#### 6. Call OI → Chg (Day)
| Detail | Value |
|--------|-------|
| **What it shows** | How much CE OI has changed **since previous day's close** |
| **How calculated** | `ce_chg_oi_day = current_ce_oi - previous_day_closing_ce_oi` |
| **Positive (+)** | New call positions added today → **Bearish** (writers selling calls = expecting resistance) |
| **Negative (-)** | Call positions closed today → **Bullish** (resistance being removed) |

#### 7. Call OI → Change
| Detail | Value |
|--------|-------|
| **What it shows** | How much CE OI changed since the last snapshot (minute-to-minute) |
| **How calculated** | `ce_oi_change = current_total_ce_oi - previous_minute_total_ce_oi` |

---

### 🟣 Columns 8-10: `PE - CE OI` Difference Group

#### 8. PE-CE → Total
| Detail | Value |
|--------|-------|
| **What it shows** | The raw difference between total Put OI and total Call OI |
| **How calculated** | `pe_ce_diff = total_pe_oi - total_ce_oi` |
| **Positive (+)** | More puts than calls → **Bullish** (more support being built) |
| **Negative (-)** | More calls than puts → **Bearish** (more resistance being built) |

#### 9. PE-CE → Chg (Day)
| Detail | Value |
|--------|-------|
| **What it shows** | How the PE-CE difference has shifted today vs yesterday |
| **How calculated** | `pe_ce_chg_day = pe_chg_oi_day - ce_chg_oi_day` |
| **Rising** | Puts being added faster than calls → market getting more bullish support |
| **Falling** | Calls being added faster than puts → market getting more bearish resistance |

#### 10. PE-CE → Change
| Detail | Value |
|--------|-------|
| **What it shows** | Minute-to-minute change in the PE-CE difference |
| **How calculated** | `pe_ce_diff_change = current_pe_ce_diff - previous_minute_pe_ce_diff` |

---

### 📈 Column 11: `PCR` (Put-Call Ratio)

| Detail | Value |
|--------|-------|
| **What it shows** | Ratio of total Put OI to total Call OI |
| **How calculated** | `pcr = total_pe_oi / total_ce_oi` |
| **PCR > 1.0** | More puts than calls → **Bullish** (writers confident market won't fall) |
| **PCR < 0.7** | Far more calls than puts → **Bearish** (heavy resistance above) |
| **PCR 0.8 – 1.2** | Neutral / sideways zone |
| **Sweet spot** | PCR between **0.9 – 1.1** = balanced market; extremes = potential reversal |

---

### 💰 Columns 12-14: `Future` Group

#### 12. Future → LTP
| Detail | Value |
|--------|-------|
| **What it shows** | Last Traded Price of NIFTY Futures (nearest month contract) |
| **How calculated** | Fetched via WebSocket or REST API from Angel One. Falls back to spot price if unavailable |
| **Why not Spot?** | Futures price includes "cost of carry" and is what institutional traders actually trade |

#### 13. Future → Straddle
| Detail | Value |
|--------|-------|
| **What it shows** | ATM Straddle price = ATM Call LTP + ATM Put LTP |
| **How calculated** | `straddle = atm_ce_ltp + atm_pe_ltp` |
| **Why it matters** | Shows the market's expected range for the day. If straddle = 200, market expects NIFTY to move ±200 points from ATM |
| **Falling straddle** | Volatility decreasing → market settling into a range |
| **Rising straddle** | Volatility increasing → big move expected |

#### 14. Future → ATM
| Detail | Value |
|--------|-------|
| **What it shows** | The At-The-Money strike price (nearest strike to current NIFTY price) |
| **How calculated** | `atm = round(underlying / 50) * 50` — rounds to nearest 50 (NIFTY strike interval) |
| **Example** | If NIFTY = 24,123 → ATM = 24,100. If NIFTY = 24,138 → ATM = 24,150 |

---

### 🔷 Columns 15-16: `Delta Chg` Group

#### 15. Delta Chg → CE
| Detail | Value |
|--------|-------|
| **What it shows** | How much the ATM Call option price changed since last snapshot |
| **How calculated** | `ce_delta_chg = current_atm_ce_ltp - previous_atm_ce_ltp` |
| **Positive** | Call premium increasing → market moving up |
| **Negative** | Call premium decreasing → market moving down or time decay eating premium |

#### 16. Delta Chg → PE
| Detail | Value |
|--------|-------|
| **What it shows** | How much the ATM Put option price changed since last snapshot |
| **How calculated** | `pe_delta_chg = current_atm_pe_ltp - previous_atm_pe_ltp` |
| **Positive** | Put premium increasing → market moving down |
| **Negative** | Put premium decreasing → market moving up or time decay |

---

### 🚦 Column 17: `Signal` — THE MOST IMPORTANT COLUMN

This tells you what institutional traders are doing RIGHT NOW.

| Signal | Full Name | Price Direction | OI Direction | What It Means |
|--------|-----------|----------------|--------------|---------------|
| **LB** | Long Buildup | ↑ Price Going Up | ↑ OI Increasing | Fresh buying! New longs entering. **Strong Bullish** |
| **SB** | Short Buildup | ↓ Price Going Down | ↑ OI Increasing | Fresh shorting! New shorts entering. **Strong Bearish** |
| **SC** | Short Covering | ↑ Price Going Up | ↓ OI Decreasing | Shorts panicking & exiting. **Weak Bullish** (reversal risk) |
| **LU** | Long Unwinding | ↓ Price Going Down | ↓ OI Decreasing | Longs giving up & exiting. **Weak Bearish** (may stabilize) |
| **S** | Startup/Neutral | — | — | First reading, no prior data to compare |

#### How Signal is Calculated

```
1. Compare current NIFTY price vs previous snapshot price → price_up = True/False
2. Compare current Total OI (CE+PE) vs previous Total OI → oi_up = True/False
3. Combine:
   - Price UP + OI UP   = LB (Long Buildup)
   - Price DOWN + OI UP  = SB (Short Buildup)
   - Price UP + OI DOWN  = SC (Short Covering)
   - Price DOWN + OI DOWN = LU (Long Unwinding)
```

---

## 🔍 LU and SC Explained In Detail

### 📉 LU — Long Unwinding

> **"Longs are giving up and leaving the market"**

| Aspect | Detail |
|--------|--------|
| **What's happening** | Traders who had bought (gone long) are now selling their positions and exiting |
| **Price** | Going **DOWN** ↓ |
| **Open Interest** | Going **DOWN** ↓ (positions being closed, not new ones being created) |
| **Market feel** | Weak, tired, lack of buying conviction |
| **Why it happens** | Buyers are booking profits or cutting losses. No fresh buying interest |
| **Strength** | **Weak bearish** — the fall may slow down because sellers are exiting too |
| **What to expect** | Market may find support soon OR could accelerate if fresh shorts (SB) follow |

**Example**: NIFTY at 24,200. LU appears for 3 consecutive readings. Price drops to 24,150 but OI also drops. This means the fall is happening because buyers are LEAVING, not because sellers are ATTACKING. The fall may stop once all weak longs exit.

### 📈 SC — Short Covering

> **"Shorts are panicking and buying back to exit"**

| Aspect | Detail |
|--------|--------|
| **What's happening** | Traders who had sold (shorted) are now buying back their positions to close them |
| **Price** | Going **UP** ↑ |
| **Open Interest** | Going **DOWN** ↓ (positions being closed, not new ones being created) |
| **Market feel** | Artificially strong — rally driven by panic, not conviction |
| **Why it happens** | Shorts are losing money as price rises, so they rush to exit (buy back) |
| **Strength** | **Weak bullish** — the rise may stop once all shorts have covered |
| **What to expect** | The up-move may be temporary. If no fresh buying (LB) follows, price can reverse back down |

**Example**: NIFTY at 24,000. SB signal was showing all morning (shorts entering). Suddenly price jumps to 24,100 and signal flips to SC. This means shorts are panicking — but the rally is NOT because new buyers arrived. Once covering is done, the move may fizzle out.

---

## ⏰ Ideal Trading Times

### Best Times to Trade

| Time Window | Why | Signal to Watch |
|-------------|-----|-----------------|
| **9:30 – 10:00 AM** | Initial OI data stabilizes. First clear signals emerge after opening volatility settles | Wait for 2-3 consistent LB or SB signals |
| **10:00 – 11:30 AM** | **PRIME TRADING WINDOW**. Institutions place their positions. OI data is most reliable | Look for strong LB/SB with rising PE-CE diff confirming direction |
| **11:30 – 1:00 PM** | Lunch lull — lower volume, signals can be noisy | Avoid new trades unless strong LB/SB persists from morning |
| **1:30 – 2:30 PM** | **SECOND BEST WINDOW**. Institutions adjust positions for closing. Fresh moves start | Watch for signal transitions (SB→LB = reversal, SC→SB = trap) |
| **2:30 – 3:15 PM** | Expiry day scramble (Thursdays). High volatility, OI data changes rapidly | Only trade if you're experienced; straddle values collapse fast |

### Times to AVOID Trading

| Time | Why |
|------|-----|
| **9:15 – 9:30 AM** | Opening chaos. Spreads are wide, data hasn't stabilized, first signal is always "S" (no prior data) |
| **3:15 – 3:30 PM** | Last 15 minutes. Extremely volatile, low liquidity, slippage risk |
| **When signal = S** | No data to compare. Wait for LB/SB/SC/LU to appear |

---

## 🎯 When to Take Trades — Signal-Based Strategy

### ✅ STRONG BUY Signal (Go Long / Buy CE)

All of these should be true simultaneously:

| Condition | What to Check |
|-----------|---------------|
| Signal = **LB** | 2-3 consecutive LB readings (not just one) |
| PE-CE Diff | **Positive and rising** (more puts than calls, difference growing) |
| PCR | **Above 0.9** and rising |
| Put OI Chg (Day) | **Positive** (new puts being added = support building) |
| Straddle | **Stable or falling** (market settling, not panicking) |
| CE Delta Chg | **Positive** (call premiums rising = market moving up) |

### ✅ STRONG SELL Signal (Go Short / Buy PE)

All of these should be true simultaneously:

| Condition | What to Check |
|-----------|---------------|
| Signal = **SB** | 2-3 consecutive SB readings |
| PE-CE Diff | **Negative and falling** (more calls than puts, difference growing negative) |
| PCR | **Below 0.8** and falling |
| Call OI Chg (Day) | **Positive** (new calls being added = resistance building) |
| Straddle | **Stable or falling** |
| PE Delta Chg | **Positive** (put premiums rising = market moving down) |

### ⚠️ AVOID Trading When

| Condition | Why |
|-----------|-----|
| Signal alternating LB↔SB | Choppy/sideways market, no clear direction |
| Signal = SC for 3+ readings | Rally is fake (just short covering), may reverse |
| Signal = LU for 3+ readings | Fall is exhausting, may bounce — bad time to short |
| PCR between 0.85–1.05 | Dead neutral zone — no edge |
| Straddle rising sharply | High volatility event incoming — unpredictable |

---

## 🔧 Transition Signals — When Signals Change

| Transition | Meaning | Action |
|------------|---------|--------|
| **SB → LB** | Bears failed, bulls taking over | Strong buy signal! |
| **LB → SB** | Bulls failed, bears taking over | Strong sell signal! |
| **SB → SC** | Shorts getting trapped, covering begins | Wait — don't chase the bounce |
| **LB → LU** | Longs getting trapped, unwinding begins | Wait — don't chase the fall |
| **SC → LB** | Short covering converting to fresh buying | Strong bullish confirmation! |
| **LU → SB** | Long unwinding converting to fresh shorting | Strong bearish confirmation! |
| **SC → SB** | Fake rally over, fresh shorts entering | Very bearish! Sell aggressively |
| **LU → LB** | Fake fall over, fresh longs entering | Very bullish! Buy aggressively |

---

## 📈 Other Dashboard Tabs

### Tab 2: Smart OI Charts

| Chart | What It Shows |
|-------|---------------|
| **NIFTY Candlestick** | TradingView-style OHLC chart built from 1-minute price data |
| **OI Lines** | Put OI (pink), Call OI (green), PE-CE Diff (purple) over time |
| **PCR Chart** | Put-Call Ratio trend line — shows sentiment shifting over the day |

### Tab 3: Price vs OI

| Chart | What It Shows |
|-------|---------------|
| **Call Price vs OI** | For a selected strike: how call premium moves relative to call OI |
| **Put Price vs OI** | For a selected strike: how put premium moves relative to put OI |
| **Straddle Price** | ATM Call + ATM Put price over time — shows volatility trend |

---

## 🔧 Range Filter

| Setting | Meaning |
|---------|---------|
| **Range = 5** | Only counts OI from 5 strikes above and below ATM (tighter view) |
| **Range = 10** | Default. Counts ±10 strikes around ATM (standard StockMojo-like view) |
| **Range = 15/20** | Wider view — includes more OTM strikes |

**How it works**: If ATM = 24,000 and Range = 10, strike interval = 50:
- Includes strikes from 24,000 - (10×50) = 23,500 to 24,000 + (10×50) = 24,500
- All OI totals, PCR, PE-CE diff are recalculated using ONLY these filtered strikes

---

## 🧮 Behind the Scenes — Greeks & IV

The app also computes these using the **Black-Scholes model** (for each strike):

| Greek | What It Measures | Formula Used |
|-------|-----------------|--------------|
| **IV** (Implied Volatility) | Market's expectation of future price movement | Newton-Raphson solver on Black-Scholes equation |
| **Delta** | How much option price moves per ₹1 move in NIFTY | N(d1) for calls, N(d1)-1 for puts |
| **Gamma** | Rate of change of Delta | n(d1) / (S × σ × √T) |
| **Theta** | Time decay per day (how much value option loses daily) | Complex formula, always negative |
| **Vega** | Sensitivity to 1% change in IV | S × n(d1) × √T / 100 |

**Risk-free rate used**: 7% (India 10-year govt bond rate)

---

## 💾 Database Schema

### Table: `market_snapshots` (1 row per minute)

| Column | Type | Description |
|--------|------|-------------|
| timestamp | TIMESTAMPTZ | When snapshot was taken |
| underlying_price | FLOAT | NIFTY spot price |
| total_ce_oi / total_pe_oi | BIGINT | Aggregate OI totals |
| pe_ce_oi_diff | BIGINT | PE OI minus CE OI |
| pcr | FLOAT | Put-Call Ratio |
| future_ltp | FLOAT | Futures LTP |
| straddle_price | FLOAT | ATM CE + ATM PE |
| atm_strike | FLOAT | Current ATM strike |
| atm_ce_ltp / atm_pe_ltp | FLOAT | ATM option prices |

### Table: `oi_snapshots` (1 row per strike per minute)

| Column | Type | Description |
|--------|------|-------------|
| strike | FLOAT | Strike price (e.g. 24000) |
| ce_oi / pe_oi | BIGINT | OI at this strike |
| ce_chg_oi / pe_chg_oi | BIGINT | OI change vs prev day |
| ce_ltp / pe_ltp | FLOAT | Option prices |
| ce_iv / pe_iv | FLOAT | Implied Volatility |
| ce_delta through ce_vega | FLOAT | Greeks |

---

## 🔌 How to Set Up

### Required Environment Variables (`.env` file)

```env
ANGEL_API_KEY=your_angel_one_api_key
ANGEL_CLIENT_ID=your_client_id
ANGEL_PASSWORD=your_password
ANGEL_TOTP_SECRET=your_totp_base32_secret
DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

### Run the App

```bash
pip install -r requirements.txt
python run.py
# Opens at http://localhost:7860
```

---

## 📁 Project File Structure

```
trader/
├── run.py              # Main entry point — starts server + data logger
├── config.py           # Environment variables + market constants
├── core/
│   ├── market_data.py  # Multi-tier data fetching (Angel One/yfinance/mock)
│   ├── option_chain.py # OI analysis engine (Max Pain, Support/Resistance)
│   ├── black_scholes.py # IV solver + Greeks calculator
│   └── instruments.py  # Instrument token mapping
├── web/
│   ├── app.py          # FastAPI app initialization
│   ├── api_routes.py   # All REST API endpoints
│   ├── templates/
│   │   └── index.html  # Dashboard HTML
│   └── static/
│       ├── css/style.css
│       └── js/app.js   # Frontend JavaScript
├── database/
│   └── __init__.py
├── data/cache/         # Cached instrument data
└── logs/               # Application logs
```

---

## 🔑 Quick Reference Cheat Sheet

```
╔══════════════════════════════════════════════════════════════╗
║                    SIGNAL CHEAT SHEET                        ║
╠══════════════════════════════════════════════════════════════╣
║  LB (Long Buildup)    = Price ↑ + OI ↑ = STRONG BULLISH    ║
║  SB (Short Buildup)   = Price ↓ + OI ↑ = STRONG BEARISH    ║
║  SC (Short Covering)  = Price ↑ + OI ↓ = WEAK BULLISH      ║
║  LU (Long Unwinding)  = Price ↓ + OI ↓ = WEAK BEARISH      ║
╠══════════════════════════════════════════════════════════════╣
║                    PCR CHEAT SHEET                           ║
╠══════════════════════════════════════════════════════════════╣
║  PCR > 1.2  = Extremely Bullish (or reversal warning)       ║
║  PCR 0.9-1.2 = Bullish                                      ║
║  PCR 0.7-0.9 = Bearish                                      ║
║  PCR < 0.7  = Extremely Bearish (or reversal warning)       ║
╠══════════════════════════════════════════════════════════════╣
║                BEST TRADING TIMES                            ║
╠══════════════════════════════════════════════════════════════╣
║  10:00 - 11:30 AM = PRIMARY window (institutional flow)     ║
║  1:30 - 2:30 PM   = SECONDARY window (afternoon adjustment) ║
║  AVOID: 9:15-9:30, 3:15-3:30, and when signal = S          ║
╚══════════════════════════════════════════════════════════════╝
```

---

> **Disclaimer**: This tool is for **educational and informational purposes only**. Options trading involves significant risk. Past OI patterns do not guarantee future results. Always use proper risk management and stop-losses.
