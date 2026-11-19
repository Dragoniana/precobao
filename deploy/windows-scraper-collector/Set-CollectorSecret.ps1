$ErrorActionPreference = 'Stop'
$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
if (-not (Test-Path $StateRoot)) { throw 'Execute Install-Collector.ps1 primeiro.' }
$secret = Read-Host 'Cole o segredo HMAC exclusivo configurado na API de ingestão' -AsSecureString
$encoded = ConvertFrom-SecureString $secret
Set-Content -Path (Join-Path $StateRoot 'hmac-secret.dpapi') -Value $encoded -Encoding ascii
Write-Host 'Segredo guardado com DPAPI para este usuário Windows.'
