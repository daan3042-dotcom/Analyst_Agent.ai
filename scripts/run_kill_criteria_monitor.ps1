<#
.SYNOPSIS
    Draait monitor_kill_criteria.py voor alle opgeslagen tickers en logt de
    output naar logs/kill_criteria/.

.DESCRIPTION
    Los, geen-Claude-aanroep-nodig bewakingsscript (zie
    src/tracking/monitor_kill_criteria.py) dat verse SEC-cijfers checkt
    tegen eerder vastgelegde kill-criteria. Dit wrapper-script bestaat
    zodat het via Windows Task Scheduler gepland kan draaien: het zet de
    working directory naar de repo-root (nodig, want track_record/ wordt
    relatief t.o.v. de working directory gelezen), en schrijft de output
    weg naar een logbestand in plaats van alleen naar de console, zodat
    een onbeheerde, geplande run ook daadwerkelijk na te lezen is.

    Schrijft zowel een tijdgestempeld logbestand als een "latest.log" die
    steeds de meest recente run bevat, voor een snelle check.

.PARAMETER PythonPath
    Pad naar de python-executable. Standaard "python" (verwacht op PATH,
    zoals in de rest van dit project -- zie README.md).

.EXAMPLE
    .\run_kill_criteria_monitor.ps1
    .\run_kill_criteria_monitor.ps1 -PythonPath "C:\Python312\python.exe"
#>

param(
    [string]$PythonPath = "python"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $RepoRoot "logs\kill_criteria"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$LogFile = Join-Path $LogDir "$Timestamp.log"
$LatestLogFile = Join-Path $LogDir "latest.log"

# monitor_kill_criteria.py leest track_record/ relatief t.o.v. de working
# directory (zelfde aanname als analyst_agent.py) -- dus eerst naar de
# repo-root, ongeacht van waaruit deze taak gestart wordt.
Set-Location $RepoRoot

$ScriptPath = Join-Path $RepoRoot "src\tracking\monitor_kill_criteria.py"

"=== TCE kill-criteria monitor -- $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" |
    Tee-Object -FilePath $LogFile

# 2>&1 voegt stderr (bijv. netwerkfouten van fetch_sec_financials) samen
# met stdout, zodat beide in hetzelfde logbestand terechtkomen.
& $PythonPath $ScriptPath 2>&1 | Tee-Object -FilePath $LogFile -Append

Copy-Item -Path $LogFile -Destination $LatestLogFile -Force

$ExitCode = $LASTEXITCODE
if ($ExitCode -ne 0) {
    "=== Script eindigde met foutcode $ExitCode -- zie hierboven ===" |
        Tee-Object -FilePath $LogFile -Append
}
exit $ExitCode
