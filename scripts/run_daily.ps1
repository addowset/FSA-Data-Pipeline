# Daily FHRS collection + parse + diff, then Companies House collection +
# parse, run together as the scheduled task's entry point. Runs each stage
# regardless of whether an earlier one had partial failures -- each script
# is idempotent and only processes what it can (an authority with a raw
# file gets parsed even if others failed; an authority that got parsed
# gets diffed even if others didn't), so a partial run still makes
# progress, and the next day's run (or a manual re-run) picks up the rest
# via each script's own resume logic. The two data sources don't depend
# on each other, but FHRS runs first since it's the higher-priority feed.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

& $python (Join-Path $root "scripts\collect_fhrs_bulk.py")
$collectExit = $LASTEXITCODE

& $python (Join-Path $root "scripts\parse_fhrs_bulk.py")
$parseExit = $LASTEXITCODE

& $python (Join-Path $root "scripts\diff_fhrs.py")
$diffExit = $LASTEXITCODE

& $python (Join-Path $root "scripts\collect_companies_house.py")
$chCollectExit = $LASTEXITCODE

& $python (Join-Path $root "scripts\parse_companies_house.py")
$chParseExit = $LASTEXITCODE

if ($collectExit -ne 0 -or $parseExit -ne 0 -or $diffExit -ne 0 -or $chCollectExit -ne 0 -or $chParseExit -ne 0) {
    Write-Error "run_daily.ps1: fhrs collect=$collectExit parse=$parseExit diff=$diffExit / ch collect=$chCollectExit parse=$chParseExit"
    exit 1
}

exit 0
