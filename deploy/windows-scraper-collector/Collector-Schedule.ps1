function New-CollectorRepeatingTrigger {
    param(
        [Parameter(Mandatory = $true)][ValidateRange(0, 14)][int]$OffsetMinutes,
        [Parameter(Mandatory = $true)][DateTime]$StartAt
    )
    $trigger = New-ScheduledTaskTrigger -Daily -At $StartAt
    $repetition = New-CimInstance `
        -Namespace 'root/Microsoft/Windows/TaskScheduler' `
        -ClassName 'MSFT_TaskRepetitionPattern' `
        -ClientOnly `
        -Property @{
            Interval = 'PT15M'
            Duration = 'P1D'
            StopAtDurationEnd = $false
        }
    $trigger.CimInstanceProperties['Repetition'].Value = $repetition
    return $trigger
}

function Get-NextCollectorSlot {
    param(
        [Parameter(Mandatory = $true)][DateTime]$Now,
        [Parameter(Mandatory = $true)][ValidateRange(0, 14)][int]$OffsetMinutes
    )
    $dayStart = $Now.Date.AddMinutes($OffsetMinutes)
    if ($Now -lt $dayStart) { return $dayStart }
    $minutesFromStart = ($Now - $dayStart).TotalMinutes
    $slotIndex = [Math]::Ceiling($minutesFromStart / 15)
    $next = $dayStart.AddMinutes($slotIndex * 15)
    if ($next -ge $Now.Date.AddDays(1)) {
        $next = $Now.Date.AddDays(1).AddMinutes($OffsetMinutes)
    }
    return $next
}
