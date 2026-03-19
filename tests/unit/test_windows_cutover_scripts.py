from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PREPARE_PATH = REPO_ROOT / "scripts" / "windows" / "prepare-runtime-cutover.ps1"
PROMOTE_PATH = REPO_ROOT / "scripts" / "windows" / "promote-runtime-cutover.ps1"


def test_prepare_runtime_cutover_stages_clean_candidate_and_rescue_archive() -> None:
    script = PREPARE_PATH.read_text(encoding="utf-8")

    assert '[string]$DeployPath = "C:\\ZetherionAI"' in script
    assert '[string]$CandidatePath = "C:\\ZetherionAI-cutover"' in script
    assert '[string]$TargetSha' in script
    assert '[string]$RepositoryUrl = "https://github.com/jimtin/zetherion-ai.git"' in script
    assert '[string[]]$FileStatePaths = @(".env")' in script
    assert '[string[]]$PersistentDirectories = @()' in script
    assert "function Get-RepositoryForensics" in script
    assert "git -c core.safecrlf=false status --short --branch" in script
    assert "git -c core.safecrlf=false diff --stat" in script
    assert "git -c core.safecrlf=false ls-files --others --exclude-standard" in script
    assert "Get-FileHash -LiteralPath $fullPath -Algorithm SHA256" in script
    assert "& robocopy $Source $Destination /E /COPY:DAT" in script
    assert 'Invoke-GitCommand @("clone", "--no-checkout", $RepositoryUrl, $CandidatePath)' in script
    assert 'Invoke-GitCommand @("fetch", "--depth=1", "--force", "origin", $TargetSha)' in script
    assert 'Invoke-GitCommand @("checkout", "--detach", "--force", $TargetSha)' in script
    assert "Copy-AllowlistedRuntimeState" in script
    assert "function Normalize-DotenvInlineComments" in script
    assert "normalized_file_state = @()" in script
    assert 'Normalize-DotenvInlineComments -Path ([string]$carriedItem.destination)' in script
    assert "carried_forward_state = @()" in script
    assert 'status = "prepared"' in script


def test_promote_runtime_cutover_swaps_clean_candidate_into_live_path() -> None:
    script = PROMOTE_PATH.read_text(encoding="utf-8")

    assert '[string]$DeployPath = "C:\\ZetherionAI"' in script
    assert '[string]$CandidatePath = "C:\\ZetherionAI-cutover"' in script
    assert 'if (-not $RetiredLivePath) {' in script
    assert '"$DeployPath-prepromotion-$(Get-ZetherionIsoTimestampForPath)"' in script
    assert 'docker compose down' in script
    assert 'Move-Item -LiteralPath $DeployPath -Destination $RetiredLivePath' in script
    assert 'Move-Item -LiteralPath $CandidatePath -Destination $DeployPath' in script
    assert 'actions = @()' in script
    assert '"archived_previous_live_tree"' in script
    assert '"promoted_clean_candidate"' in script
    assert 'status = "promoted"' in script
