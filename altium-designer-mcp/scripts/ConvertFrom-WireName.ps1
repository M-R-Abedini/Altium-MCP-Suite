<#
.SYNOPSIS
    Shared helper: decode a component name from Altium's on-wire form.

.DESCRIPTION
    Altium's PCB scripting API hands back a non-Latin component name in its
    on-wire form: the UTF-8 bytes carried one char per byte. That is not a
    defect in the file — asking Altium for the names in its OWN authored golden
    returns exactly the same string — so name comparisons accept either the
    true name or that form. Decoding it back is the inverse of what the writer
    does, through the system ANSI page, because that is the one Altium widened
    through (see scripts/README.md § "Altium's PCB scripting API returns names
    in their on-wire form").
#>
function ConvertFrom-WireName([string]$Name) {
    try {
        $bytes = [System.Text.Encoding]::Default.GetBytes($Name)
        $utf8  = New-Object System.Text.UTF8Encoding $false, $true
        return $utf8.GetString($bytes)
    } catch { return $Name }
}
