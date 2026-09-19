from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
import sqlite3

from .database import get_connection
from .pacing_engine import parse_iso_time

def get_multi_period_data(
    db_path: str,
    bucket_id: str,
    granularity: str = "1h",
    user_id: Optional[str] = "default"
) -> Dict[str, Any]:
    """
    Returns time-series aggregated usage for the requested granularity:
    '1h' (by hour), '5h' (by 5 hours), '1d' (by day), '1w' (by week).
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    if granularity == "1h":
        cursor.execute("""
            SELECT 
                strftime('%Y-%m-%d %H:00:00', timestamp) AS time_slot,
                AVG(remaining_fraction) * 100.0 AS avg_remaining,
                SUM(delta_used) * 100.0 AS consumed_percent,
                COUNT(*) as count
            FROM usage_snapshots
            WHERE bucket_id = ? AND (? IS NULL OR user_id = ?)
            GROUP BY time_slot
            ORDER BY time_slot DESC
            LIMIT 72
        """, (bucket_id, user_id, user_id))
        rows = [dict(r) for r in cursor.fetchall()]
        rows.reverse()
        conn.close()
        return {
            "granularity": "1h",
            "bucket_id": bucket_id,
            "user_id": user_id,
            "labels": [r["time_slot"] for r in rows],
            "consumed": [round(r["consumed_percent"], 2) for r in rows],
            "remaining": [round(r["avg_remaining"], 2) for r in rows]
        }

    elif granularity == "5h":
        cursor.execute("""
            SELECT 
                datetime((strftime('%s', timestamp) / 18000) * 18000, 'unixepoch') AS time_slot,
                AVG(remaining_fraction) * 100.0 AS avg_remaining,
                SUM(delta_used) * 100.0 AS consumed_percent,
                COUNT(*) as count
            FROM usage_snapshots
            WHERE bucket_id = ? AND (? IS NULL OR user_id = ?)
            GROUP BY time_slot
            ORDER BY time_slot DESC
            LIMIT 36
        """, (bucket_id, user_id, user_id))
        rows = [dict(r) for r in cursor.fetchall()]
        rows.reverse()
        conn.close()
        return {
            "granularity": "5h",
            "bucket_id": bucket_id,
            "user_id": user_id,
            "labels": [r["time_slot"] for r in rows],
            "consumed": [round(r["consumed_percent"], 2) for r in rows],
            "remaining": [round(r["avg_remaining"], 2) for r in rows]
        }

    elif granularity == "1d":
        cursor.execute("""
            SELECT 
                strftime('%Y-%m-%d', timestamp) AS time_slot,
                AVG(remaining_fraction) * 100.0 AS avg_remaining,
                SUM(delta_used) * 100.0 AS consumed_percent,
                COUNT(*) as count
            FROM usage_snapshots
            WHERE bucket_id = ? AND (? IS NULL OR user_id = ?)
            GROUP BY time_slot
            ORDER BY time_slot DESC
            LIMIT 30
        """, (bucket_id, user_id, user_id))
        rows = [dict(r) for r in cursor.fetchall()]
        rows.reverse()
        conn.close()
        return {
            "granularity": "1d",
            "bucket_id": bucket_id,
            "user_id": user_id,
            "labels": [r["time_slot"] for r in rows],
            "consumed": [round(r["consumed_percent"], 2) for r in rows],
            "remaining": [round(r["avg_remaining"], 2) for r in rows]
        }

    elif granularity == "1w":
        cursor.execute("""
            SELECT 
                strftime('%Y-W%W', timestamp) AS time_slot,
                AVG(remaining_fraction) * 100.0 AS avg_remaining,
                SUM(delta_used) * 100.0 AS consumed_percent,
                COUNT(*) as count
            FROM usage_snapshots
            WHERE bucket_id = ? AND (? IS NULL OR user_id = ?)
            GROUP BY time_slot
            ORDER BY time_slot DESC
            LIMIT 12
        """, (bucket_id, user_id, user_id))
        rows = [dict(r) for r in cursor.fetchall()]
        rows.reverse()
        conn.close()
        return {
            "granularity": "1w",
            "bucket_id": bucket_id,
            "user_id": user_id,
            "labels": [r["time_slot"] for r in rows],
            "consumed": [round(r["consumed_percent"], 2) for r in rows],
            "remaining": [round(r["avg_remaining"], 2) for r in rows]
        }

    conn.close()
    return {"granularity": granularity, "labels": [], "consumed": [], "remaining": []}

def get_comparative_cycles(
    db_path: str,
    bucket_id: str,
    user_id: Optional[str] = "default"
) -> Dict[str, Any]:
    """
    Extracts detected cycles grouped by distinct reset_times and normalizes them
    onto an elapsed hours axis (0 to 120h) for side-by-side overlay.
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT reset_time 
        FROM usage_snapshots 
        WHERE bucket_id = ? AND (? IS NULL OR user_id = ?) AND reset_time != ''
        ORDER BY reset_time DESC
        LIMIT 4
    """, (bucket_id, user_id, user_id))
    reset_times = [r["reset_time"] for r in cursor.fetchall()]

    cycles = []
    for idx, rtime in enumerate(reset_times):
        cursor.execute("""
            SELECT timestamp, remaining_fraction, (1.0 - remaining_fraction) * 100.0 as used_percent
            FROM usage_snapshots
            WHERE bucket_id = ? AND (? IS NULL OR user_id = ?) AND reset_time = ?
            ORDER BY timestamp ASC
        """, (bucket_id, user_id, user_id, rtime))
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
    return {"bucket_id": bucket_id, "user_id": user_id, "cycles": cycles}

def get_today_vs_yesterday(
    db_path: str,
    bucket_id: str,
    user_id: Optional[str] = "default"
) -> Dict[str, Any]:
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
        WHERE bucket_id = ? AND (? IS NULL OR user_id = ?) AND strftime('%Y-%m-%d', timestamp) IN (?, ?)
        GROUP BY day_str, hour_int
        ORDER BY day_str, hour_int ASC
    """, (bucket_id, user_id, user_id, today_str, yesterday_str))

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
        "yesterday": [yesterday_hours[h] for h in range(24)],
        "user_id": user_id
    }

