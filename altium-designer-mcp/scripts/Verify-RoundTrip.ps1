<#
.SYNOPSIS
    On-site: prove that a library this server writes can be opened by a real
    Altium Designer, with its component names intact.

.DESCRIPTION
    Closes the loop the other two scripts each cover half of. Generate-Samples.ps1
    proves we can READ what Altium writes; the pyaltiumlib oracle in CI proves our
    output parses independently. Neither proves that Altium itself opens a file we
    wrote and resolves the components inside it.

    The whole path is exercised end to end, through the shipped binary rather than
    library internals:

      1. build the MCP server
      2. drive it over stdio with real `write_schlib` / `write_pcblib` tool calls
      3. read the libraries back through `read_schlib` / `read_pcblib`
      4. hand the files to Altium via Verify-Libraries.ps1
      5. compare the component names Altium resolved against what was authored

    Step 5 is the point. "Opened" alone only proves the file parses; a name
    comparison proves the components are reachable and their text survived.
    Non-ASCII names are included deliberately — that is where the encodings in
    play (Windows-1252 records, UTF-16 storage names, %UTF8% promotion) disagree,
    and where a regression would otherwise be invisible.

    On-site only: needs Altium Designer installed (developed against AD24). Never CI.

.PARAMETER AltiumExe
    Path to X2.EXE. Read from the repo-root .env.local (ALTIUM_EXE) when omitted.

.PARAMETER WorkDir
    Where the generated libraries are written. Defaults to a fresh timestamped
    directory under the system temp folder, kept afterwards for inspection.

.PARAMETER KeepAltiumOpen
    Leave Altium running afterwards, with both libraries open for inspection.
    By default it is closed, since this is an automated pass/fail check.

.PARAMETER SkipBuild
    Reuse an existing debug binary instead of running cargo build.

.PARAMETER TimeoutSeconds
    How long to wait for Altium (default 180).

.EXAMPLE
    .\Verify-RoundTrip.ps1

.EXAMPLE
    .\Verify-RoundTrip.ps1 -SkipBuild -WorkDir C:\tmp\rt
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

# The names under test. ASCII first as a control: if these fail too, the problem
# is the harness, not encoding. The rest cover the scripts a user might plausibly
# name a part in, plus an ohm sign, which is outside Windows-1252 but common on
# any resistor legend.
$SymbolNames = @('PLAIN_ASCII', 'Резистор', '電阻', 'Ωmega', 'Ελλάδα')
$FootprintNames = @('PLAIN_0402', 'Резистор_0402')

if (-not $WorkDir) {
    $stamp   = Get-Date -Format 'yyyyMMdd_HHmmss'
    $WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) "adm_roundtrip_$stamp"
}
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
Write-Host "Work dir : $WorkDir"

# ---------------------------------------------------------------------------
# 1. Build the server
# ---------------------------------------------------------------------------
$Exe = Join-Path $RepoRoot 'target\debug\altium-designer-mcp.exe'
if (-not $SkipBuild) {
    Write-Host 'Building  : cargo build'
    Push-Location $RepoRoot
    try {
        # No 2>&1 here: in Windows PowerShell 5.1 redirecting a native command's
        # stderr wraps each line in an ErrorRecord, which $ErrorActionPreference
        # = 'Stop' then treats as a failure even when cargo exits 0.
        & cargo build --quiet
        if ($LASTEXITCODE -ne 0) { throw "cargo build failed ($LASTEXITCODE)" }
    } finally { Pop-Location }
}
if (-not (Test-Path $Exe)) { throw "binary not found: $Exe (run without -SkipBuild)" }

# ---------------------------------------------------------------------------
# 2. Drive the server over stdio
# ---------------------------------------------------------------------------
# allowed_paths confines the server; point it at the work directory only, which
# also exercises the sandbox on the path this test uses.
$ConfigPath = Join-Path $WorkDir 'config.json'
@{
    allowed_paths = @($WorkDir -replace '\\', '/')
    logging       = @{ level = 'warn'; audit_log_path = $null }
} | ConvertTo-Json -Depth 5 | ForEach-Object {
    # WriteAllText, not Set-Content: PowerShell 5.1's -Encoding utf8 emits a BOM,
    # and a leading BOM makes the server's JSON parser reject the config.
    [System.IO.File]::WriteAllText($ConfigPath, $_, (New-Object System.Text.UTF8Encoding $false))
}

