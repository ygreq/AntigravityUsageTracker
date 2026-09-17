from datetime import datetime, timezone, timedelta
import random
from typing import List, Dict, Any

from .database import get_connection, init_db

def seed_sample_history(db_path: str, days_back: int = 15):
    """
    Seeds realistic historical snapshots for comparative graphs and multi-period analysis,
    mimicking typical active developer usage leading to current quota state.
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as count FROM usage_snapshots")
    row = cursor.fetchone()
    if row and row["count"] > 10:
        # Already populated
        conn.close()
        return

    now = datetime.now(timezone.utc)
    current_reset = now + timedelta(days=4, hours=18)
    
    # 3 cycles: Cycle -2 (10-15 days ago), Cycle -1 (5-10 days ago), Current Cycle (0-5 days ago)
    cycles = [
        {"start": now - timedelta(days=14), "reset": now - timedelta(days=9)},
        {"start": now - timedelta(days=9), "reset": now - timedelta(days=4)},
        {"start": now - timedelta(days=4), "reset": current_reset}
    ]

    snapshots = []
    
    for c_idx, c in enumerate(cycles):
        c_start = c["start"]
        c_reset = c["reset"]
        reset_str = c_reset.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Simulate 15-minute intervals from c_start up to min(c_reset, now)
        t = c_start
        gemini_rem = 1.0
        five_h_rem = 1.0

        step_min = 15
        while t <= min(c_reset, now):
            hour_of_day = t.hour
            # Coding activity concentrated during 09:00 - 23:00
            is_active = (9 <= hour_of_day <= 23)
            
            # Gemini weekly consumption
            burn = 0.0
            if is_active and random.random() < 0.45:
                burn = random.uniform(0.001, 0.008)
            gemini_rem = max(0.0, gemini_rem - burn)

            # 5h rolling bucket refill & burn
            five_h_rem = min(1.0, five_h_rem + 0.05) # natural refill
            if is_active and burn > 0:
                five_h_rem = max(0.0, five_h_rem - (burn * 2.5))

            ts_str = t.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Snapshots for gemini-weekly and gemini-5h
            snapshots.append((
                ts_str, "Gemini Models", "gemini-weekly", "Weekly Limit Remaining",
                "weekly", round(gemini_rem, 4), round(1.0 - gemini_rem, 4), round(burn, 4),
                reset_str, "Weekly model quota"
            ))
            snapshots.append((
                ts_str, "Gemini Models", "gemini-5h", "Five Hour Limit Remaining",
                "5h", round(five_h_rem, 4), round(1.0 - five_h_rem, 4), round(burn * 2.5, 4),
                (t + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ"), "Five-hour smoothing limit"
            ))

            t += timedelta(minutes=step_min)

    cursor.executemany("""
        INSERT INTO usage_snapshots (
            timestamp, group_name, bucket_id, bucket_name, window_type,
            remaining_fraction, used_fraction, delta_used, reset_time, description
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, snapshots)

    conn.commit()
    conn.close()
