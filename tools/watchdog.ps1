# Keeps the agent running on a laptop, where there is no systemd to do it.
#
#   powershell -ExecutionPolicy Bypass -File tools\watchdog.ps1
#
# Checks every 60s that the process named in state/scheduler.lock is alive.
# If it is not, clears the stale lock, rebuilds the release manifest (the gate
# pins an exact commit, so a restart after any commit needs a fresh one), and
# starts the agent again. Everything it does is written to state/watchdog.log.
#
# This is the mitigation for not deploying to a VPS. It handles a crashed or
# killed process. It cannot help if the machine sleeps, hibernates or reboots
# -- for that, set Power > Screen and sleep > Never while plugged in.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$lockPath = Join-Path $root "state\scheduler.lock"
$logPath  = Join-Path $root "state\watchdog.log"
$agentLog = Join-Path $root "state\agent.log"

function Write-Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $logPath -Value $line -Encoding utf8
    Write-Host $line
}

function Test-AgentAlive {
    if (-not (Test-Path $lockPath)) { return $false }
    try {
        $pidValue = (Get-Content $lockPath -Raw | ConvertFrom-Json).pid
    } catch {
        return $false          # unreadable lock is treated as dead, not alive
    }
    if (-not $pidValue) { return $false }
    return [bool](Get-Process -Id $pidValue -ErrorAction SilentlyContinue)
}

function Start-Agent {
    Write-Log "agent not running; restarting"

    # A stale lock from a killed process blocks startup with an ownership
    # refusal, which looks exactly like a second scheduler racing us.
    if (Test-Path $lockPath) {
        Remove-Item $lockPath -Force -ErrorAction SilentlyContinue
        Write-Log "cleared stale lock"
    }

    # The release gate pins an exact commit. Any commit since the last manifest
    # makes startup refuse with checkout drift, so rebuild before every start
    # rather than only on the first failure.
    $manifest = & python tools\build_release_manifest.py 2>&1 | Out-String
    $sha = [regex]::Match($manifest, "GLASSBOX_APPROVED_COMMIT_SHA=([0-9a-f]{40})").Groups[1].Value
    if (-not $sha) {
        Write-Log "REFUSING to start: manifest did not verify. Evidence may have expired (24h) -- recapture the proofs."
        Write-Log ($manifest -split "`n" | Select-Object -Last 6) -join " | "
        return
    }

    $env:GLASSBOX_RELEASE_GATE = "1"
    $env:GLASSBOX_APPROVED_COMMIT_SHA = $sha
    Start-Process -FilePath "python" -ArgumentList "main.py" `
        -RedirectStandardOutput $agentLog -RedirectStandardError "$agentLog.err" `
        -WindowStyle Hidden
    Write-Log "started against commit $($sha.Substring(0,12))"
}

Write-Log "watchdog up; checking every 60s"
while ($true) {
    try {
        if (-not (Test-AgentAlive)) { Start-Agent }
    } catch {
        Write-Log "watchdog error: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 60
}
