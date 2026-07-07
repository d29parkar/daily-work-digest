# Fallback automation for machines where Task Scheduler cannot be used:
# creates a Startup-folder shortcut that runs the morning digest at logon.
# Safe to combine with the scheduled tasks: --once-per-day prevents duplicates.

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RunScript = Join-Path $ProjectRoot "scripts\run_digest.ps1"
$StartupFolder = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $StartupFolder "Daily Work Digest.lnk"

if (!(Test-Path $RunScript)) {
    throw "Missing run script: $RunScript"
}

try {
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = "powershell.exe"
    $Shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`" -Mode auto"
    $Shortcut.WorkingDirectory = $ProjectRoot
    $Shortcut.WindowStyle = 7
    $Shortcut.Description = "Run the daily work digest at Windows login."
    $Shortcut.Save()
} catch {
    Write-Error "DIAGNOSTIC: Could not create the Startup shortcut at $ShortcutPath : $($_.Exception.Message). Startup automation is NOT installed; run 'digest send --mode morning --once-per-day' manually each morning until this is fixed."
    exit 1
}

if (Test-Path $ShortcutPath) {
    Write-Host "Created startup shortcut: $ShortcutPath"
} else {
    Write-Error "DIAGNOSTIC: Shortcut save reported success but $ShortcutPath does not exist. Startup automation is NOT installed."
    exit 1
}
