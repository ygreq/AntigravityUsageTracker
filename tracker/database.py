import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

def init_db(db_path: str):
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS usage_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,           -- ISO-8601 UTC
        group_name TEXT NOT NULL,          -- 'Gemini Models' | 'Claude and GPT models'
        bucket_id TEXT NOT NULL,           -- 'gemini-weekly' | 'gemini-5h'
        bucket_name TEXT NOT NULL,         -- 'Weekly Limit Remaining'
        window_type TEXT NOT NULL,         -- 'weekly' | '5h'
        remaining_fraction REAL NOT NULL,  -- 0.0 to 1.0
        used_fraction REAL NOT NULL,       -- 1.0 - remaining_fraction
        delta_used REAL DEFAULT 0.0,       -- delta consumed since immediate previous snapshot
        reset_time TEXT NOT NULL,          -- ISO-8601 UTC reset target
        description TEXT,
        raw_payload TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_snapshots_time ON usage_snapshots(timestamp);
    CREATE INDEX IF NOT EXISTS idx_snapshots_bucket_time ON usage_snapshots(bucket_id, timestamp);

    CREATE TABLE IF NOT EXISTS quota_cycles (
        cycle_id TEXT PRIMARY KEY,         -- e.g. gemini-weekly-2026-09-12T22:30:00Z
        bucket_id TEXT NOT NULL,
        cycle_start TEXT NOT NULL,
        cycle_reset TEXT NOT NULL,
        duration_hours REAL NOT NULL,
        initial_remaining REAL DEFAULT 1.0,
        final_remaining REAL,
        status TEXT NOT NULL               -- 'ACTIVE' | 'COMPLETED'
    );

    CREATE INDEX IF NOT EXISTS idx_cycles_bucket ON quota_cycles(bucket_id, status);
    """)

    conn.commit()
    conn.close()

def record_snapshots_batch(db_path: str, snapshots: List[Dict[str, Any]]):
    conn = get_connection(db_path)
    cursor = conn.cursor()

    for s in snapshots:
        # Find previous snapshot for this bucket to compute delta_used
        cursor.execute("""
            SELECT remaining_fraction FROM usage_snapshots 
            WHERE bucket_id = ? 
            ORDER BY timestamp DESC LIMIT 1
        """, (s["bucket_id"],))
        prev_row = cursor.fetchone()

        remaining = float(s["remaining_fraction"])
        used = 1.0 - remaining
        delta = 0.0
        if prev_row:
            prev_remaining = float(prev_row["remaining_fraction"])
            # If remaining decreased, delta is positive (consumption)
            # If remaining increased, quota reset or refilled, so delta is 0 or negative
            if prev_remaining >= remaining:
                delta = prev_remaining - remaining
            else:
                delta = 0.0 # reset event

        cursor.execute("""
            INSERT INTO usage_snapshots (
                timestamp, group_name, bucket_id, bucket_name, window_type,
                remaining_fraction, used_fraction, delta_used, reset_time,
                description, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            s["timestamp"],
            s["group_name"],
            s["bucket_id"],
            s["bucket_name"],
            s["window_type"],
            remaining,
            used,
            delta,
            s["reset_time"],
            s.get("description", ""),
            s.get("raw_payload", "")
        ))

    conn.commit()
    conn.close()

def get_latest_snapshots(db_path: str) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.* FROM usage_snapshots s
        INNER JOIN (
            SELECT bucket_id, MAX(timestamp) as max_time
            FROM usage_snapshots
            GROUP BY bucket_id
        ) latest ON s.bucket_id = latest.bucket_id AND s.timestamp = latest.max_time
        ORDER BY s.group_name, s.window_type DESC
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_bucket_history(db_path: str, bucket_id: str, limit: int = 500, since: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    if since:
        cursor.execute("""
            SELECT * FROM usage_snapshots
            WHERE bucket_id = ? AND timestamp >= ?
            ORDER BY timestamp ASC
        """, (bucket_id, since))
    else:
        cursor.execute("""
            SELECT * FROM usage_snapshots
            WHERE bucket_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (bucket_id, limit))
        rows = [dict(r) for r in cursor.fetchall()]
        rows.reverse()
        conn.close()
        return rows

    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_hourly_aggregates(db_path: str, bucket_id: str, hours: int = 72) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            strftime('%Y-%m-%d %H:00:00', timestamp) AS hour_slot,
            AVG(remaining_fraction) AS avg_remaining,
            MIN(remaining_fraction) AS min_remaining,
            MAX(remaining_fraction) AS max_remaining,
            SUM(delta_used) AS total_consumed,
            COUNT(*) AS sample_count
        FROM usage_snapshots
        WHERE bucket_id = ?
        GROUP BY hour_slot
        ORDER BY hour_slot DESC
        LIMIT ?
    """, (bucket_id, hours))
    rows = [dict(r) for r in cursor.fetchall()]
    rows.reverse()
    conn.close()
    return rows

def get_daily_aggregates(db_path: str, bucket_id: str, days: int = 30) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            strftime('%Y-%m-%d', timestamp) AS day_slot,
            AVG(remaining_fraction) AS avg_remaining,
            MIN(remaining_fraction) AS min_remaining,
            MAX(remaining_fraction) AS max_remaining,
            SUM(delta_used) AS total_consumed,
            COUNT(*) AS sample_count
        FROM usage_snapshots
        WHERE bucket_id = ?
        GROUP BY day_slot
        ORDER BY day_slot DESC
        LIMIT ?
    """, (bucket_id, days))
    rows = [dict(r) for r in cursor.fetchall()]
    rows.reverse()
    conn.close()
    return rows

def get_usage_logs(
    db_path: str,
    bucket_id: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
) -> Dict[str, Any]:
    conn = get_connection(db_path)
    cursor = conn.cursor()

    query = "SELECT * FROM usage_snapshots WHERE 1=1"
    params = []

    if bucket_id:
        query += " AND bucket_id = ?"
        params.append(bucket_id)

    if search:
        query += " AND (group_name LIKE ? OR bucket_name LIKE ? OR description LIKE ?)"
        wildcard = f"%{search}%"
        params.extend([wildcard, wildcard, wildcard])

    count_query = f"SELECT COUNT(*) as total FROM ({query})"
    cursor.execute(count_query, params)
    total_count = cursor.fetchone()["total"]

    query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return {
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "logs": rows
    }
