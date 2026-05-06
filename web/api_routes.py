"""
REST API routes — Live OI Data, Charts, Historical.
Dynamically re-aggregates per-strike data based on range filter
to match StockMojo-style OI totals.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Query
from fastapi.responses import Response
from typing import Optional
import numpy as np
import config

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

def _compute_ranged_snapshot(strike_snap, atm_strike, range_strikes, strike_interval=50):
    """
    Given a per-strike snapshot (list of strike dicts) and a range,
    recompute total OI/volume/pcr/pe_ce_diff for only strikes within range.
    This is what makes our data match StockMojo exactly.
    """
    if not strike_snap or not atm_strike:
        return None

    filtered = [s for s in strike_snap
                if abs(s["strike"] - atm_strike) <= range_strikes * strike_interval]

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


def _filter_by_timeframe(rows, tf):
    """
    Filter rows to proper time-window boundaries aligned to market schedule.
    For tf=15: returns rows at 9:30, 9:45, 10:00, 10:15, ...
    For tf=5:  returns rows at 9:15, 9:20, 9:25, 9:30, ...
    For tf=30: returns rows at 9:30, 10:00, 10:30, 11:00, ...

    Takes a chronological list (oldest→newest). For each time window,
    picks the LAST row in that window (most complete data).
    Returns label time as the window boundary (e.g., 9:30 for 9:30-9:44 window).
    """
    if tf <= 1 or len(rows) <= 1:
        return rows

    # Market opens at 9:15 — all windows are aligned relative to this
    MARKET_OPEN_MINUTES = 9 * 60 + 15  # 555

    def _parse_hhmm(ts_str):
        """Parse HH:MM to minutes since midnight."""
        try:
            parts = ts_str.split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            return -1

    def _format_hhmm(total_minutes):
        """Format minutes since midnight to HH:MM."""
        h = total_minutes // 60
        m = total_minutes % 60
        return f"{h:02d}:{m:02d}"

    # Group rows into time windows
    # Window start = MARKET_OPEN + N * tf
    windows = {}  # window_start_minutes -> last row in that window
    for r in rows:
        ts = r.get("timestamp", "")
        mins = _parse_hhmm(ts)
        if mins < 0:
            continue
        # Calculate which window this row belongs to
        offset = mins - MARKET_OPEN_MINUTES
        if offset < 0:
            offset = 0
        window_idx = offset // tf
        window_start = MARKET_OPEN_MINUTES + window_idx * tf
        # Store last row per window (overwrite = keep latest in window)
        windows[window_start] = r

    # Sort by window start time and relabel timestamps to window START
    # StockMojo labels windows as: 09:15, 09:30, 09:45 (start of each window)
    result = []
    for w_start in sorted(windows.keys()):
        row = dict(windows[w_start])  # copy
        row["timestamp"] = _format_hhmm(w_start)
        result.append(row)

    return result


def _filter_by_timeframe_full(rows, tf):
    """
    Same as _filter_by_timeframe but works with rows that have
    full ISO timestamp (timestamp_full field) or datetime objects.
    Used for historical data.
    """
    if tf <= 1 or len(rows) <= 1:
        return rows

    MARKET_OPEN_MINUTES = 9 * 60 + 15

    def _get_minutes(r):
        ts = r.get("timestamp")
        if ts is None:
            return -1
        if hasattr(ts, 'hour'):
            return ts.hour * 60 + ts.minute
        ts_str = str(ts)
        if "T" in ts_str:
            time_part = ts_str.split("T")[1][:5]
        else:
            time_part = ts_str[:5]
        try:
            parts = time_part.split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            return -1

    def _format_hhmm(total_minutes):
        h = total_minutes // 60
        m = total_minutes % 60
        return f"{h:02d}:{m:02d}"

    windows = {}
    for r in rows:
        mins = _get_minutes(r)
        if mins < 0:
            continue
        offset = max(0, mins - MARKET_OPEN_MINUTES)
        window_idx = offset // tf
        window_start = MARKET_OPEN_MINUTES + window_idx * tf
        windows[window_start] = r

    result = []
    for w_start in sorted(windows.keys()):
        result.append(windows[w_start])

    return result


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


@router.get("/expiry-info")
async def expiry_info():
    """Return nearest expiry date + days-to-expiry for the dashboard badge."""
    md = _engine_state.get("market_data")
    if md and hasattr(md, 'get_nearest_expiry_info'):
        info = md.get_nearest_expiry_info("NIFTY")
    else:
        info = {"expiry": "--", "dte": 0, "label": "--", "expiry_date": ""}
    return info


@router.get("/oi-table")
async def get_oi_table(
    tf: int = Query(1, description="Timeframe in minutes (1,3,5,15,30)"),
    range_strikes: int = Query(10, description="Number of strikes from ATM each side"),
    mode: str = Query("live", description="live or historical"),
    date: str = Query("", description="Date for historical mode (YYYY-MM-DD)"),
    auto_atm: bool = Query(True, description="Auto-recalculate ATM per row from futures LTP"),
):
    """Minute-by-minute OI table data, dynamically aggregated by range."""

    # ── Historical mode: fetch from DB ──
    if mode == "historical" and date:
        return await _get_historical_oi_table(date, tf, range_strikes)

    buf = _engine_state.get("data_buffer")
    if not buf:
        return {"rows": [], "range_display": ""}

    oi_table_raw = list(buf["oi_table"])  # newest-first (appendleft)
    oi_strikes = list(buf["oi_strikes"])

    # Build strike lookup using FULL ISO timestamp (not HH:MM) to avoid collisions
    # when multiple snapshots happen in the same minute
    strike_by_full_ts = {}
    strike_by_hhmm = {}  # fallback lookup
    for snap in oi_strikes:
        ts_key = snap["timestamp"]
        strike_by_full_ts[ts_key] = snap["strikes"]
        try:
            parsed = datetime.fromisoformat(ts_key)
            hhmm = parsed.strftime("%H:%M")
        except Exception:
            hhmm = ts_key
        strike_by_hhmm[hhmm] = snap["strikes"]

    # STEP 1: Reverse to chronological order (oldest→newest)
    chrono = list(reversed(oi_table_raw))

    # STEP 2: Apply proper time-window filtering BEFORE computing deltas
    # This ensures 15m filter shows 9:30, 9:45, 10:00, etc.
    if tf > 1:
        chrono = _filter_by_timeframe(chrono, tf)

    strike_interval = config.INDICES.get("NIFTY", {}).get("strike_interval", 50)
    latest_range_display = ""

    # ATM computation: Auto vs Fixed mode (matches StockMojo toggle)
    # Auto ATM: compute from LATEST futures price, apply to ALL rows
    # Fixed: each row uses its own recorded ATM from capture time
    computed_atm = 0
    if auto_atm and chrono:
        latest_row = chrono[-1]
        latest_price = latest_row.get("future_ltp", 0) or latest_row.get("underlying", 0)
        computed_atm = round(latest_price / strike_interval) * strike_interval if latest_price > 0 else latest_row.get("atm_strike", 0)

    # STEP 3: First pass — compute ranged OI totals for each row
    enriched = []
    for r in chrono:
        ts = r["timestamp"]  # "HH:MM"
        if auto_atm:
            atm = computed_atm if computed_atm > 0 else r.get("atm_strike", 0)
        else:
            # Fixed mode: use each row's own ATM (recalculated from its own futures LTP)
            row_price = r.get("future_ltp", 0) or r.get("underlying", 0)
            atm = round(row_price / strike_interval) * strike_interval if row_price > 0 else r.get("atm_strike", 0)

        if range_strikes == 0:
            latest_range_display = "(All Strikes)"
        elif atm > 0:
            low_strike = atm - (range_strikes * strike_interval)
            high_strike = atm + (range_strikes * strike_interval)
            latest_range_display = f"({int(low_strike)} - {int(high_strike)})"

        # Look up strike data — try full ISO timestamp first, then HH:MM fallback
        full_ts = r.get("timestamp_full", "")
        strike_data = strike_by_full_ts.get(full_ts) if full_ts else None
        if not strike_data:
            strike_data = strike_by_hhmm.get(ts)

        effective_range = range_strikes if range_strikes > 0 else 9999
        if strike_data and atm > 0:
            ranged = _compute_ranged_snapshot(strike_data, atm, effective_range, strike_interval)
        else:
            ranged = None

        if ranged:
            total_pe_oi = ranged["total_pe_oi"]
            total_ce_oi = ranged["total_ce_oi"]
            pe_chg_oi_day = ranged["pe_chg_oi"]
            ce_chg_oi_day = ranged["ce_chg_oi"]
            pe_ce_diff = ranged["pe_ce_diff"]
            pcr = ranged["pcr"]
            ce_volume = ranged["total_ce_vol"]
            pe_volume = ranged["total_pe_vol"]
        else:
            total_pe_oi = r["total_pe_oi"]
            total_ce_oi = r["total_ce_oi"]
            pe_chg_oi_day = r.get("pe_oi_change_day", 0)
            ce_chg_oi_day = r.get("ce_oi_change_day", 0)
            pe_ce_diff = r["pe_ce_diff"]
            pcr = r["pcr"]
            ce_volume = 0
            pe_volume = 0

        # Extract ATM CE/PE LTP from per-strike data for the CURRENT ATM
        # This ensures Delta Change uses the correct strike even if ATM shifted
        atm_ce_ltp = r.get("atm_ce_ltp", 0)
        atm_pe_ltp = r.get("atm_pe_ltp", 0)
        if strike_data and atm > 0:
            for s in strike_data:
                if s["strike"] == atm:
                    atm_ce_ltp = s.get("ce_ltp", atm_ce_ltp)
                    atm_pe_ltp = s.get("pe_ltp", atm_pe_ltp)
                    break

        enriched.append({
            "row": r,
            "total_pe_oi": total_pe_oi,
            "total_ce_oi": total_ce_oi,
            "pe_chg_oi_day": pe_chg_oi_day,
            "ce_chg_oi_day": ce_chg_oi_day,
            "pe_ce_diff": pe_ce_diff,
            "pcr": pcr,
            "atm": atm,
            "atm_ce_ltp": atm_ce_ltp,
            "atm_pe_ltp": atm_pe_ltp,
            "ce_volume": ce_volume,
            "pe_volume": pe_volume,
        })

    # STEP 4: Second pass — compute deltas between FILTERED rows (correct for any timeframe)
    formatted = []
    for idx, e in enumerate(enriched):
        r = e["row"]
        ts = r["timestamp"]
        atm = e["atm"]
        total_pe_oi = e["total_pe_oi"]
        total_ce_oi = e["total_ce_oi"]
        pe_chg_oi_day = e["pe_chg_oi_day"]
        ce_chg_oi_day = e["ce_chg_oi_day"]
        pe_ce_diff = e["pe_ce_diff"]
        pcr = e["pcr"]

        if idx > 0:
            prev = enriched[idx - 1]
            pe_oi_change = total_pe_oi - prev["total_pe_oi"]
            ce_oi_change = total_ce_oi - prev["total_ce_oi"]
            pe_ce_diff_change = pe_ce_diff - prev["pe_ce_diff"]
            ce_delta_chg = round(e["atm_ce_ltp"] - prev["atm_ce_ltp"], 2)
            pe_delta_chg = round(e["atm_pe_ltp"] - prev["atm_pe_ltp"], 2)
            # Signal: computed from futures LTP + ranged total OI direction
            cur_price = r.get("future_ltp", r.get("underlying", 0))
            prev_price = prev["row"].get("future_ltp", prev["row"].get("underlying", 0))
            cur_total_oi = total_ce_oi + total_pe_oi
            prev_total_oi = prev["total_ce_oi"] + prev["total_pe_oi"]
            signal = _compute_signal_from_data(cur_price, prev_price, cur_total_oi, prev_total_oi)
        else:
            pe_oi_change = 0
            ce_oi_change = 0
            pe_ce_diff_change = 0
            ce_delta_chg = 0
            pe_delta_chg = 0
            signal = r.get("signal", "N/A")

        pe_ce_chg_day = pe_chg_oi_day - ce_chg_oi_day
        total_oi = r.get("futures_oi", 0)

        sig_arrow = ""
        if signal in ("LB", "SC"):
            sig_arrow = "↑"
        elif signal in ("SB", "LU"):
            sig_arrow = "↓"
        elif signal == "N/A":
            sig_arrow = "⇔"

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
            "future_ltp": round(r.get("future_ltp", 0), 2),
            "straddle": round(e["atm_ce_ltp"] + e["atm_pe_ltp"], 2),
            "atm_strike": atm,
            "total_oi": _fmt_lakh(total_oi) if total_oi else "--",
            "ce_delta_chg": ce_delta_chg,
            "pe_delta_chg": pe_delta_chg,
            "ce_volume": _fmt_lakh(e["ce_volume"]),
            "pe_volume": _fmt_lakh(e["pe_volume"]),
            "signal": signal,
            "signal_arrow": sig_arrow,
            "_raw": {
                **r,
                "total_pe_oi": total_pe_oi,
                "total_ce_oi": total_ce_oi,
                "pe_ce_diff": pe_ce_diff,
                "pcr": pcr,
                "pe_oi_change_day": pe_chg_oi_day,
                "ce_oi_change_day": ce_chg_oi_day,
                "pe_ce_diff_change": pe_ce_diff_change,
                "pe_oi_change": pe_oi_change,
                "ce_oi_change": ce_oi_change,
                "total_oi": total_oi,
                "ce_volume": e["ce_volume"],
                "pe_volume": e["pe_volume"],
            },
        })

    # Reverse to newest-first for UI display
    formatted.reverse()

    return _sanitize({"rows": formatted, "range_display": latest_range_display})


@router.get("/download-oi")
async def download_oi_csv(
    tf: int = Query(1),
    range_strikes: int = Query(5),
    auto_atm: bool = Query(True),
    mode: str = Query("live"),
    date: str = Query(""),
):
    """Download OI table data as CSV."""
    import io, csv
    # Reuse the existing OI table endpoint logic
    data = await get_oi_table(tf=tf, range_strikes=range_strikes, auto_atm=auto_atm, mode=mode, date=date)
    rows = data.get("rows", []) if isinstance(data, dict) else []

    output = io.StringIO()
    writer = csv.writer(output)
    # Header
    writer.writerow([
        "Time", "Put OI Total", "Put OI Chg(Day)", "Put OI Change",
        "Call OI Total", "Call OI Chg(Day)", "Call OI Change",
        "PE-CE OI", "PE-CE Change", "PCR",
        "CE Volume", "PE Volume", "Total OI"
    ])
    for r in rows:
        writer.writerow([
            r.get("time", ""),
            r.get("pe_oi_total", ""), r.get("pe_oi_change_day", ""), r.get("pe_oi_change", ""),
            r.get("ce_oi_total", ""), r.get("ce_oi_change_day", ""), r.get("ce_oi_change", ""),
            r.get("pe_ce_total", ""), r.get("pe_ce_change", ""), r.get("pcr", ""),
            r.get("ce_volume", ""), r.get("pe_volume", ""), r.get("total_oi", ""),
        ])

    filename = f"NIFTY_OI_{date or datetime.now(IST).strftime('%Y-%m-%d')}_{tf}m.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


def _compute_signal_from_data(cur_price, prev_price, cur_total_oi, prev_total_oi):
    """Compute LB/SB/SC/LU signal from price + OI direction between two snapshots."""
    if prev_price <= 0 or prev_total_oi <= 0:
        return "N/A"
    price_changed = abs(cur_price - prev_price) > 0.01
    oi_changed = cur_total_oi != prev_total_oi
    if not price_changed or not oi_changed:
        return "N/A"
    price_up = cur_price > prev_price
    oi_up = cur_total_oi > prev_total_oi
    if price_up and oi_up:
        return "LB"
    elif not price_up and oi_up:
        return "SB"
    elif price_up and not oi_up:
        return "SC"
    else:
        return "LU"


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

    # Build strike lookups (full ISO + HH:MM fallback)
    strike_by_full_ts = {}
    strike_by_hhmm = {}
    for snap in oi_strikes:
        ts_key = snap["timestamp"]
        strike_by_full_ts[ts_key] = snap["strikes"]
        try:
            parsed = datetime.fromisoformat(ts_key)
            hhmm = parsed.strftime("%H:%M")
        except Exception:
            hhmm = ts_key
        strike_by_hhmm[hhmm] = snap["strikes"]

    # Apply proper timeframe filtering
    if tf > 1:
        oi_table = _filter_by_timeframe(oi_table, tf)

    timestamps = []
    put_oi = []
    call_oi = []
    pe_ce = []
    pcr_vals = []
    underlying_vals = []
    straddle_vals = []

    si = config.INDICES.get("NIFTY", {}).get("strike_interval", 50)

    computed_atm = 0
    if oi_table:
        latest = oi_table[-1]
        lp = latest.get("future_ltp", 0) or latest.get("underlying", 0)
        computed_atm = round(lp / si) * si if lp > 0 else latest.get("atm_strike", 0)

    for r in oi_table:
        ts = r["timestamp"]
        atm = computed_atm if computed_atm > 0 else r.get("atm_strike", 0)

        # Dual lookup for strike data
        full_ts = r.get("timestamp_full", "")
        strike_data = strike_by_full_ts.get(full_ts) if full_ts else None
        if not strike_data:
            strike_data = strike_by_hhmm.get(ts)

        effective_range = range_strikes if range_strikes > 0 else 9999
        if strike_data and atm > 0:
            ranged = _compute_ranged_snapshot(strike_data, atm, effective_range, si)
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
        underlying_vals.append(r.get("underlying", 0))
        # Compute straddle from per-strike data for correct ATM
        straddle_val = r.get("straddle", 0)
        if strike_data and atm > 0:
            for s in strike_data:
                if s["strike"] == atm:
                    straddle_val = round(s.get("ce_ltp", 0) + s.get("pe_ltp", 0), 2)
                    break
        straddle_vals.append(straddle_val)

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
    si = config.INDICES.get("NIFTY", {}).get("strike_interval", 50)
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


@router.get("/historical-chart")
async def get_historical_chart(
    date: str = Query(..., description="Date YYYY-MM-DD"),
    tf: int = Query(1),
    range_strikes: int = Query(10),
):
    """Historical chart data: OI lines, PCR, and OHLC candles from DB."""
    import config
    db_url = os.environ.get("DATABASE_URL", config.DATABASE_URL)
    if not db_url:
        return {"timestamps": [], "put_oi": [], "call_oi": [], "pe_ce": [], "pcr": [], "candles": []}

    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        # Check if per-strike data exists for range filtering
        cur.execute("""
            SELECT COUNT(*) FROM oi_snapshots
            WHERE symbol = 'NIFTY'
              AND DATE(timestamp AT TIME ZONE 'Asia/Kolkata') = %s
            LIMIT 1
        """, (date,))
        has_strikes = cur.fetchone()[0] > 0

        timestamps = []
        put_oi_vals = []
        call_oi_vals = []
        pe_ce_vals = []
        pcr_vals = []
        candle_buf = []

        if has_strikes:
            # Range-filtered: join oi_snapshots + market_snapshots
            cur.execute("""
                SELECT o.timestamp, m.underlying_price, m.atm_strike, m.straddle_price,
                       o.strike, o.ce_oi, o.pe_oi
                FROM oi_snapshots o
                JOIN market_snapshots m ON o.timestamp = m.timestamp AND m.symbol = 'NIFTY'
                WHERE o.symbol = 'NIFTY'
                  AND DATE(o.timestamp AT TIME ZONE 'Asia/Kolkata') = %s
                ORDER BY o.timestamp ASC, o.strike ASC
            """, (date,))

            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

            from collections import OrderedDict
            ts_groups = OrderedDict()
            for row in rows:
                ts = row["timestamp"]
                if ts not in ts_groups:
                    ts_groups[ts] = {"underlying": float(row.get("underlying_price", 0) or 0),
                                     "atm": float(row.get("atm_strike", 0) or 0),
                                     "strikes": []}
                ts_groups[ts]["strikes"].append(row)

            for ts, grp in ts_groups.items():
                atm = grp["atm"]
                if atm <= 0:
                    continue
                si_hist = config.INDICES.get("NIFTY", {}).get("strike_interval", 50)
                filtered = [s for s in grp["strikes"]
                            if abs(float(s["strike"]) - atm) <= range_strikes * si_hist]
                if not filtered:
                    continue
                tce = sum(int(s.get("ce_oi", 0) or 0) for s in filtered)
                tpe = sum(int(s.get("pe_oi", 0) or 0) for s in filtered)

                if hasattr(ts, 'astimezone'):
                    ts_ist = ts.astimezone(IST)
                else:
                    ts_ist = ts + timedelta(hours=5, minutes=30)

                timestamps.append(ts_ist.strftime("%H:%M"))
                put_oi_vals.append(tpe)
                call_oi_vals.append(tce)
                pe_ce_vals.append(tpe - tce)
                pcr_vals.append(round(tpe / max(tce, 1), 4))
                candle_buf.append({"ts": ts_ist.isoformat(), "price": grp["underlying"]})
        else:
            # Fallback: market_snapshots only (no range filter)
            cur.execute("""
                SELECT timestamp, underlying_price, total_ce_oi, total_pe_oi, pe_ce_oi_diff, pcr
                FROM market_snapshots
                WHERE symbol = 'NIFTY'
                  AND DATE(timestamp AT TIME ZONE 'Asia/Kolkata') = %s
                ORDER BY timestamp ASC
            """, (date,))
            cols = [d[0] for d in cur.description]
            for r in [dict(zip(cols, row)) for row in cur.fetchall()]:
                ts = r["timestamp"]
                if hasattr(ts, 'astimezone'):
                    ts_ist = ts.astimezone(IST)
                else:
                    ts_ist = ts + timedelta(hours=5, minutes=30)
                timestamps.append(ts_ist.strftime("%H:%M"))
                put_oi_vals.append(int(r.get("total_pe_oi", 0) or 0))
                call_oi_vals.append(int(r.get("total_ce_oi", 0) or 0))
                pe_ce_vals.append(int(r.get("pe_ce_oi_diff", 0) or 0))
                pcr_vals.append(float(r.get("pcr", 0) or 0))
                candle_buf.append({"ts": ts_ist.isoformat(),
                                   "price": float(r.get("underlying_price", 0) or 0)})

        cur.close()
        conn.close()

        # Build OHLC candles
        candles = _build_candles(candle_buf, tf)

        # TF sampling for lines — proper time-window alignment
        if tf > 1 and len(timestamps) > 1:
            MARKET_OPEN_MINUTES = 9 * 60 + 15
            windows = {}  # w_start -> index
            for i, ts_str in enumerate(timestamps):
                try:
                    parts = ts_str.split(":")
                    mins = int(parts[0]) * 60 + int(parts[1])
                except Exception:
                    continue
                offset = max(0, mins - MARKET_OPEN_MINUTES)
                w_start = MARKET_OPEN_MINUTES + (offset // tf) * tf
                windows[w_start] = i  # Keep last index per window
            sorted_windows = sorted(windows.keys())
            indices = [windows[w] for w in sorted_windows]
            # Relabel timestamps to window START (matches StockMojo convention)
            def _fmt_hhmm(m):
                return f"{m // 60:02d}:{m % 60:02d}"
            timestamps = [_fmt_hhmm(w) for w in sorted_windows]
            put_oi_vals = [put_oi_vals[i] for i in indices]
            call_oi_vals = [call_oi_vals[i] for i in indices]
            pe_ce_vals = [pe_ce_vals[i] for i in indices]
            pcr_vals = [pcr_vals[i] for i in indices]

        return _sanitize({
            "timestamps": timestamps,
            "put_oi": put_oi_vals,
            "call_oi": call_oi_vals,
            "pe_ce": pe_ce_vals,
            "pcr": pcr_vals,
            "candles": candles,
        })
    except Exception as e:
        logger.error("Historical chart failed: %s", e)
        return {"timestamps": [], "put_oi": [], "call_oi": [], "pe_ce": [], "pcr": [], "candles": []}


def _build_candles(price_data, tf):
    """Build OHLC candles from [{ts, price}] grouped by tf minutes."""
    if not price_data:
        return []
    candles = []
    buf = []
    for i, p in enumerate(price_data):
        buf.append(p)
        if len(buf) >= tf or i == len(price_data) - 1:
            prices = [b["price"] for b in buf]
            candles.append({
                "timestamp": buf[0]["ts"],
                "open": prices[0],
                "high": max(prices),
                "low": min(prices),
                "close": prices[-1],
                "volume": 0,
            })
            buf = []
    return candles


async def _get_historical_oi_table(date: str, tf: int, range_strikes: int):
    """Fetch historical OI table from database with range filtering."""
    import config
    db_url = os.environ.get("DATABASE_URL", config.DATABASE_URL)
    if not db_url:
        return {"rows": [], "error": "No database"}

    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        # Always need market_snapshots for ATM, straddle, future, delta change data
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
        ms_cols = [desc[0] for desc in cur.description]
        ms_rows = [dict(zip(ms_cols, row)) for row in cur.fetchall()]

        if not ms_rows:
            cur.close()
            conn.close()
            return {"rows": [], "date": date}

        # Try to read futures_oi if column exists
        try:
            cur.execute("""
                SELECT timestamp, futures_oi FROM market_snapshots
                WHERE symbol = 'NIFTY'
                  AND DATE(timestamp AT TIME ZONE 'Asia/Kolkata') = %s
                ORDER BY timestamp ASC
            """, (date,))
            foi_map = {r[0]: int(r[1] or 0) for r in cur.fetchall()}
        except Exception:
            conn.rollback()
            foi_map = {}

        si = config.INDICES.get("NIFTY", {}).get("strike_interval", 50)

        # Auto ATM: compute from LATEST row's futures LTP (same as live mode)
        latest = ms_rows[-1]
        latest_fut = float(latest.get("future_ltp", 0) or 0) or float(latest.get("underlying_price", 0) or 0)
        computed_atm = round(latest_fut / si) * si if latest_fut > 0 else float(latest.get("atm_strike", 0) or 0)

        # Check if per-strike data is available for range filtering
        cur.execute("""
            SELECT COUNT(*) FROM oi_snapshots
            WHERE symbol = 'NIFTY'
              AND DATE(timestamp AT TIME ZONE 'Asia/Kolkata') = %s
            LIMIT 1
        """, (date,))
        has_strikes = cur.fetchone()[0] > 0

        # Build per-strike data lookup
        ts_strike_groups = {}
        if has_strikes:
            cur.execute("""
                SELECT timestamp, strike, ce_oi, pe_oi, ce_ltp, pe_ltp, ce_volume, pe_volume
                FROM oi_snapshots
                WHERE symbol = 'NIFTY'
                  AND DATE(timestamp AT TIME ZONE 'Asia/Kolkata') = %s
                ORDER BY timestamp ASC, strike ASC
            """, (date,))
            for row in cur.fetchall():
                ts = row[0]
                if ts not in ts_strike_groups:
                    ts_strike_groups[ts] = []
                ts_strike_groups[ts].append({
                    "strike": float(row[1]),
                    "ce_oi": int(row[2] or 0),
                    "pe_oi": int(row[3] or 0),
                    "ce_ltp": float(row[4] or 0),
                    "pe_ltp": float(row[5] or 0),
                    "ce_volume": int(row[6] or 0),
                    "pe_volume": int(row[7] or 0),
                })

        cur.close()
        conn.close()

        # Query PREVIOUS day's closing OI for accurate "Chg Day" (matches StockMojo)
        prev_day_ce_oi = 0
        prev_day_pe_oi = 0
        try:
            conn2 = psycopg2.connect(db_url)
            cur2 = conn2.cursor()
            cur2.execute("""
                SELECT DISTINCT DATE(timestamp AT TIME ZONE 'Asia/Kolkata') as dt
                FROM market_snapshots
                WHERE symbol = 'NIFTY'
                  AND DATE(timestamp AT TIME ZONE 'Asia/Kolkata') < %s
                ORDER BY dt DESC
                LIMIT 1
            """, (date,))
            prev_row = cur2.fetchone()
            if prev_row:
                prev_date = prev_row[0].strftime("%Y-%m-%d")
                cur2.execute("""
                    SELECT total_ce_oi, total_pe_oi FROM market_snapshots
                    WHERE symbol = 'NIFTY'
                      AND DATE(timestamp AT TIME ZONE 'Asia/Kolkata') = %s
                    ORDER BY timestamp DESC
                    LIMIT 1
                """, (prev_date,))
                close_row = cur2.fetchone()
                if close_row:
                    prev_day_ce_oi = int(close_row[0] or 0)
                    prev_day_pe_oi = int(close_row[1] or 0)
            cur2.close()
            conn2.close()
        except Exception as e:
            logger.debug("Failed to load prev day baseline for historical: %s", e)

        # Fallback: if no previous day data, use first row of current day
        if prev_day_ce_oi == 0 and prev_day_pe_oi == 0:
            prev_day_ce_oi = int(ms_rows[0].get("total_ce_oi", 0) or 0)
            prev_day_pe_oi = int(ms_rows[0].get("total_pe_oi", 0) or 0)

        # STEP 1: Build intermediate rows with ranged OI + ATM CE/PE LTP
        intermediate = []
        effective_range = range_strikes if range_strikes > 0 else 9999
        for r in ms_rows:
            ts = r["timestamp"]
            if hasattr(ts, 'astimezone'):
                ts_ist = ts.astimezone(IST)
                time_str = ts_ist.strftime("%H:%M")
            elif hasattr(ts, 'strftime'):
                ts_ist = ts + timedelta(hours=5, minutes=30)
                time_str = ts_ist.strftime("%H:%M")
            else:
                time_str = str(ts)

            atm = computed_atm  # Auto ATM: same for all rows

            # Compute ranged OI from per-strike data
            strikes = ts_strike_groups.get(ts, [])
            if strikes and atm > 0:
                filtered = [s for s in strikes if abs(s["strike"] - atm) <= effective_range * si]
                total_ce = sum(s["ce_oi"] for s in filtered)
                total_pe = sum(s["pe_oi"] for s in filtered)
                ce_vol = sum(s.get("ce_volume", 0) for s in filtered)
                pe_vol = sum(s.get("pe_volume", 0) for s in filtered)
                # Extract ATM CE/PE LTP from per-strike data
                atm_ce = float(r.get("atm_ce_ltp", 0) or 0)
                atm_pe = float(r.get("atm_pe_ltp", 0) or 0)
                for s in strikes:
                    if s["strike"] == atm:
                        atm_ce = s["ce_ltp"]
                        atm_pe = s["pe_ltp"]
                        break
            else:
                total_pe = int(r.get("total_pe_oi", 0) or 0)
                total_ce = int(r.get("total_ce_oi", 0) or 0)
                atm_ce = float(r.get("atm_ce_ltp", 0) or 0)
                atm_pe = float(r.get("atm_pe_ltp", 0) or 0)
                ce_vol = int(r.get("total_ce_volume", 0) or 0)
                pe_vol = int(r.get("total_pe_volume", 0) or 0)

            intermediate.append({
                "time_str": time_str,
                "total_pe": total_pe,
                "total_ce": total_ce,
                "straddle": round(atm_ce + atm_pe, 2),
                "future": float(r.get("future_ltp", 0) or 0),
                "atm": atm,
                "atm_ce": atm_ce,
                "atm_pe": atm_pe,
                "underlying": float(r.get("underlying_price", 0) or 0),
                "futures_oi": foi_map.get(ts, 0),
                "ce_vol": ce_vol,
                "pe_vol": pe_vol,
                "timestamp": time_str,  # needed for _filter_by_timeframe
            })

        # STEP 2: Apply proper time-window filtering BEFORE computing deltas
        if tf > 1 and len(intermediate) > 1:
            intermediate = _filter_by_timeframe(intermediate, tf)

        # STEP 3: Compute deltas between FILTERED rows
        all_rows = []
        for idx, item in enumerate(intermediate):
            total_pe = item["total_pe"]
            total_ce = item["total_ce"]
            pe_ce_diff = total_pe - total_ce
            pcr_val = round(total_pe / max(total_ce, 1), 4)

            pe_chg_day = total_pe - prev_day_pe_oi
            ce_chg_day = total_ce - prev_day_ce_oi
            pe_ce_chg_day = pe_chg_day - ce_chg_day

            if idx > 0:
                prev_item = intermediate[idx - 1]
                pe_chg_min = total_pe - prev_item["total_pe"]
                ce_chg_min = total_ce - prev_item["total_ce"]
                prev_pe_ce = prev_item["total_pe"] - prev_item["total_ce"]
                pe_ce_diff_chg = pe_ce_diff - prev_pe_ce
                ce_delta_chg = round(item["atm_ce"] - prev_item["atm_ce"], 2)
                pe_delta_chg = round(item["atm_pe"] - prev_item["atm_pe"], 2)
                # Signal uses futures LTP (matches live endpoint)
                cur_price = item["future"] if item["future"] > 0 else item["underlying"]
                prev_price = prev_item["future"] if prev_item["future"] > 0 else prev_item["underlying"]
                signal = _compute_signal_from_data(
                    cur_price, prev_price,
                    total_ce + total_pe, prev_item["total_ce"] + prev_item["total_pe"])
            else:
                pe_chg_min = 0
                ce_chg_min = 0
                pe_ce_diff_chg = 0
                ce_delta_chg = 0
                pe_delta_chg = 0
                signal = "N/A"

            # Signal arrow (matches live endpoint)
            sig_arrow = ""
            if signal in ("LB", "SC"):
                sig_arrow = "↑"
            elif signal in ("SB", "LU"):
                sig_arrow = "↓"
            elif signal == "N/A":
                sig_arrow = "⇔"

            total_oi = item.get("futures_oi", 0)

            all_rows.append({
                "time": item["time_str"],
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
                "future_ltp": round(item["future"], 2),
                "straddle": item["straddle"],
                "atm_strike": item["atm"],
                "total_oi": _fmt_lakh(total_oi) if total_oi else "--",
                "ce_delta_chg": ce_delta_chg,
                "pe_delta_chg": pe_delta_chg,
                "ce_volume": _fmt_lakh(item.get("ce_vol", 0)),
                "pe_volume": _fmt_lakh(item.get("pe_vol", 0)),
                "signal": signal,
                "signal_arrow": sig_arrow,
                "_raw": {
                    "underlying": item["underlying"],
                    "total_pe_oi": total_pe,
                    "total_ce_oi": total_ce,
                    "pe_ce_diff": pe_ce_diff,
                    "pe_ce_diff_change": pe_ce_diff_chg,
                    "pcr": pcr_val,
                    "pe_oi_change_day": pe_chg_day,
                    "ce_oi_change_day": ce_chg_day,
                    "pe_oi_change": pe_chg_min,
                    "ce_oi_change": ce_chg_min,
                    "total_oi": total_oi,
                    "ce_volume": item.get("ce_vol", 0),
                    "pe_volume": item.get("pe_vol", 0),
                },
            })

        # Reverse to newest-first for display
        all_rows.reverse()

        return _sanitize({"rows": all_rows, "date": date, "mode": "historical"})

    except Exception as e:
        logger.error("Historical OI table failed: %s", e)
        return {"rows": [], "error": str(e)}


