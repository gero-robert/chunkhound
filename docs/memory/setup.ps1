# Interactive setup for ChunkHound Memory (init + embeddings + env + client configs).
# Usage: .\setup.ps1
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir "_lib.ps1")

$RepoRoot = Resolve-ChunkHoundRepoRoot -ScriptDir $ScriptDir

function Prompt-Value([string]$Message, [string]$Default = "") {
    if ($Default) {
        $v = Read-Host "$Message [$Default]"
        if ($v) { return $v }
        return $Default
    }
    return Read-Host $Message
}

function Prompt-YesNo([string]$Message, [string]$Default = "y") {
    $v = Read-Host "$Message [y/n] (default $Default)"
    if (-not $v) { $v = $Default }
    return $v -match '^[Yy]'
}

Write-Host "============================================================"
Write-Host " ChunkHound Memory - interactive setup"
Write-Host "============================================================"
Write-Host "Repo: $RepoRoot"
Write-Host ""

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "'uv' not found. Install from https://docs.astral.sh/uv/ then re-run."
    exit 1
}

$defaultDir = Join-Path $HOME ".chunkhound-memory"
$MemoryDir = Prompt-Value "Memory directory" $defaultDir
$MemoryDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($MemoryDir)
New-Item -ItemType Directory -Force -Path $MemoryDir | Out-Null

Write-Host ""
Write-Host "-> Initializing memory directory..."
Set-Location $RepoRoot
uv run chunkhound memory init --dir $MemoryDir
if ($LASTEXITCODE -ne 0) {
    throw "memory init failed (exit $LASTEXITCODE)"
}

$Config = Join-Path $MemoryDir ".chunkhound.json"
if (-not (Test-Path $Config)) {
    Write-Error "Expected $Config after init."
    exit 1
}

Write-Host ""
Write-Host "Embedding provider (required for semantic recall)"
Write-Host "  1) openai"
Write-Host "  2) voyageai"
Write-Host "  3) skip (configure .chunkhound.json manually later)"
$choice = Prompt-Value "Choice" "1"
$provider = switch ($choice) {
    "1" { "openai" }
    "openai" { "openai" }
    "2" { "voyageai" }
    "voyageai" { "voyageai" }
    "3" { "" }
    "skip" { "" }
    default { $choice }
}

if ($provider) {
    $modelDefault = if ($provider -eq "voyageai") { "voyage-3" } else { "text-embedding-3-small" }
    $model = Prompt-Value "Embedding model" $modelDefault
    $secure = Read-Host "API key for $provider" -AsSecureString
    $BSTR = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $apiKey = [Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR)
    }
    if ($apiKey) {
        # Python merge avoids ConvertTo-Json BOM / array mangling; key via env not argv
        Merge-EmbeddingConfig -ConfigPath $Config -Provider $provider -ApiKey $apiKey -Model $model
    } else {
        Write-Host "No API key entered - leaving embedding config unchanged."
    }
} else {
    Write-Host ""
    Write-Host "MANUAL: edit $Config and add an 'embedding' block, then re-index."
}

Write-Host ""
$dbPath = Join-Path $MemoryDir ".chunkhound\db"
if (Prompt-YesNo "Re-index memory directory with embeddings now?" "y") {
    Write-Host "-> Indexing (may take a minute)..."
    uv run chunkhound index $MemoryDir --config $Config --db $dbPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Index failed (exit $LASTEXITCODE). Fix embeddings/config and run:"
        Write-Host "  uv run chunkhound index `"$MemoryDir`" --config `"$Config`" --db `"$dbPath`""
    }
} else {
    Write-Host "MANUAL later:"
    Write-Host "  uv run chunkhound index `"$MemoryDir`" --config `"$Config`" --db `"$dbPath`""
}

Write-Host ""
Write-Host "LAN serve settings"
$HostName = Prompt-Value "Bind host (0.0.0.0 = all interfaces)" "0.0.0.0"
$Port = Prompt-Value "Port" "8765"
if (-not (Test-MemoryPort -Port $Port)) {
    throw "Invalid port '$Port' (need 1-65535)"
}

