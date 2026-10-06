<#
.SYNOPSIS
    Establishes what Altium does with the mask-expansion cache state this server writes.

.DESCRIPTION
    The mask-expansion mode is Altium's TCacheState = (eCacheInvalid, eCacheValid,
    eCacheManual) — ordinals 0/1/2, from the Advpcb.dll RTTI. It records whether a
    *cached* expansion is authoritative, so a pad claiming eCacheValid asserts that its
    stored number is a rule result Altium should honour verbatim. Paired with a zero
    expansion that reads as "the rule resolved to zero", which would suppress the mask
    opening rather than defer to the rule.

    This drives the whole question end to end and repeatably:

      1. the MCP server writes a library whose pads carry each of the three states,
         including the suspect eCacheValid-with-zero combination;
      2. Altium opens it, reports the state it sees on every pad, and re-saves;
      3. the two files' states are compared, showing whether Altium accepts them as
         written or replaces them with its own.

    Pad 1 (none) and pad 2 (from_rule) differ ONLY in that byte, so any difference in
    what Altium reports or writes is attributable to the cache state alone. Pad 3
    (manual, 7 mil) is the control that proves the reporting path works at all.

    Both readings go through the server's own read_pcblib rather than hand-parsed
    record offsets, so the comparison uses the shipped code path.

.PARAMETER AltiumExe
    Path to X2.EXE. Defaults to the ALTIUM_EXE entry in .env.local at the repo root.

.PARAMETER WorkDir
    Where the libraries are written. Defaults to a temp directory.

.PARAMETER SkipBuild
    Reuse an existing release binary instead of rebuilding.

.PARAMETER KeepAltiumOpen
    Leave Altium running afterwards. By default it is closed once the response arrives.

.EXAMPLE
    .\Verify-MaskCacheState.ps1
