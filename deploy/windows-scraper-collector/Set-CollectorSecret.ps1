param(
    [Security.SecureString]$Secret
)

$ErrorActionPreference = 'Stop'
$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
if (-not (Test-Path $StateRoot)) { throw 'Execute Install-Collector.ps1 primeiro.' }
$secretToStore = if ($Secret) {
    $Secret
} else {
    Read-Host 'Cole o segredo HMAC exclusivo configurado na API de ingestão' -AsSecureString
}
if ($secretToStore.Length -lt 32) { throw 'O segredo HMAC deve conter pelo menos 32 caracteres.' }
$encoded = ConvertFrom-SecureString $secretToStore
Set-Content -Path (Join-Path $StateRoot 'hmac-secret.dpapi') -Value $encoded -Encoding ascii -NoNewline
Write-Host 'Segredo guardado com DPAPI para este usuário Windows.'
