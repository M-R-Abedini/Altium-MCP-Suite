param([string]$DataDir)

# The unconfigured status server uses only Windows PowerShell. It makes setup
# discoverable before Python dependencies exist; ordinary launch never installs.
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$pluginRoot = Split-Path -Parent $PSScriptRoot

if (-not $DataDir) {
    if ($env:ALTIUM_SUITE_PLUGIN_DATA) {
        $DataDir = $env:ALTIUM_SUITE_PLUGIN_DATA
    } else {
        $codexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
        $DataDir = Join-Path $codexRoot 'plugins\data\mcp-altium-suite'
    }
}

$setupIssue = 'Plugin runtime has not been configured. Run the bundled setup skill.'
$statePath = Join-Path $DataDir 'state.json'
if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    try {
        $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (($state.schema -ne 1) -or (-not [IO.Path]::IsPathRooted([string]$state.python))) {
            throw 'Invalid plugin state. Run doctor or restore the configuration backup.'
        }
        if (-not (Test-Path -LiteralPath $state.python -PathType Leaf)) {
            throw 'Configured Python interpreter is missing. Run the bundled setup skill.'
        }
        $env:PYTHONIOENCODING = 'utf-8'
        $env:PYTHONUTF8 = '1'
        $env:PYTHONDONTWRITEBYTECODE = '1'
        # No pipeline or string-built command: JSON protocol/stdin are relayed to
        # Python, and errors remain on stderr. The Python process drains workers.
        $ErrorActionPreference = 'Continue'
        & ([string]$state.python) -B -u (Join-Path $PSScriptRoot 'plugin_runtime.py') --data-dir $DataDir --plugin-root $pluginRoot serve
        exit $LASTEXITCODE
    } catch {
        $setupIssue = $_.Exception.Message
    }
}

$statusTool = @{
    name = 'suite_status'
    description = 'Report missing plugin setup. Run the bundled altium-suite-setup skill to configure the local runtime.'
    inputSchema = @{ type = 'object'; properties = @{}; additionalProperties = $false }
    annotations = @{ readOnlyHint = $true; destructiveHint = $false; openWorldHint = $false }
}

while ($null -ne ($line = [Console]::ReadLine())) {
    if (-not $line.Trim()) { continue }
    $request = $null
    try {
        $request = $line | ConvertFrom-Json -ErrorAction Stop
        if ($null -eq $request -or $request.jsonrpc -ne '2.0' -or -not $request.method) {
            throw 'Invalid JSON-RPC request'
        }
        # Notifications have no id and must never receive a response.
        if (-not ($request.PSObject.Properties.Name -contains 'id')) { continue }
        $response = @{ jsonrpc = '2.0'; id = $request.id }
        switch ([string]$request.method) {
            'initialize' {
                $supported = @('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25')
                $protocol = if ($supported -contains $request.params.protocolVersion) { $request.params.protocolVersion } else { '2025-11-25' }
                $response.result = @{
                    protocolVersion = $protocol
                    capabilities = @{ tools = @{} }
                    serverInfo = @{ name = 'mcp altium suite'; version = '0.3.0' }
                    instructions = 'Runtime setup is incomplete. Use the bundled altium-suite-setup skill; this status server does not inspect Altium or designs.'
                }
            }
            'ping' { $response.result = @{} }
            'tools/list' { $response.result = @{ tools = @($statusTool) } }
            'tools/call' {
                if ($request.params.name -ne 'suite_status') {
                    $response.result = @{ isError = $true; content = @(@{ type = 'text'; text = ('Unknown setup tool: ' + $request.params.name) }) }
                } else {
                    $value = @{
                        server = 'mcp altium suite'; configured = $false; ready = $false
                        setup_required = $true; issue = $setupIssue
                        setup_skill = 'altium-suite-setup'
                        setup_script = (Join-Path $PSScriptRoot 'plugin_runtime.py')
                        data_directory = $DataDir; native_editor_probed = $false
                    }
                    $response.result = @{ isError = $false; structuredContent = $value; content = @(@{ type = 'text'; text = ($value | ConvertTo-Json -Depth 10 -Compress) }) }
                }
            }
            default { $response.error = @{ code = -32601; message = 'Method not found' } }
        }
    } catch {
        $response = @{ jsonrpc = '2.0'; id = $null; error = @{ code = -32700; message = 'Invalid JSON-RPC input' } }
    }
    [Console]::WriteLine(($response | ConvertTo-Json -Depth 30 -Compress))
}