def get_family_usage_breakdown(db_path: str) -> Dict[str, Any]:
    """
    Computes comparative usage metrics across all family members:
    - Latest remaining % and used % for Gemini and Claude
    - Total consumed % in the last 7 days
    - Status and last seen timestamp
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 
            user_id,
            MAX(display_name) AS display_name,
            MAX(timestamp) AS last_seen,
            COUNT(*) AS snapshot_count
        FROM usage_snapshots
        GROUP BY user_id
        ORDER BY (CASE WHEN user_id = 'default' THEN 0 ELSE 1 END), last_seen DESC
    """)
    users_meta = [dict(r) for r in cursor.fetchall()]

    now_utc = datetime.now(timezone.utc)
    seven_days_ago = (now_utc - timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ')

    user_summaries = []
    total_family_consumed = 0.0

    for u in users_meta:
        uid = u["user_id"]
        dname = u["display_name"]
        if uid == "default" and dname == "You":
            dname = "You (Primary Workstation)"

        # Latest gemini snapshot
        cursor.execute("""
            SELECT remaining_fraction, reset_time FROM usage_snapshots
            WHERE user_id = ? AND bucket_id = 'gemini-weekly'
            ORDER BY timestamp DESC LIMIT 1
        """, (uid,))
        gem_row = cursor.fetchone()
        gem_rem = round(float(gem_row["remaining_fraction"]) * 100.0, 1) if gem_row else 100.0
        gem_reset = gem_row["reset_time"] if gem_row else ""

        # Latest claude snapshot
        cursor.execute("""
            SELECT remaining_fraction, reset_time FROM usage_snapshots
            WHERE user_id = ? AND bucket_id = '3p-weekly'
            ORDER BY timestamp DESC LIMIT 1
        """, (uid,))
        c_row = cursor.fetchone()
        c_rem = round(float(c_row["remaining_fraction"]) * 100.0, 1) if c_row else 100.0

        # Consumed in the past 7 days across all buckets
        cursor.execute("""
            SELECT SUM(delta_used) * 100.0 as consumed
            FROM usage_snapshots
            WHERE user_id = ? AND bucket_id = 'gemini-weekly' AND timestamp >= ?
        """, (uid, seven_days_ago))
        cons_row = cursor.fetchone()
        consumed_7d = round(float(cons_row["consumed"] or 0.0), 1)

        total_family_consumed += consumed_7d

        # Determine active status (last seen within 2 hours)
        last_dt = parse_iso_time(u["last_seen"]) if u["last_seen"] else None
        is_online = bool(last_dt and (now_utc - last_dt).total_seconds() < 7200)

        user_summaries.append({
            "user_id": uid,
            "display_name": dname,
            "last_seen": u["last_seen"],
            "is_online": is_online,
            "gemini_remaining_pct": gem_rem,
            "gemini_used_pct": round(100.0 - gem_rem, 1),
            "gemini_reset_time": gem_reset,
            "claude_remaining_pct": c_rem,
            "claude_used_pct": round(100.0 - c_rem, 1),
            "consumed_7d_pct": consumed_7d,
            "snapshot_count": u["snapshot_count"]
        })

    conn.close()

    # Calculate share percentage
    for u in user_summaries:
        if total_family_consumed > 0:
            u["share_pct"] = round((u["consumed_7d_pct"] / total_family_consumed) * 100.0, 1)
        else:
            u["share_pct"] = 0.0

    return {
        "users": user_summaries,
        "total_users": len(user_summaries),
        "total_family_consumed_7d": round(total_family_consumed, 1)
    }
