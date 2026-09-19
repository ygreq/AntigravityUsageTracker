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
        user_id TEXT NOT NULL DEFAULT 'default',
        display_name TEXT NOT NULL DEFAULT 'You',
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

    # Automatic Schema Migration: Check if user_id and display_name columns exist in usage_snapshots
    cursor.execute("PRAGMA table_info(usage_snapshots)")
    cols = [row["name"] for row in cursor.fetchall()]
    if "user_id" not in cols:
        cursor.execute("ALTER TABLE usage_snapshots ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'")
    if "display_name" not in cols:
        cursor.execute("ALTER TABLE usage_snapshots ADD COLUMN display_name TEXT NOT NULL DEFAULT 'You'")

    # Ensure index on user_id exists after column is guaranteed present
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_user_bucket ON usage_snapshots(user_id, bucket_id, timestamp)")

    # Ensure quota_cycles migration as well
    cursor.execute("PRAGMA table_info(quota_cycles)")
    cycle_cols = [row["name"] for row in cursor.fetchall()]
    if "user_id" not in cycle_cols:
        cursor.execute("ALTER TABLE quota_cycles ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'")

    conn.commit()
    conn.close()

def record_snapshots_batch(
    db_path: str,
    snapshots: List[Dict[str, Any]],
    user_id: str = "default",
    display_name: str = "You"
):
    conn = get_connection(db_path)
    cursor = conn.cursor()

    for s in snapshots:
        row_user = s.get("user_id", user_id)
        row_name = s.get("display_name", display_name)

        # Find previous snapshot for this bucket and user to compute delta_used
        cursor.execute("""
            SELECT remaining_fraction FROM usage_snapshots 
            WHERE bucket_id = ? AND user_id = ?
            ORDER BY timestamp DESC LIMIT 1
        """, (s["bucket_id"], row_user))
        prev_row = cursor.fetchone()

        remaining = float(s["remaining_fraction"])
        used = 1.0 - remaining
        delta = 0.0
        if prev_row:
            prev_remaining = float(prev_row["remaining_fraction"])
            if prev_remaining >= remaining:
                delta = prev_remaining - remaining
            else:
                delta = 0.0 # reset event

        cursor.execute("""
            INSERT INTO usage_snapshots (
                user_id, display_name, timestamp, group_name, bucket_id, bucket_name, window_type,
                remaining_fraction, used_fraction, delta_used, reset_time,
                description, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row_user,
            row_name,
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

def get_latest_snapshots(db_path: str, user_id: Optional[str] = "default") -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    if user_id:
        cursor.execute("""
            SELECT s.* FROM usage_snapshots s
            INNER JOIN (
                SELECT bucket_id, MAX(timestamp) as max_time
                FROM usage_snapshots
                WHERE user_id = ?
                GROUP BY bucket_id
            ) latest ON s.bucket_id = latest.bucket_id AND s.timestamp = latest.max_time
            WHERE s.user_id = ?
            ORDER BY s.group_name, s.window_type DESC
        """, (user_id, user_id))
    else:
        cursor.execute("""
            SELECT s.* FROM usage_snapshots s
            INNER JOIN (
                SELECT user_id, bucket_id, MAX(timestamp) as max_time
                FROM usage_snapshots
                GROUP BY user_id, bucket_id
            ) latest ON s.user_id = latest.user_id AND s.bucket_id = latest.bucket_id AND s.timestamp = latest.max_time
            ORDER BY s.user_id, s.group_name, s.window_type DESC
        """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_bucket_history(
    db_path: str,
    bucket_id: str,
    limit: int = 500,
    since: Optional[str] = None,
    user_id: Optional[str] = "default"
) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    
    query = "SELECT * FROM usage_snapshots WHERE bucket_id = ?"
    params: List[Any] = [bucket_id]

    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)

    if since:
        query += " AND timestamp >= ? ORDER BY timestamp ASC"
        params.append(since)
        cursor.execute(query, params)
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows
    else:
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        rows = [dict(r) for r in cursor.fetchall()]
        rows.reverse()
        conn.close()
        return rows

def get_hourly_aggregates(
    db_path: str,
    bucket_id: str,
    hours: int = 72,
    user_id: Optional[str] = "default"
) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    query = """
        SELECT 
            strftime('%Y-%m-%d %H:00:00', timestamp) AS hour_slot,
            AVG(remaining_fraction) AS avg_remaining,
            MIN(remaining_fraction) AS min_remaining,
            MAX(remaining_fraction) AS max_remaining,
            SUM(delta_used) AS total_consumed,
            COUNT(*) AS sample_count
        FROM usage_snapshots
        WHERE bucket_id = ?
    """
    params: List[Any] = [bucket_id]
    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)
    query += """
        GROUP BY hour_slot
        ORDER BY hour_slot DESC
        LIMIT ?
    """
    params.append(hours)
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    rows.reverse()
    conn.close()
    return rows

def get_daily_aggregates(
    db_path: str,
    bucket_id: str,
    days: int = 30,
    user_id: Optional[str] = "default"
) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    query = """
        SELECT 
            strftime('%Y-%m-%d', timestamp) AS day_slot,
            AVG(remaining_fraction) AS avg_remaining,
            MIN(remaining_fraction) AS min_remaining,
            MAX(remaining_fraction) AS max_remaining,
            SUM(delta_used) AS total_consumed,
            COUNT(*) AS sample_count
        FROM usage_snapshots
        WHERE bucket_id = ?
    """
    params: List[Any] = [bucket_id]
    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)
    query += """
        GROUP BY day_slot
        ORDER BY day_slot DESC
        LIMIT ?
    """
    params.append(days)
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    rows.reverse()
    conn.close()
    return rows

def get_usage_logs(
    db_path: str,
    bucket_id: Optional[str] = None,
    user_id: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
) -> Dict[str, Any]:
    conn = get_connection(db_path)
    cursor = conn.cursor()

    query = "SELECT * FROM usage_snapshots WHERE 1=1"
    params: List[Any] = []

    if bucket_id:
        query += " AND bucket_id = ?"
        params.append(bucket_id)

    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)

    if search:
        query += " AND (group_name LIKE ? OR bucket_name LIKE ? OR description LIKE ? OR user_id LIKE ? OR display_name LIKE ?)"
        wildcard = f"%{search}%"
        params.extend([wildcard, wildcard, wildcard, wildcard, wildcard])

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

def get_active_users(db_path: str) -> List[Dict[str, Any]]:
    """Returns a list of all distinct users/devices recorded in the database."""
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
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    # Ensure 'default' user is present even if database is fresh
    if not any(r["user_id"] == "default" for r in rows):
        rows.insert(0, {
            "user_id": "default",
            "display_name": "You (Primary Workstation)",
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "snapshot_count": 0
        })
    else:
        for r in rows:
            if r["user_id"] == "default" and r.get("display_name") == "You":
                r["display_name"] = "You (Primary Workstation)"

    return rows
