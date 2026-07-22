"""
Fast SQL-based backfill: compute the Z-score signal engine (Std(10)/Z/Net Z/
Signal) at range=5 (ATM ± 5 strikes) for every historical trading day,
entirely in Postgres. Verified to produce identical results to the Python
logic in web/api_routes.py (see conversation).

For row t, Avg10/Std10 come from the 10 rows STRICTLY BEFORE t (rows [t-10,
t-1], current row excluded) — this is one row later than the old Avg(10m)
window, which included the current row.

Run manually: python scripts/backfill_zscore_sql.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
import config

BACKFILL_SQL = """
WITH day_atm AS (
    SELECT DISTINCT ON (1) 1 as k,
        ROUND(COALESCE(NULLIF(future_ltp,0), underlying_price) / 50) * 50 as atm
    FROM market_snapshots
    WHERE symbol='NIFTY' AND DATE(timestamp AT TIME ZONE 'Asia/Kolkata') = %(date)s
    ORDER BY 1, timestamp DESC
),
ranged AS (
    SELECT o.timestamp,
        SUM(o.pe_oi) as total_pe,
        SUM(o.ce_oi) as total_ce
    FROM oi_snapshots o, day_atm a
    WHERE o.symbol='NIFTY'
      AND DATE(o.timestamp AT TIME ZONE 'Asia/Kolkata') = %(date)s
      AND ABS(o.strike - a.atm) <= 5*50
    GROUP BY o.timestamp
),
changes AS (
    SELECT timestamp,
        ROW_NUMBER() OVER (ORDER BY timestamp) as rn,
        total_pe - LAG(total_pe) OVER (ORDER BY timestamp) as pe_chg_raw,
        total_ce - LAG(total_ce) OVER (ORDER BY timestamp) as ce_chg_raw
    FROM ranged
),
changes_clean AS (
    SELECT timestamp, rn,
        CASE WHEN rn = 1 THEN 0 ELSE pe_chg_raw END as pe_chg,
        CASE WHEN rn = 1 THEN 0 ELSE ce_chg_raw END as ce_chg
    FROM changes
),
with_stats AS (
    SELECT timestamp, rn, pe_chg, ce_chg,
        CASE WHEN rn >= 12 THEN
            AVG(pe_chg) OVER (ORDER BY rn ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING)
        ELSE NULL END as pe_avg,
        CASE WHEN rn >= 12 THEN
            AVG(ce_chg) OVER (ORDER BY rn ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING)
        ELSE NULL END as ce_avg,
        CASE WHEN rn >= 12 THEN
            STDDEV_SAMP(pe_chg) OVER (ORDER BY rn ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING)
        ELSE NULL END as pe_std,
        CASE WHEN rn >= 12 THEN
            STDDEV_SAMP(ce_chg) OVER (ORDER BY rn ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING)
        ELSE NULL END as ce_std
    FROM changes_clean
),
with_z AS (
    SELECT timestamp, pe_std, ce_std,
        CASE WHEN pe_std IS NOT NULL AND pe_std > 0 THEN (pe_chg - pe_avg) / pe_std ELSE 0 END as z_pe,
        CASE WHEN ce_std IS NOT NULL AND ce_std > 0 THEN (ce_chg - ce_avg) / ce_std ELSE 0 END as z_ce,
        pe_avg
    FROM with_stats
)
UPDATE market_snapshots ms
SET pe_std10 = COALESCE(w.pe_std, 0),
    ce_std10 = COALESCE(w.ce_std, 0),
    pe_z = COALESCE(w.z_pe, 0),
    ce_z = COALESCE(w.z_ce, 0),
    net_z = COALESCE(w.z_pe - w.z_ce, 0),
    signal_z = CASE
        WHEN w.pe_avg IS NULL THEN '--'
        WHEN (w.z_pe - w.z_ce) < -3.0 THEN 'BUY'
        WHEN (w.z_pe - w.z_ce) > 3.0 THEN 'SELL'
        ELSE 'WAIT'
    END
FROM with_z w
WHERE ms.symbol = 'NIFTY' AND ms.timestamp = w.timestamp;
"""


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

    print(f"Backfilling Z-score engine for {len(dates)} trading day(s) via SQL...", flush=True)
    total = 0
    for d in dates:
        cur.execute(BACKFILL_SQL, {"date": d})
        n = cur.rowcount
        conn.commit()
        total += n
        print(f"  {d}: updated {n} rows", flush=True)

    cur.close()
    conn.close()
    print(f"Done. {total} rows updated across {len(dates)} day(s).", flush=True)


if __name__ == "__main__":
    main()
