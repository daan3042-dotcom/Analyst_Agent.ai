<#
.SYNOPSIS
    Eenmalig setup-script: registreert een wekelijkse Windows Task
    Scheduler-taak die run_kill_criteria_monitor.ps1 aanroept.

.DESCRIPTION
    Draai dit ÉÉN keer (in een PowerShell-venster) om de taak aan te
    maken. Standaard: elke maandag om 08:00, alleen als je op dat moment
    ingelogd bent (geen opgeslagen wachtwoord nodig) -- zie de opmerking
    hieronder als je 'm ook wilt laten draaien terwijl je uitgelogd bent.

    Opnieuw draaien (bijv. na het verplaatsen van de repo) overschrijft de
    bestaande taak (-Force), zodat dit ook als "update" gebruikt kan
    worden.

.PARAMETER TaskName
    Naam van de Task Scheduler-taak. Standaard "TCE-KillCriteriaMonitor".

.PARAMETER DayOfWeek
    Op welke dag de taak wekelijks draait. Standaard Monday.

.PARAMETER Time
    Op welk tijdstip (24-uurs, "HH:mm"). Standaard "08:00".

.EXAMPLE
    .\register_kill_criteria_task.ps1
    .\register_kill_criteria_task.ps1 -DayOfWeek Friday -Time "17:30"

.NOTES
    Om de taak weer te verwijderen:
        Unregister-ScheduledTask -TaskName "TCE-KillCriteriaMonitor" -Confirm:$false

    Deze taak draait standaard alleen als jij op dat moment bent ingelogd
    (Task Scheduler's normale gedrag zonder opgeslagen inloggegevens). Wil
    je dat 'm ook draait terwijl je uitgelogd bent, gebruik dan de Task
    Scheduler-GUI (taskschd.msc) op deze taak: Properties -> General ->
    "Run whether user is logged on or not" -- dat vraagt eenmalig om je
    Windows-wachtwoord, wat dit script bewust niet zelf afhandelt.
#>

param(
    [string]$TaskName = "TCE-KillCriteriaMonitor",
    [ValidateSet("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")]
    [string]$DayOfWeek = "Monday",
    [string]$Time = "08:00"
)

$ErrorActionPreference = "Stop"

$RunScript = Join-Path $PSScriptRoot "run_kill_criteria_monitor.ps1"
if (-not (Test-Path $RunScript)) {
    throw "Kan run_kill_criteria_monitor.ps1 niet vinden op $RunScript -- staat dit script nog in dezelfde map?"
}

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`""
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DayOfWeek -At $Time
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Force `
    -Description "TCE Financial Analyst Agent: wekelijkse check of opgeslagen kill-criteria zijn geraakt (src/tracking/monitor_kill_criteria.py). Log: logs/kill_criteria/latest.log" `
    | Out-Null

Write-Host "Taak '$TaskName' geregistreerd: elke $DayOfWeek om $Time."
Write-Host "Logbestanden komen in: $(Join-Path (Split-Path -Parent $PSScriptRoot) 'logs\kill_criteria')"
Write-Host "Verwijderen: Unregister-ScheduledTask -TaskName `"$TaskName`" -Confirm:`$false"
