#!/usr/bin/env pwsh
# Canonical full local validation pipeline (Windows wrapper).

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent $ScriptDir

Set-Location $RepoDir

if (-not (Get-Command bash -ErrorAction SilentlyContinue)) {
    Write-Error "bash is required to run the canonical test pipeline. Install Git for Windows and ensure 'bash' is on PATH."
}

$env:RUN_DISCORD_E2E_REQUIRED = if ($env:RUN_DISCORD_E2E_REQUIRED) { $env:RUN_DISCORD_E2E_REQUIRED } else { "true" }
$env:STRICT_REQUIRED_TESTS = if ($env:STRICT_REQUIRED_TESTS) { $env:STRICT_REQUIRED_TESTS } else { "true" }
$env:RUN_OPTIONAL_E2E = if ($env:RUN_OPTIONAL_E2E) { $env:RUN_OPTIONAL_E2E } else { "false" }
$env:DISCORD_E2E_PROVIDER = if ($env:DISCORD_E2E_PROVIDER) { $env:DISCORD_E2E_PROVIDER } else { "groq" }
$env:RUN_DISCORD_E2E_LOCAL_MODEL = if ($env:RUN_DISCORD_E2E_LOCAL_MODEL) { $env:RUN_DISCORD_E2E_LOCAL_MODEL } else { "false" }
$env:EMBEDDINGS_BACKEND = if ($env:EMBEDDINGS_BACKEND) { $env:EMBEDDINGS_BACKEND } else { "openai" }
$env:OPENAI_EMBEDDING_MODEL = if ($env:OPENAI_EMBEDDING_MODEL) { $env:OPENAI_EMBEDDING_MODEL } else { "text-embedding-3-large" }
$env:OPENAI_EMBEDDING_DIMENSIONS = if ($env:OPENAI_EMBEDDING_DIMENSIONS) { $env:OPENAI_EMBEDDING_DIMENSIONS } else { "3072" }

& bash "$ScriptDir/test-full.sh" @args
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
