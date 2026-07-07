param(
    [string]$MorningTime = "09:00",
    [string]$TrelloTime = "12:45",
    [string]$NightTime = "21:30"
)

# Registers two scheduled tasks for the current user:
#   "Work Digest Morning" - daily at $MorningTime + 5 minutes after logon
#   "Work Digest Night"   - daily at $NightTime
# Both call scripts\run_digest.ps1, which uses --once-per-day so overlapping
# triggers (e.g. 9 AM task + logon trigger) never double-send.
#
# If the ScheduledTasks cmdlets are blocked, falls back to schtasks.exe plus a
# Startup-folder shortcut. Never fails silently: every outcome is printed and
# the final verification step re-queries the tasks that were just registered.

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RunScript = Join-Path $ProjectRoot "scripts\run_digest.ps1"

if (!(Test-Path $RunScript)) {
    throw "Missing run script: $RunScript"
}

# The morning task uses -Mode auto: its 9:00 trigger resolves to morning, but
# its logon trigger can fire at any hour, and a login at 8 PM should produce a
# night digest, not a "morning" one. The Trello card update runs weekdays only.
$Tasks = @(
    @{ Name = "Work Digest Morning"; Mode = "auto";   Time = $MorningTime; LogonTrigger = $true;  Weekdays = $false },
    @{ Name = "Work Digest Trello";  Mode = "trello"; Time = $TrelloTime;  LogonTrigger = $false; Weekdays = $true },
    @{ Name = "Work Digest Night";   Mode = "night";  Time = $NightTime;   LogonTrigger = $false; Weekdays = $false }
)

# Remove the legacy single-task name from the first version, if present.
try {
    Unregister-ScheduledTask -TaskName "Daily Work Digest" -Confirm:$false -ErrorAction Stop
    Write-Host "Removed legacy task: Daily Work Digest"
} catch {}

$UsedFallback = $false

foreach ($Task in $Tasks) {
    $Argument = "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`" -Mode $($Task.Mode)"
    try {
        $Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Argument

        if ($Task.Weekdays) {
            $Triggers = @(New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $Task.Time)
        } else {
            $Triggers = @(New-ScheduledTaskTrigger -Daily -At $Task.Time)
        }
        if ($Task.LogonTrigger) {
            $LogonTrigger = New-ScheduledTaskTrigger -AtLogOn
            $LogonTrigger.Delay = "PT5M"
            $Triggers += $LogonTrigger
        }

        $Settings = New-ScheduledTaskSettingsSet `
            -StartWhenAvailable `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries

        $Principal = New-ScheduledTaskPrincipal `
            -UserId "$env:USERDOMAIN\$env:USERNAME" `
            -LogonType Interactive `
            -RunLevel Limited

        Register-ScheduledTask `
            -TaskName $Task.Name `
            -Action $Action `
            -Trigger $Triggers `
            -Settings $Settings `
            -Principal $Principal `
            -Description "$($Task.Mode) work digest (generate + email). --once-per-day prevents duplicates." `
            -Force | Out-Null

        Write-Host "Registered scheduled task: $($Task.Name) (daily at $($Task.Time)$(if ($Task.LogonTrigger) { ' + 5 min after logon' }))"
    } catch {
        Write-Warning "ScheduledTask cmdlet registration failed for $($Task.Name): $($_.Exception.Message)"
        Write-Host "Falling back to schtasks.exe for $($Task.Name)."
        $UsedFallback = $true
        $TaskRun = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \`"$RunScript\`" -Mode $($Task.Mode)"
        if ($Task.Weekdays) {
            & schtasks.exe /Create /TN $Task.Name /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST $Task.Time /TR $TaskRun /F | Out-Host
        } else {
            & schtasks.exe /Create /TN $Task.Name /SC DAILY /ST $Task.Time /TR $TaskRun /F | Out-Host
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Error "DIAGNOSTIC: Could not register '$($Task.Name)' with either ScheduledTasks cmdlets or schtasks.exe. Scheduler automation is NOT installed for this task. Run scripts\install_windows_startup_shortcut.ps1 as a fallback, or run 'digest send --mode $($Task.Mode) --once-per-day' manually."
        }
    }
}

if ($UsedFallback) {
    Write-Host "Installing Startup-folder shortcut as the logon fallback (schtasks.exe path has no logon trigger)."
    & (Join-Path $ProjectRoot "scripts\install_windows_startup_shortcut.ps1")
}

Write-Host ""
Write-Host "Verification:"
$AllOk = $true
foreach ($Task in $Tasks) {
    & schtasks.exe /Query /TN $Task.Name | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  INSTALLED: $($Task.Name)"
    } else {
        Write-Host "  MISSING:   $($Task.Name)  <-- scheduler setup FAILED for this task"
        $AllOk = $false
    }
}
if ($AllOk) {
    Write-Host ""
    Write-Host "Done. No manual daily action is needed. Confirm anytime with: digest doctor"
} else {
    Write-Error "One or more tasks failed to install. See diagnostics above."
    exit 1
}
