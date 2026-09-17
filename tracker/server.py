import os
import json
import csv
import io
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
    init_db, get_latest_snapshots, get_bucket_history, get_usage_logs
)
from .collector import collect_and_store
from .pacing_engine import calculate_pacing
from .aggregator import (
    get_multi_period_data, get_comparative_cycles, get_today_vs_yesterday
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
        "server_host": "127.0.0.1",
        "server_port": 8778,
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

app = FastAPI(title="Antigravity Usage & 5-Day Pacing Tracker", version="1.0.0")

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

from .pacing_engine import calculate_pacing, format_remaining_time

# --- API Endpoints ---

@app.get("/api/status")
async def get_status():
    latest = get_latest_snapshots(DB_PATH)
    now_utc = datetime.now(timezone.utc)

    # Attach countdown info to each snapshot
    for s in latest:
        s["countdown"] = format_remaining_time(s.get("reset_time", ""), now_utc)

    gemini_weekly = next((s for s in latest if s["bucket_id"] == "gemini-weekly"), None)
    claude_weekly = next((s for s in latest if s["bucket_id"] == "3p-weekly"), None)

    gemini_pacing = None
    if gemini_weekly:
        history = get_bucket_history(DB_PATH, "gemini-weekly", limit=1000)
        gemini_pacing = calculate_pacing(gemini_weekly, history)

    claude_pacing = None
    if claude_weekly:
        c_history = get_bucket_history(DB_PATH, "3p-weekly", limit=1000)
        claude_pacing = calculate_pacing(claude_weekly, c_history)

    seconds_until_next = 0
    if next_poll_time:
        seconds_until_next = max(0, int((next_poll_time - now_utc).total_seconds()))

    return {
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
        # Immediately reschedule next poll
        next_poll_time = datetime.now(timezone.utc) + timedelta(minutes=new_val)

    for k, v in payload.items():
        CURRENT_CONFIG[k] = v

    save_config(CURRENT_CONFIG)
    return {"status": "success", "config": CURRENT_CONFIG}

@app.post("/api/poll")
async def trigger_manual_poll():
    global last_poll_time, next_poll_time
    last_poll_time = datetime.now(timezone.utc)
    snaps = collect_and_store(DB_PATH)
    poll_mins = CURRENT_CONFIG.get("poll_interval_minutes", 15)
    next_poll_time = last_poll_time + timedelta(minutes=poll_mins)
    return {"status": "success", "snapshots_count": len(snaps)}

@app.get("/api/pacing")
async def get_pacing_details(
    bucket_id: str = Query("gemini-weekly"),
    cycle_hours: float = Query(120.0),
    tolerance: float = Query(5.0)
):
    latest = get_latest_snapshots(DB_PATH)
    target_snap = next((s for s in latest if s["bucket_id"] == bucket_id), None)
    if not target_snap:
        return JSONResponse(status_code=404, content={"error": f"Bucket {bucket_id} not found"})

    history = get_bucket_history(DB_PATH, bucket_id, limit=1500)
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
    bucket_id: str = Query("gemini-weekly")
):
    data = get_multi_period_data(DB_PATH, bucket_id, granularity)
    return data

@app.get("/api/comparative")
async def get_comparative(bucket_id: str = Query("gemini-weekly")):
    cycles = get_comparative_cycles(DB_PATH, bucket_id)
    return cycles

@app.get("/api/day-comparison")
async def get_day_comp(bucket_id: str = Query("gemini-weekly")):
    data = get_today_vs_yesterday(DB_PATH, bucket_id)
    return data

@app.get("/api/logs")
async def get_logs(
    bucket_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    return get_usage_logs(DB_PATH, bucket_id=bucket_id, search=search, limit=limit, offset=offset)

@app.get("/api/logs/export.csv")
async def export_logs_csv(bucket_id: Optional[str] = Query(None)):
    res = get_usage_logs(DB_PATH, bucket_id=bucket_id, limit=10000, offset=0)
    rows = res.get("logs", [])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Timestamp (UTC)", "Group", "Bucket ID", "Bucket Name",
        "Window", "Remaining %", "Used %", "Delta Used %", "Reset Time (UTC)", "Description"
    ])
    for r in rows:
        rem_pct = round(float(r["remaining_fraction"]) * 100.0, 2)
        used_pct = round(float(r["used_fraction"]) * 100.0, 2)
        delta_pct = round(float(r["delta_used"]) * 100.0, 4)
        writer.writerow([
            r["id"], r["timestamp"], r["group_name"], r["bucket_id"], r["bucket_name"],
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
        # Reset timer
        interval_min = CURRENT_CONFIG.get("poll_interval_minutes", 15)
        next_poll_time = datetime.now(timezone.utc) + timedelta(minutes=interval_min)

    background_tasks.add_task(run_poll)
    return {"status": "polling_started", "message": "Manual poll initiated"}

# Mount static frontend
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

@app.get("/")
async def serve_dashboard():
    return FileResponse(str(WEB_DIR / "index.html"))

def run_server(host: str = "127.0.0.1", port: int = 8778):
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    run_server()
