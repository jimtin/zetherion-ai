from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FORENSIC_PATH = REPO_ROOT / "scripts" / "windows" / "capture-forensic-receipt.ps1"


def test_forensic_receipt_script_captures_expected_windows_state() -> None:
    script = FORENSIC_PATH.read_text(encoding="utf-8")

    assert '[string]$WslDistribution = "Ubuntu"' in script
    assert "$env:ZETHERION_WSL_DISTRIBUTION = $WslDistribution" in script
    assert "Get-GitForensics" in script
    assert "Get-WslFacts" in script
    assert "Get-WslHostConfigFacts" in script
    assert "Get-ZetherionWslHostConfig" in script
    assert "Get-DockerFacts" in script
    assert "Get-ScheduledTaskFacts" in script
    assert "Get-ZetherionDockerDesktopStatus" in script
    assert "desktop_status = $desktopStatus" in script
    assert '"ZetherionWslKeepalive"' in script
    assert 'execution_backend = "wsl_docker"' in script
    assert 'docker_backend = "wsl_docker"' in script
    assert "wsl_host_config = Get-WslHostConfigFacts" in script
    assert "Forensic receipt written to" in script
