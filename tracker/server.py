import os
import json
import csv
import io
import re
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, BackgroundTasks, Query, Response, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
import uvicorn

from .database import (
    init_db, get_latest_snapshots, get_bucket_history, get_usage_logs,
    get_active_users, record_snapshots_batch
)
from .collector import collect_and_store, parse_usage_output
from .pacing_engine import calculate_pacing, format_remaining_time
from .aggregator import (
    get_multi_period_data, get_comparative_cycles, get_today_vs_yesterday,
    get_family_usage_breakdown
)
from .seed_data import seed_sample_history

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("agy_tracker.server")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
WEB_DIR = BASE_DIR / "web"
DB_PATH = str(BASE_DIR / "usage_tracker.db")

# Load and persist config
def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading config: {e}")
    return {
        "poll_interval_minutes": 15,
        "server_host": "0.0.0.0",
        "server_port": 8778,
        "family_secret": "",
        "default_cycle_duration_hours": 120,
        "pacing_tolerance_percent": 5.0,
        "primary_bucket": "gemini-weekly"
    }

def save_config(cfg: Dict[str, Any]):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving config: {e}")

CURRENT_CONFIG = load_config()

app = FastAPI(title="Antigravity Usage & 5-Day Pacing Tracker", version="1.1.0")

# Background Poller Task with Dynamic Timer
polling_task: Optional[asyncio.Task] = None
next_poll_time: Optional[datetime] = None
last_poll_time: Optional[datetime] = None

async def periodic_poller():
    global next_poll_time, last_poll_time
    logger.info("Background poller initialized.")
    while True:
        try:
            interval_min = max(1, CURRENT_CONFIG.get("poll_interval_minutes", 15))
            next_poll_time = datetime.now(timezone.utc) + timedelta(minutes=interval_min)
            logger.info(f"Next poll scheduled for {next_poll_time.isoformat()} (in {interval_min}m)")
            
            # Sleep in short increments so changes to poll_interval_minutes or manual triggers take effect
            while datetime.now(timezone.utc) < next_poll_time:
                await asyncio.sleep(1)
                
            logger.info("Timer triggered: polling agy usage...")
            last_poll_time = datetime.now(timezone.utc)
            collect_and_store(DB_PATH)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic poller: {e}")
            await asyncio.sleep(10)

@app.on_event("startup")
async def on_startup():
    global polling_task, last_poll_time
    init_db(DB_PATH)
    seed_sample_history(DB_PATH)
    try:
        last_poll_time = datetime.now(timezone.utc)
        collect_and_store(DB_PATH)
    except Exception as e:
        logger.warning(f"Initial poll warning: {e}")
    polling_task = asyncio.create_task(periodic_poller())

@app.on_event("shutdown")
async def on_shutdown():
    global polling_task
    if polling_task:
        polling_task.cancel()

# --- API Endpoints ---

@app.get("/api/users")
async def list_users():
    """Lists all detected family members / devices recorded in the database."""
    users = get_active_users(DB_PATH)
    return {"users": users}

@app.get("/api/family/summary")
async def get_family_summary():
    """Returns comparative usage metrics and quota breakdown across all family members."""
    return get_family_usage_breakdown(DB_PATH)

