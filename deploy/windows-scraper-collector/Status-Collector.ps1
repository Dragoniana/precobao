$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
$settingsPath = Join-Path $StateRoot 'settings.json'
if (-not (Test-Path $settingsPath)) { throw 'Coletor ainda não instalado.' }
$settings = Get-Content -Raw $settingsPath | ConvertFrom-Json
Write-Host "Ativo: $($settings.collector_enabled); API pronta: $($settings.api_ready); fuso: $($settings.timezone)"
foreach ($source in @('araujo', 'pacheco')) {
    $path = Join-Path $settings.state_dir "$source.json"
    if (Test-Path $path) {
        $state = Get-Content -Raw $path | ConvertFrom-Json
        Write-Host "$source`: $($state.status) recebidos=$($state.products_sent + $state.products_rejected) aceitos=$($state.products_sent) rejeitados=$($state.products_rejected) ofertas_novas=$($state.offers_created) ofertas_atualizadas=$($state.offers_updated) mudancas_preco=$($state.price_changes) imagens=$($state.image_blobs_uploaded) atualizado=$($state.finished_at) motivo=$($state.stop_reason)"
    } else { Write-Host "$source`: sem execução" }
    $checkpointPath = Join-Path (Join-Path $settings.state_dir "sources\$source") $(if ($source -eq 'araujo') { 'source-checkpoint.json' } else { 'checkpoint.json' })
    if (Test-Path -LiteralPath $checkpointPath) {
        $checkpoint = Get-Content -Raw $checkpointPath | ConvertFrom-Json
        if ($source -eq 'araujo') {
            Write-Host "$source checkpoint: pagina_concluida=$($checkpoint.last_completed_page) proxima_pagina=$($checkpoint.next_page) item_inicial=$($checkpoint.next_item_index)"
        } else {
            Write-Host "$source checkpoint: offset_concluido=$($checkpoint.last_completed_offset) cursor=$($checkpoint.scan_cursor | ConvertTo-Json -Compress)"
        }
    }
    $rejectedPath = Join-Path $settings.state_dir "rejected\$source.json"
    if (Test-Path $rejectedPath) {
        $rejected = @(Get-Content -Raw $rejectedPath | ConvertFrom-Json)
        Write-Host "$source`: conflitos persistidos=$($rejected.Count)"
    }
    $imageOutbox = Join-Path $settings.state_dir "outbox\images\$source"
    $imageCount = if (Test-Path $imageOutbox) {
        @(Get-ChildItem -LiteralPath $imageOutbox -File -Filter '*.json').Count
    } else { 0 }
    Write-Host "$source`: imagens pendentes=$imageCount"
    $lastSlotPath = Join-Path $StateRoot "last-scheduled-slot-$source.txt"
    if (Test-Path -LiteralPath $lastSlotPath) {
        Write-Host "$source ultimo_slot=$(Get-Content -Raw $lastSlotPath).Trim()"
    }
}
$tasks = Get-ScheduledTask -ErrorAction SilentlyContinue |
    Where-Object { $_.TaskName -like 'PrecoBaoCollector-*' } |
    ForEach-Object {
        $info = Get-ScheduledTaskInfo -TaskName $_.TaskName -ErrorAction SilentlyContinue
        [pscustomobject]@{ TaskName = $_.TaskName; State = $_.State; LastRunTime = $info.LastRunTime; LastTaskResult = $info.LastTaskResult; Arguments = (@($_.Actions | ForEach-Object { $_.Arguments }) -join '; ') }
    }
$tasks | Format-Table -AutoSize
$lockPath = Join-Path $settings.state_dir 'locks\collector.running.lock'
if (Test-Path -LiteralPath $lockPath) {
    try {
        $lock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None')
        $lock.Dispose()
        Write-Host 'Lock global: livre'
    } catch { Write-Host 'Lock global: ocupado' }
} else { Write-Host 'Lock global: ainda não criado' }
Write-Host 'Códigos de saída: 0 concluída; 2 acesso negado; 3 parcial; 4 parada segura; 5 limite da origem; 6 falha API/HMAC; 7 erro; 8 desativada; 9 lock ocupado; 10 horário perdido; 11 repetição do slot.'
Write-Host "Log: $($settings.log_file)"
