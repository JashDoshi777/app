"""
REST API routes — Live OI Data, Charts, Historical.
Dynamically re-aggregates per-strike data based on range filter
to match StockMojo-style OI totals.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Query
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

router = APIRouter(prefix="/api", tags=["OI Data API"])


def _sanitize(obj):
    """Recursively convert numpy types to Python native."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    elif isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        return float(obj)
    elif isinstance(obj, (np.bool_,)):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


_engine_state = {
    "market_data": None,
    "option_chain_analyzer": None,
    "data_buffer": None,
    "db_engine": None,
}


def inject_engines(state: dict):
    _engine_state.update(state)


def _is_market_open() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def _fmt_lakh(n):
    if abs(n) >= 10000000:
        return f"{n/10000000:.1f} Cr"
    if abs(n) >= 100000:
        return f"{n/100000:.1f} L"
    if abs(n) >= 1000:
        return f"{n/1000:.1f} K"
    return str(int(n))


# ═══════════════════════════════════════════════════════════
#  HELPER: Recompute OI totals from per-strike data + range
# ═══════════════════════════════════════════════════════════

def _compute_ranged_snapshot(strike_snap, atm_strike, range_strikes):
    """
    Given a per-strike snapshot (list of strike dicts) and a range,
    recompute total OI/volume/pcr/pe_ce_diff for only strikes within range.
    This is what makes our data match StockMojo exactly.
    """
    if not strike_snap or not atm_strike:
        return None

    filtered = [s for s in strike_snap
                if abs(s["strike"] - atm_strike) <= range_strikes * 50]

    if not filtered:
        return None

    total_ce_oi = sum(s["ce_oi"] for s in filtered)
    total_pe_oi = sum(s["pe_oi"] for s in filtered)
    total_ce_vol = sum(s.get("ce_volume", 0) for s in filtered)
    total_pe_vol = sum(s.get("pe_volume", 0) for s in filtered)
    ce_chg_oi = sum(s.get("ce_chg_oi", 0) for s in filtered)
    pe_chg_oi = sum(s.get("pe_chg_oi", 0) for s in filtered)
    pe_ce_diff = total_pe_oi - total_ce_oi
    pcr = round(total_pe_oi / max(total_ce_oi, 1), 4)

    return {
        "total_ce_oi": total_ce_oi,
        "total_pe_oi": total_pe_oi,
        "total_ce_vol": total_ce_vol,
        "total_pe_vol": total_pe_vol,
        "ce_chg_oi": ce_chg_oi,
        "pe_chg_oi": pe_chg_oi,
        "pe_ce_diff": pe_ce_diff,
        "pcr": pcr,
    }


@router.get("/market-status")
async def market_status():
    now = datetime.now(IST)
    md = _engine_state.get("market_data")
    data_source = md._data_source_log if md and hasattr(md, '_data_source_log') else "UNKNOWN"
    data_tier = md.data_tier if md else "NONE"
    db = _engine_state.get("data_buffer")
    db_ok = db.get("db_available", False) if db else False
    return {
        "is_open": _is_market_open(),
        "timestamp": now.isoformat(),
        "time": now.strftime("%H:%M:%S"),
        "date": now.strftime("%d %b %Y"),
        "data_source": data_source,
        "data_tier": data_tier,
        "db_connected": db_ok,
    }