$SchLibPath = Join-Path $WorkDir 'RoundTrip.SchLib'
$PcbLibPath = Join-Path $WorkDir 'RoundTrip.PcbLib'

# One JSON-RPC message per line, in the order the protocol requires.
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
    clientInfo      = @{ name = 'verify-roundtrip'; version = '1.0.0' }
}
Add-Request -1 'notifications/initialized' $null

# A symbol per name: one pin each, enough for Altium to treat it as a real part.
$symbols = @()
foreach ($n in $SymbolNames) {
    $symbols += @{
        name        = $n
        designator  = 'U?'
        description = "$n description"
        pins        = @(@{ designator = '1'; name = 'A'; x = -30; y = 0; length = 30; orientation = 'left' })
        rectangles  = @(@{ x1 = -20; y1 = -10; x2 = 20; y2 = 10 })
    }
}
Add-Request 2 'tools/call' @{
    name      = 'write_schlib'
    arguments = @{ filepath = $SchLibPath; symbols = $symbols }
}

$footprints = @()
foreach ($n in $FootprintNames) {
    $footprints += @{
        name = $n
        pads = @(
            @{ designator = '1'; x = -0.5; y = 0; width = 0.6; height = 0.5 },
            @{ designator = '2'; x = 0.5; y = 0; width = 0.6; height = 0.5 }
        )
    }
}
Add-Request 3 'tools/call' @{
    name      = 'write_pcblib'
    arguments = @{ filepath = $PcbLibPath; footprints = $footprints }
}

Add-Request 4 'tools/call' @{ name = 'read_schlib'; arguments = @{ filepath = $SchLibPath } }
Add-Request 5 'tools/call' @{ name = 'read_pcblib'; arguments = @{ filepath = $PcbLibPath } }

$RequestPath = Join-Path $WorkDir 'requests.jsonl'
# UTF-8 without BOM: a BOM on the first line is not valid JSON-RPC framing.
[System.IO.File]::WriteAllLines($RequestPath, $requests, (New-Object System.Text.UTF8Encoding $false))

Write-Host 'MCP       : writing and reading back libraries'
$OutPath = Join-Path $WorkDir 'responses.jsonl'
$ErrPath = Join-Path $WorkDir 'server.log'
$proc = Start-Process -FilePath $Exe -ArgumentList "`"$ConfigPath`"" -NoNewWindow -PassThru `
    -RedirectStandardInput $RequestPath -RedirectStandardOutput $OutPath -RedirectStandardError $ErrPath
$proc | Wait-Process -Timeout 120

$responses = @(Get-Content -Path $OutPath -Encoding utf8 | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })

function Get-ToolText([int]$Id) {
    $r = $responses | Where-Object { $_.id -eq $Id } | Select-Object -First 1
    if (-not $r) { throw "no response for request $Id (see $ErrPath)" }
    if ($r.PSObject.Properties.Name -contains 'error') { throw "request $Id failed: $($r.error.message)" }
    if ($r.result.isError) { throw "request ${Id}: tool reported an error: $($r.result.content[0].text)" }
    $r.result.content[0].text
}

# ---------------------------------------------------------------------------
# 3. What the server itself read back
# ---------------------------------------------------------------------------
$schRead = Get-ToolText 4 | ConvertFrom-Json
$pcbRead = Get-ToolText 5 | ConvertFrom-Json
$serverSymbols    = @($schRead.symbols    | ForEach-Object { $_.name })
$serverFootprints = @($pcbRead.footprints | ForEach-Object { $_.name })

# ---------------------------------------------------------------------------
# 4. What Altium resolves
# ---------------------------------------------------------------------------
Write-Host 'Altium    : opening both libraries'
$verifyArgs = @{ Files = @($SchLibPath, $PcbLibPath); TimeoutSeconds = $TimeoutSeconds }
if ($AltiumExe) { $verifyArgs['AltiumExe'] = $AltiumExe }
$altium = & (Join-Path $ScriptDir 'Verify-Libraries.ps1') @verifyArgs

function Get-AltiumResult([string]$Path) {
    $r = $altium | Where-Object { $_.file -eq $Path } | Select-Object -First 1
    if (-not $r) { throw "Altium returned no result for $Path" }
    $r
}

# ---------------------------------------------------------------------------
# 5. Compare
# ---------------------------------------------------------------------------
$failures = New-Object System.Collections.Generic.List[string]

