# End-to-end smoke test. Safe: uses a temp workspace, the fixture summarizer,
# and dry-run email only. Never touches your real DB/outputs, never calls
# OpenAI, never sends email.

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (!(Test-Path $Python)) { $Python = "python" }
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

$Failures = 0
function Step($Name, $ScriptBlock) {
    Write-Host "--- $Name"
    & $ScriptBlock
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAIL: $Name" -ForegroundColor Red
        $script:Failures++
    } else {
        Write-Host "PASS: $Name" -ForegroundColor Green
    }
}

Step "compileall" { & $Python -m compileall -q src }
Step "cli --help" { & $Python -m digest.cli --help | Out-Null }
Step "pytest" { & $Python -m pytest -q }

# Isolated end-to-end run in a temp workspace with the fixture summarizer.
$Work = Join-Path $env:TEMP ("digest-smoke-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $Work | Out-Null
try {
    $Fixture = Join-Path $Work "fixture.md"
    "## Executive summary`n- Smoke test fixture body." | Out-File $Fixture -Encoding utf8
    $ConfigPath = Join-Path $Work "config.yaml"
    @"
parent_dir: $Work
repos:
  smoke-repo: $Work\smoke-repo
claude:
  paths: []
  lookback_hours: 48
codex:
  paths: []
  lookback_hours: 48
notes:
  path: $Work\notes
  lookback_hours: 168
output:
  path: $Work\outputs
data:
  sqlite_path: $Work\digest.sqlite
model:
  provider: fixture
  fixture_path: $Fixture
email:
  enabled: true
  recipient: smoke@example.com
"@ | Out-File $ConfigPath -Encoding utf8

    Step "generate morning (fixture)" { & $Python -m digest.cli --config $ConfigPath generate --mode morning }
    Step "generate night (fixture)" { & $Python -m digest.cli --config $ConfigPath generate --mode night }
    Step "once-per-day skip" { & $Python -m digest.cli --config $ConfigPath generate --mode morning --once-per-day }
    Step "send dry-run" { & $Python -m digest.cli --config $ConfigPath send --mode morning --dry-run }
    Step "doctor" { & $Python -m digest.cli --config $ConfigPath doctor }
} finally {
    Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
}

# Validate the PowerShell scripts parse cleanly (no execution).
Write-Host "--- powershell script syntax"
$ParseErrors = 0
Get-ChildItem (Join-Path $ProjectRoot "scripts") -Filter *.ps1 | ForEach-Object {
    $Tokens = $null; $Errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$Tokens, [ref]$Errors) | Out-Null
    if ($Errors.Count -gt 0) {
        Write-Host "Parse errors in $($_.Name): $($Errors | ForEach-Object Message)"
        $ParseErrors++
    }
}
if ($ParseErrors -gt 0) {
    Write-Host "FAIL: powershell script syntax" -ForegroundColor Red
    $Failures++
} else {
    Write-Host "PASS: powershell script syntax" -ForegroundColor Green
}

Write-Host ""
if ($Failures -eq 0) {
    Write-Host "SMOKE TEST: all steps passed." -ForegroundColor Green
    exit 0
} else {
    Write-Host "SMOKE TEST: $Failures step(s) FAILED." -ForegroundColor Red
    exit 1
}
