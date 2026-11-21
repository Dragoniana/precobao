param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
)

$ErrorActionPreference = 'Stop'
$RepositoryRoot = [IO.Path]::GetFullPath((Resolve-Path $RepositoryRoot).Path)
$CollectorRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'))
$TargetState = [IO.Path]::GetFullPath((Join-Path $CollectorRoot 'state'))
$SettingsPath = Join-Path $CollectorRoot 'settings.json'
$DirectorySeparator = [IO.Path]::DirectorySeparatorChar

function Test-PathWithin {
    param([string]$Path, [string]$Root)
    $resolvedPath = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $resolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    if ($resolvedPath.Equals($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    return $resolvedPath.StartsWith(
        $resolvedRoot + $DirectorySeparator,
        [StringComparison]::OrdinalIgnoreCase
    )
}

function Get-ArchiveDestination {
    param([string]$RelativePath)
    $archiveRoot = Join-Path $TargetState 'migration-archive'
    $candidate = Join-Path $archiveRoot $RelativePath
    if (Test-Path -LiteralPath $candidate) {
        $suffix = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
        $candidate = "$candidate-$suffix"
    }
    $resolved = [IO.Path]::GetFullPath($candidate)
    if (-not (Test-PathWithin -Path $resolved -Root $TargetState)) {
        throw 'Archive path escaped the collector state directory.'
    }
    return $resolved
}

function Copy-FileWithoutOverwrite {
    param([string]$Source, [string]$Destination)
    $sourcePath = [IO.Path]::GetFullPath($Source)
    $destinationPath = [IO.Path]::GetFullPath($Destination)
    if (-not (Test-PathWithin -Path $destinationPath -Root $TargetState)) {
        throw 'Migration destination escaped the collector state directory.'
    }
    if (Test-Path -LiteralPath $destinationPath) {
        $sourceHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
        $destinationHash = (Get-FileHash -LiteralPath $destinationPath -Algorithm SHA256).Hash
        if ($sourceHash -ne $destinationHash) {
            throw "State migration conflict; existing destination was preserved: $destinationPath"
        }
        return
    }

    $parent = Split-Path -Parent $destinationPath
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temporary = Join-Path $parent ('.migration-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        Copy-Item -LiteralPath $sourcePath -Destination $temporary
        $sourceHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
        $temporaryHash = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash
        if ($sourceHash -ne $temporaryHash) {
            throw 'Copied state file failed SHA-256 verification.'
        }
        Move-Item -LiteralPath $temporary -Destination $destinationPath
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Copy-TreeWithoutOverwrite {
    param([string]$Source, [string]$Destination)
    $sourceRoot = [IO.Path]::GetFullPath($Source).TrimEnd('\', '/')
    $destinationRoot = [IO.Path]::GetFullPath($Destination).TrimEnd('\', '/')
    if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) { return }
    if (-not (Test-PathWithin -Path $destinationRoot -Root $TargetState)) {
        throw 'Migration directory escaped the collector state directory.'
    }
    if ($sourceRoot.Equals($destinationRoot, [StringComparison]::OrdinalIgnoreCase)) {
        return
    }

    foreach ($file in Get-ChildItem -LiteralPath $sourceRoot -File -Recurse -Force) {
        $relative = $file.FullName.Substring($sourceRoot.Length).TrimStart('\', '/')
        $destination = Join-Path $destinationRoot $relative
        Copy-FileWithoutOverwrite -Source $file.FullName -Destination $destination
    }
}

function Move-VerifiedSourceToArchive {
    param([string]$Source, [string]$RelativeArchivePath)
    if (-not (Test-Path -LiteralPath $Source)) { return }
    $sourcePath = [IO.Path]::GetFullPath($Source)
    $archivePath = Get-ArchiveDestination -RelativePath $RelativeArchivePath
    $archiveParent = Split-Path -Parent $archivePath
    New-Item -ItemType Directory -Force -Path $archiveParent | Out-Null
    Move-Item -LiteralPath $sourcePath -Destination $archivePath
}

New-Item -ItemType Directory -Force -Path $TargetState | Out-Null
$configuredState = $TargetState
$configuredLog = Join-Path $TargetState 'logs\collector.log'
$configuredStop = Join-Path $TargetState 'stop.requested'
$legacyTrees = [System.Collections.Generic.List[object]]::new()
$archiveOnlyTrees = [System.Collections.Generic.List[object]]::new()
$legacyFiles = [System.Collections.Generic.List[object]]::new()

if (Test-Path -LiteralPath $SettingsPath) {
    $settings = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
    if ($settings.repository_root) {
        $configuredRepository = [IO.Path]::GetFullPath([string]$settings.repository_root).TrimEnd('\', '/')
        $requestedRepository = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\', '/')
        if (-not $configuredRepository.Equals($requestedRepository, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'State belongs to another collector installation; migration stopped before moving files.'
        }
    }
    if ($settings.state_dir) {
        $candidateState = [IO.Path]::GetFullPath([string]$settings.state_dir)
        if (-not $candidateState.Equals($TargetState, [StringComparison]::OrdinalIgnoreCase)) {
            if (-not (Test-PathWithin -Path $candidateState -Root $CollectorRoot)) {
                throw 'Configured legacy state is outside PrecoBaoCollector and needs manual review.'
            }
            if (Test-Path -LiteralPath $candidateState -PathType Container) {
                $legacyTrees.Add([pscustomobject]@{
                    Source = $candidateState
                    Destination = $TargetState
                    Archive = "previous-state\$([IO.Path]::GetFileName($candidateState))"
                })
            }
        }
    }

    if ($settings.log_file -and (Test-Path -LiteralPath ([string]$settings.log_file) -PathType Leaf)) {
        $legacyLog = [IO.Path]::GetFullPath([string]$settings.log_file)
        if (-not (Test-PathWithin -Path $legacyLog -Root $TargetState)) {
            if (-not (Test-PathWithin -Path $legacyLog -Root $CollectorRoot)) {
                throw 'Configured legacy log is outside PrecoBaoCollector and needs manual review.'
            }
            $configuredLog = Join-Path (Join-Path $TargetState 'logs') ([IO.Path]::GetFileName($legacyLog))
            $legacyFiles.Add([pscustomobject]@{
                Source = $legacyLog
                Destination = $configuredLog
                Archive = "external-files\$([IO.Path]::GetFileName($legacyLog))"
            })
        } else {
            $configuredLog = $legacyLog
        }
    }

    if ($settings.stop_file -and (Test-Path -LiteralPath ([string]$settings.stop_file) -PathType Leaf)) {
        $legacyStop = [IO.Path]::GetFullPath([string]$settings.stop_file)
        if (-not (Test-PathWithin -Path $legacyStop -Root $TargetState)) {
            if (-not (Test-PathWithin -Path $legacyStop -Root $CollectorRoot)) {
                throw 'Configured stop marker is outside PrecoBaoCollector and needs manual review.'
            }
            $legacyFiles.Add([pscustomobject]@{
                Source = $legacyStop
                Destination = $configuredStop
                Archive = "external-files\$([IO.Path]::GetFileName($legacyStop))"
            })
        }
    }
}

$legacyLock = Join-Path $CollectorRoot 'collector.running.lock'
if (Test-Path -LiteralPath $legacyLock -PathType Leaf) {
    $lockProbe = $null
    try {
        $lockProbe = [IO.File]::Open($legacyLock, 'OpenOrCreate', 'ReadWrite', 'None')
    } catch {
        throw 'A collector is still running; state migration was stopped without moving files.'
    } finally {
        if ($lockProbe) { $lockProbe.Dispose() }
    }
    $legacyFiles.Add([pscustomobject]@{
        Source = [IO.Path]::GetFullPath($legacyLock)
        Destination = Join-Path (Join-Path $TargetState 'locks') 'collector.running.lock'
        Archive = 'external-files\collector.running.lock'
    })
}

foreach ($name in @('araujo', 'pacheco')) {
    $legacyState = Join-Path $TargetState $name
    if (Test-Path -LiteralPath $legacyState -PathType Container) {
        $archiveOnlyTrees.Add([pscustomobject]@{
            Source = [IO.Path]::GetFullPath($legacyState)
            Archive = "legacy-source-state\$name"
        })
    }
    $legacySource = Join-Path $RepositoryRoot "backend\data\$name"
    if (Test-Path -LiteralPath $legacySource -PathType Container) {
        $legacyTrees.Add([pscustomobject]@{
            Source = [IO.Path]::GetFullPath($legacySource)
            Destination = Join-Path (Join-Path $TargetState 'sources') $name
            Archive = "backend-data\$name"
        })
    }
}

# Copy and hash-check every file before moving any source directory.
foreach ($tree in $legacyTrees) {
    Copy-TreeWithoutOverwrite -Source $tree.Source -Destination $tree.Destination
}
foreach ($file in $legacyFiles) {
    Copy-FileWithoutOverwrite -Source $file.Source -Destination $file.Destination
}

# Previous source checkpoints can be materially larger and must never be
# overwritten by a newer limited pilot checkpoint. Keep them as dated archives.
foreach ($tree in $archiveOnlyTrees) {
    Move-VerifiedSourceToArchive -Source $tree.Source -RelativeArchivePath $tree.Archive
}

# Archives stay under state/ and preserve the exact legacy bytes.
foreach ($tree in $legacyTrees) {
    Move-VerifiedSourceToArchive -Source $tree.Source -RelativeArchivePath $tree.Archive
}
foreach ($file in $legacyFiles) {
    Move-VerifiedSourceToArchive -Source $file.Source -RelativeArchivePath $file.Archive
}

if (Test-Path -LiteralPath $SettingsPath) {
    $settings = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
    $settings.repository_root = $RepositoryRoot
    $settings.state_dir = $TargetState
    $settings.stop_file = $configuredStop
    $settings.log_file = $configuredLog
    $settings | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $SettingsPath -Encoding utf8
}

Write-Host "Collector state uses $TargetState"