# A non-Latin name comes back from Altium in its on-wire form; accept either
# (see the shared helper).
. (Join-Path $PSScriptRoot 'ConvertFrom-WireName.ps1')

function Compare-Names([string]$Label, [string[]]$Expected, [string[]]$Actual) {
    $resolved = @($Actual | ForEach-Object {
        if ($Expected -contains $_) { $_ } else { ConvertFrom-WireName $_ }
    })
    $Actual = $resolved
    $missing = @($Expected | Where-Object { $Actual -notcontains $_ })
    $extra   = @($Actual   | Where-Object { $Expected -notcontains $_ })
    if ($missing.Count -or $extra.Count) {
        $script:failures.Add("${Label}: missing [$($missing -join ', ')] unexpected [$($extra -join ', ')]")
        Write-Host "  FAIL  $Label" -ForegroundColor Red
        Write-Host "        expected: $($Expected -join ', ')"
        Write-Host "        actual  : $($Actual -join ', ')"
    } else {
        Write-Host "  ok    $Label ($($Expected.Count) components)" -ForegroundColor Green
    }
}

Write-Host ''
Write-Host 'Server read-back:'
Compare-Names 'SchLib via read_schlib' $SymbolNames    $serverSymbols
Compare-Names 'PcbLib via read_pcblib' $FootprintNames $serverFootprints

Write-Host ''
Write-Host 'Altium:'
foreach ($pair in @(@{ P = $SchLibPath; N = $SymbolNames; L = 'SchLib' },
                    @{ P = $PcbLibPath; N = $FootprintNames; L = 'PcbLib' })) {
    $res = Get-AltiumResult $pair.P
    if (-not $res.opened) {
        $failures.Add("$($pair.L): Altium could not open it — $($res.detail)")
        Write-Host "  FAIL  $($pair.L) did not open: $($res.detail)" -ForegroundColor Red
        continue
    }
    Compare-Names "$($pair.L) as resolved by Altium" $pair.N @($res.components)
}

# Primitive counts as Altium resolved them: every symbol was authored with one
# pin and one rectangle, every footprint with two pads. "Opened" and the names
# alone would pass a file whose primitives Altium quietly dropped.
function Compare-Counts([string]$Label, $Result, [string]$Kind, [int]$Expected) {
    $counts = @($Result.primitive_counts)
    for ($i = 0; $i -lt $counts.Count; $i++) {
        $actual = [int]$counts[$i].$Kind
        if ($actual -ne $Expected) {
            $script:failures.Add("${Label}[$i]: $Kind expected $Expected, Altium resolved $actual")
            Write-Host "  FAIL  ${Label}[$i]: $Kind $actual (wanted $Expected)" -ForegroundColor Red
        }
    }
    if (-not ($counts | Where-Object { [int]$_.$Kind -ne $Expected })) {
        Write-Host "  ok    $Label ($Kind = $Expected on all $($counts.Count))" -ForegroundColor Green
    }
}
$schRes = Get-AltiumResult $SchLibPath
$pcbRes = Get-AltiumResult $PcbLibPath
if ($schRes.opened) {
    Compare-Counts 'SchLib counts' $schRes 'pins' 1
    Compare-Counts 'SchLib counts' $schRes 'rectangles' 1
}
if ($pcbRes.opened) {
    Compare-Counts 'PcbLib counts' $pcbRes 'pads' 2
}

# Close Altium unless asked otherwise. Verify-Libraries.ps1 deliberately leaves
# the documents open for a human to look at; this script is a pass/fail check, so
# the default is the other way round. Loop-kill: Altium takes a moment to exit and
# can spawn helper X2 processes.
if (-not $KeepAltiumOpen) {
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Process X2 -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
        Get-Process X2 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
    if (Get-Process X2 -ErrorAction SilentlyContinue) {
        Write-Host 'Altium still running (could not fully close).' -ForegroundColor Yellow
    } else {
        Write-Host 'Closed Altium.'
    }
}

Write-Host ''
if ($failures.Count) {
    Write-Host "FAILED ($($failures.Count))" -ForegroundColor Red
    $failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host "Artefacts kept in $WorkDir"
    exit 1
}

Write-Host 'PASS — every component round-tripped through the server and Altium.' -ForegroundColor Green
Write-Host "Artefacts kept in $WorkDir"
