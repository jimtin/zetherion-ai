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

& bash "$ScriptDir/test-full.sh" @args
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
