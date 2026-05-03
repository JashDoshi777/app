"""
Options Data Engine — Production Entry Point.
Fetches live option chain every minute, logs to NeonDB, serves dashboard.
Includes: auto-reconnect, DB retry, health checks.
"""

import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from collections import deque

import uvicorn

import config
from core.market_data import MarketDataService
from core.option_chain import OptionChainAnalyzer
from web.app import app
from web.api_routes import router as api_router, inject_engines

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))

# ─── Logging Setup ───────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_DIR / "app.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("MAIN")


# ═══════════════════════════════════════════════════════════
#  IN-MEMORY DATA BUFFER (for fast UI serving)
# ═══════════════════════════════════════════════════════════

data_buffer = {
    "oi_table": deque(maxlen=500),
    "oi_strikes": deque(maxlen=500),
    "candles_1m": deque(maxlen=500),
    "latest_chain": None,
    "latest_underlying": 0,
    "prev_pe_ce_diff": 0,
    "prev_total_ce_oi": 0,
    "prev_total_pe_oi": 0,
    "prev_total_oi": 0,
    "db_available": False,
    "last_log_time": None,
    "total_logs": 0,
    "errors": 0,
    "data_source": "NONE",
    "start_time": datetime.now(IST).isoformat(),
}


def is_market_open():
    """Check if Indian market is currently open (IST)."""
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


# ═══════════════════════════════════════════════════════════
#  DATA LOGGING LOOP (every 60 seconds)
# ═══════════════════════════════════════════════════════════

