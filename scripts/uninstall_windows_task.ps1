# Removes the digest scheduled tasks and the Startup-folder shortcut.

$ErrorActionPreference = "Continue"

$TaskNames = @(
    "Work Digest Morning",
    "Work Digest Trello",
    "Work Digest Night",
    "Daily Work Digest",      # legacy name
    "Daily Work Digest 9AM"   # legacy schtasks fallback name
)

foreach ($TaskName in $TaskNames) {
    & schtasks.exe /Query /TN $TaskName 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        & schtasks.exe /Delete /TN $TaskName /F | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Removed scheduled task: $TaskName"
        } else {
            Write-Warning "Could not remove scheduled task: $TaskName"
        }
    }
}

$StartupFolder = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $StartupFolder "Daily Work Digest.lnk"
if (Test-Path $ShortcutPath) {
    Remove-Item $ShortcutPath -Force
    Write-Host "Removed startup shortcut: $ShortcutPath"
}

Write-Host "Uninstall complete. Verify with: digest doctor"