@app.post("/api/family/report")
async def receive_family_report(payload: Dict[str, Any] = Body(...)):
    """
    Ingests usage telemetry from a remote family member's machine.
    Payload:
    {
      "user_id": "alex",
      "display_name": "Alex",
      "data": { ...agy output json... },
      "secret": "..." (optional)
    }
    """
    configured_secret = str(CURRENT_CONFIG.get("family_secret", "")).strip()
    if configured_secret:
        provided_secret = str(payload.get("secret", "")).strip()
        if provided_secret != configured_secret:
            return JSONResponse(status_code=401, content={"error": "Invalid or missing family authentication secret"})

    raw_user_id = str(payload.get("user_id", "")).strip().lower()
    if not raw_user_id:
        return JSONResponse(status_code=400, content={"error": "Missing required field: user_id"})

    clean_user_id = re.sub(r'[^a-z0-9_\-]', '', raw_user_id)
    if not clean_user_id:
        return JSONResponse(status_code=400, content={"error": "Invalid user_id format. Use letters, numbers, dashes, underscores."})

    display_name = payload.get("display_name") or clean_user_id.capitalize()
    usage_data = payload.get("data")
    if not usage_data or not isinstance(usage_data, dict):
        return JSONResponse(status_code=400, content={"error": "Missing or invalid 'data' object (must be raw agy usage JSON)"})

    snapshots = parse_usage_output(usage_data)
    if not snapshots:
        return JSONResponse(status_code=400, content={"error": "No valid buckets/groups found in provided usage telemetry data"})

    record_snapshots_batch(DB_PATH, snapshots, user_id=clean_user_id, display_name=display_name)
    logger.info(f"Ingested telemetry for family member '{clean_user_id}' ({display_name}): {len(snapshots)} snapshots recorded")

    return {
        "status": "success",
        "user_id": clean_user_id,
        "display_name": display_name,
        "recorded_snapshots": len(snapshots),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/api/status")
async def get_status(user_id: Optional[str] = Query("default")):
    latest = get_latest_snapshots(DB_PATH, user_id=user_id)
    now_utc = datetime.now(timezone.utc)

    # Attach countdown info to each snapshot
    for s in latest:
        s["countdown"] = format_remaining_time(s.get("reset_time", ""), now_utc)

    gemini_weekly = next((s for s in latest if s["bucket_id"] == "gemini-weekly"), None)
    claude_weekly = next((s for s in latest if s["bucket_id"] == "3p-weekly"), None)

    gemini_pacing = None
    if gemini_weekly:
        history = get_bucket_history(DB_PATH, "gemini-weekly", limit=1000, user_id=user_id)
        gemini_pacing = calculate_pacing(gemini_weekly, history)

    claude_pacing = None
    if claude_weekly:
        c_history = get_bucket_history(DB_PATH, "3p-weekly", limit=1000, user_id=user_id)
        claude_pacing = calculate_pacing(claude_weekly, c_history)

    seconds_until_next = 0
    if next_poll_time:
        seconds_until_next = max(0, int((next_poll_time - now_utc).total_seconds()))

    active_users = get_active_users(DB_PATH)

    return {
        "current_user_id": user_id,
        "users": active_users,
        "latest_snapshots": latest,
        "primary_pacing": gemini_pacing,
        "claude_gpt_pacing": claude_pacing,
        "poller": {
            "poll_interval_minutes": CURRENT_CONFIG.get("poll_interval_minutes", 15),
            "next_poll_iso": next_poll_time.isoformat() if next_poll_time else None,
            "seconds_until_next_poll": seconds_until_next,
            "last_poll_iso": last_poll_time.isoformat() if last_poll_time else None
        }
    }

@app.get("/api/settings")
async def get_settings():
    return CURRENT_CONFIG

@app.post("/api/settings")
async def update_settings(payload: Dict[str, Any] = Body(...)):
    global CURRENT_CONFIG, next_poll_time
    if "poll_interval_minutes" in payload:
        new_val = int(payload["poll_interval_minutes"])
        if new_val < 1:
            return JSONResponse(status_code=400, content={"error": "Interval must be at least 1 minute"})
        CURRENT_CONFIG["poll_interval_minutes"] = new_val
        next_poll_time = datetime.now(timezone.utc) + timedelta(minutes=new_val)

    for k, v in payload.items():
        CURRENT_CONFIG[k] = v

    save_config(CURRENT_CONFIG)
    return {"status": "success", "config": CURRENT_CONFIG}

@app.get("/api/pacing")
async def get_pacing_details(
    bucket_id: str = Query("gemini-weekly"),
    user_id: Optional[str] = Query("default"),
    cycle_hours: float = Query(120.0),
    tolerance: float = Query(5.0)
):
    latest = get_latest_snapshots(DB_PATH, user_id=user_id)
    target_snap = next((s for s in latest if s["bucket_id"] == bucket_id), None)
    if not target_snap:
        return JSONResponse(status_code=404, content={"error": f"Bucket {bucket_id} for user {user_id} not found"})

    history = get_bucket_history(DB_PATH, bucket_id, limit=1500, user_id=user_id)
    pacing_data = calculate_pacing(
        target_snap,
        history,
        cycle_duration_hours=cycle_hours,
        tolerance_percent=tolerance
    )
    return pacing_data

@app.get("/api/multi-period")
async def get_multi_period(
    granularity: str = Query("1h", pattern="^(1h|5h|1d|1w)$"),
    bucket_id: str = Query("gemini-weekly"),
    user_id: Optional[str] = Query("default")
):
    data = get_multi_period_data(DB_PATH, bucket_id, granularity, user_id=user_id)
    return data

@app.get("/api/comparative")
async def get_comparative(
    bucket_id: str = Query("gemini-weekly"),
    user_id: Optional[str] = Query("default")
):
    cycles = get_comparative_cycles(DB_PATH, bucket_id, user_id=user_id)
    return cycles

@app.get("/api/day-comparison")
async def get_day_comp(
    bucket_id: str = Query("gemini-weekly"),
    user_id: Optional[str] = Query("default")
):
    data = get_today_vs_yesterday(DB_PATH, bucket_id, user_id=user_id)
    return data

@app.get("/api/logs")
async def get_logs(
    bucket_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    return get_usage_logs(
        DB_PATH,
        bucket_id=bucket_id,
        user_id=user_id,
        search=search,
        limit=limit,
        offset=offset
    )

@app.get("/api/logs/export.csv")
async def export_logs_csv(
    bucket_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None)
):
    res = get_usage_logs(DB_PATH, bucket_id=bucket_id, user_id=user_id, limit=10000, offset=0)
    rows = res.get("logs", [])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "User ID", "Display Name", "Timestamp (UTC)", "Group", "Bucket ID", "Bucket Name",
        "Window", "Remaining %", "Used %", "Delta Used %", "Reset Time (UTC)", "Description"
    ])
    for r in rows:
        rem_pct = round(float(r["remaining_fraction"]) * 100.0, 2)
        used_pct = round(float(r["used_fraction"]) * 100.0, 2)
        delta_pct = round(float(r["delta_used"]) * 100.0, 4)
        writer.writerow([
            r["id"], r.get("user_id", "default"), r.get("display_name", "You"),
            r["timestamp"], r["group_name"], r["bucket_id"], r["bucket_name"],
            r["window_type"], rem_pct, used_pct, delta_pct, r["reset_time"], r["description"]
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=agy_usage_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"}
    )

@app.post("/api/poll")
async def trigger_manual_poll(background_tasks: BackgroundTasks):
    global last_poll_time, next_poll_time
    def run_poll():
        global last_poll_time, next_poll_time
        last_poll_time = datetime.now(timezone.utc)
        collect_and_store(DB_PATH)
        interval_min = CURRENT_CONFIG.get("poll_interval_minutes", 15)
        next_poll_time = datetime.now(timezone.utc) + timedelta(minutes=interval_min)

    background_tasks.add_task(run_poll)
    return {"status": "polling_started", "message": "Manual poll initiated"}

# Mount static frontend
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

@app.get("/")
async def serve_dashboard():
    return FileResponse(str(WEB_DIR / "index.html"))

def run_server(host: Optional[str] = None, port: Optional[int] = None):
    h = host or CURRENT_CONFIG.get("server_host", "0.0.0.0")
    p = port or CURRENT_CONFIG.get("server_port", 8778)
    uvicorn.run(app, host=h, port=p)

if __name__ == "__main__":
    run_server()

