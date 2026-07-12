# Internal self-test for memory setup helpers. Run with:
#   powershell -ExecutionPolicy Bypass -File docs/memory/_selftest.ps1
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir "_lib.ps1")

$tmp = Join-Path $env:TEMP ("chmem-" + [guid]::NewGuid().ToString("n"))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    $envFile = Join-Path $tmp "memory-serve.env"
    @(
        "CHUNKHOUND_MEMORY_TOKEN=abc123"
        "PATH=C:\evil"
        "CHUNKHOUND_MEMORY_PORT=8765"
    ) | Set-Content -Path $envFile -Encoding ascii

    $vals = Read-MemoryEnvFile -Path $envFile
    if ($vals["CHUNKHOUND_MEMORY_TOKEN"] -ne "abc123") { throw "token parse fail" }
    if ($vals.ContainsKey("PATH")) { throw "PATH should be ignored" }
    if ($vals["CHUNKHOUND_MEMORY_PORT"] -ne "8765") { throw "port parse fail" }
    Write-Host "PASS: env parser allowlist"

    $out = Join-Path $tmp "out.env"
    Write-MemoryEnvFile -Path $out -Values @{
        CHUNKHOUND_MEMORY_DIR         = "D:\data\mem"
        CHUNKHOUND_MEMORY_TOKEN       = "deadbeef"
        CHUNKHOUND_MEMORY_HOST        = "0.0.0.0"
        CHUNKHOUND_MEMORY_PORT        = "9000"
        CHUNKHOUND_MEMORY_PUBLIC_HOST = "127.0.0.1"
    }
    $round = Read-MemoryEnvFile -Path $out
    if ($round["CHUNKHOUND_MEMORY_PORT"] -ne "9000") { throw "roundtrip port fail" }
    Write-Host "PASS: env write/read"

    if (-not (Test-MemoryPort -Port "8765")) { throw "port valid fail" }
    if (Test-MemoryPort -Port "99999") { throw "port invalid fail" }
    Write-Host "PASS: port validation"

    Write-Host "All self-tests passed."
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
