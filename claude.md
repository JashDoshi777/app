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
| **4. Compute** | Calculates totals — Total CE OI, Total PE OI, PE-CE Diff, PCR, Volume, Greeks, IV |
| **5. Store** | Saves snapshot to NeonDB (PostgreSQL) for historical playback — including per-strike volume |
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

The main table has **13 columns** (in "Total OI" display mode) organized into groups: Time, Put OI (Total/Change/Std(10)/Z), Call OI (Total/Change/Std(10)/Z), PE-CE OI (Total/Change/Net Z/Signal), plus PCR/Volume/Total OI. Here is every single one:

---

### 🕐 Column 1: `Time`

| Detail | Value |
|--------|-------|
| **What it shows** | The time (HH:MM) when this data snapshot was captured |
| **How calculated** | `datetime.now(IST).strftime("%H:%M")` — current Indian Standard Time |
| **Why it matters** | Each row = one snapshot taken every 60 seconds during market hours |

---

### 🟢 Columns 2-4: `Put OI` Group (2-3 sub-columns depending on display mode)

#### Put OI → Total
| Detail | Value |
|--------|-------|
| **What it shows** | Sum of Open Interest across ALL Put (PE) option contracts within selected strike range |
| **How calculated** | `total_pe_oi = sum of pe_oi for all strikes within ±(range × 50) of ATM` |
| **Example** | If ATM is 24000, Range=10 → sums PE OI from strike 23500 to 24500 |
| **Displayed as** | Lakh notation: `45.2 L` = 45,20,000 contracts |

#### Put OI → Chg (Day)
| Detail | Value |
|--------|-------|
| **What it shows** | How much PE OI has changed **since previous day's close** |
| **How calculated** | `pe_chg_oi_day = current_pe_oi - previous_day_closing_pe_oi` |
| **Positive (+)** | New put positions added today → **Bullish** (writers selling puts = expecting support) |
| **Negative (-)** | Put positions closed today → **Bearish** (support being removed) |

#### Put OI → Change
| Detail | Value |
|--------|-------|
| **What it shows** | How much PE OI changed **since the last snapshot** (row-to-row based on timeframe) |
| **How calculated** | `pe_oi_change = current_total_pe_oi - previous_row_total_pe_oi` |
| **Why it matters** | Shows real-time flow — are traders adding or removing puts RIGHT NOW? |

---

### 🔴 Columns 5-7: `Call OI` Group (2-3 sub-columns depending on display mode)

#### Call OI → Total
| Detail | Value |
|--------|-------|
| **What it shows** | Sum of Open Interest across ALL Call (CE) option contracts within selected strike range |
| **How calculated** | `total_ce_oi = sum of ce_oi for all strikes within range` |

#### Call OI → Chg (Day)
| Detail | Value |
|--------|-------|
| **What it shows** | How much CE OI has changed **since previous day's close** |
| **How calculated** | `ce_chg_oi_day = current_ce_oi - previous_day_closing_ce_oi` |
| **Positive (+)** | New call positions added today → **Bearish** (writers selling calls = expecting resistance) |
| **Negative (-)** | Call positions closed today → **Bullish** (resistance being removed) |

#### Call OI → Change
| Detail | Value |
|--------|-------|
| **What it shows** | How much CE OI changed since the last snapshot (row-to-row) |
| **How calculated** | `ce_oi_change = current_total_ce_oi - previous_row_total_ce_oi` |

---

### 🟣 Columns 8-9: `PE-CE OI` Group (2 sub-columns: Total + Change)

#### PE-CE OI → Total
| Detail | Value |
|--------|-------|
| **What it shows** | The raw difference between total Put OI and total Call OI |
| **How calculated** | `pe_ce_diff = total_pe_oi - total_ce_oi` |
| **Positive (+)** | More puts than calls → **Bullish** (more support being built) |
| **Negative (-)** | More calls than puts → **Bearish** (more resistance being built) |
| **Color** | Green background for positive, Red background for negative |

#### PE-CE OI → Change
| Detail | Value |
|--------|-------|
| **What it shows** | How the PE-CE difference changed from the previous row |
| **How calculated** | `pe_ce_diff_change = current_pe_ce_diff - previous_row_pe_ce_diff` |
| **Positive (+)** | Sentiment shifting bullish (more puts or fewer calls vs previous row) |
| **Negative (-)** | Sentiment shifting bearish (more calls or fewer puts vs previous row) |
| **Matches** | StockMojo's "Change" column in the PE-CE OI section |

---

### 📈 Column 10: `PCR` (Put-Call Ratio)

