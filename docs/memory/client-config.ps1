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

Write-Host @"

# ============================================================
# ChunkHound Memory - client configs
# URL: $Url
# ============================================================

## Generic (Cursor, VS Code, Grok Build, many others)

``````json
{
  "mcpServers": {
    "chunkhound-memory": {
      "url": "$Url",
      "headers": {
        "Authorization": "Bearer $Token"
      }
    }
  }
}
``````

Some clients want an explicit transport type:

``````json
{
  "mcpServers": {
    "chunkhound-memory": {
      "type": "http",
      "url": "$Url",
      "headers": {
        "Authorization": "Bearer $Token"
      }
    }
  }
}
``````

## Claude Code (CLI)

``````powershell
claude mcp add --transport http -s user chunkhound-memory ``
  $Url ``
  --header "Authorization: Bearer $Token"
``````

## Claude Desktop / Cowork (UI)

1. Settings -> Integrations / MCP / Connectors -> Add custom
2. Name: chunkhound-memory
3. URL: $Url
4. Header Authorization: Bearer $Token
   (or X-ChunkHound-Token: $Token)

## Health check

``````powershell
Invoke-RestMethod "http://${displayHost}:${Port}/health"
``````

## Manual steps you still do

- Paste the JSON into each harness MCP settings (or run the Claude Code command).
- On other machines, use this host's LAN IP, not 127.0.0.1.
- Open firewall TCP $Port if clients are remote.
- Do not run 'chunkhound memory mcp' against the same dir while serve is up.

Full guide: docs/memory-setup.md
"@
