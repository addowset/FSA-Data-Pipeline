# Entry point for the "FSA Data Pipeline Run Check" scheduled task (13:15
# daily). Runs check_daily_run.py first (writes to _alerts_<date>.log if
# today's run didn't start or didn't complete), then
# send_run_status_email.py second, so the email always reflects whatever
# check_daily_run.py just found -- same ordering dependency as
# run_daily.ps1's own stages.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

& $python (Join-Path $root "scripts\check_daily_run.py")
$checkExit = $LASTEXITCODE

& $python (Join-Path $root "scripts\send_run_status_email.py")
$emailExit = $LASTEXITCODE

if ($checkExit -ne 0 -or $emailExit -ne 0) {
    Write-Error "run_check.ps1: check=$checkExit email=$emailExit"
    exit 1
}

exit 0
