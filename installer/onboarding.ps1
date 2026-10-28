# Local setup wizard; no network/account registration is required for Free Core.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$suiteRoot = Split-Path -Parent $PSScriptRoot
$suitePython = Join-Path $suiteRoot 'python\python.exe'
$suiteHub = Join-Path $suiteRoot 'suite_hub.py'
$suiteForm = New-Object System.Windows.Forms.Form
$suiteForm.Text = 'Set up MCP Altium Suite'
$suiteForm.Size = New-Object System.Drawing.Size(660, 480)
$suiteForm.StartPosition = 'CenterScreen'
$suiteIntro = New-Object System.Windows.Forms.Label
$suiteIntro.Text = 'Free Core runs locally without registration. Altium Designer and an AI client must be installed. Setup preserves other MCP servers. Restart your AI client after setup.'
$suiteIntro.SetBounds(18, 16, 600, 60)
$suiteForm.Controls.Add($suiteIntro)
$suitePath = New-Object System.Windows.Forms.TextBox
$suitePath.SetBounds(18, 90, 495, 28)
$suiteForm.Controls.Add($suitePath)
$suiteBrowse = New-Object System.Windows.Forms.Button
$suiteBrowse.Text = 'Altium X2.EXE...'
$suiteBrowse.SetBounds(520, 88, 110, 30)
$suiteBrowse.Add_Click({
    $suiteDialog = New-Object System.Windows.Forms.OpenFileDialog
    $suiteDialog.Filter = 'Altium executable (X2.EXE)|X2.EXE'
    if ($suiteDialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        $suitePath.Text = $suiteDialog.FileName
    }
})
$suiteForm.Controls.Add($suiteBrowse)
$suiteClients = New-Object System.Windows.Forms.ComboBox
$suiteClients.DropDownStyle = 'DropDownList'
$suiteClients.SetBounds(18, 135, 250, 30)
@('codex', 'claude-desktop', 'claude-code', 'cursor', 'windsurf', 'vscode') | ForEach-Object { [void]$suiteClients.Items.Add($_) }
$suiteClients.SelectedIndex = 0
$suiteForm.Controls.Add($suiteClients)
$suiteButton = New-Object System.Windows.Forms.Button
$suiteButton.Text = 'Configure selected client'
$suiteButton.SetBounds(280, 133, 220, 32)
$suiteForm.Controls.Add($suiteButton)
$suiteOutput = New-Object System.Windows.Forms.TextBox
$suiteOutput.Multiline = $true
$suiteOutput.ReadOnly = $true
$suiteOutput.ScrollBars = 'Vertical'
$suiteOutput.SetBounds(18, 185, 612, 240)
$suiteOutput.Text = 'Select the intended Altium executable. No script is run and no design is changed during setup.'
$suiteForm.Controls.Add($suiteOutput)
$suiteButton.Add_Click({
    $suiteButton.Enabled = $false
    try {
        if (-not (Test-Path -LiteralPath $suitePath.Text -PathType Leaf)) { throw 'Select an existing Altium X2.EXE.' }
        $suiteResult = & $suitePython -B $suiteHub setup-bundled --altium-exe $suitePath.Text --client $suiteClients.SelectedItem 2>&1
        $suiteOutput.Text = ($suiteResult | Out-String)
        if ($LASTEXITCODE -ne 0) { $suiteOutput.Text += "`r`nSetup failed. No readiness claim is made. Review the message above." }
    } catch { $suiteOutput.Text = $_.Exception.Message }
    finally { $suiteButton.Enabled = $true }
})
try {
    $suiteDetected = (& $suitePython -B $suiteHub discover-altium | ConvertFrom-Json).installations
    if ($suiteDetected.Count -eq 1) { $suitePath.Text = $suiteDetected[0].executable }
} catch { }
[void]$suiteForm.ShowDialog()
