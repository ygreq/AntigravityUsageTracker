#!/usr/bin/env python3
"""
Antigravity Usage Tracker - Family Member Client Agent (Python)
Queries local `agy` usage and reports telemetry to your central home hub.

Usage:
  python report_usage.py --server http://192.168.1.100:8778 --user alex --name "Alex"
  python report_usage.py --server http://192.168.1.100:8778 --loop --interval 15
"""

import sys
import os
import json
import time
import shutil
import argparse
import subprocess
import urllib.request
import urllib.error
from datetime import datetime

def get_agy_usage(timeout: int = 30):
    exe = shutil.which("agy") or "agy"
    cmd = [exe, "-p", "/usage", "--output-format", "json"]

    env = os.environ.copy()
    env["CI"] = "1"
    env["BROWSER"] = "none"
    env["ANTIGRAVITY_BROWSER"] = "none"

    extra_kwargs = {}
    if sys.platform == "win32":
        extra_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        extra_kwargs["startupinfo"] = si

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        shell=False,
        env=env,
        **extra_kwargs
    )
    if proc.returncode != 0:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] agy error ({proc.returncode}): {proc.stderr.strip()}", file=sys.stderr)
        return None

    raw_str = proc.stdout.strip()
    if not raw_str:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] agy returned empty output.", file=sys.stderr)
        return None

    return json.loads(raw_str)

def send_report(server_url: str, user_id: str, display_name: str, secret: str = ""):
    data = get_agy_usage()
    if not data:
        return False

    payload = {
        "user_id": user_id,
        "display_name": display_name,
        "secret": secret,
        "data": data
    }
    encoded = json.dumps(payload).encode("utf-8")
    endpoint = f"{server_url.rstrip('/')}/api/family/report"

    req = urllib.request.Request(
        endpoint,
        data=encoded,
        headers={"Content-Type": "application/json", "User-Agent": "AgyFamilyClient/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            res = json.loads(body)
            count = res.get("recorded_snapshots", 0)
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Telemetry reported successfully for {display_name} ({user_id}): {count} snapshot(s) recorded.")
            return True
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] HTTP Error {e.code}: {err_msg}", file=sys.stderr)
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Connection failed to {server_url}: {e}", file=sys.stderr)
    return False

def main():
    parser = argparse.ArgumentParser(description="Report local Antigravity usage to family hub.")
    parser.add_argument("--server", default="http://localhost:8778", help="Hub server URL (default: http://localhost:8778)")
    parser.add_argument("--user", default=os.getenv("USERNAME", "member"), help="Unique user identifier")
    parser.add_argument("--name", default="", help="Friendly display name (defaults to user)")
    parser.add_argument("--secret", default="", help="Optional authentication secret")
    parser.add_argument("--loop", action="store_true", help="Run continuously in background")
    parser.add_argument("--interval", type=int, default=15, help="Interval in minutes when looping (default: 15)")

    args = parser.parse_args()
    dname = args.name or args.user.capitalize()

    print(f"Target Server: {args.server}")
    print(f"Reporting as:  {dname} ({args.user})")

    if args.loop:
        print(f"Starting loop mode (polling every {args.interval}m). Press Ctrl+C to stop.\n")
        while True:
            send_report(args.server, args.user, dname, args.secret)
            time.sleep(max(1, args.interval) * 60)
    else:
        send_report(args.server, args.user, dname, args.secret)

if __name__ == "__main__":
    main()
