param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Container })]
    [string]$BackupDirectory
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Collector-IO.ps1')
$BackupDirectory = (Resolve-Path -LiteralPath $BackupDirectory).Path
$manifestPath = Join-Path $BackupDirectory 'manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw 'Manifesto do backup das tarefas não encontrado.'
}
$manifest = Read-CollectorJson -Path $manifestPath
$expectedNames = @(
    'PrecoBaoCollector-araujo-0800',
    'PrecoBaoCollector-araujo-2000',
    'PrecoBaoCollector-pacheco-1000',
    'PrecoBaoCollector-pacheco-2200'
)
$entries = @($manifest.previous_tasks)
if ($entries.Count -ne 4 -or @($entries.name | Where-Object { $_ -notin $expectedNames }).Count -gt 0) {
    throw 'O manifesto não contém exatamente as quatro tarefas esperadas; nada foi alterado.'
}
foreach ($entry in $entries) {
    if (-not (Test-Path -LiteralPath (Join-Path $BackupDirectory $entry.path) -PathType Leaf)) {
        throw "XML de backup ausente para $($entry.name); nada foi alterado."
    }
}

foreach ($name in @('PrecoBaoCollector-araujo-15m', 'PrecoBaoCollector-pacheco-15m')) {
    Disable-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue | Out-Null
}
foreach ($entry in $entries) {
    $xml = Read-CollectorUtf8 -Path (Join-Path $BackupDirectory $entry.path)
    Register-ScheduledTask -TaskName $entry.name -Xml $xml -Force -ErrorAction Stop | Out-Null
    if ($entry.state -eq 'Ready') {
        Enable-ScheduledTask -TaskName $entry.name -ErrorAction Stop | Out-Null
    } else {
        Disable-ScheduledTask -TaskName $entry.name -ErrorAction Stop | Out-Null
    }
}
Write-Host 'As tarefas novas foram suspensas e as quatro tarefas anteriores foram restauradas conforme o estado salvo.'
