"""
REST API routes — Live OI Data, Charts, Historical.
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
    "instruments": None,
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
async def get_oi_table():
    """Minute-by-minute OI table data (like StockMojo PE-CE OI Difference)."""
    buf = _engine_state.get("data_buffer")
    if not buf:
        return {"rows": []}

    rows = list(buf["oi_table"])
    # Format for display
    formatted = []
    for r in rows:
        formatted.append({
            "time": r["timestamp"],
            "pe_oi_total": _fmt_lakh(r["total_pe_oi"]),
            "pe_oi_change_day": _fmt_lakh(r["pe_oi_change_day"]),
            "pe_oi_change": _fmt_lakh(r["pe_oi_change"]),
            "ce_oi_total": _fmt_lakh(r["total_ce_oi"]),
            "ce_oi_change_day": _fmt_lakh(r["ce_oi_change_day"]),
            "ce_oi_change": _fmt_lakh(r["ce_oi_change"]),
            "pe_ce_total": _fmt_lakh(r["pe_ce_diff"]),
            "pe_ce_change": _fmt_lakh(r["pe_ce_diff_change"]),
            "pe_ce_pct": round(r["pe_ce_diff"] / max(abs(r["total_ce_oi"]), 1) * 100, 1),
            "pcr": r["pcr"],
            "future_ltp": round(r["future_ltp"], 2),
            "straddle": r["straddle"],
            "atm_strike": r["atm_strike"],
            # Raw values for charts
            "_raw": r,
        })

    return _sanitize({"rows": formatted})


@router.get("/oi-chart")
async def get_oi_chart():
    """Time series for Put OI, Call OI, PE-CE diff, PCR charts."""
    buf = _engine_state.get("data_buffer")
    if not buf:
        return {"timestamps": [], "put_oi": [], "call_oi": [], "pe_ce": [], "pcr": []}

    rows = list(reversed(list(buf["oi_table"])))  # Chronological order
    return _sanitize({
        "timestamps": [r["timestamp"] for r in rows],
        "put_oi": [r["total_pe_oi"] for r in rows],
        "call_oi": [r["total_ce_oi"] for r in rows],
        "pe_ce": [r["pe_ce_diff"] for r in rows],
        "pcr": [r["pcr"] for r in rows],
        "underlying": [r["underlying"] for r in rows],
        "straddle": [r["straddle"] for r in rows],
    })


@router.get("/candles")
async def get_candles():
    """NIFTY 1-min candlestick data."""
    buf = _engine_state.get("data_buffer")
    if not buf:
        return {"candles": []}
    return _sanitize({"candles": list(buf["candles_1m"])})


@router.get("/price-vs-oi")
async def get_price_vs_oi(strike: float = 0):
    """Call Price vs OI and Put Price vs OI for a specific strike."""
    buf = _engine_state.get("data_buffer")
    if not buf:
        return {"call": [], "put": [], "straddle": []}

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
    if not buf or not buf.get("latest_chain") is not None:
        return {"strikes": [], "atm": 0}

    chain = buf.get("latest_chain")
    if chain is None or chain.empty:
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
                WHERE symbol = 'NIFTY' AND DATE(timestamp) = %s AND strike = %s
                ORDER BY timestamp ASC
            """, (target_date, strike))
        else:
            cur.execute("""
                SELECT timestamp, underlying_price, total_ce_oi, total_pe_oi,
                       pe_ce_oi_diff, pe_ce_oi_diff_change, pcr, future_ltp,
                       straddle_price, atm_strike
                FROM market_snapshots
                WHERE symbol = 'NIFTY' AND DATE(timestamp) = %s
                ORDER BY timestamp ASC
            """, (target_date,))

        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

        # Convert timestamps to strings
        for row in rows:
            if "timestamp" in row and row["timestamp"]:
                row["timestamp"] = row["timestamp"].isoformat()

        cur.close()
        conn.close()

        return _sanitize({"date": target_date, "rows": rows, "count": len(rows)})

    except Exception as e:
        logger.error("Historical query failed: %s", e)
        return {"error": str(e), "rows": []}
