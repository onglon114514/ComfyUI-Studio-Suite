param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("koboldcpp", "llama_cpp_server")]
    [string]$Provider,

    [Parameter(Mandatory = $true)]
    [string]$SourcePath,

    [string]$BaseUrl = "http://127.0.0.1:5001",

    [switch]$SkipCopy
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $scriptDir
$configPath = Join-Path $projectDir "config\task_agent_config.local.json"

if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Config file not found: $configPath"
}

function Resolve-TargetDir {
    param([string]$ProviderName)
    if ($ProviderName -eq "koboldcpp") {
        return Join-Path $projectDir "runtime\koboldcpp"
    }
    return Join-Path $projectDir "runtime\llama.cpp"
}

function Resolve-ExeRelativePath {
    param([string]$ProviderName)
    if ($ProviderName -eq "koboldcpp") {
        return "runtime/koboldcpp/koboldcpp.exe"
    }
    return "runtime/llama.cpp/llama-server.exe"
}

function Resolve-HealthPath {
    param([string]$ProviderName)
    if ($ProviderName -eq "koboldcpp") {
        return "/api/extra/version"
    }
    return "/health"
}

$targetDir = Resolve-TargetDir -ProviderName $Provider
$exeRelativePath = Resolve-ExeRelativePath -ProviderName $Provider
$healthPath = Resolve-HealthPath -ProviderName $Provider

New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

if (-not $SkipCopy) {
    if (-not (Test-Path -LiteralPath $SourcePath)) {
        throw "Source path not found: $SourcePath"
    }

    $sourceItem = Get-Item -LiteralPath $SourcePath
    if ($sourceItem.PSIsContainer) {
        Get-ChildItem -LiteralPath $SourcePath -Force | ForEach-Object {
            $destination = Join-Path $targetDir $_.Name
            if ($_.PSIsContainer) {
                Copy-Item -LiteralPath $_.FullName -Destination $destination -Recurse -Force
            } else {
                Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
            }
        }
    } else {
        Copy-Item -LiteralPath $sourceItem.FullName -Destination (Join-Path $targetDir $sourceItem.Name) -Force
    }
}

$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json

if (-not $config.backend) {
    $config | Add-Member -NotePropertyName backend -NotePropertyValue ([pscustomobject]@{})
}

$config.backend.mode = "managed_process"
$config.backend.provider = $Provider
$config.backend.base_url = $BaseUrl
$config.backend.health_path = $healthPath

if ($Provider -eq "koboldcpp") {
    $config.backend.koboldcpp_exe = $exeRelativePath
} else {
    $config.backend.llama_cpp_server_exe = $exeRelativePath
}

$config | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $configPath -Encoding UTF8

Write-Host "Managed backend configured."
Write-Host "Provider: $Provider"
Write-Host "Runtime dir: $targetDir"
Write-Host "Config updated: $configPath"