def data_logger_loop(state):
    """
    Background thread: every 60 seconds during market hours,
    fetch option chain, compute metrics, store in DB + memory.
    """
    md = state["market_data"]
    db_engine = state.get("db_engine")

    logger.info("Data logger started — will log every 60s during market hours (IST).")

    # Track candle building
    candle_open = candle_high = candle_low = candle_close = 0
    candle_volume = 0
    last_candle_minute = -1

    consecutive_failures = 0
    MAX_FAILURES_BEFORE_RECONNECT = 5

    while True:
        try:
            now_ist = datetime.now(IST)

            if not is_market_open():
                time.sleep(30)
                continue

            # ── Auto-reconnect Angel One if too many failures ──
            if consecutive_failures >= MAX_FAILURES_BEFORE_RECONNECT:
                logger.warning("[RECONNECT] %d consecutive failures, re-initializing...", consecutive_failures)
                try:
                    md._init_tier1_smartapi()
                    consecutive_failures = 0
                    logger.info("[RECONNECT] Angel One session refreshed.")
                except Exception as re:
                    logger.error("[RECONNECT] Failed: %s", re)

            # ── Fetch live data ──────────────────────────
            underlying = md.get_ltp("NIFTY") or 0
            chain_df = md.get_option_chain("NIFTY")

            if chain_df is None or chain_df.empty or underlying <= 0:
                consecutive_failures += 1
                data_buffer["errors"] += 1
                logger.warning("No data received (attempt %d) - retrying in 30s", consecutive_failures)
                time.sleep(30)
                continue

            consecutive_failures = 0  # Reset on success

            data_buffer["latest_chain"] = chain_df
            data_buffer["latest_underlying"] = underlying

            # ── Compute aggregate metrics ────────────────
            total_ce_oi = int(chain_df["ce_oi"].sum())
            total_pe_oi = int(chain_df["pe_oi"].sum())
            total_ce_vol = int(chain_df["ce_volume"].sum())
            total_pe_vol = int(chain_df["pe_volume"].sum())
            pe_ce_diff = total_pe_oi - total_ce_oi
            pe_ce_diff_change = pe_ce_diff - data_buffer["prev_pe_ce_diff"]
            pcr = round(total_pe_oi / max(total_ce_oi, 1), 4)

            # Future LTP — compute FIRST (needed for accurate ATM)
            future_ltp = md.get_futures_ltp("NIFTY")
            if future_ltp <= 0:
                future_ltp = underlying  # Fallback to spot

            # ATM strike — use Futures LTP for ATM (matches StockMojo)
            # StockMojo uses futures price for ATM, not spot.
            # e.g. Spot=24160, Futures=24232 → ATM should be 24250, not 24150
            strike_interval = config.INDICES["NIFTY"]["strike_interval"]
            atm_price = future_ltp if future_ltp > 0 else underlying
            atm = round(atm_price / strike_interval) * strike_interval
            atm_row = chain_df[chain_df["strike"] == atm]
            atm_ce_ltp = float(atm_row["ce_ltp"].iloc[0]) if not atm_row.empty else 0
            atm_pe_ltp = float(atm_row["pe_ltp"].iloc[0]) if not atm_row.empty else 0
            straddle = round(atm_ce_ltp + atm_pe_ltp, 2)

            # ── Build 1-min candle ───────────────────────
            current_minute = now_ist.minute
            if current_minute != last_candle_minute:
                # Save previous candle
                if last_candle_minute >= 0 and candle_open > 0:
                    data_buffer["candles_1m"].append({
                        "timestamp": now_ist.replace(second=0, microsecond=0).isoformat(),
                        "open": candle_open, "high": candle_high,
                        "low": candle_low, "close": candle_close,
                        "volume": candle_volume,
                    })
                # Start new candle
                candle_open = underlying
                candle_high = underlying
                candle_low = underlying
                candle_close = underlying
                candle_volume = total_ce_vol + total_pe_vol
                last_candle_minute = current_minute
            else:
                candle_high = max(candle_high, underlying)
                candle_low = min(candle_low, underlying)
                candle_close = underlying
                candle_volume += total_ce_vol + total_pe_vol

            # ── Market snapshot for table ────────────────
            futures_oi = md.get_futures_oi("NIFTY") if hasattr(md, 'get_futures_oi') else 0
            snapshot = {
                "timestamp": now_ist.strftime("%H:%M"),
                "timestamp_full": now_ist.isoformat(),
                "underlying": underlying,
                "total_pe_oi": total_pe_oi,
                "pe_oi_change_day": int(chain_df["pe_chg_oi"].sum()),
                "pe_oi_change": total_pe_oi - data_buffer["prev_total_pe_oi"] if data_buffer["prev_total_pe_oi"] > 0 else 0,
                "total_ce_oi": total_ce_oi,
                "ce_oi_change_day": int(chain_df["ce_chg_oi"].sum()),
                "ce_oi_change": total_ce_oi - data_buffer["prev_total_ce_oi"] if data_buffer["prev_total_ce_oi"] > 0 else 0,
                "pe_ce_diff": pe_ce_diff,
                "pe_ce_diff_change": pe_ce_diff_change,
                "pcr": pcr,
                "future_ltp": future_ltp,
                "futures_oi": futures_oi,
                "straddle": straddle,
                "atm_strike": atm,
                "atm_ce_ltp": atm_ce_ltp,
                "atm_pe_ltp": atm_pe_ltp,
                "signal": _compute_signal(future_ltp, data_buffer, total_ce_oi + total_pe_oi),
            }
            data_buffer["oi_table"].appendleft(snapshot)

            # ── Per-strike data ──────────────────────────
            strike_rows = []
            for _, row in chain_df.iterrows():
                strike_rows.append({
                    "timestamp": now_ist.isoformat(),
                    "strike": float(row["strike"]),
                    "ce_oi": int(row["ce_oi"]),
                    "pe_oi": int(row["pe_oi"]),
                    "ce_chg_oi": int(row["ce_chg_oi"]),
                    "pe_chg_oi": int(row["pe_chg_oi"]),
                    "ce_ltp": float(row["ce_ltp"]),
                    "pe_ltp": float(row["pe_ltp"]),
                    "ce_iv": float(row.get("ce_iv", 0)),
                    "pe_iv": float(row.get("pe_iv", 0)),
                    "ce_volume": int(row["ce_volume"]),
                    "pe_volume": int(row["pe_volume"]),
                    "pe_ce_diff": int(row["pe_oi"]) - int(row["ce_oi"]),
                })
            data_buffer["oi_strikes"].appendleft({
                "timestamp": now_ist.isoformat(),
                "strikes": strike_rows,
            })

            # Update prev values
            data_buffer["prev_pe_ce_diff"] = pe_ce_diff
            data_buffer["prev_total_ce_oi"] = total_ce_oi
            data_buffer["prev_total_pe_oi"] = total_pe_oi
            data_buffer["prev_total_oi"] = total_ce_oi + total_pe_oi
            data_buffer["last_log_time"] = now_ist.isoformat()
            data_buffer["total_logs"] += 1
            data_buffer["data_source"] = getattr(md, '_data_source_log', 'UNKNOWN')

            # ── Save to database (with retry) ────────────
            if db_engine:
                for attempt in range(3):
                    try:
                        _save_to_db(db_engine, now_ist, snapshot, chain_df, underlying, future_ltp, pcr, pe_ce_diff, pe_ce_diff_change, straddle, atm, atm_ce_ltp, atm_pe_ltp, total_ce_oi, total_pe_oi, total_ce_vol, total_pe_vol)
                        break
                    except Exception as e:
                        if attempt < 2:
                            logger.warning("DB write attempt %d failed: %s - retrying...", attempt + 1, e)
                            time.sleep(2)
                        else:
                            logger.error("DB write failed after 3 attempts: %s", e)
                            data_buffer["errors"] += 1

            logger.info(
                "[DATA] %s | NIFTY: %.2f | CE OI: %s | PE OI: %s | PE-CE: %s | PCR: %.2f | Straddle: %.2f | Src: %s",
                now_ist.strftime("%H:%M:%S"), underlying,
                _fmt_lakh(total_ce_oi), _fmt_lakh(total_pe_oi),
                _fmt_lakh(pe_ce_diff), pcr, straddle,
                data_buffer["data_source"]
            )

            time.sleep(60)

        except Exception as e:
            data_buffer["errors"] += 1
            logger.error("Data logger error: %s", e, exc_info=True)
            time.sleep(15)


