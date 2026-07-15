"""
Fast SQL-based backfill: compute rolling Avg(10m)/Ratio at range=5 (ATM ± 5
strikes) for every historical trading day, entirely in Postgres (one UPDATE
per day instead of one per row). Verified to produce identical results to
the Python rolling-window logic in web/api_routes.py (see conversation).

Run manually: python scripts/backfill_sql.py
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
with_avg AS (
    SELECT timestamp, rn, pe_chg, ce_chg,
        CASE WHEN rn >= 11 THEN
            AVG(pe_chg) OVER (ORDER BY rn ROWS BETWEEN 9 PRECEDING AND CURRENT ROW)
        ELSE NULL END as pe_avg,
        CASE WHEN rn >= 11 THEN
            AVG(ce_chg) OVER (ORDER BY rn ROWS BETWEEN 9 PRECEDING AND CURRENT ROW)
        ELSE NULL END as ce_avg
    FROM changes_clean
),
with_ratio AS (
    SELECT timestamp, pe_chg, ce_chg, pe_avg, ce_avg,
        LAG(pe_avg) OVER (ORDER BY rn) as prev_pe_avg,
        LAG(ce_avg) OVER (ORDER BY rn) as prev_ce_avg
    FROM with_avg
)
UPDATE market_snapshots ms
SET pe_change_avg_10m = COALESCE(w.pe_avg, 0),
    ce_change_avg_10m = COALESCE(w.ce_avg, 0),
    pe_change_ratio = CASE WHEN w.prev_pe_avg IS NOT NULL AND ABS(w.prev_pe_avg) > 100
        THEN ROUND((w.pe_chg / w.prev_pe_avg)::numeric, 1) ELSE 0 END,
    ce_change_ratio = CASE WHEN w.prev_ce_avg IS NOT NULL AND ABS(w.prev_ce_avg) > 100
        THEN ROUND((w.ce_chg / w.prev_ce_avg)::numeric, 1) ELSE 0 END
FROM with_ratio w
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

    print(f"Backfilling {len(dates)} trading day(s) via SQL...", flush=True)
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
