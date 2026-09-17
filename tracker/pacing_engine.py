from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
import math

def parse_iso_time(iso_str: str) -> datetime:
    """Parses ISO-8601 string, ensuring timezone awareness (UTC)."""
    # Replace Z with +00:00 for python fromisoformat
    iso_clean = iso_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(iso_clean)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def format_remaining_time(reset_time_iso: str, now_dt: Optional[datetime] = None) -> Dict[str, Any]:
    """Calculates exactly how many days, hours, and minutes until reset."""
    if not reset_time_iso:
        return {"text": "N/A", "days": 0, "hours": 0, "minutes": 0, "total_hours": 0.0, "total_hours_mins": "N/A"}
    if not now_dt:
        now_dt = datetime.now(timezone.utc)
    reset_dt = parse_iso_time(reset_time_iso)
    total_seconds = max(0, int((reset_dt - now_dt).total_seconds()))
    days = total_seconds // 86400
    rem_seconds = total_seconds % 86400
    hours = rem_seconds // 3600
    minutes = (rem_seconds % 3600) // 60

    # Total hours and remaining minutes (e.g. 79h 5m)
    total_hours_all = total_seconds // 3600
    total_hours_mins = f"{total_hours_all}h {minutes}m"

    # Always state both days and hours
    day_label = f"{days} day{'s' if days != 1 else ''}"
    hour_label = f"{hours} hour{'s' if hours != 1 else ''}"
    
    if days == 0 and hours == 0:
        text = f"0 days, 0 hours, {minutes} min{'s' if minutes != 1 else ''}"
    elif days == 0:
        text = f"0 days, {hour_label}, {minutes} min{'s' if minutes != 1 else ''}"
    else:
        text = f"{day_label}, {hour_label}"

    return {
        "text": text,
        "days": days,
        "hours": hours,
        "minutes": minutes,
        "total_hours": round(total_seconds / 3600.0, 1),
        "total_hours_mins": total_hours_mins
    }

