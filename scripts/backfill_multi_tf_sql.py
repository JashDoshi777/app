"""
Backfill the Multi-Timeframe Confirmation composite (agreement score,
conviction, Z cascade, persistence-filtered final signal) at range=5
(ATM ± 5 strikes) for every row of every historical trading day.

Hybrid approach: for each of the 6 timeframes (1/3/5/10/15/30m), a single
fast SQL query (identical window-function pattern to the already-verified
single-TF Z-score backfill) computes that timeframe's Net Z per row. The
6 resulting per-timeframe Net Z series are then combined in a light Python
pass (as-of lookup + the same weight/threshold arithmetic as
web/api_routes.py's multi-TF engine) and written back in one UPDATE per
day. This avoids expensive correlated-subquery joins in Postgres while
keeping the core per-timeframe math in fast SQL.

Verified against web/api_routes.py's Python multi-TF engine before trusting
this for all days — re-verify both stay in sync if either changes.

Run manually: python scripts/backfill_multi_tf_sql.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
import psycopg2.extras
import config

TIMEFRAMES = [1, 3, 5, 10, 15, 30]
MTF_WEIGHTS = {1: 25, 3: 20, 5: 20, 10: 15, 15: 10, 30: 10}
TREND_THRESHOLD = 1.0
MARKET_OPEN_MINUTES = 9 * 60 + 15  # 555


def _tf_netz_sql(tf):
    """
    Fast per-timeframe Net Z query — same bucket-and-window pattern as the
    verified single-TF backfill (scripts/backfill_zscore_sql.py), just
    parameterized by tf. Returns (timestamp, net_z, ready) rows.
    """
    if tf == 1:
        bucket_expr = "o.timestamp"
    else:
        bucket_expr = f"""
            {MARKET_OPEN_MINUTES} + FLOOR((
                (EXTRACT(HOUR FROM o.timestamp AT TIME ZONE 'Asia/Kolkata')*60
                 + EXTRACT(MINUTE FROM o.timestamp AT TIME ZONE 'Asia/Kolkata')
                 - {MARKET_OPEN_MINUTES}) / {tf}
            )) * {tf}
        """
    return f"""
WITH day_atm AS (
    SELECT DISTINCT ON (1) 1 as k,
        ROUND(COALESCE(NULLIF(future_ltp,0), underlying_price) / 50) * 50 as atm
    FROM market_snapshots
    WHERE symbol='NIFTY' AND DATE(timestamp AT TIME ZONE 'Asia/Kolkata') = %(date)s
    ORDER BY 1, timestamp DESC
),
ranged AS (
    SELECT o.timestamp, SUM(o.pe_oi) as total_pe, SUM(o.ce_oi) as total_ce
    FROM oi_snapshots o, day_atm a
    WHERE o.symbol='NIFTY' AND DATE(o.timestamp AT TIME ZONE 'Asia/Kolkata') = %(date)s
      AND ABS(o.strike - a.atm) <= 5*50
    GROUP BY o.timestamp
),
bucketed AS (
    SELECT timestamp, total_pe, total_ce, ({bucket_expr}) as window_start
    FROM ranged o
),
windowed AS (
    SELECT DISTINCT ON (window_start) window_start, timestamp, total_pe, total_ce
    FROM bucketed
    ORDER BY window_start, timestamp DESC
),
changes AS (
    SELECT timestamp, ROW_NUMBER() OVER (ORDER BY window_start) as rn,
        total_pe - LAG(total_pe) OVER (ORDER BY window_start) as pe_chg_raw,
        total_ce - LAG(total_ce) OVER (ORDER BY window_start) as ce_chg_raw
    FROM windowed
),
changes_clean AS (
    SELECT timestamp, rn,
        CASE WHEN rn = 1 THEN 0 ELSE pe_chg_raw END as pe_chg,
        CASE WHEN rn = 1 THEN 0 ELSE ce_chg_raw END as ce_chg
    FROM changes
),
stats AS (
    SELECT timestamp, rn, pe_chg, ce_chg,
        CASE WHEN rn >= 12 THEN AVG(pe_chg) OVER (ORDER BY rn ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING) ELSE NULL END as pe_avg,
        CASE WHEN rn >= 12 THEN AVG(ce_chg) OVER (ORDER BY rn ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING) ELSE NULL END as ce_avg,
        CASE WHEN rn >= 12 THEN STDDEV_SAMP(pe_chg) OVER (ORDER BY rn ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING) ELSE NULL END as pe_std,
        CASE WHEN rn >= 12 THEN STDDEV_SAMP(ce_chg) OVER (ORDER BY rn ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING) ELSE NULL END as ce_std
    FROM changes_clean
)
SELECT timestamp,
    CASE WHEN pe_std IS NOT NULL AND pe_std > 0 THEN (pe_chg - pe_avg) / pe_std ELSE 0 END as z_pe,
    CASE WHEN ce_std IS NOT NULL AND ce_std > 0 THEN (ce_chg - ce_avg) / ce_std ELSE 0 END as z_ce,
    (pe_avg IS NOT NULL) as ready
