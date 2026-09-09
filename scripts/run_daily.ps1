# Daily FHRS collection + parse + diff, then Companies House collection +
# parse, then operator-name lookup, then postcode backfill, then the
# matcher, then classification, run together as the scheduled task's
# entry point. Runs each stage regardless of whether an earlier one had
# partial failures -- each script is idempotent and only processes what
# it can (an authority with a raw file gets parsed even if others
# failed; an authority that got parsed gets diffed even if others
# didn't), so a partial run still makes progress, and the next day's run
# (or a manual re-run) picks up the rest via each script's own resume
# logic. Operator-name lookup runs right after Companies House parsing
# (it enriches companies_current with the same live-search results
# match_companies_house.py's operator-prefix channel would otherwise
# never see -- see lookup_operator_companies.py) and before matching, so
# today's finds are available for today's matching pass. Postcode
# backfill runs after FHRS parsing (it only needs that day's bulk
# post_code values) and before matching, so today's backfilled postcodes
# are available for today's matching pass, which reads
# COALESCE(post_code, postcode_from_live_api). Classification runs last
# -- it needs both the matcher's evidence and the full
# establishments_current baseline for its address-history check.

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

& $python (Join-Path $root "scripts\lookup_operator_companies.py")
$operatorSearchExit = $LASTEXITCODE

& $python (Join-Path $root "scripts\backfill_postcodes.py")
$backfillExit = $LASTEXITCODE

& $python (Join-Path $root "scripts\match_companies_house.py")
$matchExit = $LASTEXITCODE

& $python (Join-Path $root "scripts\classify_insertions.py")
$classifyExit = $LASTEXITCODE

if ($collectExit -ne 0 -or $parseExit -ne 0 -or $diffExit -ne 0 -or $chCollectExit -ne 0 -or $chParseExit -ne 0 -or $operatorSearchExit -ne 0 -or $backfillExit -ne 0 -or $matchExit -ne 0 -or $classifyExit -ne 0) {
    Write-Error "run_daily.ps1: fhrs collect=$collectExit parse=$parseExit diff=$diffExit / ch collect=$chCollectExit parse=$chParseExit / operator search=$operatorSearchExit / backfill=$backfillExit / match=$matchExit / classify=$classifyExit"
    exit 1
}

exit 0
