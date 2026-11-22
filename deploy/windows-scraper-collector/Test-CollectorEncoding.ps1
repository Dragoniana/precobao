$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Collector-IO.ps1')

$scripts = Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1' -File
foreach ($scriptFile in $scripts) {
    $tokens = $null
    $parseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $scriptFile.FullName,
        [ref]$tokens,
        [ref]$parseErrors
    )
    if ($parseErrors.Count -gt 0) {
        throw "Falha de sintaxe em $($scriptFile.Name): $($parseErrors -join '; ')"
    }
}

$testRoot = Join-Path $env:TEMP ('Preço Bão - Usuário João José ' + [guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($testRoot)
try {
    $expected = 'Usuário João José — São Paulo'
    $bomPath = Join-Path $testRoot 'configuração-com-bom.json'
    $plainPath = Join-Path $testRoot 'configuração-sem-bom.json'
    $logPath = Join-Path $testRoot 'logscoleta.log'
    $payload = @{ mensagem = $expected; caminho = 'C:UsersUsuárioJoão JoséPreço Bão' }
    $json = ConvertTo-Json -InputObject $payload -Depth 4

    [IO.File]::WriteAllText($bomPath, $json, [Text.UTF8Encoding]::new($true))
    [IO.File]::WriteAllText($plainPath, $json, [Text.UTF8Encoding]::new($false))
    foreach ($path in @($bomPath, $plainPath)) {
        $read = Read-CollectorJson -Path $path
        if ($read.mensagem -ne $expected -or $read.caminho -ne $payload.caminho) {
            throw "Leitura UTF-8 incorreta em caminho Unicode: $path"
        }
    }

    Write-CollectorJson -Path $plainPath -Value $payload -Depth 4
    $writtenBytes = [IO.File]::ReadAllBytes($plainPath)
    if ($writtenBytes.Length -ge 3 -and $writtenBytes[0] -eq 0xEF -and
        $writtenBytes[1] -eq 0xBB -and $writtenBytes[2] -eq 0xBF) {
        throw 'A gravação JSON deve usar UTF-8 sem BOM para compatibilidade com Python.'
    }
    Add-CollectorUtf8Line -Path $logPath -Text $expected
    if ((Read-CollectorUtf8 -Path $logPath).Trim() -ne $expected) {
        throw 'A gravação/leitura do log UTF-8 falhou.'
    }
} finally {
    if ([IO.Directory]::Exists($testRoot)) {
        [IO.Directory]::Delete($testRoot, $true)
    }
}

Write-Host 'Windows PowerShell UTF-8 and syntax checks passed.'
