# Daily FHRS collection + parse, run together as the scheduled task's entry
# point. Runs collection first, then parsing, regardless of whether
# collection had partial failures -- parsing is idempotent and only
# processes authorities that actually have a raw file on disk, so a partial
# collection still gets whatever succeeded parsed, and the next day's run
# (or a manual re-run) picks up the rest via each script's own resume logic.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

& $python (Join-Path $root "scripts\collect_fhrs_bulk.py")
$collectExit = $LASTEXITCODE

& $python (Join-Path $root "scripts\parse_fhrs_bulk.py")
$parseExit = $LASTEXITCODE

if ($collectExit -ne 0 -or $parseExit -ne 0) {
    Write-Error "run_daily.ps1: collect exit=$collectExit parse exit=$parseExit"
    exit 1
}

exit 0
