# Start ChunkHound Memory serve (Streamable HTTP).
# Loads <memory-dir>\memory-serve.env if present (strict allowlist, never executed).
#
# Precedence: parameters > process environment > memory-serve.env > defaults
#
# Usage:
#   .\serve.ps1 [-Dir PATH] [-HostName HOST] [-Port PORT] [-Token TOKEN]
param(
    [string]$Dir = "",
    [string]$HostName = "",
    [string]$Port = "",
    [string]$Token = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir "_lib.ps1")

$RepoRoot = Resolve-ChunkHoundRepoRoot -ScriptDir $ScriptDir

# Snapshot process env
$procDir = $env:CHUNKHOUND_MEMORY_DIR
$procHost = $env:CHUNKHOUND_MEMORY_HOST
$procPort = $env:CHUNKHOUND_MEMORY_PORT
$procToken = $env:CHUNKHOUND_MEMORY_TOKEN

# Parameters override process env
if (-not $Dir) { $Dir = $procDir }
if (-not $HostName) { $HostName = $procHost }
if (-not $Port) { $Port = $procPort }
if (-not $Token) { $Token = $procToken }

if (-not $Dir) {
    $default = Join-Path $HOME ".chunkhound-memory"
    $inputDir = Read-Host "Memory directory [$default]"
    $Dir = if ($inputDir) { $inputDir } else { $default }
}
$Dir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Dir)

$envFile = Join-Path $Dir "memory-serve.env"
if (Test-Path $envFile) {
    Write-Host "Loading $envFile (strict allowlist parser)"
    $fileVals = Read-MemoryEnvFile -Path $envFile
    # CLI/param and process env already applied; file fills remaining gaps only
    # Re-apply with full precedence using whether param was passed is hard in PS;
    # Use: if param empty AND process empty, use file.
    if (-not $PSBoundParameters.ContainsKey("Dir") -or -not $PSBoundParameters["Dir"]) {
        if (-not $procDir -and $fileVals.ContainsKey("CHUNKHOUND_MEMORY_DIR")) {
            $Dir = $fileVals["CHUNKHOUND_MEMORY_DIR"]
            $Dir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Dir)
        }
    }
    if (-not $PSBoundParameters.ContainsKey("HostName") -or -not $PSBoundParameters["HostName"]) {
        if (-not $procHost -and $fileVals.ContainsKey("CHUNKHOUND_MEMORY_HOST")) {
            $HostName = $fileVals["CHUNKHOUND_MEMORY_HOST"]
        }
    }
    if (-not $PSBoundParameters.ContainsKey("Port") -or -not $PSBoundParameters["Port"]) {
        if (-not $procPort -and $fileVals.ContainsKey("CHUNKHOUND_MEMORY_PORT")) {
            $Port = $fileVals["CHUNKHOUND_MEMORY_PORT"]
        }
    }
    if (-not $PSBoundParameters.ContainsKey("Token") -or -not $PSBoundParameters["Token"]) {
        if (-not $procToken -and $fileVals.ContainsKey("CHUNKHOUND_MEMORY_TOKEN")) {
            $Token = $fileVals["CHUNKHOUND_MEMORY_TOKEN"]
        }
    }
}

if (-not $HostName) { $HostName = "0.0.0.0" }
if (-not $Port) { $Port = "8765" }
if (-not (Test-MemoryPort -Port $Port)) {
    throw "Invalid port '$Port' (need 1-65535)"
}

$configPath = Join-Path $Dir ".chunkhound.json"
if (-not (Test-Path $configPath)) {
    Write-Error "$Dir is not initialized. Run docs\memory\setup.ps1 first."
    exit 1
}

if (-not $Token) {
    Write-Error "No token. Set CHUNKHOUND_MEMORY_TOKEN, pass -Token, or run setup.ps1. Refusing to mint a random token (would not match existing clients)."
    exit 1
}

# Env only — avoid putting token on process argv
$env:CHUNKHOUND_MEMORY_DIR = $Dir
$env:CHUNKHOUND_MEMORY_TOKEN = $Token
$env:CHUNKHOUND_MEMORY_HOST = $HostName
$env:CHUNKHOUND_MEMORY_PORT = $Port

Set-Location $RepoRoot

Write-Host "Starting memory serve"
Write-Host "  dir:   $Dir"
Write-Host "  bind:  ${HostName}:${Port}"
Write-Host "  mcp:   http://<this-host-or-lan-ip>:${Port}/mcp"
Write-Host ""
Write-Host "Press Ctrl+C to stop."
Write-Host ""

uv run chunkhound memory serve --dir $Dir --host $HostName --port $Port
exit $LASTEXITCODE