FROM stats
ORDER BY timestamp;
"""


def _trend_score(net_z, ready):
    if not ready or net_z is None:
        return 0
    if net_z <= -TREND_THRESHOLD:
        return 1
    if net_z >= TREND_THRESHOLD:
        return -1
    return 0


def _as_of(series, ts):
    """Last entry in a (timestamp-sorted) series with timestamp <= ts. series: list of (ts, net_z, ready)."""
    lo, hi = 0, len(series) - 1
    result = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= ts:
            result = series[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return result


def backfill_date(conn, date_str):
    cur = conn.cursor()
    per_tf_series = {}
    for tf in TIMEFRAMES:
        cur.execute(_tf_netz_sql(tf), {"date": date_str})
        per_tf_series[tf] = [(row[0], float(row[1] - row[2]) if row[1] is not None else 0.0, row[3]) for row in cur.fetchall()]

    base_series = per_tf_series[1]
    if not base_series:
        cur.close()
        return 0

    updates = []
    for ts, net_z_1, ready_1 in base_series:
        per_tf_now = {}
        for tf in TIMEFRAMES:
            entry = _as_of(per_tf_series[tf], ts)
            if entry is None:
                per_tf_now[tf] = (0.0, False)
            else:
                per_tf_now[tf] = (entry[1], entry[2])

        nz1, r1 = per_tf_now[1]
        trigger_dir = _trend_score(nz1, r1)

        agreement_score = 0
        if trigger_dir != 0:
            for tf in TIMEFRAMES:
                nz, ready = per_tf_now[tf]
                if _trend_score(nz, ready) == trigger_dir:
                    agreement_score += MTF_WEIGHTS[tf]

        if agreement_score >= 90:
            conviction = "very_strong"
        elif agreement_score >= 70:
            conviction = "strong"
        elif agreement_score >= 50:
            conviction = "watch"
        else:
            conviction = "no_edge"

        chain_tfs = [1, 3, 5, 10]
        scores = [_trend_score(*per_tf_now[tf]) for tf in chain_tfs]
        if scores[0] == 0:
            cascade_dir, cascade_depth = "--", 0
        else:
            direction = scores[0]
            depth = 0
            for s in scores:
                if s == direction:
                    depth += 1
                else:
                    break
            cascade_dir = "bullish" if direction == 1 else "bearish"
            cascade_depth = depth

        nz3, r3 = per_tf_now[3]
        nz5, r5 = per_tf_now[5]
        tf3_score = _trend_score(nz3, r3)
        tf5_score = _trend_score(nz5, r5)

        if not r1:
            final_signal = "--"
        elif nz1 < -3.0:
            final_signal = "BUY" if (tf3_score == 1 and tf5_score != -1) else "WAIT"
        elif nz1 > 3.0:
            final_signal = "SELL" if (tf3_score == -1 and tf5_score != 1) else "WAIT"
        else:
            final_signal = "WAIT"

        updates.append((agreement_score, conviction, cascade_dir, cascade_depth, final_signal, ts))

    # Single bulk UPDATE via a VALUES list joined back to market_snapshots —
    # avoids one network round-trip per row (executemany was the bottleneck:
    # ~376 sequential round-trips per day over the network to Neon).
    psycopg2.extras.execute_values(
        cur,
        """
        UPDATE market_snapshots ms
        SET mtf_agreement_score = v.agreement_score,
            mtf_conviction = v.conviction,
            mtf_cascade_direction = v.cascade_dir,
            mtf_cascade_depth = v.cascade_depth,
            mtf_compression_expansion = FALSE,
            mtf_final_signal = v.final_signal
        FROM (VALUES %s) AS v(agreement_score, conviction, cascade_dir, cascade_depth, final_signal, ts)
        WHERE ms.symbol = 'NIFTY' AND ms.timestamp = v.ts
        """,
        updates,
        template="(%s, %s, %s, %s, %s, %s::timestamptz)",
    )
    conn.commit()
    cur.close()
    return len(updates)


def main():
    db_url = config.DATABASE_URL
    if not db_url:
        print("No DATABASE_URL configured — aborting.")
        return

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT DATE(timestamp AT TIME ZONE 'Asia/Kolkata') as dt
        FROM market_snapshots
        WHERE symbol = 'NIFTY'
        ORDER BY dt ASC
    """)
    dates = [row[0].strftime("%Y-%m-%d") for row in cur.fetchall()]
    cur.close()

    print(f"Backfilling Multi-TF confirmation for {len(dates)} trading day(s)...", flush=True)
    total = 0
    for d in dates:
        n = backfill_date(conn, d)
        total += n
        print(f"  {d}: updated {n} rows", flush=True)

    conn.close()
    print(f"Done. {total} rows updated across {len(dates)} day(s).", flush=True)


if __name__ == "__main__":
    main()
