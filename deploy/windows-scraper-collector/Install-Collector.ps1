param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
)

$ErrorActionPreference = 'Stop'
$RepositoryRoot = (Resolve-Path $RepositoryRoot).Path
$Backend = Join-Path $RepositoryRoot 'backend'
$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
$StateDir = Join-Path $StateRoot 'state'
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Python Launcher não encontrado. Instale Python 3.12 e execute novamente.'
}
py -3.12 --version
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 é obrigatório.' }

$env:POETRY_VIRTUALENVS_IN_PROJECT = 'true'
py -3.12 -m pip install --upgrade poetry
if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar Poetry.' }
Push-Location $Backend
try {
    poetry install --only main --no-interaction
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar dependências do coletor.' }
} finally { Pop-Location }

$settingsPath = Join-Path $StateRoot 'settings.json'
if (-not (Test-Path $settingsPath)) {
    $settings = [ordered]@{
        repository_root = $RepositoryRoot
        api_url = 'https://precobao.app/backend/api/v1/internal/scraper-batches'
        api_ready = $false
        collector_enabled = $false
        collection_environment_authorized = $true
        state_dir = $StateDir
        stop_file = (Join-Path $StateRoot 'stop.requested')
        log_file = (Join-Path $StateRoot 'collector.log')
        timezone = 'America/Sao_Paulo'
        schedule_times = @('08:00', '20:00')
        missed_run_grace_minutes = 120
        sources = @{
            araujo = @{ mode = 'DISABLED'; access_confirmed = $false }
            pacheco = @{ mode = 'DISABLED'; access_confirmed = $false }
        }
    }
    $settings | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 $settingsPath
}

$acl = Get-Acl $StateRoot
$acl.SetAccessRuleProtection($true, $false)
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
    'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'
)
$acl.SetAccessRule($rule)
Set-Acl -Path $StateRoot -AclObject $acl
Write-Host "Instalado. Configuração: $settingsPath"
Write-Host 'O coletor e o agendamento continuam desativados. Configure a API, o segredo DPAPI e as fontes antes de executar.'
