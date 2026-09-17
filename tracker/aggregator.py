from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
import sqlite3

from .database import get_connection
from .pacing_engine import parse_iso_time

def get_multi_period_data(db_path: str, bucket_id: str, granularity: str = "1h") -> Dict[str, Any]:
    """
    Returns time-series aggregated usage for the requested granularity:
    '1h' (by hour), '5h' (by 5 hours), '1d' (by day), '1w' (by week).
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    if granularity == "1h":
        # Group by individual hour
        cursor.execute("""
            SELECT 
                strftime('%Y-%m-%d %H:00:00', timestamp) AS time_slot,
                AVG(remaining_fraction) * 100.0 AS avg_remaining,
                SUM(delta_used) * 100.0 AS consumed_percent,
                COUNT(*) as count
            FROM usage_snapshots
            WHERE bucket_id = ?
            GROUP BY time_slot
            ORDER BY time_slot DESC
            LIMIT 72
        """, (bucket_id,))
        rows = [dict(r) for r in cursor.fetchall()]
        rows.reverse()
        conn.close()
        return {
            "granularity": "1h",
            "bucket_id": bucket_id,
            "labels": [r["time_slot"] for r in rows],
            "consumed": [round(r["consumed_percent"], 2) for r in rows],
            "remaining": [round(r["avg_remaining"], 2) for r in rows]
        }

    elif granularity == "5h":
        # Group by 5-hour blocks (Unix epoch / (5 * 3600))
        cursor.execute("""
            SELECT 
                datetime((strftime('%s', timestamp) / 18000) * 18000, 'unixepoch') AS time_slot,
                AVG(remaining_fraction) * 100.0 AS avg_remaining,
                SUM(delta_used) * 100.0 AS consumed_percent,
                COUNT(*) as count
            FROM usage_snapshots
            WHERE bucket_id = ?
            GROUP BY time_slot
            ORDER BY time_slot DESC
            LIMIT 36
        """, (bucket_id,))
        rows = [dict(r) for r in cursor.fetchall()]
        rows.reverse()
        conn.close()
        return {
            "granularity": "5h",
            "bucket_id": bucket_id,
            "labels": [r["time_slot"] for r in rows],
            "consumed": [round(r["consumed_percent"], 2) for r in rows],
            "remaining": [round(r["avg_remaining"], 2) for r in rows]
        }

    elif granularity == "1d":
        # Group by day
        cursor.execute("""
            SELECT 
                strftime('%Y-%m-%d', timestamp) AS time_slot,
                AVG(remaining_fraction) * 100.0 AS avg_remaining,
                SUM(delta_used) * 100.0 AS consumed_percent,
                COUNT(*) as count
            FROM usage_snapshots
            WHERE bucket_id = ?
            GROUP BY time_slot
            ORDER BY time_slot DESC
            LIMIT 30
        """, (bucket_id,))
        rows = [dict(r) for r in cursor.fetchall()]
        rows.reverse()
        conn.close()
        return {
            "granularity": "1d",
            "bucket_id": bucket_id,
            "labels": [r["time_slot"] for r in rows],
            "consumed": [round(r["consumed_percent"], 2) for r in rows],
            "remaining": [round(r["avg_remaining"], 2) for r in rows]
        }

    elif granularity == "1w":
        # Group by week (%Y-W%W)
        cursor.execute("""
            SELECT 
                strftime('%Y-W%W', timestamp) AS time_slot,
                AVG(remaining_fraction) * 100.0 AS avg_remaining,
                SUM(delta_used) * 100.0 AS consumed_percent,
                COUNT(*) as count
            FROM usage_snapshots
            WHERE bucket_id = ?
            GROUP BY time_slot
            ORDER BY time_slot DESC
            LIMIT 12
        """, (bucket_id,))
        rows = [dict(r) for r in cursor.fetchall()]
        rows.reverse()
        conn.close()
        return {
            "granularity": "1w",
            "bucket_id": bucket_id,
            "labels": [r["time_slot"] for r in rows],
            "consumed": [round(r["consumed_percent"], 2) for r in rows],
            "remaining": [round(r["avg_remaining"], 2) for r in rows]
        }

    conn.close()
    return {"granularity": granularity, "labels": [], "consumed": [], "remaining": []}

def get_comparative_cycles(db_path: str, bucket_id: str) -> Dict[str, Any]:
    """
    Extracts detected cycles grouped by distinct reset_times and normalizes them
    onto an elapsed hours axis (0 to 120h) for side-by-side overlay.
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT reset_time 
        FROM usage_snapshots 
        WHERE bucket_id = ? AND reset_time != ''
        ORDER BY reset_time DESC
        LIMIT 4
    """, (bucket_id,))
    reset_times = [r["reset_time"] for r in cursor.fetchall()]

    cycles = []
    for idx, rtime in enumerate(reset_times):
        cursor.execute("""
            SELECT timestamp, remaining_fraction, (1.0 - remaining_fraction) * 100.0 as used_percent
            FROM usage_snapshots
            WHERE bucket_id = ? AND reset_time = ?
            ORDER BY timestamp ASC
        """, (bucket_id, rtime))
        rows = cursor.fetchall()
        if not rows:
            continue

        r_dt = parse_iso_time(rtime)
        first_dt = parse_iso_time(rows[0]["timestamp"])
        cycle_start = r_dt - timedelta(hours=120)
        if first_dt < cycle_start:
            cycle_start = first_dt

        points = []
        for r in rows:
            t_dt = parse_iso_time(r["timestamp"])
            elapsed_h = max(0.0, (t_dt - cycle_start).total_seconds() / 3600.0)
            points.append({
                "elapsed_hours": round(elapsed_h, 2),
                "timestamp": r["timestamp"],
                "used_percent": round(r["used_percent"], 2)
            })

        cycle_name = "Current Cycle" if idx == 0 else f"Cycle {r_dt.strftime('%b %d')}"
        cycles.append({
            "name": cycle_name,
            "reset_time": rtime,
            "is_current": (idx == 0),
            "points": points
        })

    conn.close()
    return {"bucket_id": bucket_id, "cycles": cycles}

def get_today_vs_yesterday(db_path: str, bucket_id: str) -> Dict[str, Any]:
    """Compares hourly usage for today vs yesterday."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.strftime('%Y-%m-%d')
    yesterday_str = (now_utc - timedelta(days=1)).strftime('%Y-%m-%d')

    cursor.execute("""
        SELECT 
            strftime('%Y-%m-%d', timestamp) as day_str,
            cast(strftime('%H', timestamp) as integer) as hour_int,
            SUM(delta_used) * 100.0 as consumed
        FROM usage_snapshots
        WHERE bucket_id = ? AND strftime('%Y-%m-%d', timestamp) IN (?, ?)
        GROUP BY day_str, hour_int
        ORDER BY day_str, hour_int ASC
    """, (bucket_id, today_str, yesterday_str))

    rows = cursor.fetchall()
    conn.close()

    today_hours = {h: 0.0 for h in range(24)}
    yesterday_hours = {h: 0.0 for h in range(24)}

    for r in rows:
        if r["day_str"] == today_str:
            today_hours[r["hour_int"]] = round(r["consumed"], 2)
        elif r["day_str"] == yesterday_str:
            yesterday_hours[r["hour_int"]] = round(r["consumed"], 2)

    return {
        "hours": [f"{h:02d}:00" for h in range(24)],
        "today": [today_hours[h] for h in range(24)],
        "yesterday": [yesterday_hours[h] for h in range(24)]
    }
