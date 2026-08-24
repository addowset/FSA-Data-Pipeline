# Daily FHRS collection + parse + diff, run together as the scheduled
# task's entry point. Runs each stage regardless of whether an earlier one
# had partial failures -- each script is idempotent and only processes
# what it can (an authority with a raw file gets parsed even if others
# failed; an authority that got parsed gets diffed even if others didn't),
# so a partial run still makes progress, and the next day's run (or a
# manual re-run) picks up the rest via each script's own resume logic.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

& $python (Join-Path $root "scripts\collect_fhrs_bulk.py")
$collectExit = $LASTEXITCODE

& $python (Join-Path $root "scripts\parse_fhrs_bulk.py")
$parseExit = $LASTEXITCODE

& $python (Join-Path $root "scripts\diff_fhrs.py")
$diffExit = $LASTEXITCODE

if ($collectExit -ne 0 -or $parseExit -ne 0 -or $diffExit -ne 0) {
    Write-Error "run_daily.ps1: collect exit=$collectExit parse exit=$parseExit diff exit=$diffExit"
    exit 1
}

exit 0