| Detail | Value |
|--------|-------|
| **What it shows** | Ratio of total Put OI to total Call OI |
| **How calculated** | `pcr = total_pe_oi / total_ce_oi` |
| **PCR > 1.0** | More puts than calls → **Bullish** (writers confident market won't fall) |
| **PCR < 0.7** | Far more calls than puts → **Bearish** (heavy resistance above) |
| **PCR 0.8 – 1.2** | Neutral / sideways zone |
| **Sweet spot** | PCR between **0.9 – 1.1** = balanced market; extremes = potential reversal |

---

### 📊 Column 11: `CE Volume`

| Detail | Value |
|--------|-------|
| **What it shows** | Total Call option trading volume across all strikes in the selected ATM ± Range |
| **How calculated** | `ce_volume = sum of ce_volume for all strikes within range` |
| **Why it matters** | High volume = active trading/liquidity on the call side. Volume shows trading activity, OI shows outstanding positions |
| **Data source** | Per-strike volume from Angel One API, aggregated within range |

### 📊 Column 12: `PE Volume`

| Detail | Value |
|--------|-------|
| **What it shows** | Total Put option trading volume across all strikes in the selected ATM ± Range |
| **How calculated** | `pe_volume = sum of pe_volume for all strikes within range` |
| **Why it matters** | High volume = active trading/liquidity on the put side |
| **Historical** | Volume is saved to DB per-strike (`oi_snapshots.ce_volume`, `oi_snapshots.pe_volume`) and available in historical mode |

---

### 🔷 Column 13: `Total OI`

| Detail | Value |
|--------|-------|
| **What it shows** | NIFTY Futures Open Interest |
| **How calculated** | Fetched from the futures contract data via Angel One API |
| **Rising OI** | New positions being created |
| **Falling OI** | Existing positions being closed |

---

## 📦 Display Modes

The table supports three display modes (toggled via buttons):

| Mode | Put OI Shows | Call OI Shows | PE-CE OI Shows |
|------|-------------|--------------|----------------|
| **Total OI** | Total + Change | Total + Change | Total + Change |
| **OI Change (Day)** | Chg(Day) + Change | Chg(Day) + Change | Total + Change |
| **All** | Total + Chg(Day) + Change | Total + Chg(Day) + Change | Total + Change |

---

## ⏱️ Timeframe Filters

| Button | What it does |
|--------|-------------|
| **1m** | Shows every minute's data (default) |
| **3m** | Aggregates to 3-minute intervals |
| **5m** | Aggregates to 5-minute intervals |
| **10m** | Aggregates to 10-minute intervals |
| **15m** | Aggregates to 15-minute intervals (matches StockMojo) |
| **30m** | Aggregates to 30-minute intervals |

The timeframe filter works by selecting the row nearest to each time window boundary. Change values are recalculated between the filtered rows.

---

## ⬇️ CSV Download

- Click the **⬇ CSV** button next to the display mode buttons
- Downloads the currently displayed data as a CSV file
- Filename format: `NIFTY_OI_2026-05-07_1m.csv` (date + timeframe)
- Works for both **Live** and **Historical** mode
- Uses the same data as the table (same range, timeframe, ATM mode)

### CSV Columns (13 total):
```
Time, Put OI Total, Put OI Chg(Day), Put OI Change,
Call OI Total, Call OI Chg(Day), Call OI Change,
PE-CE OI, PE-CE Change, PCR,
CE Volume, PE Volume, Total OI
```

---

## ⓘ Column Info Icons

Every column header and sub-column header has a small **ⓘ** info icon.

- **Click** the icon → a popup appears with a detailed explanation of that column
- **Click anywhere outside** → popup dismisses
- Covers all 17 unique column/sub-column definitions
- Includes formulas, interpretation guides, and bullish/bearish signals

---

## 🎨 Row Highlighting

The table highlights rows based on consecutive OI patterns:

| Highlight | Condition | Meaning |
|-----------|-----------|---------|
| **Green row** (left border) | Call OI negative for 3+ consecutive rows AND Put OI positive | Bullish: calls being unwound while puts added |
| **Red row** (left border) | Put OI negative for 3+ consecutive rows AND Call OI positive | Bearish: puts being unwound while calls added |

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

## 🔍 Range Filter

| Setting | Meaning |
|---------|---------|
| **Range = 5** | Only counts OI from 5 strikes above and below ATM (tighter view) |
| **Range = 10** | Default. Counts ±10 strikes around ATM (standard StockMojo-like view) |
| **Range = 15/20** | Wider view — includes more OTM strikes |

**How it works**: If ATM = 24,000 and Range = 10, strike interval = 50:
- Includes strikes from 24,000 - (10×50) = 23,500 to 24,000 + (10×50) = 24,500
- All OI totals, volumes, PCR, PE-CE diff are recalculated using ONLY these filtered strikes

---

## 🔄 ATM Modes

| Mode | Behavior |
|------|----------|
| **Auto ATM** (default) | Uses the latest Futures LTP to compute ATM, applies same ATM to all rows |
| **Fixed** | Each row uses its own ATM computed from its own Futures LTP at capture time |

---

## 🧮 Behind the Scenes — Greeks & IV

The app also computes these using the **Black-Scholes model** (for each strike):

| Greek | What It Measures | Formula Used |
|-------|--------------------|--------------|
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
| total_ce_volume / total_pe_volume | BIGINT | Aggregate volume totals |
| pe_ce_oi_diff | BIGINT | PE OI minus CE OI |
| pcr | FLOAT | Put-Call Ratio |
| future_ltp | FLOAT | Futures LTP |
| straddle_price | FLOAT | ATM CE + ATM PE |
| atm_strike | FLOAT | Current ATM strike |
| atm_ce_ltp / atm_pe_ltp | FLOAT | ATM option prices |
| futures_oi | BIGINT | NIFTY Futures OI |

### Table: `oi_snapshots` (1 row per strike per minute)

| Column | Type | Description |
|--------|------|-------------|
| strike | FLOAT | Strike price (e.g. 24000) |
| ce_oi / pe_oi | BIGINT | OI at this strike |
| ce_chg_oi / pe_chg_oi | BIGINT | OI change vs prev day |
| ce_volume / pe_volume | BIGINT | Trading volume at this strike |
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
├── claude.md           # This documentation file
├── core/
│   ├── market_data.py  # Multi-tier data fetching (Angel One/yfinance/mock)
│   ├── option_chain.py # OI analysis engine (Max Pain, Support/Resistance)
│   ├── black_scholes.py # IV solver + Greeks calculator
│   └── instruments.py  # Instrument token mapping
├── web/
│   ├── app.py          # FastAPI app initialization
│   ├── api_routes.py   # All REST API endpoints + CSV download
│   ├── templates/
│   │   └── index.html  # Dashboard HTML
│   └── static/
│       ├── css/style.css  # Styling + info popup + download button
│       └── js/app.js      # Frontend JavaScript + column info system
├── database/
│   └── __init__.py
├── data/cache/         # Cached instrument data
└── logs/               # Application logs
```

---

## 🔧 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/oi-table` | GET | Main OI table data (live + historical) |
| `/api/download-oi` | GET | Download OI table as CSV |
| `/api/oi-chart` | GET | Chart data for Smart OI tab |
| `/api/candles` | GET | OHLC candlestick data |
| `/api/price-vs-oi` | GET | Strike-level price vs OI data |
| `/api/strikes` | GET | Available strikes list |
| `/api/market-status` | GET | Is market open/closed |
| `/api/expiry-info` | GET | Current expiry label |
| `/api/historical-dates` | GET | Available historical dates |
| `/api/historical-chart` | GET | Historical chart data |

---

## 🔑 Quick Reference Cheat Sheet

```
╔══════════════════════════════════════════════════════════════╗
║                    PCR CHEAT SHEET                           ║
╠══════════════════════════════════════════════════════════════╣
║  PCR > 1.2  = Extremely Bullish (or reversal warning)       ║
║  PCR 0.9-1.2 = Bullish                                      ║
║  PCR 0.7-0.9 = Bearish                                      ║
║  PCR < 0.7  = Extremely Bearish (or reversal warning)       ║
╠══════════════════════════════════════════════════════════════╣
║                    PE-CE OI GUIDE                            ║
╠══════════════════════════════════════════════════════════════╣
║  PE-CE Positive & Rising  = Bullish support building        ║
║  PE-CE Negative & Falling = Bearish pressure building       ║
║  PE-CE Change Positive    = Sentiment shifting bullish      ║
║  PE-CE Change Negative    = Sentiment shifting bearish      ║
╠══════════════════════════════════════════════════════════════╣
║                BEST TRADING TIMES                            ║
╠══════════════════════════════════════════════════════════════╣
║  10:00 - 11:30 AM = PRIMARY window (institutional flow)     ║
║  1:30 - 2:30 PM   = SECONDARY window (afternoon adjustment) ║
║  AVOID: 9:15-9:30, 3:15-3:30                               ║
╠══════════════════════════════════════════════════════════════╣
║                TABLE COLUMNS (11-13)                         ║
╠══════════════════════════════════════════════════════════════╣
║  Time | Put OI | Call OI | PE-CE OI | PCR                   ║
║  CE Vol | PE Vol | Total OI                                  ║
╚══════════════════════════════════════════════════════════════╝
```

---

## 📝 Key Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| **Ranged OI aggregation** | OI is re-summed from per-strike data for the selected ATM ± Range, matching StockMojo's approach |
| **Auto ATM from Futures LTP** | Uses latest Futures price (not spot) for ATM computation, matching institutional standards |
| **Previous day closing OI** | "Chg (Day)" compares against the last snapshot of the previous trading day |
| **Per-strike DB storage** | Enables re-aggregation with different ranges in historical mode |
| **Volume per-strike storage** | CE/PE volume saved per-strike in `oi_snapshots`, enabling ranged volume in historical mode |
| **Expiry handling** | Compares expiry dates against `today.date()` (midnight-normalized) to include same-day expiries |
| **CSV uses same API** | Download endpoint reuses `get_oi_table()` — guaranteed data consistency |

---

> **Disclaimer**: This tool is for **educational and informational purposes only**. Options trading involves significant risk. Past OI patterns do not guarantee future results. Always use proper risk management and stop-losses.
