# 👨‍👩‍👧‍👦 Family Member Client Agent Setup

Send local Antigravity (`agy`) quota and burn-rate telemetry from family members' or secondary PCs directly to your central **Antigravity Usage Tracker** hub.

---

## 🚀 Quick Setup (Under 1 Minute)

### Option A: Windows PowerShell (Zero Install)

On the family member's PC, open PowerShell and run:

```powershell
# 1. Test single report:
.\report_usage.ps1 -ServerUrl "http://<YOUR_HUB_IP>:8778" -UserId "alex" -DisplayName "Alex"

# 2. Run continuously every 15 minutes:
.\report_usage.ps1 -ServerUrl "http://<YOUR_HUB_IP>:8778" -UserId "alex" -DisplayName "Alex" -Loop -IntervalMinutes 15
```

> Replace `<YOUR_HUB_IP>` with your workstation's local IP address (e.g., `192.168.1.100` or Tailscale IP).

---

### Option B: Python (Cross-Platform - Windows, macOS, Linux)

```bash
# 1. Single report:
python report_usage.py --server "http://<YOUR_HUB_IP>:8778" --user alex --name "Alex"

# 2. Continuous background loop:
python report_usage.py --server "http://<YOUR_HUB_IP>:8778" --user alex --name "Alex" --loop --interval 15
```

---

## ⏰ Schedule Automatic Daily/Hourly Reporting (Windows Task Scheduler)

To have the family PC report automatically in the background without needing a terminal open:

1. Open PowerShell on the family PC as Administrator.
2. Run this command to register a background scheduled task:

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PWD\report_usage.ps1`" -ServerUrl `"http://<YOUR_HUB_IP>:8778`" -UserId `"$env:USERNAME`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName "AntigravityFamilyTracker" -Action $action -Trigger $trigger -Description "Reports agy quota telemetry to home hub"
```

---

## 🔒 Optional Shared Secret Authentication

If you set `"family_secret": "mySecretPassword123"` in `config.json` on the main hub, pass `-Secret "mySecretPassword123"` in the client script:

```powershell
.\report_usage.ps1 -ServerUrl "http://<YOUR_HUB_IP>:8778" -UserId "alex" -Secret "mySecretPassword123"
```