@router.get("/oi-table")
async def get_oi_table(
    tf: int = Query(1, description="Timeframe in minutes (1,3,5,15,60)"),
    range_strikes: int = Query(10, description="Number of strikes from ATM each side"),
    mode: str = Query("live", description="live or historical"),
    date: str = Query("", description="Date for historical mode (YYYY-MM-DD)"),
):
    """Minute-by-minute OI table data, dynamically aggregated by range."""

    # ── Historical mode: fetch from DB ──
    if mode == "historical" and date:
        return await _get_historical_oi_table(date, tf, range_strikes)

    buf = _engine_state.get("data_buffer")
    if not buf:
        return {"rows": []}

    oi_table_raw = list(buf["oi_table"])  # newest-first (appendleft)
    oi_strikes = list(buf["oi_strikes"])

    # Match timestamps between oi_table and oi_strikes
    strike_by_ts = {}
    for snap in oi_strikes:
        ts_key = snap["timestamp"]
        try:
            parsed = datetime.fromisoformat(ts_key)
            hhmm = parsed.strftime("%H:%M")
        except Exception:
            hhmm = ts_key
        strike_by_ts[hhmm] = snap["strikes"]

    # Timeframe aggregation: sample every N minutes (on newest-first list)
    if tf > 1 and len(oi_table_raw) > 1:
        sampled = []
        for i in range(0, len(oi_table_raw), tf):
            sampled.append(oi_table_raw[i])
        oi_table_raw = sampled

    # CRITICAL: Reverse to chronological order (oldest→newest)
    # so delta changes are computed correctly: current - previous
    chrono = list(reversed(oi_table_raw))

    # Compute all values in chronological order
    formatted = []
    prev_ce_ltp = 0
    prev_pe_ltp = 0
    prev_pe_ce_diff = 0

    for r in chrono:
        ts = r["timestamp"]  # "HH:MM"
        atm = r.get("atm_strike", 0)

        # Recompute from per-strike data using range filter
        strike_data = strike_by_ts.get(ts)
        if strike_data and atm > 0:
            ranged = _compute_ranged_snapshot(strike_data, atm, range_strikes)
        else:
            ranged = None

        if ranged:
            total_pe_oi = ranged["total_pe_oi"]
            total_ce_oi = ranged["total_ce_oi"]
            pe_chg_oi_day = ranged["pe_chg_oi"]
            ce_chg_oi_day = ranged["ce_chg_oi"]
            pe_ce_diff = ranged["pe_ce_diff"]
            pcr = ranged["pcr"]
        else:
            total_pe_oi = r["total_pe_oi"]
            total_ce_oi = r["total_ce_oi"]
            pe_chg_oi_day = r.get("pe_oi_change_day", 0)
            ce_chg_oi_day = r.get("ce_oi_change_day", 0)
            pe_ce_diff = r["pe_ce_diff"]
            pcr = r["pcr"]

        pe_oi_change = r.get("pe_oi_change", 0)
        ce_oi_change = r.get("ce_oi_change", 0)
        pe_ce_diff_change = pe_ce_diff - prev_pe_ce_diff if prev_pe_ce_diff != 0 else 0
        pe_ce_chg_day = pe_chg_oi_day - ce_chg_oi_day

        # Delta change: current minus previous (chronological order ensures correct sign)
        ce_delta_chg = round(r["atm_ce_ltp"] - prev_ce_ltp, 2) if prev_ce_ltp > 0 else 0
        pe_delta_chg = round(r["atm_pe_ltp"] - prev_pe_ltp, 2) if prev_pe_ltp > 0 else 0

        formatted.append({
            "time": ts,
            "pe_oi_total": _fmt_lakh(total_pe_oi),
            "pe_oi_change_day": _fmt_lakh(pe_chg_oi_day),
            "pe_oi_change": _fmt_lakh(pe_oi_change),
            "ce_oi_total": _fmt_lakh(total_ce_oi),
            "ce_oi_change_day": _fmt_lakh(ce_chg_oi_day),
            "ce_oi_change": _fmt_lakh(ce_oi_change),
            "pe_ce_total": _fmt_lakh(pe_ce_diff),
            "pe_ce_change_day": _fmt_lakh(pe_ce_chg_day),
            "pe_ce_change": _fmt_lakh(pe_ce_diff_change),
            "pcr": pcr,
            "future_ltp": round(r["future_ltp"], 2),
            "straddle": r["straddle"],
            "atm_strike": r["atm_strike"],
            "ce_delta_chg": ce_delta_chg,
            "pe_delta_chg": pe_delta_chg,
            "_raw": {
                **r,
                "total_pe_oi": total_pe_oi,
                "total_ce_oi": total_ce_oi,
                "pe_ce_diff": pe_ce_diff,
                "pcr": pcr,
                "pe_oi_change_day": pe_chg_oi_day,
                "ce_oi_change_day": ce_chg_oi_day,
                "pe_ce_diff_change": pe_ce_diff_change,
            },
        })
        prev_ce_ltp = r["atm_ce_ltp"]
        prev_pe_ltp = r["atm_pe_ltp"]
        prev_pe_ce_diff = pe_ce_diff

    # Reverse back to newest-first for UI display (latest at top)
    formatted.reverse()

    return _sanitize({"rows": formatted})