def calculate_pacing(
    current_snapshot: Dict[str, Any],
    history_snapshots: List[Dict[str, Any]],
    cycle_duration_hours: float = 120.0,
    tolerance_percent: float = 5.0
) -> Dict[str, Any]:
    """
    Calculates detailed 5-day credit pacing, burn-rate metrics, and projections.
    """
    now_utc = parse_iso_time(current_snapshot.get("timestamp", datetime.now(timezone.utc).isoformat()))
    reset_time_str = current_snapshot.get("reset_time", "")
    
    if not reset_time_str:
        reset_time = now_utc + timedelta(hours=cycle_duration_hours)
    else:
        reset_time = parse_iso_time(reset_time_str)

    # Determine Cycle Start: Earliest snapshot sharing this exact reset_time, or fallback to reset_time - cycle_duration
    same_cycle_snaps = [
        s for s in history_snapshots 
        if s.get("reset_time") == reset_time_str
    ]
    
    if same_cycle_snaps:
        earliest_snap_time = parse_iso_time(same_cycle_snaps[0]["timestamp"])
        # If the gap between reset_time and earliest is known, check if it exceeds cycle_duration
        computed_cycle_hours = (reset_time - earliest_snap_time).total_seconds() / 3600.0
        # Use whichever is reasonable (at least 24h, default 120h for 5 days or 168h for 7 days)
        if computed_cycle_hours > cycle_duration_hours:
            cycle_duration_hours = computed_cycle_hours
        cycle_start = reset_time - timedelta(hours=cycle_duration_hours)
        if earliest_snap_time < cycle_start:
            cycle_start = earliest_snap_time
    else:
        cycle_start = reset_time - timedelta(hours=cycle_duration_hours)

    total_cycle_hours = max(1.0, (reset_time - cycle_start).total_seconds() / 3600.0)
    elapsed_hours = max(0.0, min(total_cycle_hours, (now_utc - cycle_start).total_seconds() / 3600.0))
    remaining_hours = max(0.001, (reset_time - now_utc).total_seconds() / 3600.0)

    remaining_fraction = float(current_snapshot.get("remaining_fraction", 1.0))
    used_fraction = 1.0 - remaining_fraction
    used_percent = used_fraction * 100.0
    remaining_percent = remaining_fraction * 100.0

    # Ideal target consumption at this exact elapsed point in the cycle:
    ideal_used_percent = (elapsed_hours / total_cycle_hours) * 100.0
    ideal_remaining_percent = 100.0 - ideal_used_percent

    # Variance delta: Positive means ahead of schedule (used more than ideal)
    variance_delta = used_percent - ideal_used_percent

    # Status determination
    if variance_delta > tolerance_percent:
        pacing_status = "OVER_BURNING"
        pacing_summary = f"Burning too fast (+{variance_delta:.1f}% ahead of ideal). Risk of running out early."
        status_color = "#ef4444" # red
    elif variance_delta < -tolerance_percent:
        pacing_status = "UNDER_BURNING"
        pacing_summary = f"Burning too slow ({variance_delta:.1f}% behind ideal). Unused credit surplus."
        status_color = "#3b82f6" # blue / warning
    else:
        pacing_status = "ON_TRACK"
        pacing_summary = f"On Track! Within ±{tolerance_percent:.0f}% corridor of ideal 100% pacing."
        status_color = "#10b981" # green

    # Target Burn Rate: How much % per hour should be consumed from now to reset to finish at 100%
    target_rate_percent_per_hour = remaining_percent / remaining_hours

    # Recent Velocity Calculation (e.g. over last 3 hours and last 24 hours)
    recent_velocity_per_hour = 0.0
    if len(same_cycle_snaps) >= 2:
        lookback_window = timedelta(hours=6)
        recent_snaps = [
            s for s in same_cycle_snaps 
            if (now_utc - parse_iso_time(s["timestamp"])) <= lookback_window
        ]
        if len(recent_snaps) >= 2:
            first_recent = recent_snaps[0]
            last_recent = recent_snaps[-1]
            dt_hours = max(0.25, (parse_iso_time(last_recent["timestamp"]) - parse_iso_time(first_recent["timestamp"])).total_seconds() / 3600.0)
            delta_used = (float(first_recent["remaining_fraction"]) - float(last_recent["remaining_fraction"])) * 100.0
            if delta_used >= 0:
                recent_velocity_per_hour = delta_used / dt_hours

    # Fallback to average cycle burn velocity if recent snaps too sparse
    if recent_velocity_per_hour <= 0 and elapsed_hours > 0.5:
        recent_velocity_per_hour = used_percent / elapsed_hours

    # Projected Exhaustion Calculation
    projected_exhaustion_time = None
    projected_hours_remaining = None
    exhaustion_delta_hours = None

    if recent_velocity_per_hour > 0.001:
        projected_hours_remaining = remaining_percent / recent_velocity_per_hour
        projected_exhaustion_time = (now_utc + timedelta(hours=projected_hours_remaining)).isoformat()
        exhaustion_delta_hours = projected_hours_remaining - remaining_hours
    else:
        # Burning 0% per hour -> won't exhaust
        projected_hours_remaining = 9999.0

    # Build Pacing Corridor Points for Charting (0 to 100% time)
    # Generate 11 sample points from cycle_start to reset_time
    corridor = []
    step_hours = total_cycle_hours / 10.0
    for i in range(11):
        pt_hours = i * step_hours
        pt_time = cycle_start + timedelta(hours=pt_hours)
        pt_ideal = (pt_hours / total_cycle_hours) * 100.0
        corridor.append({
            "hour": round(pt_hours, 1),
            "timestamp": pt_time.isoformat(),
            "ideal_percent": round(pt_ideal, 2),
            "upper_bound": min(100.0, round(pt_ideal + tolerance_percent, 2)),
            "lower_bound": max(0.0, round(pt_ideal - tolerance_percent, 2))
        })

    countdown = format_remaining_time(reset_time_str, now_utc)

    return {
        "cycle_info": {
            "cycle_start": cycle_start.isoformat(),
            "cycle_reset": reset_time.isoformat(),
            "total_cycle_hours": round(total_cycle_hours, 1),
            "elapsed_hours": round(elapsed_hours, 1),
            "remaining_hours": round(remaining_hours, 1),
            "progress_fraction": round(elapsed_hours / total_cycle_hours, 4),
            "countdown": countdown
        },
        "metrics": {
            "used_percent": round(used_percent, 2),
            "remaining_percent": round(remaining_percent, 2),
            "ideal_used_percent": round(ideal_used_percent, 2),
            "variance_delta_percent": round(variance_delta, 2),
            "target_burn_rate_percent_hr": round(target_rate_percent_per_hour, 3),
            "current_velocity_percent_hr": round(recent_velocity_per_hour, 3),
            "projected_exhaustion_time": projected_exhaustion_time,
            "projected_hours_until_exhaustion": round(projected_hours_remaining, 1) if projected_hours_remaining else None,
            "exhaustion_delta_hours": round(exhaustion_delta_hours, 1) if exhaustion_delta_hours is not None else None
        },
        "status": {
            "code": pacing_status,
            "color": status_color,
            "summary": pacing_summary
        },
        "corridor": corridor
    }
