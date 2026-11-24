# Shared UTF-8 helpers compatible with Windows PowerShell 5.1 and PowerShell 7.
$script:CollectorUtf8 = [System.Text.UTF8Encoding]::new($false)

function Read-CollectorUtf8 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $reader = New-Object System.IO.StreamReader($Path, [System.Text.Encoding]::UTF8, $true)
    try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
}

function Read-CollectorJson {
    param([Parameter(Mandatory = $true)][string]$Path)
    $text = Read-CollectorUtf8 -Path $Path
    if ([string]::IsNullOrWhiteSpace($text)) { throw "JSON vazio: $Path" }
    return ($text | ConvertFrom-Json -ErrorAction Stop)
}

function Write-CollectorUtf8 {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text
    )
    $parent = [System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($Path))
    if (-not [System.IO.Directory]::Exists($parent)) {
        [void][System.IO.Directory]::CreateDirectory($parent)
    }
    [System.IO.File]::WriteAllText($Path, $Text, $script:CollectorUtf8)
}

function Write-CollectorJson {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value,
        [int]$Depth = 12
    )
    $json = ConvertTo-Json -InputObject $Value -Depth $Depth
    Write-CollectorUtf8 -Path $Path -Text ($json + [Environment]::NewLine)
}

function Add-CollectorUtf8Line {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text
    )
    $parent = [System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($Path))
    if (-not [System.IO.Directory]::Exists($parent)) {
        [void][System.IO.Directory]::CreateDirectory($parent)
    }
    [System.IO.File]::AppendAllText($Path, $Text + [Environment]::NewLine, $script:CollectorUtf8)
}

function Write-CollectorLog {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Event
    )
    Add-CollectorUtf8Line -Path $Path -Text ("{0} {1}" -f (Get-Date -Format o), $Event)
}