@router.get("/oi-chart")
async def get_oi_chart(
    tf: int = Query(1),
    range_strikes: int = Query(10),
):
    """Time series for Put OI, Call OI, PE-CE diff, PCR charts."""
    buf = _engine_state.get("data_buffer")
    if not buf:
        return {"timestamps": [], "put_oi": [], "call_oi": [], "pe_ce": [], "pcr": []}

    oi_table = list(reversed(list(buf["oi_table"])))  # Chronological
    oi_strikes = list(buf["oi_strikes"])

    # Build strike lookup by HH:MM
    strike_by_ts = {}
    for snap in oi_strikes:
        try:
            parsed = datetime.fromisoformat(snap["timestamp"])
            hhmm = parsed.strftime("%H:%M")
        except Exception:
            hhmm = snap["timestamp"]
        strike_by_ts[hhmm] = snap["strikes"]

    # Timeframe sampling
    if tf > 1 and len(oi_table) > 1:
        oi_table = oi_table[::tf]

    timestamps = []
    put_oi = []
    call_oi = []
    pe_ce = []
    pcr_vals = []
    underlying_vals = []
    straddle_vals = []

    for r in oi_table:
        ts = r["timestamp"]
        atm = r.get("atm_strike", 0)
        strike_data = strike_by_ts.get(ts)

        if strike_data and atm > 0:
            ranged = _compute_ranged_snapshot(strike_data, atm, range_strikes)
        else:
            ranged = None

        timestamps.append(ts)
        if ranged:
            put_oi.append(ranged["total_pe_oi"])
            call_oi.append(ranged["total_ce_oi"])
            pe_ce.append(ranged["pe_ce_diff"])
            pcr_vals.append(ranged["pcr"])
        else:
            put_oi.append(r["total_pe_oi"])
            call_oi.append(r["total_ce_oi"])
            pe_ce.append(r["pe_ce_diff"])
            pcr_vals.append(r["pcr"])
        underlying_vals.append(r["underlying"])
        straddle_vals.append(r["straddle"])

    return _sanitize({
        "timestamps": timestamps,
        "put_oi": put_oi,
        "call_oi": call_oi,
        "pe_ce": pe_ce,
        "pcr": pcr_vals,
        "underlying": underlying_vals,
        "straddle": straddle_vals,
    })


@router.get("/candles")
async def get_candles(tf: int = Query(1)):
    """NIFTY candlestick data."""
    buf = _engine_state.get("data_buffer")
    if not buf:
        return {"candles": []}

    candles = list(buf["candles_1m"])

    # Aggregate candles for higher timeframes
    if tf > 1 and len(candles) > 1:
        agg = []
        for i in range(0, len(candles), tf):
            batch = candles[i:i+tf]
            if not batch:
                continue
            agg.append({
                "timestamp": batch[0]["timestamp"],
                "open": batch[0]["open"],
                "high": max(c["high"] for c in batch),
                "low": min(c["low"] for c in batch),
                "close": batch[-1]["close"],
                "volume": sum(c["volume"] for c in batch),
            })
        candles = agg

    return _sanitize({"candles": candles})


@router.get("/price-vs-oi")
async def get_price_vs_oi(strike: float = 0):
    """Call Price vs OI and Put Price vs OI for a specific strike."""
    buf = _engine_state.get("data_buffer")
    if not buf:
        return {"call": [], "put": [], "straddle": [], "strike": 0}

    # If no strike specified, use ATM
    if strike == 0:
        latest = list(buf["oi_table"])
        if latest:
            strike = latest[0].get("atm_strike", 0)
        if strike == 0:
            return {"call": [], "put": [], "straddle": [], "strike": 0}

    # Extract time series for this strike
    call_data = []
    put_data = []
    straddle_data = []

    for snap in reversed(list(buf["oi_strikes"])):
        ts = snap["timestamp"]
        for s in snap["strikes"]:
            if s["strike"] == strike:
                call_data.append({
                    "timestamp": ts,
                    "price": s["ce_ltp"],
                    "oi": s["ce_oi"],
                })
                put_data.append({
                    "timestamp": ts,
                    "price": s["pe_ltp"],
                    "oi": s["pe_oi"],
                })
                straddle_data.append({
                    "timestamp": ts,
                    "price": round(s["ce_ltp"] + s["pe_ltp"], 2),
                })
                break

    return _sanitize({
        "strike": strike,
        "call": call_data,
        "put": put_data,
        "straddle": straddle_data,
    })


@router.get("/strikes")
async def get_strikes():
    """Available strikes around ATM."""
    buf = _engine_state.get("data_buffer")
    if not buf:
        return {"strikes": [], "atm": 0}

    chain = buf.get("latest_chain")
    if chain is None or (hasattr(chain, 'empty') and chain.empty):
        return {"strikes": [], "atm": 0}

    underlying = buf.get("latest_underlying", 0)
    si = 50  # NIFTY strike interval
    atm = round(underlying / si) * si
    strikes = sorted(chain["strike"].unique().tolist())

    return _sanitize({"strikes": strikes, "atm": atm, "underlying": underlying})