def _fmt_lakh(n):
    """Format number in Indian lakh notation."""
    if abs(n) >= 10000000:
        return f"{n/10000000:.1f} Cr"
    if abs(n) >= 100000:
        return f"{n/100000:.1f} L"
    if abs(n) >= 1000:
        return f"{n/1000:.1f} K"
    return str(n)


def _compute_signal(current_price, data_buffer, current_total_oi):
    """
    Compute market signal based on price direction + total OI direction.
    Matches StockMojo's OI buildup classification:
    - LB: Long Buildup  (Price ↑, Total OI ↑) — fresh longs entering
    - SB: Short Buildup (Price ↓, Total OI ↑) — fresh shorts entering
    - SC: Short Covering (Price ↑, Total OI ↓) — shorts exiting
    - LU: Long Unwinding (Price ↓, Total OI ↓) — longs exiting
    - N/A: No clear signal (price or OI unchanged)
    """
    prev_price = data_buffer.get("_prev_underlying", 0)
    prev_total_oi = data_buffer.get("prev_total_oi", 0)

    if prev_price <= 0 or prev_total_oi <= 0:
        data_buffer["_prev_underlying"] = current_price
        return "N/A"

    # StockMojo shows N/A when price or OI is unchanged between minutes
    price_changed = abs(current_price - prev_price) > 0.01
    oi_changed = current_total_oi != prev_total_oi

    data_buffer["_prev_underlying"] = current_price

    if not price_changed or not oi_changed:
        return "N/A"

    price_up = current_price > prev_price
    oi_up = current_total_oi > prev_total_oi

    if price_up and oi_up:
        return "LB"  # Long Buildup
    elif not price_up and oi_up:
        return "SB"  # Short Buildup
    elif price_up and not oi_up:
        return "SC"  # Short Covering
    elif not price_up and not oi_up:
        return "LU"  # Long Unwinding
    return "N/A"