#>
param(
    [string]$AltiumExe,
    [string]$WorkDir,
    [switch]$SkipBuild,
    [switch]$KeepAltiumOpen,
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir

if (-not $WorkDir) { $WorkDir = Join-Path $env:TEMP 'altium_mcp_maskcache' }
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$LibPath = Join-Path $WorkDir 'MaskCache.PcbLib'
# Must match the name AltiumMaskCache.pas derives for its re-saved copy.
$AltiumSavedPath = Join-Path $WorkDir 'MaskCache_Altium.PcbLib'
foreach ($stale in @($LibPath, $AltiumSavedPath)) {
    if (Test-Path $stale) { Remove-Item $stale -Force }
}

# ---------------------------------------------------------------------------
# 1. Build the server
# ---------------------------------------------------------------------------
$Exe = Join-Path $RepoRoot 'target\release\altium-designer-mcp.exe'
if (-not $SkipBuild) {
    Write-Host 'Build     : cargo build --release'
    Push-Location $RepoRoot
    try {
        # No 2>&1 here: redirecting a native command's stderr in Windows PowerShell
        # wraps each line in an ErrorRecord, so a clean build with warnings would
        # be treated as a failure.
        & cargo build --release --quiet
        if ($LASTEXITCODE -ne 0) { throw "cargo build failed ($LASTEXITCODE)" }
    } finally { Pop-Location }
}
if (-not (Test-Path $Exe)) { throw "Server binary not found: $Exe" }

# ---------------------------------------------------------------------------
# 2. Write the library through the MCP server
# ---------------------------------------------------------------------------
$ConfigPath = Join-Path $WorkDir 'config.json'
$config = @{
    allowed_paths = @($WorkDir -replace '\\', '/')
    logging       = @{ level = 'warn'; audit_log_path = $null }
} | ConvertTo-Json -Depth 5
# UTF8Encoding($false): Set-Content -Encoding utf8 emits a BOM, which the config
# parser rejects.
[System.IO.File]::WriteAllText($ConfigPath, $config, (New-Object System.Text.UTF8Encoding $false))

$requests = New-Object System.Collections.Generic.List[string]
function Add-Request([int]$Id, [string]$Method, $Params) {
    $msg = [ordered]@{ jsonrpc = '2.0'; method = $Method }
    if ($Id -ge 0) { $msg['id'] = $Id }
    if ($null -ne $Params) { $msg['params'] = $Params }
    $requests.Add(($msg | ConvertTo-Json -Depth 20 -Compress))
}

Add-Request 1 'initialize' @{
    protocolVersion = '2024-11-05'
    capabilities    = @{}
    clientInfo      = @{ name = 'verify-mask-cache'; version = '1.0.0' }
}
Add-Request -1 'notifications/initialized' $null

# Pads 1 and 2 differ only in the cache state; pad 3 is the manual control.
$pads = @(
    @{ designator = '1'; x = -1.0; y = 0; width = 0.6; height = 0.5
       solder_mask_expansion_mode = 'none' },
    @{ designator = '2'; x = 0.0; y = 0; width = 0.6; height = 0.5
       solder_mask_expansion_mode = 'from_rule' },
    @{ designator = '3'; x = 1.0; y = 0; width = 0.6; height = 0.5
       solder_mask_expansion_mode = 'manual'; solder_mask_expansion = 0.1778 }
)
Add-Request 2 'tools/call' @{
    name      = 'write_pcblib'
    arguments = @{ filepath = $LibPath; footprints = @(@{ name = 'MASKCACHE'; pads = $pads }) }
}

$RequestPath = Join-Path $WorkDir 'requests.jsonl'
[System.IO.File]::WriteAllLines($RequestPath, $requests, (New-Object System.Text.UTF8Encoding $false))

Write-Host 'MCP       : writing MaskCache.PcbLib'
$OutPath = Join-Path $WorkDir 'responses.jsonl'
$ErrPath = Join-Path $WorkDir 'server.log'
$proc = Start-Process -FilePath $Exe -ArgumentList "`"$ConfigPath`"" -NoNewWindow -PassThru `
    -RedirectStandardInput $RequestPath -RedirectStandardOutput $OutPath -RedirectStandardError $ErrPath
$proc | Wait-Process -Timeout 120

$responses = @(Get-Content -Path $OutPath -Encoding utf8 |
    Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
$write = $responses | Where-Object { $_.id -eq 2 } | Select-Object -First 1
if (-not $write) { throw "no write_pcblib response (see $ErrPath)" }
if ($write.PSObject.Properties.Name -contains 'error') { throw "write_pcblib failed: $($write.error.message)" }
if ($write.result.isError) { throw "write_pcblib reported an error: $($write.result.content[0].text)" }
if (-not (Test-Path $LibPath)) { throw "server reported success but $LibPath is missing" }

# ---------------------------------------------------------------------------
# 3. Read the states back through the server's own reader
# ---------------------------------------------------------------------------
# Reading through read_pcblib rather than hand-parsing the record offsets: it is the
# shipped code path, and its decoding of this tri-state is already pinned to
# Altium-authored bytes by the golden tests.
function Get-PadModes([string]$Path) {
    $reqs = New-Object System.Collections.Generic.List[string]
    $reqs.Add((@{ jsonrpc = '2.0'; id = 1; method = 'initialize'; params = @{
        protocolVersion = '2024-11-05'; capabilities = @{}
        clientInfo = @{ name = 'verify-mask-cache'; version = '1.0.0' } } } |
        ConvertTo-Json -Depth 20 -Compress))
    $reqs.Add((@{ jsonrpc = '2.0'; method = 'notifications/initialized' } |
        ConvertTo-Json -Depth 20 -Compress))
    $reqs.Add((@{ jsonrpc = '2.0'; id = 2; method = 'tools/call'; params = @{
        name = 'read_pcblib'; arguments = @{ filepath = $Path } } } |
        ConvertTo-Json -Depth 20 -Compress))

    $rp = Join-Path $WorkDir 'read_requests.jsonl'
    $op = Join-Path $WorkDir 'read_responses.jsonl'
    [System.IO.File]::WriteAllLines($rp, $reqs, (New-Object System.Text.UTF8Encoding $false))
    $pr = Start-Process -FilePath $Exe -ArgumentList "`"$ConfigPath`"" -NoNewWindow -PassThru `
        -RedirectStandardInput $rp -RedirectStandardOutput $op -RedirectStandardError $ErrPath
    $pr | Wait-Process -Timeout 120

    $rs = @(Get-Content -Path $op -Encoding utf8 |
        Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
    $hit = $rs | Where-Object { $_.id -eq 2 } | Select-Object -First 1
    if (-not $hit) { throw "no read_pcblib response for $Path (see $ErrPath)" }
    if ($hit.result.isError) { throw "read_pcblib failed on ${Path}: $($hit.result.content[0].text)" }
    $lib = $hit.result.content[0].text | ConvertFrom-Json
    @($lib.footprints | ForEach-Object { $_.pads } | ForEach-Object {
        [pscustomobject]@{
            designator = $_.designator
            solder     = $_.solder_mask_expansion_mode
            solder_mm  = $_.solder_mask_expansion
            paste      = $_.paste_mask_expansion_mode
        }
    })
}

$before = Get-PadModes $LibPath
Write-Host "`nWritten by the server:"
foreach ($p in $before) {
    Write-Host ("  pad {0}  solder={1} ({2} mm)  paste={3}" -f $p.designator, $p.solder, $p.solder_mm, $p.paste)
}

# 4. Hand it to Altium
# ---------------------------------------------------------------------------
$BridgeDir    = 'C:\Users\Public\altium_designer_mcp'
$RequestFile  = Join-Path $BridgeDir 'maskcache_request.txt'
$ResponseFile = Join-Path $BridgeDir 'maskcache_response.json'
$PrjScr       = Join-Path $ScriptDir 'altium\verify\AltiumMaskCache.PrjScr'
if (-not (Test-Path $PrjScr)) { throw "Script project not found: $PrjScr" }

. (Join-Path $ScriptDir 'Resolve-AltiumExe.ps1')
$AltiumExe = Resolve-AltiumExe -Override $AltiumExe -EnvFile (Join-Path $RepoRoot '.env.local')
Write-Host "`nAltium    : $AltiumExe"

New-Item -ItemType Directory -Force -Path $BridgeDir | Out-Null
[System.IO.File]::WriteAllLines($RequestFile, [string[]]@($LibPath))
if (Test-Path $ResponseFile) { Remove-Item $ResponseFile -Force }

# The `^|` separator RunScript expects survives most reliably through a .bat.
$bat = Join-Path $env:TEMP 'altium_designer_mcp_maskcache_launch.bat'
"`"$AltiumExe`" -RScriptingSystem:RunScript(ProjectName=`"$PrjScr`"^|ProcName=`"AltiumMaskCache>Run`")" |
    Set-Content -Path $bat -Encoding ASCII
Start-Process -FilePath $bat -WindowStyle Hidden

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while (-not (Test-Path $ResponseFile) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
if (-not (Test-Path $ResponseFile)) {
    throw "Timed out after $TimeoutSeconds s. Altium may be showing a modal dialog — check its window."
}

$raw = Get-Content $ResponseFile -Raw
$results = $raw | ConvertFrom-Json
if ($raw.TrimStart([char]0xFEFF, ' ', "`t", "`r", "`n").StartsWith('{')) {
    throw "Altium script error: $($results.error)"
}
$r = @($results)[0]
if (-not $r.opened) { throw "Altium could not open the library: $($r.detail)" }

Write-Host "`nAs Altium reports it (TCacheState ordinal: 0=eCacheInvalid, 1=eCacheValid, 2=eCacheManual):"
foreach ($p in @($r.primitives)) {
    Write-Host ("  pad {0}  solder_valid={1} solder_coord={2}  paste_valid={3} paste_coord={4}" -f `
        $p.designator, $p.solder_valid, $p.solder_coord, $p.paste_valid, $p.paste_coord)
}

# ---------------------------------------------------------------------------
# 5. Compare what Altium wrote back
# ---------------------------------------------------------------------------
$verdict = 'inconclusive'
if ($r.saved -and (Test-Path $AltiumSavedPath)) {
    $after = Get-PadModes $AltiumSavedPath
    Write-Host "`nRe-saved by Altium:"
    $changed = $false
    foreach ($p in $after) {
        $b = $before | Where-Object { $_.designator -eq $p.designator } | Select-Object -First 1
        $note = ''
        if ($b -and ($b.solder -ne $p.solder -or $b.paste -ne $p.paste)) {
            $note = ("   <- changed from solder={0} paste={1}" -f $b.solder, $b.paste)
            $changed = $true
        }
        Write-Host ("  pad {0}  solder={1} ({2} mm)  paste={3}{4}" -f `
            $p.designator, $p.solder, $p.solder_mm, $p.paste, $note)
    }
    if ($changed) {
        $verdict = 'Altium rewrote the cache states, so it does not take them at face value'
    } else {
        $verdict = 'Altium preserved every cache state, so the byte we write is the byte it carries forward'
    }
} else {
    Write-Host "`nAltium did not produce a re-saved copy ($($r.detail))." -ForegroundColor Yellow
}

Write-Host "`nVerdict   : $verdict" -ForegroundColor Cyan

if (-not $KeepAltiumOpen) {
    Get-Process -Name 'X2' -ErrorAction SilentlyContinue | Stop-Process -Force
    Write-Host 'Altium    : closed'
}