@router.get("/option-chain")
async def get_option_chain():
    """Full live option chain snapshot."""
    buf = _engine_state.get("data_buffer")
    md = _engine_state.get("market_data")

    chain = buf.get("latest_chain") if buf else None
    if chain is None or chain.empty:
        if md:
            chain = md.get_option_chain("NIFTY")
        else:
            return {"chain": []}

    underlying = buf.get("latest_underlying", 0) if buf else 0
    if underlying == 0 and md:
        underlying = md.get_ltp("NIFTY") or 0

    return _sanitize({
        "chain": chain.to_dict("records") if not chain.empty else [],
        "underlying": underlying,
        "is_live": _is_market_open(),
    })


@router.get("/historical")
async def get_historical(
    date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format"),
    strike: Optional[float] = Query(None),
):
    """Fetch historical data from database."""
    import config
    db_url = os.environ.get("DATABASE_URL", config.DATABASE_URL)
    if not db_url:
        return {"error": "No database configured", "rows": []}

    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        if date:
            target_date = date
        else:
            target_date = datetime.now(IST).strftime("%Y-%m-%d")

        if strike:
            cur.execute("""
                SELECT timestamp, strike, ce_oi, pe_oi, ce_ltp, pe_ltp, ce_chg_oi, pe_chg_oi,
                       ce_iv, pe_iv, pe_ce_oi_diff, pcr, future_ltp
                FROM oi_snapshots
                WHERE symbol = 'NIFTY'
                  AND DATE(timestamp AT TIME ZONE 'Asia/Kolkata') = %s
                  AND strike = %s
                ORDER BY timestamp ASC
            """, (target_date, strike))
        else:
            cur.execute("""
                SELECT timestamp, underlying_price, total_ce_oi, total_pe_oi,
                       pe_ce_oi_diff, pe_ce_oi_diff_change, pcr, future_ltp,
                       straddle_price, atm_strike, volume,
                       total_ce_volume, total_pe_volume
                FROM market_snapshots
                WHERE symbol = 'NIFTY'
                  AND DATE(timestamp AT TIME ZONE 'Asia/Kolkata') = %s
                ORDER BY timestamp ASC
            """, (target_date,))

        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

        # Convert timestamps to IST strings
        for row in rows:
            if "timestamp" in row and row["timestamp"]:
                ts = row["timestamp"]
                if hasattr(ts, 'astimezone'):
                    row["timestamp"] = ts.astimezone(IST).isoformat()
                else:
                    row["timestamp"] = (ts + timedelta(hours=5, minutes=30)).isoformat()

        cur.close()
        conn.close()

        return _sanitize({"date": target_date, "rows": rows, "count": len(rows)})

    except Exception as e:
        logger.error("Historical query failed: %s", e)
        return {"error": str(e), "rows": []}


