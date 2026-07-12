# Print MCP client config snippets for ChunkHound Memory.
# Usage:
#   .\client-config.ps1 [-HostName HOST] [-Port PORT] [-Token TOKEN] [-Url URL]
param(
    [string]$HostName = $env:CHUNKHOUND_MEMORY_PUBLIC_HOST,
    [string]$Port = $(if ($env:CHUNKHOUND_MEMORY_PORT) { $env:CHUNKHOUND_MEMORY_PORT } else { "8765" }),
    [string]$Token = $env:CHUNKHOUND_MEMORY_TOKEN,
    [string]$Url = ""
)

if (-not $Token) {
    $Token = Read-Host "Bearer token"
}
if (-not $Token) {
    Write-Error "Token is required."
    exit 1
}

if (-not $Url) {
    if (-not $HostName) {
        $HostName = Read-Host "Public host/IP clients will use [127.0.0.1]"
        if (-not $HostName) { $HostName = "127.0.0.1" }
    }
    $Url = "http://${HostName}:${Port}/mcp"
}

$displayHost = if ($HostName) { $HostName } else { "127.0.0.1" }

# Escape JSON properly via Python
$env:_CC_URL = $Url
$env:_CC_TOKEN = $Token
$env:_CC_HOST = $displayHost
$env:_CC_PORT = $Port
try {
    python -c @"
import json, os
url = os.environ['_CC_URL']
token = os.environ['_CC_TOKEN']
host = os.environ['_CC_HOST']
port = os.environ['_CC_PORT']
cfg = {
    'mcpServers': {
        'chunkhound-memory': {
            'url': url,
            'headers': {'Authorization': 'Bearer ' + token},
        }
    }
}
cfg_typed = {
    'mcpServers': {
        'chunkhound-memory': {
            'type': 'http',
            'url': url,
            'headers': {'Authorization': 'Bearer ' + token},
        }
    }
}
print('# ============================================================')
print('# ChunkHound Memory - client configs')
print('# URL:', url)
print('# ============================================================')
print()
print('## Generic (Cursor, VS Code, Grok Build, many others)')
print()
print('```json')
print(json.dumps(cfg, indent=2))
print('```')
print()
print('Some clients want an explicit transport type:')
print()
print('```json')
print(json.dumps(cfg_typed, indent=2))
print('```')
print()
print('## Claude Code (CLI)')
print()
print('```powershell')
print('claude mcp add --transport http -s user chunkhound-memory ``')
# PowerShell: avoid embedding token in a fragile string; show pattern
print(f'  \"{url}\" ``')
print(f'  --header \"Authorization: Bearer <paste-token>\"')
print('```')
print()
print('(Use the same token as CHUNKHOUND_MEMORY_TOKEN / setup output.)')
print()
print('## Claude Desktop / Cowork (UI)')
print()
print('1. Settings -> Integrations / MCP / Connectors -> Add custom')
print('2. Name: chunkhound-memory')
print('3. URL:', url)
print('4. Header Authorization: Bearer <token>')
print('   (or X-ChunkHound-Token: <token>)')
print()
print('## Health check')
print()
print('```powershell')
print(f'Invoke-RestMethod \"http://{host}:{port}/health\"')
print('```')
print()
print('## Manual steps you still do')
print()
print('- Paste the JSON into each harness MCP settings (or run the Claude Code command).')
print('- On other machines, use this host LAN IP, not 127.0.0.1.')
print(f'- Open firewall TCP {port} if clients are remote.')
print('- Do not run chunkhound memory mcp against the same dir while serve is up.')
print()
print('Full guide: docs/memory-setup.md')
"@
    if ($LASTEXITCODE -ne 0) {
        throw "client-config generation failed (exit $LASTEXITCODE)"
    }
} finally {
    Remove-Item Env:_CC_URL -ErrorAction SilentlyContinue
    Remove-Item Env:_CC_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:_CC_HOST -ErrorAction SilentlyContinue
    Remove-Item Env:_CC_PORT -ErrorAction SilentlyContinue
}
