import json
import subprocess
import sys
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from pathlib import Path

from .database import record_snapshots_batch

logger = logging.getLogger("agy_tracker.collector")

def parse_usage_output(data: Dict[str, Any], timestamp_iso: Optional[str] = None) -> List[Dict[str, Any]]:
    if not timestamp_iso:
        timestamp_iso = datetime.now(timezone.utc).isoformat()

    snapshots = []
    groups = data.get("command", {}).get("data", {}).get("groups", [])
    raw_str = json.dumps(data)

    for group in groups:
        gname = group.get("name", "Unknown Group")
        buckets = group.get("buckets", [])
        for b in buckets:
            snapshots.append({
                "timestamp": timestamp_iso,
                "group_name": gname,
                "bucket_id": b.get("id", f"{gname}-{b.get('window', 'bucket')}"),
                "bucket_name": b.get("name", "Limit"),
                "window_type": b.get("window", "custom"),
                "remaining_fraction": float(b.get("remaining_fraction", 1.0)),
                "reset_time": b.get("reset_time", ""),
                "description": b.get("description", ""),
                "raw_payload": raw_str
            })

    return snapshots

def poll_agy_usage(agy_executable: str = "agy", timeout_seconds: int = 30) -> Optional[Dict[str, Any]]:
    """Runs 'agy -p "/usage" --output-format json' and parses JSON output."""
    try:
        cmd = [agy_executable, "-p", "/usage", "--output-format", "json"]
        extra_kwargs = {}
        if sys.platform == "win32":
            extra_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0  # SW_HIDE
            extra_kwargs["startupinfo"] = si

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            encoding="utf-8",
            shell=True,
            **extra_kwargs
        )
        if proc.returncode != 0:
            logger.error(f"agy command failed with exit code {proc.returncode}: {proc.stderr}")
            return None

        # Clean potential BOM or leading/trailing whitespace
        output = proc.stdout.strip()
        if not output:
            logger.error("agy command returned empty output")
            return None

        data = json.loads(output)
        return data
    except subprocess.TimeoutExpired:
        logger.error(f"agy command timed out after {timeout_seconds}s")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON from agy output: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error executing agy: {e}")
        return None

def collect_and_store(db_path: str, agy_executable: str = "agy") -> List[Dict[str, Any]]:
    logger.info("Polling agy usage...")
    raw_data = poll_agy_usage(agy_executable)
    if not raw_data:
        return []

    snapshots = parse_usage_output(raw_data)
    if snapshots:
        record_snapshots_batch(db_path, snapshots)
        logger.info(f"Recorded {len(snapshots)} usage snapshot(s) to {db_path}")
    return snapshots