@router.get("/historical-dates")
async def get_historical_dates():
    """Get list of dates that have data in DB."""
    import config
    db_url = os.environ.get("DATABASE_URL", config.DATABASE_URL)
    if not db_url:
        return {"dates": []}

    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT DATE(timestamp AT TIME ZONE 'Asia/Kolkata') as dt
            FROM market_snapshots
            WHERE symbol = 'NIFTY'
            ORDER BY dt DESC
            LIMIT 30
        """)
        dates = [row[0].strftime("%Y-%m-%d") for row in cur.fetchall()]
        cur.close()
        conn.close()
        return {"dates": dates}
    except Exception as e:
        logger.error("Historical dates query failed: %s", e)
        return {"dates": []}


async def _get_historical_oi_table(date: str, tf: int, range_strikes: int):
    """Fetch historical OI table from database."""
    import config
    db_url = os.environ.get("DATABASE_URL", config.DATABASE_URL)
    if not db_url:
        return {"rows": [], "error": "No database"}

    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        # Fetch in chronological order (ASC) so we can compute changes
        cur.execute("""
            SELECT timestamp, underlying_price, total_ce_oi, total_pe_oi,
                   pe_ce_oi_diff, pe_ce_oi_diff_change, pcr, future_ltp,
                   straddle_price, atm_strike, atm_ce_ltp, atm_pe_ltp,
                   volume, total_ce_volume, total_pe_volume
            FROM market_snapshots
            WHERE symbol = 'NIFTY'
              AND DATE(timestamp AT TIME ZONE 'Asia/Kolkata') = %s
            ORDER BY timestamp ASC
        """, (date,))

        columns = [desc[0] for desc in cur.description]
        db_rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        cur.close()
        conn.close()

        if not db_rows:
            return {"rows": [], "date": date}

        # Use first row as "day open" baseline
        first_ce_oi = int(db_rows[0].get("total_ce_oi", 0) or 0)
        first_pe_oi = int(db_rows[0].get("total_pe_oi", 0) or 0)

        # Process chronologically to compute changes
        all_rows = []
        prev_ce_oi = 0
        prev_pe_oi = 0
        prev_pe_ce_diff = 0
        prev_ce_ltp = 0
        prev_pe_ltp = 0

        for r in db_rows:
            ts = r["timestamp"]
            if hasattr(ts, 'astimezone'):
                ts_ist = ts.astimezone(IST)
                time_str = ts_ist.strftime("%H:%M")
            elif hasattr(ts, 'strftime'):
                ts_ist = ts + timedelta(hours=5, minutes=30)
                time_str = ts_ist.strftime("%H:%M")
            else:
                time_str = str(ts)

            total_pe = int(r.get("total_pe_oi", 0) or 0)
            total_ce = int(r.get("total_ce_oi", 0) or 0)
            pe_ce_diff = total_pe - total_ce
            pcr_val = float(r.get("pcr", 0) or 0)
            straddle = float(r.get("straddle_price", 0) or 0)
            future = float(r.get("future_ltp", 0) or 0)
            atm = float(r.get("atm_strike", 0) or 0)
            atm_ce = float(r.get("atm_ce_ltp", 0) or 0)
            atm_pe = float(r.get("atm_pe_ltp", 0) or 0)

            # Change (Day) = current - first row of day
            pe_chg_day = total_pe - first_pe_oi
            ce_chg_day = total_ce - first_ce_oi

            # Change (minute) = current - previous row
            pe_chg_min = total_pe - prev_pe_oi if prev_pe_oi > 0 else 0
            ce_chg_min = total_ce - prev_ce_oi if prev_ce_oi > 0 else 0
            pe_ce_diff_chg = pe_ce_diff - prev_pe_ce_diff if prev_pe_ce_diff != 0 else 0
            pe_ce_chg_day = pe_chg_day - ce_chg_day

            ce_delta_chg = round(atm_ce - prev_ce_ltp, 2) if prev_ce_ltp > 0 else 0
            pe_delta_chg = round(atm_pe - prev_pe_ltp, 2) if prev_pe_ltp > 0 else 0

            all_rows.append({
                "time": time_str,
                "pe_oi_total": _fmt_lakh(total_pe),
                "pe_oi_change_day": _fmt_lakh(pe_chg_day),
                "pe_oi_change": _fmt_lakh(pe_chg_min),
                "ce_oi_total": _fmt_lakh(total_ce),
                "ce_oi_change_day": _fmt_lakh(ce_chg_day),
                "ce_oi_change": _fmt_lakh(ce_chg_min),
                "pe_ce_total": _fmt_lakh(pe_ce_diff),
                "pe_ce_change_day": _fmt_lakh(pe_ce_chg_day),
                "pe_ce_change": _fmt_lakh(pe_ce_diff_chg),
                "pcr": pcr_val,
                "future_ltp": round(future, 2),
                "straddle": straddle,
                "atm_strike": atm,
                "ce_delta_chg": ce_delta_chg,
                "pe_delta_chg": pe_delta_chg,
                "_raw": {
                    "underlying": float(r.get("underlying_price", 0) or 0),
                    "total_pe_oi": total_pe,
                    "total_ce_oi": total_ce,
                    "pe_ce_diff": pe_ce_diff,
                    "pe_ce_diff_change": pe_ce_diff_chg,
                    "pcr": pcr_val,
                    "pe_oi_change_day": pe_chg_day,
                    "ce_oi_change_day": ce_chg_day,
                    "pe_oi_change": pe_chg_min,
                    "ce_oi_change": ce_chg_min,
                },
            })
            prev_ce_oi = total_ce
            prev_pe_oi = total_pe
            prev_pe_ce_diff = pe_ce_diff
            prev_ce_ltp = atm_ce
            prev_pe_ltp = atm_pe

        # Timeframe sampling (after computing changes)
        if tf > 1 and len(all_rows) > 1:
            all_rows = all_rows[::tf]

        # Reverse to newest-first for display
        all_rows.reverse()

        return _sanitize({"rows": all_rows, "date": date, "mode": "historical"})

    except Exception as e:
        logger.error("Historical OI table failed: %s", e)
        return {"rows": [], "error": str(e)}