def _save_to_db(db_engine, timestamp, snapshot, chain_df, underlying, future_ltp, pcr, pe_ce_diff, pe_ce_diff_change, straddle, atm, atm_ce_ltp, atm_pe_ltp, total_ce_oi, total_pe_oi, total_ce_vol, total_pe_vol):
    """Synchronous DB write using psycopg2 (runs in background thread)."""
    import psycopg2

    db_url = os.environ.get("DATABASE_URL", config.DATABASE_URL)
    if not db_url:
        return

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    try:
        # Insert market snapshot
        futures_oi = snapshot.get("futures_oi", 0)
        cur.execute("""
            INSERT INTO market_snapshots
            (timestamp, symbol, underlying_price, open, high, low, close, volume,
             total_ce_oi, total_pe_oi, total_ce_volume, total_pe_volume,
             pe_ce_oi_diff, pe_ce_oi_diff_change, pcr,
             future_ltp, future_oi_change, atm_strike, atm_ce_ltp, atm_pe_ltp, straddle_price, futures_oi)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            timestamp, "NIFTY", underlying, underlying, underlying, underlying, underlying,
            total_ce_vol + total_pe_vol,
            total_ce_oi, total_pe_oi, total_ce_vol, total_pe_vol,
            pe_ce_diff, pe_ce_diff_change, pcr,
            future_ltp, 0, atm, atm_ce_ltp, atm_pe_ltp, straddle, futures_oi
        ))

        # Insert per-strike snapshots
        for _, row in chain_df.iterrows():
            strike_pe_ce_diff = int(row["pe_oi"]) - int(row["ce_oi"])
            cur.execute("""
                INSERT INTO oi_snapshots
                (timestamp, symbol, strike, underlying_price, expiry,
                 ce_oi, ce_chg_oi, ce_volume, ce_ltp, ce_iv, ce_delta, ce_gamma, ce_theta, ce_vega,
                 pe_oi, pe_chg_oi, pe_volume, pe_ltp, pe_iv, pe_delta, pe_gamma, pe_theta, pe_vega,
                 pe_ce_oi_diff, pe_ce_oi_diff_change, pcr, future_ltp, future_oi_change)
                VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s)
            """, (
                timestamp, "NIFTY", float(row["strike"]), underlying, str(row.get("expiry", "")),
                int(row["ce_oi"]), int(row["ce_chg_oi"]), int(row["ce_volume"]),
                float(row["ce_ltp"]), float(row.get("ce_iv", 0)),
                float(row.get("ce_delta", 0)), float(row.get("ce_gamma", 0)),
                float(row.get("ce_theta", 0)), float(row.get("ce_vega", 0)),
                int(row["pe_oi"]), int(row["pe_chg_oi"]), int(row["pe_volume"]),
                float(row["pe_ltp"]), float(row.get("pe_iv", 0)),
                float(row.get("pe_delta", 0)), float(row.get("pe_gamma", 0)),
                float(row.get("pe_theta", 0)), float(row.get("pe_vega", 0)),
                strike_pe_ce_diff, int(row["pe_chg_oi"]) - int(row["ce_chg_oi"]),
                pcr, future_ltp, 0
            ))

        conn.commit()
        logger.debug("DB: saved %d strike rows", len(chain_df))

    finally:
        cur.close()
        conn.close()


# ═══════════════════════════════════════════════════════════
#  DATABASE INIT
# ═══════════════════════════════════════════════════════════

def init_database_sync():
    """Create tables using psycopg2 (synchronous)."""
    db_url = os.environ.get("DATABASE_URL", config.DATABASE_URL)
    if not db_url:
        logger.warning("No DATABASE_URL — DB logging disabled.")
        return None

    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS market_snapshots (
                id BIGSERIAL PRIMARY KEY,
                timestamp TIMESTAMPTZ NOT NULL,
                symbol VARCHAR(20) DEFAULT 'NIFTY',
                underlying_price FLOAT NOT NULL,
                open FLOAT DEFAULT 0, high FLOAT DEFAULT 0,
                low FLOAT DEFAULT 0, close FLOAT DEFAULT 0,
                volume BIGINT DEFAULT 0,
                total_ce_oi BIGINT DEFAULT 0, total_pe_oi BIGINT DEFAULT 0,
                total_ce_volume BIGINT DEFAULT 0, total_pe_volume BIGINT DEFAULT 0,
                pe_ce_oi_diff BIGINT DEFAULT 0, pe_ce_oi_diff_change BIGINT DEFAULT 0,
                pcr FLOAT DEFAULT 0,
                future_ltp FLOAT DEFAULT 0, future_oi_change BIGINT DEFAULT 0,
                atm_strike FLOAT DEFAULT 0, atm_ce_ltp FLOAT DEFAULT 0,
                atm_pe_ltp FLOAT DEFAULT 0, straddle_price FLOAT DEFAULT 0,
                futures_oi BIGINT DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS ix_mkt_ts ON market_snapshots(timestamp);
            CREATE INDEX IF NOT EXISTS ix_mkt_sym_ts ON market_snapshots(symbol, timestamp);
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS oi_snapshots (
                id BIGSERIAL PRIMARY KEY,
                timestamp TIMESTAMPTZ NOT NULL,
                symbol VARCHAR(20) DEFAULT 'NIFTY',
                strike FLOAT NOT NULL,
                underlying_price FLOAT NOT NULL,
                expiry VARCHAR(20) DEFAULT '',
                ce_oi BIGINT DEFAULT 0, ce_chg_oi BIGINT DEFAULT 0,
                ce_volume BIGINT DEFAULT 0, ce_ltp FLOAT DEFAULT 0,
                ce_iv FLOAT DEFAULT 0, ce_delta FLOAT DEFAULT 0,
                ce_gamma FLOAT DEFAULT 0, ce_theta FLOAT DEFAULT 0, ce_vega FLOAT DEFAULT 0,
                pe_oi BIGINT DEFAULT 0, pe_chg_oi BIGINT DEFAULT 0,
                pe_volume BIGINT DEFAULT 0, pe_ltp FLOAT DEFAULT 0,
                pe_iv FLOAT DEFAULT 0, pe_delta FLOAT DEFAULT 0,
                pe_gamma FLOAT DEFAULT 0, pe_theta FLOAT DEFAULT 0, pe_vega FLOAT DEFAULT 0,
                pe_ce_oi_diff BIGINT DEFAULT 0, pe_ce_oi_diff_change BIGINT DEFAULT 0,
                pcr FLOAT DEFAULT 0,
                future_ltp FLOAT DEFAULT 0, future_oi_change BIGINT DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS ix_oi_ts ON oi_snapshots(timestamp);
            CREATE INDEX IF NOT EXISTS ix_oi_ts_strike ON oi_snapshots(timestamp, strike);
            CREATE INDEX IF NOT EXISTS ix_oi_sym_ts ON oi_snapshots(symbol, timestamp);
        """)

        conn.commit()

        # Migration: add futures_oi column if it doesn't exist (for existing DBs)
        try:
            cur2 = conn.cursor()
            cur2.execute("ALTER TABLE market_snapshots ADD COLUMN IF NOT EXISTS futures_oi BIGINT DEFAULT 0;")
            conn.commit()
            cur2.close()
        except Exception:
            conn.rollback()  # Column might already exist
        cur.close()
        conn.close()
        logger.info("[OK] Database tables ready.")
        return True

    except Exception as e:
        logger.error("DB init failed: %s", e)
        return None


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════

