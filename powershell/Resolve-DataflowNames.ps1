[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PairsFile
)

$ErrorActionPreference = "Stop"

function Write-Result {
    param([bool]$Success, [string]$Stage, [string]$Message, [array]$Mappings = @(), [array]$Failures = @())
    $result = @{
        success  = $Success
        stage    = $Stage
        message  = $Message
        mappings = $Mappings
        failures = $Failures
    }
    Write-Output ("##RESULT##" + ($result | ConvertTo-Json -Compress -Depth 5))
}

try {
    $pairs = @(Get-Content -Path $PairsFile -Raw -Encoding UTF8 | ConvertFrom-Json)
}
catch {
    Write-Result -Success $false -Stage "input" -Message "Could not read the lookup list: $($_.Exception.Message)"
    exit 1
}

if ($pairs.Count -eq 0) {
    Write-Result -Success $true -Stage "empty" -Message "Nothing to look up."
    exit 0
}

try {
    if (-not (Get-Module -ListAvailable -Name MicrosoftPowerBIMgmt)) {
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
    Connect-PowerBIServiceAccount -ErrorAction Stop | Out-Null
}
catch {
    Write-Result -Success $false -Stage "auth" -Message "Sign-in failed or was cancelled: $($_.Exception.Message)"
    exit 1
}

$mappings = @()
$failures = @()

foreach ($group in ($pairs | Group-Object -Property workspace_id)) {
    $workspaceId = $group.Name
    if ([string]::IsNullOrWhiteSpace($workspaceId)) {
        foreach ($pair in $group.Group) { $failures += "Missing workspace id for dataflow $($pair.dataflow_id)." }
        continue
    }

    $workspaceName = $null
    try { $workspaceName = (Get-PowerBIWorkspace -Id $workspaceId -ErrorAction Stop).Name } catch { $workspaceName = $null }

    # One listing call per workspace names every dataflow in it at once.
    $byId = @{}
    try {
        Write-Output "Looking up dataflow names in workspace $workspaceId..."
        $listResponse = Invoke-PowerBIRestMethod -Url "groups/$workspaceId/dataflows" -Method Get -ErrorAction Stop
        foreach ($df in ($listResponse | ConvertFrom-Json).value) {
            $byId["$($df.objectId)".ToLower()] = "$($df.name)"
        }
    }
    catch {
        $failures += "No access to workspace $workspaceId : $($_.Exception.Message)"
        continue
    }

    foreach ($pair in $group.Group) {
        $dataflowId = "$($pair.dataflow_id)"
        $name = $byId[$dataflowId.ToLower()]
        if ([string]::IsNullOrWhiteSpace($name)) {
            $failures += "Dataflow $dataflowId was not found in workspace $workspaceId."
            continue
        }
        $mappings += @{
            key            = "$workspaceId/$dataflowId"
            workspace_id   = $workspaceId
            dataflow_id    = $dataflowId
            dataflow_name  = $name
            workspace_name = $workspaceName
        }
    }
}

$message = "$($mappings.Count) of $($pairs.Count) dataflow GUID(s) resolved."
if ($failures.Count -gt 0) {
    $message += " Unresolved: $($failures.Count)."
}
Write-Result -Success $true -Stage "done" -Message $message -Mappings $mappings -Failures $failures
