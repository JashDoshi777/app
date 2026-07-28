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

The on-screen table has **13 columns** (in "Total OI" display mode): Time, Put OI (Total/Change/Std(10)/Z), Call OI (Total/Change/Std(10)/Z), PE-CE OI (Total/Change/Net Z/Signal). PCR, CE Volume, PE Volume, and Total OI are **not rendered as on-screen table columns** — they're computed and available in the API response and included in CSV/MD downloads, but only the groups above are displayed. Here is every field explained:

---

### 🕐 Column 1: `Time`

| Detail | Value |
|--------|-------|
| **What it shows** | The time (HH:MM) when this data snapshot was captured |
| **How calculated** | `datetime.now(IST).strftime("%H:%M")` — current Indian Standard Time |
| **Why it matters** | Each row = one snapshot taken every 60 seconds during market hours |

---

### 🟢 `Put OI` Group (4-5 sub-columns depending on display mode)

A vertical white divider line separates this group from the Call OI group (after the Z sub-column).

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

#### Put OI → Std(10)
| Detail | Value |
|--------|-------|
| **What it shows** | Sample standard deviation of Put OI Change over the 10 rows STRICTLY BEFORE the current row |
| **How calculated** | `Std10_Put = sqrt( Σ(PutChange_t-i − Avg10_Put)² / 9 )` for i = 1..10 (denominator n-1, sample stdev) |
| **Window rule** | Never includes the current row, never includes row 0 (the day's first row, whose Change is a placeholder 0). First value appears on the row completing 10 real prior changes (9:26 if the day starts 9:15) |
| **Why it matters** | Measures how "normal" recent Put OI Change has been — feeds directly into the Z-score below |

#### Put OI → Z
| Detail | Value |
|--------|-------|
| **What it shows** | How extreme THIS row's Put OI Change is compared to its own recent history |
| **How calculated** | `Z_Put = (PutChange_t − Avg10_Put) / Std10_Put` |
| **Interpretation** | abs(Z) ≥ 3 = statistically extreme move relative to the last 10 minutes; small abs(Z) = normal/expected move |

---

### 🔴 `Call OI` Group (4-5 sub-columns depending on display mode)

A vertical white divider line separates this group from the PE-CE OI group (after the Z sub-column).

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

#### Call OI → Std(10) / Call OI → Z
| Detail | Value |
|--------|-------|
| **What they show** | Same rolling sample-stdev and Z-score logic as Put OI → Std(10)/Z, applied to Call OI Change instead of Put OI Change |

---

### 🟣 `PE-CE OI` Group (4 sub-columns: Total, Change, Net Z, Signal)

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

#### PE-CE OI → Net Z
| Detail | Value |
|--------|-------|
| **What it shows** | Compares how extreme the Put move is vs how extreme the Call move is, right now |
| **How calculated** | `Net Z = Z_Put − Z_Call` |
| **Why it matters** | A large Put Z alone could just mean puts are noisy; Net Z confirms puts are moving extreme **specifically relative to** calls, filtering out moves where both sides are equally chaotic (which would be a "confused market", not a real signal) |

#### PE-CE OI → Signal
| Detail | Value |
|--------|-------|
| **What it shows** | The Z-score engine's trade signal: `BUY`, `SELL`, or `WAIT` |
| **Decision rule** | `Net Z < -3.0` → **BUY** (puts panicking much harder than calls = bullish squeeze). `Net Z > +3.0` → **SELL** (calls panicking much harder than puts = bearish squeeze). Otherwise → **WAIT** |
| **Row highlight** | The entire row is highlighted **yellow** whenever Signal is BUY or SELL (same color for both — read the Signal text to tell direction) |
| **No guardrails** | Implemented as the exact formula with no denominator floor / outlier filtering — a near-zero Std(10) can still produce a large, less-trustworthy Z. See "Known Limitations" below |

---

### 📈 `PCR` (Put-Call Ratio) — API/export only, not shown as a table column

| Detail | Value |
|--------|-------|
| **What it shows** | Ratio of total Put OI to total Call OI |
| **How calculated** | `pcr = total_pe_oi / total_ce_oi` |
| **PCR > 1.0** | More puts than calls → **Bullish** (writers confident market won't fall) |
| **PCR < 0.7** | Far more calls than puts → **Bearish** (heavy resistance above) |
| **PCR 0.8 – 1.2** | Neutral / sideways zone |
| **Sweet spot** | PCR between **0.9 – 1.1** = balanced market; extremes = potential reversal |

---

### 📊 `CE Volume` — API/export only, not shown as a table column

| Detail | Value |
|--------|-------|
| **What it shows** | Total Call option trading volume across all strikes in the selected ATM ± Range |
| **How calculated** | `ce_volume = sum of ce_volume for all strikes within range` |
| **Why it matters** | High volume = active trading/liquidity on the call side. Volume shows trading activity, OI shows outstanding positions |
| **Data source** | Per-strike volume from Angel One API, aggregated within range |

### 📊 `PE Volume` — API/export only, not shown as a table column

| Detail | Value |
|--------|-------|
| **What it shows** | Total Put option trading volume across all strikes in the selected ATM ± Range |
| **How calculated** | `pe_volume = sum of pe_volume for all strikes within range` |
| **Why it matters** | High volume = active trading/liquidity on the put side |
| **Historical** | Volume is saved to DB per-strike (`oi_snapshots.ce_volume`, `oi_snapshots.pe_volume`) and available in historical mode |

---

### 🔷 `Total OI` — API/export only, not shown as a table column

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
| **Total OI** | Total + Change + Std(10) + Z | Total + Change + Std(10) + Z | Total + Change + Net Z + Signal |
| **OI Change (Day)** | Chg(Day) + Change + Std(10) + Z | Chg(Day) + Change + Std(10) + Z | Total + Change + Net Z + Signal |
| **All** | Total + Chg(Day) + Change + Std(10) + Z | Total + Chg(Day) + Change + Std(10) + Z | Total + Change + Net Z + Signal |

Std(10)/Z/Net Z/Signal are always shown in every display mode — only the OI columns to their left change.

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

## ⬇️ CSV / Markdown Download

- Click the **⬇ CSV** or **⬇ MD** button next to the display mode buttons
- Downloads the currently displayed data as a CSV or structured Markdown file
- Filename format: `NIFTY_OI_2026-07-22_1m.csv` / `.md` (date + timeframe)
- Works for both **Live** and **Historical** mode — if historical data is loaded, downloads that loaded day's data
- Both reuse the same `get_oi_table()` call the on-screen table uses (same range, timeframe, ATM mode) — guaranteed to match what's displayed
- The Markdown file includes a header block (mode, timeframe, range, generated time, row count) followed by a full Markdown table

### CSV / MD Columns (19 total):
```
Time,
Put OI Total, Put OI Chg(Day), Put OI Change, Put Std(10), Put Z,
Call OI Total, Call OI Chg(Day), Call OI Change, Call Std(10), Call Z,
PE-CE OI, PE-CE Change, Net Z, Signal, PCR,
CE Volume, PE Volume, Total OI
```

---

## ⓘ Column Info Icons

The Time column and each group header (Put OI, Call OI, PE-CE OI) has a small **ⓘ** info icon.

- **Click** the icon → a popup appears with a detailed explanation of that group's columns
- **Click anywhere outside** → popup dismisses
- Definitions live in `COL_INFO` in `web/static/js/app.js` — includes formulas, interpretation guides, and bullish/bearish signals

---

## 🎨 Row Highlighting

| Highlight | Condition | Meaning |
|-----------|-----------|---------|
| **Yellow row** (left border) | PE-CE Signal column is `BUY` or `SELL` for that row | The Z-score signal engine detected a statistically extreme, one-sided OI move — same yellow color for both BUY and SELL, read the Signal text/color to tell direction |

The Z/Net Z cell text itself is also colored: green-ish (`val-pos`) when abs(value) ≥ 2, red-ish (`val-neg`) when abs(value) ≥ 3, neutral otherwise. Signal text is green for BUY, red for SELL.

*(Earlier versions of this dashboard highlighted rows based on Avg(10m)/Ratio columns and a 3-consecutive-negative-streak rule — both were removed in favor of the Z-score signal engine, described in full in the next section.)*

---

## 🎯 The Z-Score Signal Engine (Std(10) / Z / Net Z / Signal)

This replaced an earlier "Avg(10m) / Ratio" rolling-average feature. The full math:

**Step 1 — Rolling window.** For row `t`, look at the 10 rows STRICTLY BEFORE it (`t-1` to `t-10`) for both Put OI Change and Call OI Change. The current row is never included in its own window, and the window never reaches back into row 0 (the day's very first row, whose Change is a placeholder 0, not real data).

**Step 2 — Averages.**
```
Avg10_Put  = mean(PutChange_t-1 .. PutChange_t-10)
Avg10_Call = mean(CallChange_t-1 .. CallChange_t-10)
```

**Step 3 — Sample standard deviations** (denominator n-1):
```
Std10_Put  = sqrt( Σ(PutChange_t-i  − Avg10_Put)²  / 9 )
Std10_Call = sqrt( Σ(CallChange_t-i − Avg10_Call)² / 9 )
```

**Step 4 — Individual Z-scores:**
```
Z_Put  = (PutChange_t  − Avg10_Put)  / Std10_Put
Z_Call = (CallChange_t − Avg10_Call) / Std10_Call
```

**Step 5 — Net Z (the master signal):**
```
Net Z = Z_Put − Z_Call
```

**Step 6 — Decision (threshold ±3.0):**
- `Net Z < -3.0` → **BUY** (Bullish Squeeze — puts panicking much harder than calls)
- `Net Z > +3.0` → **SELL** (Bearish Squeeze — calls panicking much harder than puts)
- Otherwise → **WAIT** (no clear institutional direction)

### First valid row

Row 0 (day's first snapshot, e.g. 9:15) and rows 1-10 (9:16-9:25) always show `--` — not enough prior real changes to fill a 10-row window yet. The first row with a full valid window is the one completing 10 prior real changes — **9:26** if the day starts at 9:15 (one row later than the retired Avg(10m) feature's 9:25, because Avg(10m)'s window included the current row while Z's window strictly excludes it).

### Where it's computed

| Context | Computed by | Notes |
|---------|------------|-------|
| **Live UI** | `web/api_routes.py` → `get_oi_table()` STEP 4.5 | Recomputed on every request, for whatever strike range is selected in the UI |
| **Historical UI** | `web/api_routes.py` → `_get_historical_oi_table()` STEP 4 | Identical logic to live, reads from `oi_snapshots`/`market_snapshots` |
| **Live DB persistence** | `run.py` → `_update_z_signal()` | Runs every ~60s tick during market hours, fixed at **range=5** (ATM ± 5 strikes), writes to `market_snapshots.pe_std10/ce_std10/pe_z/ce_z/net_z/signal_z` |
| **Historical DB backfill** | `scripts/backfill_zscore_sql.py` | One-off SQL script (window functions, `STDDEV_SAMP`) — backfilled all historical trading days at range=5. Verified to match the Python engine exactly, row for row |

**Important**: the UI always recomputes live for whichever range you've selected (1, 5, 10, 15, 20, or All) — it never reads the DB's persisted `pe_std10`/`ce_z`/etc. columns. Those DB columns exist only as a fixed-range=5 snapshot for external querying/export (e.g. if you want to query the raw numbers directly in Postgres without going through the API). Same design as the retired Avg(10m)/Ratio columns, which still exist in the DB (still computed live + backfilled) even though they're no longer shown in the UI.

### Known limitations (discussed but not yet implemented)

- **No denominator floor.** If Std10 is near zero (an unusually calm preceding 10 minutes), even a small, ordinary Change can produce a very large, less-meaningful Z-score.
- **No outlier/data-glitch guard.** A single bad snapshot (e.g. a WebSocket reconnect causing OI to briefly read as a huge spike then bounce back) will corrupt every Z-score computed from a window that includes it, for the next 10 rows.
- **Net Z compares two independently-scaled quantities.** Z_Put and Z_Call are each standardized against their own volatility; if one side has been much calmer than the other recently, subtracting them isn't a perfectly fair comparison.
- **Row-count window, not wall-clock window.** "10 rows" can span more than 10 real minutes if a fetch failure causes a gap.
- **The ±3.0 threshold is not empirically tuned** against this dashboard's actual historical signal distribution — it was supplied as a round number.

---

## 🧭 Multi-Timeframe Confirmation

Builds directly on top of the single-timeframe Z-score engine above to address its biggest weakness: **a lone 1-minute Z spike is noise-prone** (one bad snapshot, or a Std10 that happens to be unusually small, can trip BUY/SELL on an otherwise ordinary move). Multi-timeframe confirmation checks whether the same direction shows up across 1m/3m/5m/10m/15m/30m before trusting it — standard multi-timeframe confluence, applied to this dashboard's existing Z-scores.

Shown as a compact strip above the OI table (**Multi-TF Confirmation**), computed identically for live and historical mode.

### The pieces

| Piece | What it does |
|-------|--------------|
| **Per-TF badges** | Each of the 6 timeframes' current Net Z, colored green (bullish lean) / red (bearish) / neutral, outlined when it agrees with the 1-minute trigger direction |
| **Trend Score** | Per timeframe: `+1` if Net Z ≤ -1.0 (bullish lean), `-1` if Net Z ≥ +1.0 (bearish lean), `0` otherwise. Deliberately looser than the ±3.0 BUY/SELL signal threshold — this asks "which way is this timeframe leaning", not "is it a full signal" (longer timeframes rarely swing past ±3.0) |
| **Z Cascade** | Does Net Z's direction hold across 1m→3m→5m→10m in order? Reports direction + how many consecutive timeframes (starting from 1m) keep agreeing before the chain breaks |
| **Agreement Score (0-100)** | Weighted sum: awards each timeframe's weight (1m=25, 3m=20, 5m=20, 10m=15, 15m=10, 30m=10) if its Trend Score matches the 1-minute trigger's direction. 0 if the 1-minute trigger itself is neutral (nothing to confirm) |
| **Conviction** | Bucketed from Agreement Score: ≥90 `very_strong`, ≥70 `strong`, ≥50 `watch`, else `no_edge` |
| **Std Expansion** | Compares each timeframe's CURRENT Std10 to ITS OWN Std10 from 5 rows ago (same timeframe, not cross-timeframe — Std10 naturally grows with timeframe size since longer windows accumulate bigger OI swings, so comparing 1m's Std to 3m's Std directly would almost never show "expansion" regardless of what's happening). Flags "Compression→Expansion" when short TFs (1m/3m) show their OWN volatility rising while long TFs (10m/15m) show their OWN volatility still flat/unclear — a setup that often precedes a directional move |
| **Persistence Filter → Final Signal** | The single highest-value piece. Takes the raw 1-minute Signal and **downgrades BUY/SELL to WAIT** unless 3m's Trend Score confirms the same direction AND 5m's Trend Score doesn't actively contradict it. This is what actually suppresses false positives — see the worked 10:18 example below |

**Important: Agreement Score and Final Signal answer two different questions, and can legitimately disagree.** Agreement Score's "trigger direction" comes from the 1-minute Trend Score (the loose ±1.0 threshold — "is 1m even leaning a direction, at all"), so it can be a high, confident-looking number even when nothing has actually crossed the ±3.0 BUY/SELL bar yet. Final Signal only exists/fires off the raw 1-minute **Signal** (the strict ±3.0 threshold), then applies the persistence filter on top. So seeing e.g. Agreement Score 80 ("strong") next to Final Signal WAIT is not a bug — it means the move is building consensus across timeframes but hasn't reached a hard trigger on 1m yet.

### Worked example (2026-07-22, verified against real data)

At 10:18, the raw 1-minute engine showed Net Z = **-5.53**, which alone would trigger a BUY (crosses -3.0). But:
- 3-minute Net Z = **+0.44** → Trend Score = 0 (neutral, does NOT confirm bullish)
- 5-minute Net Z = **+2.14** → Trend Score = -1 (actively bearish, contradicts)

Per the persistence rule, 3m failing to confirm alone is enough to downgrade the signal — **Final Signal = WAIT**, not BUY. This is exactly the "lone noisy 1-minute spike" failure mode the whole feature exists to catch. Compare to 10:27/10:29 the same day, where 1m, 3m, and 5m all agreed bullish — those kept their raw BUY signal through the filter, and 10:29 reached Agreement Score 65 ("watch").

### Where it's computed

| Context | Computed by | Notes |
|---------|------------|-------|
| **Live UI** | `web/api_routes.py` → `get_multi_tf_signal()` | Calls the existing `get_oi_table()` once per timeframe (reused as-is, no duplicated aggregation logic), reads from the in-memory live buffer — no DB round-trip, ~30ms |
| **Historical UI** | Same endpoint, `mode=historical` | Calls `_get_historical_oi_table()` once per timeframe; the 6 calls run concurrently via `asyncio.to_thread` (both historical functions are `async def` but do blocking `psycopg2` I/O with no real `await` points, so plain `asyncio.gather` would have serialized them) — a full day takes ~12s |
| **Live DB persistence** | `run.py` → calls `get_multi_tf_signal()` once per tick via `asyncio.run()` inside the data-logger thread, writes the composite to `market_snapshots.mtf_*` | Only the FINAL composite is stored (agreement score, conviction, cascade direction/depth, compression-expansion flag, final signal) — not all 6 raw per-TF Z-scores, which are cheap to recompute live and not worth 30+ extra columns |
| **Historical DB backfill** | `scripts/backfill_multi_tf_sql.py` | Hybrid approach: 6 fast per-timeframe SQL queries (identical window-function pattern to the verified single-TF backfill) + a lightweight Python merge pass per day (as-of lookup + the same weight/threshold arithmetic as the live engine), bulk-written via `psycopg2.extras.execute_values`. A pure-SQL version using correlated `LATERAL` joins was tried first and was too slow/complex (timed out on a single day) — abandoned in favor of this hybrid. Does NOT backfill `mtf_compression_expansion` (always `FALSE` for historical rows) — that flag depends on comparing Std10 trend across all 6 timeframes' own history, not yet built into the backfill |

**Important caveats**:
- The weights (25/20/20/15/10/10) and the ±1.0 trend threshold are first-pass defaults, explicitly **not** empirically tuned against this dashboard's actual historical signal distribution — same caveat as the single-TF ±3.0 threshold. Revisit once there's enough historical Final Signal data to backtest against.
- `mtf_compression_expansion` is only ever computed live, never backfilled — historical rows always read `FALSE` for that column.
- Live persistence and historical backfill are, once again, **independent implementations of the same math** (Python in-process vs. hybrid SQL+Python) — verified to agree exactly on sampled rows, but not shared code, so re-verify after changing either one.

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
| pe_change_avg_10m / ce_change_avg_10m | FLOAT | Retired Avg(10m) feature — still computed live + backfilled at range=5, no longer shown in UI |
| pe_change_ratio / ce_change_ratio | FLOAT | Retired Ratio feature — still computed live + backfilled at range=5, no longer shown in UI |
| pe_std10 / ce_std10 | FLOAT | Z-score engine's sample std dev of OI Change over the prior 10 rows, at range=5 |
| pe_z / ce_z | FLOAT | Individual Z-scores at range=5 |
| net_z | FLOAT | `pe_z - ce_z`, at range=5 |
| signal_z | VARCHAR(10) | `'BUY'` / `'SELL'` / `'WAIT'` / `'--'`, at range=5 |
| mtf_agreement_score | INT | Multi-TF weighted agreement score (0-100), at range=5 |
| mtf_conviction | VARCHAR(20) | `'very_strong'` / `'strong'` / `'watch'` / `'no_edge'` |
| mtf_cascade_direction | VARCHAR(10) | `'bullish'` / `'bearish'` / `'--'` — Z cascade direction across 1m→3m→5m→10m |
| mtf_cascade_depth | INT | How many consecutive timeframes (from 1m) keep agreeing before the cascade breaks |
| mtf_compression_expansion | BOOLEAN | Std Expansion "Compression→Expansion" setup flag — live-only, always `FALSE` for backfilled historical rows |
| mtf_final_signal | VARCHAR(10) | The persistence-filtered signal (`'BUY'`/`'SELL'`/`'WAIT'`/`'--'`) — may downgrade the raw 1m `signal_z` to `WAIT` |

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
├── scripts/
│   ├── backfill_sql.py             # One-off: backfill Avg(10m)/Ratio at range=5 for all historical days
│   ├── backfill_zscore_sql.py      # One-off: backfill Std(10)/Z/Net Z/Signal at range=5 for all historical days
│   └── backfill_multi_tf_sql.py    # One-off: backfill multi-TF agreement/cascade/final signal at range=5 for all historical days
├── data/cache/         # Cached instrument data
└── logs/               # Application logs
```

---

## 🔧 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/oi-table` | GET | Main OI table data (live + historical) |
| `/api/download-oi` | GET | Download OI table as CSV |
| `/api/download-oi-md` | GET | Download OI table as a structured Markdown file |
| `/api/multi-tf-signal` | GET | Multi-timeframe confirmation snapshot (per-TF Z, cascade, agreement score, final signal) |
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
║           ON-SCREEN TABLE COLUMNS (13 total)                  ║
╠══════════════════════════════════════════════════════════════╣
║  Time | Put OI(+Std10,Z) | Call OI(+Std10,Z)                 ║
║  PE-CE OI(+Net Z, Signal)                                     ║
║  (PCR/CE Vol/PE Vol/Total OI: in CSV/MD export only)          ║
╠══════════════════════════════════════════════════════════════╣
║                Z-SCORE SIGNAL GUIDE                           ║
╠══════════════════════════════════════════════════════════════╣
║  Net Z < -3.0  = BUY  (puts collapsing vs calls)             ║
║  Net Z > +3.0  = SELL (calls collapsing vs puts)             ║
║  -3.0 to +3.0  = WAIT (no clear direction)                   ║
║  Row highlights YELLOW on BUY or SELL                         ║
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
| **CSV/MD use same API** | Both download endpoints reuse `get_oi_table()` — guaranteed data consistency with the on-screen table |
| **Z-score window excludes current row** | Matches the originally-specified formula exactly (Avg10/Std10 computed from the 10 PRIOR rows, not including the row being scored) — this is why Z's first valid row (9:26) is one minute later than the retired Avg(10m)'s first row (9:25) |
| **DB persistence fixed at range=5** | Avg/Ratio and Std/Z/NetZ/Signal are persisted to `market_snapshots` only at range=5 (live per-tick + SQL backfill for history) for external querying — the UI never reads these columns back, it always recomputes live for whatever range is selected |
| **Live vs historical use independent code paths for the same math** | `run.py`'s `_update_z_signal()` (live, in-memory rolling deque) and `scripts/backfill_zscore_sql.py` (historical, Postgres window functions) are separate implementations of the identical formula — verified to agree exactly via direct comparison, not shared code, so re-verify after changing either one |

---

> **Disclaimer**: This tool is for **educational and informational purposes only**. Options trading involves significant risk. Past OI patterns do not guarantee future results. Always use proper risk management and stop-losses.
