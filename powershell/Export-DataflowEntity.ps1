<#
.SYNOPSIS
    Exports the actual data rows of a Power BI (Gen1) Dataflow entity to a
    local CSV file.

.DESCRIPTION
    Authenticates interactively to Power BI (via the MicrosoftPowerBIMgmt
    module - no secrets are ever stored on disk), resolves the requested
    workspace/dataflow/entity, and downloads the entity's actual row data
    (not just its metadata) as CSV.

    Gen1 dataflows only expose row-level data programmatically when the
    workspace is linked to a customer-owned Azure Data Lake Storage Gen2
    account ("Bring your own storage"). This script detects that via the
    workspace's `dataflowStorageId` property and fails clearly, without
    guessing, if it is not configured - there is no supported public API to
    fetch raw entity rows for a Gen1 dataflow on Microsoft-managed storage.

    Every stage prints a plain progress line to stdout, and the very last
    line is always a single-line JSON object prefixed with `##RESULT##` that
    the calling Python code parses for a reliable success/failure signal:
        ##RESULT##{"success":true,"path":"C:\...\ENTITY.csv","rows":123}
        ##RESULT##{"success":false,"stage":"auth","message":"..."}

.PARAMETER WorkspaceId
    The Power BI workspace (group) GUID.

.PARAMETER DataflowId
    The Gen1 dataflow GUID within that workspace.

.PARAMETER EntityName
    The dataflow entity/table name whose rows should be exported.

.PARAMETER OutputPath
    Full path (including filename) to write the CSV to. The caller is
    responsible for sanitizing the filename and archiving any pre-existing
    file - this script only writes to the given path.
#>
param(
    [Parameter(Mandatory = $true)][string]$WorkspaceId,
    [Parameter(Mandatory = $true)][string]$DataflowId,
    [Parameter(Mandatory = $true)][string]$EntityName,
    [Parameter(Mandatory = $true)][string]$OutputPath
)

$ErrorActionPreference = "Stop"

function Write-Result($obj) {
    Write-Output ("##RESULT##" + ($obj | ConvertTo-Json -Compress -Depth 6))
}

function Fail($stage, $message) {
    Write-Result @{ success = $false; stage = $stage; message = $message }
    exit 1
}

Write-Output "Starting Power BI Dataflow export"
Write-Output "Workspace: $WorkspaceId"
Write-Output "Dataflow: $DataflowId"
Write-Output "Entity: $EntityName"
Write-Output "Output: $OutputPath"

# --- Module + auth ----------------------------------------------------------
Write-Output ""
Write-Output "Authenticating..."
try {
    if (-not (Get-Module -ListAvailable -Name MicrosoftPowerBIMgmt)) {
        Write-Output "Installing MicrosoftPowerBIMgmt module (first run only)..."
        Install-Module -Name MicrosoftPowerBIMgmt -Scope CurrentUser -Force -AllowClobber -ErrorAction Stop
    }
    Import-Module MicrosoftPowerBIMgmt.Profile -ErrorAction Stop
    Import-Module MicrosoftPowerBIMgmt.Data -ErrorAction Stop
    Connect-PowerBIServiceAccount -ErrorAction Stop | Out-Null
} catch {
    Fail "auth" "Could not authenticate to Power BI: $($_.Exception.Message)"
}

# --- Resolve workspace storage type ------------------------------------------
Write-Output "Checking workspace storage configuration..."
try {
    $wsJson = Invoke-PowerBIRestMethod -Url "groups/$WorkspaceId" -Method Get -ErrorAction Stop
    $ws = $wsJson | ConvertFrom-Json
} catch {
    Fail "workspace" "Could not resolve workspace '$WorkspaceId': $($_.Exception.Message)"
}

