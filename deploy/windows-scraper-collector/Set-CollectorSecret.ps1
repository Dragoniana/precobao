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
$secretPath = Join-Path $StateRoot 'hmac-secret.dpapi'
Set-Content -LiteralPath $secretPath -Value $encoded -Encoding ascii -NoNewline
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$icacls = Join-Path $env:SystemRoot 'System32\icacls.exe'
& $icacls $secretPath /inheritance:r /grant:r "*$($currentSid):F" /C /Q | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'DPAPI credential was written, but its file ACL could not be restricted. Re-run the installer and verify folder ownership.'
}
Write-Host 'Segredo guardado com DPAPI para este usuário Windows.'
