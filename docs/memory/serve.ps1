# Start ChunkHound Memory serve (Streamable HTTP).
# Loads <memory-dir>\memory-serve.env if present (from setup.ps1).
#
# Usage:
#   .\serve.ps1 [-Dir PATH] [-HostName HOST] [-Port PORT] [-Token TOKEN]
param(
    [string]$Dir = $env:CHUNKHOUND_MEMORY_DIR,
    [string]$HostName = $(if ($env:CHUNKHOUND_MEMORY_HOST) { $env:CHUNKHOUND_MEMORY_HOST } else { "0.0.0.0" }),
    [string]$Port = $(if ($env:CHUNKHOUND_MEMORY_PORT) { $env:CHUNKHOUND_MEMORY_PORT } else { "8765" }),
    [string]$Token = $env:CHUNKHOUND_MEMORY_TOKEN
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path

if (-not $Dir) {
    $default = Join-Path $HOME ".chunkhound-memory"
    $inputDir = Read-Host "Memory directory [$default]"
    $Dir = if ($inputDir) { $inputDir } else { $default }
}
$Dir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Dir)

$envFile = Join-Path $Dir "memory-serve.env"
if (Test-Path $envFile) {
    Write-Host "Loading $envFile"
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $parts = $line -split "=", 2
        if ($parts.Count -ne 2) { return }
        $k = $parts[0].Trim()
        $v = $parts[1].Trim().Trim('"').Trim("'")
        Set-Item -Path "Env:$k" -Value $v
    }
    if ($env:CHUNKHOUND_MEMORY_DIR) { $Dir = $env:CHUNKHOUND_MEMORY_DIR }
    if ($env:CHUNKHOUND_MEMORY_HOST) { $HostName = $env:CHUNKHOUND_MEMORY_HOST }
    if ($env:CHUNKHOUND_MEMORY_PORT) { $Port = $env:CHUNKHOUND_MEMORY_PORT }
    if ($env:CHUNKHOUND_MEMORY_TOKEN) { $Token = $env:CHUNKHOUND_MEMORY_TOKEN }
}

$configPath = Join-Path $Dir ".chunkhound.json"
if (-not (Test-Path $configPath)) {
    Write-Error "$Dir is not initialized. Run docs\memory\setup.ps1 first."
    exit 1
}

if (-not $Token) {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $Token = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
    Write-Host "Generated session token (save for clients):"
    Write-Host "  $Token"
}

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

uv run chunkhound memory serve `
    --dir $Dir `
    --host $HostName `
    --port $Port `
    --token $Token
