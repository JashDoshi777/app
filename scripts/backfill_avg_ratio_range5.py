"""
One-off backfill: compute rolling Avg(10m)/Ratio at range=5 (ATM ± 5 strikes)
for every historical trading day and store into market_snapshots.

This does NOT change how the UI displays historical data — the UI always
recomputes live for whatever range the user picks. This script only persists
a range=5 snapshot into the DB for external querying/export.

Run manually: python scripts/backfill_avg_ratio_range5.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
import config
from web.api_routes import _get_historical_oi_table

RANGE_STRIKES = 5


async def backfill_date(db_url: str, date_str: str):
    result = await _get_historical_oi_table(date_str, tf=1, range_strikes=RANGE_STRIKES)
    rows = result.get("rows", [])
    if not rows:
        print(f"  {date_str}: no rows, skipping")
        return 0

    # _get_historical_oi_table returns newest-first; put back in chronological order
    chrono = list(reversed(rows))

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    # Fetch the actual DB timestamps in the same chronological order (tf=1 means 1:1 row mapping)
    cur.execute("""
        SELECT id, timestamp FROM market_snapshots
        WHERE symbol = 'NIFTY'
          AND DATE(timestamp AT TIME ZONE 'Asia/Kolkata') = %s
        ORDER BY timestamp ASC
    """, (date_str,))
    db_rows = cur.fetchall()

    if len(db_rows) != len(chrono):
        print(f"  {date_str}: row count mismatch (db={len(db_rows)}, computed={len(chrono)}), skipping")
        cur.close()
        conn.close()
        return 0

    updated = 0
    for (row_id, _ts), r in zip(db_rows, chrono):
        raw = r["_raw"]
        cur.execute("""
            UPDATE market_snapshots
            SET pe_change_avg_10m = %s,
                ce_change_avg_10m = %s,
                pe_change_ratio = %s,
                ce_change_ratio = %s
            WHERE id = %s
        """, (
            raw.get("pe_change_avg", 0),
            raw.get("ce_change_avg", 0),
            raw.get("pe_change_ratio", 0),
            raw.get("ce_change_ratio", 0),
            row_id,
        ))
        updated += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"  {date_str}: updated {updated} rows")
    return updated


async def main():
    db_url = os.environ.get("DATABASE_URL", config.DATABASE_URL)
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
    conn.close()

    print(f"Backfilling Avg(10m)/Ratio at range={RANGE_STRIKES} for {len(dates)} trading day(s)...")
    total = 0
    for d in dates:
        total += await backfill_date(db_url, d)
    print(f"Done. {total} rows updated across {len(dates)} day(s).")


if __name__ == "__main__":
    asyncio.run(main())
