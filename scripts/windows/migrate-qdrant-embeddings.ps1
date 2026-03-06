param(
    [Parameter(Mandatory = $true)]
    [string]$DeployPath,
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "qdrant-migration.json",
    [Parameter(Mandatory = $false)]
    [switch]$AllowSkip
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-MigrationResult {
    param(
        [object]$Result,
        [string]$Path
    )

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $Result | ConvertTo-Json -Depth 8 | Out-File $Path -Encoding utf8
}

$collections = @(
    "conversations",
    "long_term_memory",
    "docs_knowledge",
    "tenant_documents",
    "user_profiles",
    "skill_tasks",
    "skill_calendar",
    "skill_dev_journal",
    "skill_milestones"
)

$result = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    deploy_path = $DeployPath
    service = "zetherion-ai-bot"
    status = "failed"
    collections = $collections
    allow_skip = [bool]$AllowSkip
    preflight_exit_code = $null
    migrate_exit_code = $null
    preflight_output = ""
    migrate_output = ""
    error = ""
}

try {
    if (-not (Test-Path $DeployPath)) {
        throw "Deploy path not found: $DeployPath"
    }

    Push-Location $DeployPath
    try {
        $baseArgs = New-Object 'System.Collections.Generic.List[string]'
        foreach ($arg in @(
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "-e",
            "LOG_TO_FILE=false",
            "zetherion-ai-bot",
            "/app/scripts/migrate-qdrant-embeddings.py"
        )) {
            $baseArgs.Add($arg)
        }

        foreach ($collection in $collections) {
            $baseArgs.Add("--collection")
            $baseArgs.Add($collection)
        }

        $preflightArgs = New-Object 'System.Collections.Generic.List[string]'
        foreach ($arg in $baseArgs) {
            $preflightArgs.Add($arg)
        }
        $preflightArgs.Add("--mode")
        $preflightArgs.Add("preflight")

        $preflightOutput = (& docker @preflightArgs 2>&1 | Out-String).Trim()
        $preflightExitCode = $LASTEXITCODE
        $result.preflight_exit_code = $preflightExitCode
        $result.preflight_output = $preflightOutput

        if ($preflightExitCode -eq 0) {
            $result.status = "aligned"
            Write-MigrationResult -Result $result -Path $OutputPath
            exit 0
        }

        if ($preflightExitCode -ne 2) {
            throw "Qdrant preflight failed with exit code $preflightExitCode"
        }

        $migrateArgs = New-Object 'System.Collections.Generic.List[string]'
        foreach ($arg in $baseArgs) {
            $migrateArgs.Add($arg)
        }
        $migrateArgs.Add("--mode")
        $migrateArgs.Add("migrate")
        $migrateArgs.Add("--yes")
        if ($AllowSkip) {
            $migrateArgs.Add("--allow-skip")
        }

        $migrateOutput = (& docker @migrateArgs 2>&1 | Out-String).Trim()
        $migrateExitCode = $LASTEXITCODE
        $result.migrate_exit_code = $migrateExitCode
        $result.migrate_output = $migrateOutput

        if ($migrateExitCode -ne 0) {
            throw "Qdrant migration failed with exit code $migrateExitCode"
        }

        $result.status = "migrated"
        Write-MigrationResult -Result $result -Path $OutputPath
        exit 0
    } finally {
        Pop-Location
    }
} catch {
    $result.error = $_.Exception.Message
    Write-MigrationResult -Result $result -Path $OutputPath
    throw
}