$envFile = Join-Path $MemoryDir "memory-serve.env"
$existingToken = ""
if (Test-Path $envFile) {
    $existing = Read-MemoryEnvFile -Path $envFile
    if ($existing.ContainsKey("CHUNKHOUND_MEMORY_TOKEN")) {
        $existingToken = $existing["CHUNKHOUND_MEMORY_TOKEN"]
    }
}
if (-not $existingToken -and $env:CHUNKHOUND_MEMORY_TOKEN) {
    $existingToken = $env:CHUNKHOUND_MEMORY_TOKEN
}

if ($existingToken) {
    Write-Host "Found existing token in env file or environment."
    if (Prompt-YesNo "Reuse existing token? (n = generate/rotate - breaks existing clients)" "y") {
        $Token = $existingToken
        Write-Host "Reusing existing token."
    } else {
        Write-Host "WARNING: Rotating the token invalidates every harness config using the old one."
        if (Prompt-YesNo "Generate a new random token?" "y") {
            $bytes = New-Object byte[] 32
            [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
            $Token = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
            Write-Host "Generated token (store safely; shown once here):"
            Write-Host "  $Token"
        } else {
            $secureTok = Read-Host "Enter new token" -AsSecureString
            $BSTR2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureTok)
            try {
                $Token = [Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR2)
            } finally {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR2)
            }
        }
    }
} elseif ($env:CHUNKHOUND_MEMORY_TOKEN) {
    $Token = $env:CHUNKHOUND_MEMORY_TOKEN
    Write-Host "Using CHUNKHOUND_MEMORY_TOKEN from environment."
} elseif (Prompt-YesNo "Generate a random API token?" "y") {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $Token = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
    Write-Host "Generated token (store safely; shown once here):"
    Write-Host "  $Token"
} else {
    $secureTok = Read-Host "Enter token" -AsSecureString
    $BSTR2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureTok)
    try {
        $Token = [Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR2)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR2)
    }
}

if (-not $Token) {
    throw "Empty token not allowed."
}

$PublicHost = Prompt-Value "Hostname/IP other machines should use (for printed configs)" "127.0.0.1"

$vals = @{
    CHUNKHOUND_MEMORY_DIR         = $MemoryDir
    CHUNKHOUND_MEMORY_TOKEN       = $Token
    CHUNKHOUND_MEMORY_HOST        = $HostName
    CHUNKHOUND_MEMORY_PORT        = $Port
    CHUNKHOUND_MEMORY_PUBLIC_HOST = $PublicHost
}
Write-MemoryEnvFile -Path $envFile -Values $vals
Write-Host "Wrote $envFile (ACL restricted when possible)"

Write-Host ""
Write-Host "-> Client configuration snippets:"
& (Join-Path $ScriptDir "client-config.ps1") -HostName $PublicHost -Port $Port -Token $Token

Write-Host ""
Write-Host "============================================================"
Write-Host " Next steps"
Write-Host "============================================================"
Write-Host "1. Start the server on this host:"
Write-Host "     $($ScriptDir)\serve.ps1 -Dir `"$MemoryDir`""
Write-Host ""
Write-Host "2. MANUAL - open Windows Firewall for TCP $Port if other PCs connect:"
Write-Host "     New-NetFirewallRule -DisplayName 'ChunkHound Memory' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow"
Write-Host ""
Write-Host "3. MANUAL - paste client JSON into each harness (Claude Code, Cursor,"
Write-Host "   Grok Build, etc.) using the snippets printed above."
Write-Host ""
Write-Host "4. Do NOT also run 'chunkhound memory mcp' on the same directory while"
Write-Host "   serve is running. All clients (including this PC) use HTTP."
Write-Host ""
Write-Host "5. Optional LLM for better memory_research: edit $Config and add llm."
Write-Host ""

if (Prompt-YesNo "Start memory serve now?" "n") {
    & (Join-Path $ScriptDir "serve.ps1") -Dir $MemoryDir -HostName $HostName -Port $Port -Token $Token
} else {
    Write-Host "Done. Full guide: docs/memory-setup.md"
}
