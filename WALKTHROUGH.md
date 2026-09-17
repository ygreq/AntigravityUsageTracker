# Antigravity (`agy`) Quota Verifier & 5-Day Credit Pacing Tracker

## Overview
A dedicated background telemetry monitor, analytical logger, and visual dashboard application for Google Antigravity (`agy`).

The application automatically verifies `agy` usage, maintains an audit log to monitor trends now and into the future, renders comparative multi-period charts (**1 hour, 5 hours, 1 day, 1 week**), and calculates your exact burn-rate pacing against your quota reset window for both **Gemini Models** and **Claude & GPT Models**.

---

## What Was Implemented

### 1. Dedicated Stacked "Scheduled Reset" & "Resets in" Layout
All top pill badges (`⏳ Resets in:`) have been removed from the quota cards for a cleaner look. The countdown is now formatted vertically underneath **Scheduled Reset** with both human-friendly days/hours and exact total hours and minutes:
- **Gemini Weekly Quota**:
  - `Scheduled Reset:` `Sep 17, 10:23 PM`
  - `Resets in:`
  - `3 days, 7 hours`
  - `79h 3m`
- **Gemini 5-Hour Limit**:
  - `Scheduled Reset:` `Sep 14, 7:43 PM`
  - `Resets in:`
  - `0 days, 4 hours, 24 mins`
  - `4h 24m`
- **Claude/GPT Weekly Quota**:
  - `Scheduled Reset:` `Sep 21, 3:19 PM`
  - `Resets in:`
  - `6 days, 23 hours`
  - `167h 59m`
- **Claude/GPT 5-Hour Limit**:
  - `Scheduled Reset:` `Sep 14, 8:19 PM`
  - `Resets in:`
  - `0 days, 4 hours, 59 mins`
  - `4h 59m`

### 2. Streamlined Visuals (Confusing Diagonal Chart Removed)
- The abstract diagonal "Burndown & Pacing Corridor" chart has been removed from the UI.
- All pacing variance and burn rate targets are surfaced directly through high-visibility KPI cards (`Target Pacing: +46.2% vs ideal`, `Target Burn Rate: 0.25% / hour`).
- The dashboard focuses on practical, intuitive visuals:
  - **Multi-Period Aggregations** (1h, 5h, 1d, 1w)
  - **Cycle-over-Cycle Comparative Overlays** (0–120h)
  - **Today vs. Yesterday Hourly Profiles**
  - **Historical Usage Logger & Audit Trail**

### 3. Telemetry Collector & Configurable Poller
- **Native Data Ingestion**: Executes `agy -p "/usage" --output-format json` directly, pulling multi-day and 5-hour rolling quotas with zero API token consumption.
- **Configurable Polling Timer**: Selectable directly from the UI header (**1m, 5m, 10m, 15m, 30m, 60m**). Changes take effect immediately without restarting.
- **Live Countdown Badge**: Displays a real-time countdown timer in the header indicating seconds until the next check.
- **Manual "Check Now"**: One-click manual poll trigger with instant dashboard refresh.

### 4. Historical Usage Logger & Audit Trail
- **Persistent Ledger**: Stores every snapshot with exact timestamp, model group, bucket, remaining %, delta used, and reset target.
- **Search & Filter**: Instant search box and bucket dropdown (Gemini Weekly, Gemini 5h, Claude/GPT Weekly, Claude/GPT 5h).
- **Exporting**: One-click **Export CSV** for offline spreadsheets, analysis, and auditing.

---

## File Structure

```
AntigravityUsageTracker/
├── config.json                     # Polling timer (15m), server settings, bucket definitions
├── run_tracker.bat                 # One-click launcher (starts server & opens browser)
├── README.md                       # Complete documentation & usage guide
├── WALKTHROUGH.md                  # Project walkthrough & validation report
├── usage_tracker.db                # SQLite WAL database storing all telemetry snapshots
├── tracker/
│   ├── __init__.py
│   ├── collector.py                # Executes agy --output-format json & records deltas
│   ├── database.py                 # SQLite schema, queries, audit log pagination & CSV export
│   ├── pacing_engine.py            # Linear pacing math, variance corridor, burn-rate metrics
│   ├── aggregator.py               # 1h, 5h, 1d, 1w multi-period rollups & cycle normalizer
│   ├── seed_data.py                # Populates historical comparison cycles for instant graphs
│   ├── server.py                   # FastAPI application with REST API & dynamic scheduler
│   └── cli.py                      # Terminal dashboard with rich tables & pacing panels
└── web/
    ├── index.html                  # Clean dark-mode dashboard (Tailwind + ECharts)
    └── app.js                      # Multi-period chart rendering, countdown ticker, and audit logger logic
```

---

## How to Run

### Option A: Launch Web Dashboard
Double-click `run_tracker.bat` or open your browser:
👉 **`http://127.0.0.1:8778`**

### Option B: Quick CLI Check
Check status anytime directly in your terminal:
```powershell
python -m tracker.cli status
```
Or force an immediate poll:
```powershell
python -m tracker.cli poll
```