def main():
    logger.info("=" * 60)
    logger.info("  NIFTY OI TRACKER — Production")
    logger.info("=" * 60)

    # Init database (with retry)
    db_ok = None
    for attempt in range(3):
        db_ok = init_database_sync()
        if db_ok:
            data_buffer["db_available"] = True
            break
        logger.warning("DB init attempt %d failed, retrying in 5s...", attempt + 1)
        time.sleep(5)

    # Core engines
    market_data = MarketDataService()
    option_chain = OptionChainAnalyzer("NIFTY")

    state = {
        "market_data": market_data,
        "option_chain_analyzer": option_chain,
        "db_engine": db_ok,
        "data_buffer": data_buffer,
    }

    logger.info("Data Tier: %s", market_data.data_tier)
    logger.info("DB: %s", "CONNECTED" if db_ok else "DISABLED")

    # Load previous day's closing OI from DB for accurate Chg Day computation
    if db_ok:
        db_url = os.environ.get("DATABASE_URL", config.DATABASE_URL)
        if db_url:
            market_data.load_prev_day_oi_from_db(db_url)

    # Health endpoint for HuggingFace container monitoring
    @app.get("/health")
    async def health_check():
        return {
            "status": "healthy",
            "uptime": str(datetime.now(IST) - datetime.fromisoformat(data_buffer["start_time"])),
            "total_logs": data_buffer["total_logs"],
            "errors": data_buffer["errors"],
            "last_log": data_buffer["last_log_time"],
            "data_source": data_buffer["data_source"],
            "db_connected": data_buffer["db_available"],
            "market_open": is_market_open(),
        }

    # Inject into API
    inject_engines(state)
    app.include_router(api_router)

    # Start data logger in background thread
    logger_thread = threading.Thread(target=data_logger_loop, args=(state,), daemon=True)
    logger_thread.start()
    logger.info("Background data logger started (60s interval).")

    # Start server
    port = int(os.environ.get("PORT", 7860))
    logger.info("Starting server at http://0.0.0.0:%d", port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