if ([string]::IsNullOrEmpty($ws.dataflowStorageId)) {
    Fail "storage-unsupported" (
        "This workspace uses Microsoft-managed dataflow storage. Row-level " +
        "export via API is only supported for Gen1 dataflows in a workspace " +
        "linked to your own Azure Data Lake Storage Gen2 account (Workspace " +
        "settings -> Dataflow storage settings -> 'Bring your own storage'). " +
        "Ask your Power BI admin to enable it, or consume this dataflow via " +
        "Power Query's Dataflows connector instead."
    )
}

# --- Retrieve dataflow entity (metadata first, to validate + locate) --------
Write-Output "Retrieving dataflow entity..."
try {
    $dfJson = Invoke-PowerBIRestMethod -Url "groups/$WorkspaceId/dataflows/$DataflowId" -Method Get -ErrorAction Stop
    $model = $dfJson | ConvertFrom-Json
} catch {
    Fail "dataflow" "Could not resolve dataflow '$DataflowId' in workspace '$WorkspaceId': $($_.Exception.Message)"
}

$entity = $model.entities | Where-Object { $_.name -eq $EntityName } | Select-Object -First 1
if (-not $entity) {
    $available = ($model.entities | ForEach-Object { $_.name }) -join ", "
    Fail "entity" "Entity '$EntityName' not found in dataflow. Available entities: $available"
}

# --- Read the entity's CDM partition data from the linked ADLS Gen2 account -
try {
    Import-Module Az.Storage -ErrorAction Stop
    Import-Module Az.Accounts -ErrorAction Stop
} catch {
    Fail "storage-modules" (
        "The Az.Storage/Az.Accounts PowerShell modules are required to read " +
        "dataflow entity data from ADLS Gen2, but are not installed. " +
        "Install with: Install-Module Az.Storage, Az.Accounts -Scope CurrentUser"
    )
}

try {
    Connect-AzAccount -ErrorAction Stop | Out-Null

    $storageAccounts = Invoke-PowerBIRestMethod -Url "dataflowStorageAccounts" -Method Get -ErrorAction Stop | ConvertFrom-Json
    $account = $storageAccounts.value | Where-Object { $_.id -eq $ws.dataflowStorageId } | Select-Object -First 1
    if (-not $account) {
        Fail "storage-account" "Could not resolve the linked ADLS Gen2 storage account for this workspace."
    }

    $ctx = New-AzStorageContext -StorageAccountName $account.name -UseConnectedAccount -ErrorAction Stop

    $rows = @()
    foreach ($partition in $entity.partitions) {
        # partition.location is a full blob URL; only the path after the
        # container name is needed for Get-AzStorageBlobContent.
        $uri = [Uri]$partition.location
        $blobPath = $uri.AbsolutePath.TrimStart('/').Split('/', 2)[1]
        $tempFile = [System.IO.Path]::GetTempFileName()
        Get-AzStorageBlobContent -Container "powerbi" -Blob $blobPath -Context $ctx `
            -Destination $tempFile -Force -ErrorAction Stop | Out-Null
        $rows += Import-Csv -Path $tempFile
        Remove-Item $tempFile -Force -ErrorAction SilentlyContinue
    }
} catch {
    Fail "data-retrieval" "Failed to read entity data from storage: $($_.Exception.Message)"
}

if (-not $rows -or $rows.Count -eq 0) {
    Fail "empty" "Entity '$EntityName' returned no rows."
}

Write-Output "Rows retrieved: $($rows.Count)"

# --- Write CSV ----------------------------------------------------------------
Write-Output "Writing CSV..."
try {
    $outDir = Split-Path -Path $OutputPath -Parent
    if (-not (Test-Path $outDir)) {
        New-Item -ItemType Directory -Path $outDir -Force | Out-Null
    }
    $rows | Export-Csv -Path $OutputPath -NoTypeInformation -Encoding UTF8 -Force
} catch {
    Fail "filesystem" "Failed to write CSV to '$OutputPath': $($_.Exception.Message)"
}

Write-Output ""
Write-Output "Export completed successfully"
Write-Result @{ success = $true; path = $OutputPath; rows = $rows.Count }
