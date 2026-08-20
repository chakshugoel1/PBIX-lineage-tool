[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WorkspaceId,
    [Parameter(Mandatory = $true)][string]$OutputDir
)

$ErrorActionPreference = "Stop"

function Write-Result {
    param([bool]$Success, [string]$Stage, [string]$Message, [array]$Files = @())
    $result = @{
        success = $Success
        stage   = $Stage
        message = $Message
        files   = $Files
    }
    Write-Output ("##RESULT##" + ($result | ConvertTo-Json -Compress -Depth 5))
}

function Get-SafeFileName {
    param([string]$Name)
    $invalid = [System.IO.Path]::GetInvalidFileNameChars() -join ""
    $pattern = "[{0}]" -f [Regex]::Escape($invalid)
    $cleaned = ($Name -replace $pattern, "_").Trim().Trim(".")
    if ([string]::IsNullOrWhiteSpace($cleaned)) { return "dataflow" }
    return $cleaned
}

try {
    if (-not (Get-Module -ListAvailable -Name MicrosoftPowerBIMgmt)) {
        # First-run-only: Install-Module -Force alone does not suppress the
        # separate "NuGet provider is required" prompt, which would otherwise
        # hang this non-interactive subprocess waiting for input.
        if (-not (Get-PackageProvider -Name NuGet -ErrorAction SilentlyContinue)) {
            Write-Output "Installing NuGet package provider (one-time, current user only)..."
            Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Scope CurrentUser -Force | Out-Null
        }
        Write-Output "Installing MicrosoftPowerBIMgmt module (one-time, current user only)..."
        Install-Module -Name MicrosoftPowerBIMgmt -Scope CurrentUser -Force -AllowClobber -Confirm:$false
    }
    Import-Module MicrosoftPowerBIMgmt -ErrorAction Stop
}
catch {
    Write-Result -Success $false -Stage "module" -Message "Could not install/import MicrosoftPowerBIMgmt: $($_.Exception.Message)"
    exit 1
}

try {
    # Reuses a cached token silently if a previous interactive sign-in is
    # still valid, so this only prompts a sign-in popup once in a while.
    Connect-PowerBIServiceAccount -ErrorAction Stop | Out-Null
}
catch {
    Write-Result -Success $false -Stage "auth" -Message "Sign-in failed or was cancelled: $($_.Exception.Message)"
    exit 1
}

try {
    $workspace = Get-PowerBIWorkspace -Id $WorkspaceId -ErrorAction Stop
    if (-not $workspace) {
        Write-Result -Success $false -Stage "workspace" -Message "Workspace '$WorkspaceId' was not found, or you don't have access to it."
        exit 1
    }
}
catch {
    Write-Result -Success $false -Stage "workspace" -Message "Workspace '$WorkspaceId' was not found, or you don't have access to it: $($_.Exception.Message)"
    exit 1
}

try {
    $listResponse = Invoke-PowerBIRestMethod -Url "groups/$WorkspaceId/dataflows" -Method Get -ErrorAction Stop
    $dataflows = ($listResponse | ConvertFrom-Json).value
}
catch {
    Write-Result -Success $false -Stage "list" -Message "Could not list dataflows in this workspace: $($_.Exception.Message)"
    exit 1
}

if (-not $dataflows -or $dataflows.Count -eq 0) {
    Write-Result -Success $true -Stage "empty" -Message "No dataflows found in this workspace." -Files @()
    exit 0
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$files = @()
$failures = @()
foreach ($df in $dataflows) {
    $name = Get-SafeFileName -Name $df.name
    $destPath = Join-Path $OutputDir "$name.json"
    Write-Output "Exporting '$($df.name)' ($($df.objectId))..."
    try {
        Invoke-PowerBIRestMethod -Url "groups/$WorkspaceId/dataflows/$($df.objectId)" -Method Get -OutFile $destPath -ErrorAction Stop
        $files += $destPath
    }
    catch {
        Write-Output "Failed to export '$($df.name)': $($_.Exception.Message)"
        $failures += $df.name
    }
}

if ($files.Count -eq 0) {
    Write-Result -Success $false -Stage "export" -Message "All $($dataflows.Count) dataflow(s) failed to export."
    exit 1
}

$message = "$($files.Count) of $($dataflows.Count) dataflow(s) exported."
if ($failures.Count -gt 0) {
    $message += " Failed: $($failures -join ', ')"
}
Write-Result -Success $true -Stage "done" -Message $message -Files $files
