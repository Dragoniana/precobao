$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
. (Join-Path $PSScriptRoot 'Collector-IO.ps1')
$settingsPath = Join-Path $StateRoot 'settings.json'
if (-not (Test-Path $settingsPath)) { throw 'Coletor ainda não instalado.' }
$settings = Read-CollectorJson -Path $settingsPath
Write-Host "Ativo: $($settings.collector_enabled); API pronta: $($settings.api_ready); fuso: $($settings.timezone); limite manual=$($settings.max_products_per_run) produtos; tarefas usam orçamento de requisições"
$rateStatePath = Join-Path $settings.state_dir 'rate-limit\external-requests.json'
if (Test-Path -LiteralPath $rateStatePath) {
    $rateLockPath = [IO.Path]::ChangeExtension($rateStatePath, '.lock')
    $rateLock = [IO.File]::Open($rateLockPath, 'Open', 'ReadWrite', 'ReadWrite')
    $lockAcquired = $false
    try {
        for ($attempt = 0; $attempt -lt 40 -and -not $lockAcquired; $attempt++) {
            try {
                $rateLock.Lock(0, 1)
                $lockAcquired = $true
            } catch [IO.IOException] {
                Start-Sleep -Milliseconds 25
            }
        }
        if (-not $lockAcquired) {
            Write-Host 'Orçamento externo móvel: leitura adiada porque o estado está sendo atualizado.'
        } else {
            $rateState = Read-CollectorJson -Path $rateStatePath
            $windowStart = [DateTimeOffset]::UtcNow.AddSeconds(-[int]$rateState.window_seconds)
            $activeRequests = @($rateState.events | Where-Object {
                $eventTime = if ($_.at -is [datetime]) {
                    [DateTimeOffset]$_.at
                } else {
                    [DateTimeOffset]::Parse(
                        [string]$_.at,
                        [Globalization.CultureInfo]::InvariantCulture,
                        [Globalization.DateTimeStyles]::RoundtripKind
                    )
                }
                $eventTime.ToUniversalTime() -gt $windowStart
            })
            $araujoRequests = @($activeRequests | Where-Object { $_.source -eq 'araujo' }).Count
            $pachecoRequests = @($activeRequests | Where-Object { $_.source -eq 'pacheco' }).Count
            $httpSourceLimit = if ($rateState.per_source_limit) { [int]$rateState.per_source_limit } else { [math]::Ceiling([int]$rateState.limit / 2) }
            Write-Host "Chamadas HTTP externas (janela móvel $([int]($rateState.window_seconds / 60)) min): total=$($activeRequests.Count)/$($rateState.limit); Araújo=$araujoRequests/$httpSourceLimit; Pacheco=$pachecoRequests/$httpSourceLimit"
        }
    } finally {
        if ($lockAcquired) { $rateLock.Unlock(0, 1) }
        $rateLock.Dispose()
    }
} else {
    Write-Host 'Chamadas HTTP externas (janela móvel 15 min): total=0/50; Araújo=0/25; Pacheco=0/25'
}
$medicineLimit = if ($settings.PSObject.Properties.Name -contains 'max_medicines_per_window') { [int]$settings.max_medicines_per_window } else { 50 }
$medicineShare = if ($settings.PSObject.Properties.Name -contains 'max_medicines_per_source_share') { [int]$settings.max_medicines_per_source_share } else { 25 }
$medicinePath = Join-Path $settings.state_dir 'medicine-budget\processed-medicines.json'
if (Test-Path -LiteralPath $medicinePath) {
    try {
        $medicineState = Read-CollectorJson -Path $medicinePath
        $medicineStart = [DateTimeOffset]::UtcNow.AddSeconds(-[int]$medicineState.window_seconds)
        $activeMedicines = @($medicineState.events | Where-Object {
            $eventTime = [DateTimeOffset]::Parse(
                [string]$_.at,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind
            )
            $eventTime.ToUniversalTime() -gt $medicineStart
        })
        $araujoMedicines = @($activeMedicines | Where-Object { $_.source -eq 'araujo' }).Count
        $pachecoMedicines = @($activeMedicines | Where-Object { $_.source -eq 'pacheco' }).Count
        Write-Host "Medicamentos distintos processados (janela móvel $([int]($medicineState.window_seconds / 60)) min): total=$($activeMedicines.Count)/$medicineLimit; Araújo=$araujoMedicines (quota inicial $medicineShare); Pacheco=$pachecoMedicines (quota inicial $medicineShare)"
        $statusGroups = $activeMedicines | Group-Object status | ForEach-Object { "$($_.Name)=$($_.Count)" }
        Write-Host "Estados dos medicamentos: $($statusGroups -join ', ')"
    } catch {
        Write-Host 'Estado do limite de medicamentos inválido; coletor deve falhar fechado.'
    }
} else {
    Write-Host "Medicamentos distintos processados (janela móvel 15 min): total=0/$medicineLimit; Araújo=0 (quota inicial $medicineShare); Pacheco=0 (quota inicial $medicineShare)"
}
foreach ($source in @('araujo', 'pacheco')) {
    $path = Join-Path $settings.state_dir "$source.json"
    if (Test-Path $path) {
        $state = Read-CollectorJson -Path $path
        $displayStatus = [string]$state.status
        if ($displayStatus -eq 'RUNNING') {
            $activePython = @(Get-CimInstance -ClassName Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object {
                $_.CommandLine -match 'app\.scrapers\.scraper_collector' -and
                $_.CommandLine -match "\s$source(?:\s|$)"
            })
            if ($activePython.Count -eq 0) { $displayStatus = 'INTERRUPTED (estado salvo)' }
        }
        $imageFailures = if ($null -ne $state.image_failures) { $state.image_failures } else { 0 }
        Write-Host "$source`: $displayStatus recebidos=$($state.products_sent + $state.products_rejected) aceitos=$($state.products_sent) rejeitados=$($state.products_rejected) ofertas_novas=$($state.offers_created) ofertas_atualizadas=$($state.offers_updated) mudancas_preco=$($state.price_changes) imagens_enviadas=$($state.image_blobs_uploaded) falhas_imagem=$imageFailures sincronizado=$($state.finished_at) batch=$($state.last_batch_id) motivo=$($state.stop_reason)"
        Write-Host "$source medicamentos nesta execucao: distintos=$($state.medicines_processed) imagem_real=$($state.medicines_with_real_image) generica=$($state.medicines_with_generic_image) sem_imagem=$($state.medicines_without_image) imagem_adiada=$($state.medicines_image_deferred)"
    } else { Write-Host "$source`: sem execução" }
    $checkpointPath = Join-Path (Join-Path $settings.state_dir "sources\$source") $(if ($source -eq 'araujo') { 'source-checkpoint.json' } else { 'checkpoint.json' })
    if (Test-Path -LiteralPath $checkpointPath) {
        $checkpoint = Read-CollectorJson -Path $checkpointPath
        if ($source -eq 'araujo') {
            Write-Host "$source checkpoint: pagina_concluida=$($checkpoint.last_completed_page) proxima_pagina=$($checkpoint.next_page) item_inicial=$($checkpoint.next_item_index)"
        } else {
            Write-Host "$source checkpoint: offset_concluido=$($checkpoint.last_completed_offset) cursor=$($checkpoint.scan_cursor | ConvertTo-Json -Compress)"
        }
    }
    $rejectedPath = Join-Path $settings.state_dir "rejected\$source.json"
    if (Test-Path $rejectedPath) {
        $rejected = @(Read-CollectorJson -Path $rejectedPath)
        Write-Host "$source`: conflitos persistidos=$($rejected.Count)"
    }
    $batchOutboxPath = Join-Path $settings.state_dir "outbox\$source.pending.json"
    $batchPending = Test-Path -LiteralPath $batchOutboxPath
    Write-Host "$source`: lote pendente na outbox=$batchPending"
    $imageOutbox = Join-Path $settings.state_dir "outbox\images\$source"
    $imageCount = if (Test-Path $imageOutbox) {
        @(Get-ChildItem -LiteralPath $imageOutbox -File -Filter '*.json').Count
    } else { 0 }
    Write-Host "$source`: imagens pendentes=$imageCount"
    $imageFetchOutbox = Join-Path $settings.state_dir "outbox\image-fetch\$source"
    $imageFetchCount = if (Test-Path $imageFetchOutbox) {
        @(Get-ChildItem -LiteralPath $imageFetchOutbox -File -Filter '*.json').Count
    } else { 0 }
    $imageFetchBlocked403 = 0
    $imageFetchBlocked404 = 0
    $imageFetchGeneric = 0
    $imageFetchRetry = 0
    $imageFetchDeadLetter = 0
    $imageFetchInvalid = 0
    $blockedImagePath = Join-Path $settings.state_dir 'blocked-source-images.json'
    $blockedImageMap = if (Test-Path -LiteralPath $blockedImagePath) { Read-CollectorJson -Path $blockedImagePath } else { $null }
    if (Test-Path $imageFetchOutbox) {
        foreach ($manifestPath in (Get-ChildItem -LiteralPath $imageFetchOutbox -File -Filter '*.json')) {
            try {
                $manifest = Read-CollectorJson -Path $manifestPath.FullName
                if ($manifest.status -eq 'DEAD_LETTER') { $imageFetchDeadLetter++ }
                elseif ($manifest.next_retry_at) { $imageFetchRetry++ }
                $imageUrl = [string]$manifest.imagem_url
                $parsedImageUri = $null
                if (-not [Uri]::TryCreate($imageUrl, [UriKind]::Absolute, [ref]$parsedImageUri) -or $parsedImageUri.Scheme -ne 'https') { $imageFetchInvalid++; continue }
                $hashInput = "$source`n$imageUrl"
                $sha = [Security.Cryptography.SHA256]::Create()
                try { $imageKey = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($hashInput))).Replace('-', '').ToLowerInvariant() } finally { $sha.Dispose() }
                $blockedEntry = if ($null -ne $blockedImageMap) { $blockedImageMap.PSObject.Properties[$imageKey].Value } else { $null }
                if ($blockedEntry) {
                    if ($blockedEntry.reason -match '403') { $imageFetchBlocked403++ }
                    elseif ($blockedEntry.reason -match '404') { $imageFetchBlocked404++ }
                } elseif ($imageUrl -match '(?i)generico|sem[-_]?imagem|no[-_]?image|placeholder') {
                    $imageFetchGeneric++
                }
            } catch { $imageFetchInvalid++ }
        }
    }
    $imageFetchBlocked = $imageFetchBlocked403 + $imageFetchBlocked404
    $imageFetchOrdinary = [math]::Max(0, $imageFetchCount - $imageFetchBlocked - $imageFetchGeneric - $imageFetchInvalid)
    Write-Host "$source downloads de imagem: pendentes=$imageFetchCount aguardando/retry=$imageFetchRetry dead_letter=$imageFetchDeadLetter URL_real=$imageFetchOrdinary generica=$imageFetchGeneric bloqueada_403=$imageFetchBlocked403 bloqueada_404=$imageFetchBlocked404 URL_invalida=$imageFetchInvalid"
    $lastSlotPath = Join-Path $StateRoot "last-scheduled-slot-$source.txt"
    if (Test-Path -LiteralPath $lastSlotPath) {
        $lastSlot = (Read-CollectorUtf8 -Path $lastSlotPath).Trim()
        Write-Host "$source ultimo_slot=$lastSlot"
    }
}
$tasks = Get-ScheduledTask -ErrorAction SilentlyContinue |
    Where-Object { $_.TaskName -like 'PrecoBaoCollector-*' } |
    ForEach-Object {
        $info = Get-ScheduledTaskInfo -TaskName $_.TaskName -ErrorAction SilentlyContinue
        $triggerSummary = @($_.Triggers | ForEach-Object {
            $start = ([DateTime]$_.StartBoundary).ToString('HH:mm')
            if ($_.Repetition.Interval) {
                "a cada $($_.Repetition.Interval), início $start, duração $($_.Repetition.Duration)"
            } else { "diária às $start" }
        }) -join '; '
        [pscustomobject]@{ TaskName = $_.TaskName; State = $_.State; LastRunTime = $info.LastRunTime; NextRunTime = $info.NextRunTime; LastTaskResult = $info.LastTaskResult; Schedule = $triggerSummary; Arguments = (@($_.Actions | ForEach-Object { $_.Arguments }) -join '; ') }
    }
$tasks | Format-Table -AutoSize
Write-Host 'Códigos de saída: 0 concluída; 2 acesso negado; 3 parcial; 4 parada segura; 5 limite da origem; 6 falha API/HMAC; 7 erro; 8 desativada; 9 lock ocupado; 10 horário perdido; 11 repetição do slot; 12 limite HTTP externo; 13 limite configurado por execução; 14 limite de medicamentos da janela.'
Write-Host "Log: $($settings.log_file)"
