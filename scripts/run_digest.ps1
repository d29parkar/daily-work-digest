param(
    [ValidateSet("morning", "night", "trello", "auto")]
    [string]$Mode = "auto"
)

# Runs one digest cycle: ingest -> generate -> send (or save-only if SMTP is
# not configured). Called by Task Scheduler and the Startup shortcut.
# Never fails silently: errors land in outputs\logs\ and LAST_RUN_ERROR.txt.

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $ProjectRoot "outputs\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$LogPath = Join-Path $LogDir "$Mode-digest-$Timestamp.log"
$ErrorMarker = Join-Path $LogDir "LAST_RUN_ERROR.txt"

Start-Transcript -Path $LogPath -Append | Out-Null
try {
    Set-Location $ProjectRoot

    $VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $VenvPython) {
        $Python = $VenvPython
    } else {
        $Python = "python"
        Write-Warning "Virtualenv python not found at $VenvPython; using 'python' from PATH."
    }

    $env:PYTHONPATH = Join-Path $ProjectRoot "src"
    & $Python -m digest.cli send --mode $Mode --once-per-day
    if ($LASTEXITCODE -ne 0) {
        throw "digest send --mode $Mode failed with exit code $LASTEXITCODE"
    }

    # Successful run: clear any stale error marker.
    if (Test-Path $ErrorMarker) { Remove-Item $ErrorMarker -Force }
    Write-Host "Digest run ($Mode) completed successfully."
} catch {
    $Message = "[$(Get-Date -Format o)] Digest run ($Mode) FAILED: $($_.Exception.Message)`nLog: $LogPath"
    $Message | Out-File -FilePath $ErrorMarker -Encoding utf8
    Write-Error $Message
    exit 1
} finally {
    Stop-Transcript | Out-Null
}
