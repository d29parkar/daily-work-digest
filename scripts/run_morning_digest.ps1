# Back-compat wrapper; the scheduled tasks call run_digest.ps1 directly.
& (Join-Path $PSScriptRoot "run_digest.ps1") -Mode morning
exit $LASTEXITCODE
