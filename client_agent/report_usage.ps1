<#
.SYNOPSIS
    Antigravity Usage Tracker - Family Member Client Agent (PowerShell)
    Runs silently, captures local 'agy' quota telemetry, and submits it to your central home hub.

.PARAMETER ServerUrl
    The URL of your family Antigravity Usage Tracker server (e.g. http://192.168.1.100:8778 or http://localhost:8778).

.PARAMETER UserId
    Unique identifier for this family member or device (e.g. "alex", "maria", "laptop-work"). Defaults to current Windows username.

.PARAMETER DisplayName
    Friendly display name shown on the dashboard (e.g. "Alex", "Maria"). Defaults to Windows username.

.PARAMETER Secret
    Optional shared authentication secret if configured in server config.json.

.PARAMETER Loop
    If switch is passed, continuously reports every $IntervalMinutes.
#>

[CmdletBinding()]
param(
    [Parameter(Position=0)]
    [string]$ServerUrl = "http://localhost:8778",

    [Parameter(Position=1)]
    [string]$UserId = $env:USERNAME,

    [Parameter(Position=2)]
    [string]$DisplayName = $env:USERNAME,

    [Parameter(Position=3)]
    [string]$Secret = "",

    [switch]$Loop,

    [int]$IntervalMinutes = 15
)

$ErrorActionPreference = "Stop"

function Send-AgyTelemetry {
    param(
        [string]$HubUrl,
        [string]$Uid,
        [string]$DName,
        [string]$AuthSecret
    )

    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Querying local agy usage telemetry..." -ForegroundColor Cyan

    # Environment variables to suppress browser popups or prompts
    $env:CI = "1"
    $env:BROWSER = "none"
    $env:ANTIGRAVITY_BROWSER = "none"

    # Find agy executable
    $agyCmd = Get-Command "agy" -ErrorAction SilentlyContinue
    if (-not $agyCmd) {
        Write-Error "Error: 'agy' command was not found on PATH. Make sure Antigravity is installed."
        return
    }

    try {
        # Execute agy headlessly
        $pinfo = New-Object System.Diagnostics.ProcessStartInfo
        $pinfo.FileName = $agyCmd.Source
        $pinfo.Arguments = "-p `"/usage`" --output-format json"
        $pinfo.RedirectStandardOutput = $true
        $pinfo.RedirectStandardError = $true
        $pinfo.UseShellExecute = $false
        $pinfo.CreateNoWindow = $true

        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $pinfo
        $null = $process.Start()
        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        $process.WaitForExit()

        if ($process.ExitCode -ne 0) {
            Write-Warning "agy command returned exit code $($process.ExitCode): $stderr"
            return
        }

        $rawJson = $stdout.Trim()
        if (-not $rawJson) {
            Write-Warning "agy returned empty output."
            return
        }

        $usageObj = $rawJson | ConvertFrom-Json

        # Prepare HTTP payload
        $payload = @{
            user_id      = $Uid
            display_name = $DName
            secret       = $AuthSecret
            data         = $usageObj
        } | ConvertTo-Json -Depth 10

        $endpoint = "$($HubUrl.TrimEnd('/'))/api/family/report"
        $response = Invoke-RestMethod -Uri $endpoint -Method Post -Body $payload -ContentType "application/json" -TimeoutSec 15

        Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Successfully reported telemetry for '$DName' ($Uid): $($response.recorded_snapshots) snapshots recorded." -ForegroundColor Green
    }
    catch {
        Write-Warning "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Telemetry report failed: $_"
    }
}

# Main Execution
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host " Antigravity Usage Tracker - Family Member Client Agent" -ForegroundColor White
Write-Host " Target Server: $ServerUrl" -ForegroundColor Gray
Write-Host " Reporting as:  $DisplayName (ID: $UserId)" -ForegroundColor Gray
Write-Host "========================================================" -ForegroundColor Cyan

if ($Loop) {
    Write-Host "Running in continuous background mode (polling every $IntervalMinutes minutes). Press Ctrl+C to stop.`n" -ForegroundColor Yellow
    while ($true) {
        Send-AgyTelemetry -HubUrl $ServerUrl -Uid $UserId -DName $DisplayName -AuthSecret $Secret
        Start-Sleep -Seconds ($IntervalMinutes * 60)
    }
} else {
    Send-AgyTelemetry -HubUrl $ServerUrl -Uid $UserId -DName $DisplayName -AuthSecret $Secret
}
