"""Tests for local encrypted backup manager."""

from __future__ import annotations

import io
import os
import shutil
import tarfile
from pathlib import Path

import pytest

from zetherion_ai.backup.local_backup import (
    BACKUP_ARCHIVE_SUFFIX,
    BackupError,
    BackupManager,
    CommandResult,
)


def _build_qdrant_tarball_bytes() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        payload = b"qdrant-state"
        info = tarfile.TarInfo(name="storage/collections/.keep")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


class FakeRunner:
    """Test double for command execution."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], bytes | None]] = []
        self.postgres_dump = b"-- postgres dump\nSELECT 1;\n"
        self.qdrant_archive = _build_qdrant_tarball_bytes()

    def run(self, command: list[str], *, input_bytes: bytes | None = None) -> CommandResult:
        self.calls.append((list(command), input_bytes))
        command_text = " ".join(command)

        if "pg_dump" in command:
            return CommandResult(returncode=0, stdout=self.postgres_dump, stderr=b"")
        if "tar -czf - storage" in command_text:
            return CommandResult(returncode=0, stdout=self.qdrant_archive, stderr=b"")
        return CommandResult(returncode=0, stdout=b"", stderr=b"")


class CopyCrypto:
    """Crypto adapter for tests (copy-only)."""

    def encrypt(self, input_path: Path, output_path: Path, recipient: str) -> None:
        del recipient
        shutil.copy2(input_path, output_path)

    def decrypt(self, input_path: Path, output_path: Path, identity_path: Path) -> None:
        del identity_path
        shutil.copy2(input_path, output_path)


def _backup_id_for_archive(archive_path: Path) -> str:
    return archive_path.name.removesuffix(BACKUP_ARCHIVE_SUFFIX)


def _build_manager(
    tmp_path: Path,
    *,
    runner: FakeRunner,
    retention_count: int = 3,
) -> BackupManager:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "updater-state.json").write_text('{"version":"1.0.0"}\n', encoding="utf-8")

    return BackupManager(
        backup_dir=tmp_path / "backups",
        state_dir=state_dir,
        compose_file=tmp_path / "docker-compose.yml",
        postgres_service="postgres",
        postgres_user="zetherion",
        postgres_db="zetherion",
        qdrant_service="qdrant",
        retention_count=retention_count,
        runner=runner,
        crypto=CopyCrypto(),
    )


def test_create_and_verify_backup_with_manifest_and_checksums(tmp_path: Path) -> None:
    runner = FakeRunner()
    manager = _build_manager(tmp_path, runner=runner)
    identity = tmp_path / "age-identity.txt"
    identity.write_text("AGE-SECRET-KEY-TEST", encoding="utf-8")

    summary = manager.create_backup(age_recipient="age1examplepublickey")
    backup_id = _backup_id_for_archive(summary.archive_path)

    assert summary.archive_path.exists()
    assert summary.component_count == 3
    assert (manager.backup_dir / f"{backup_id}.manifest.json").exists()
    assert (manager.backup_dir / f"{backup_id}.sha256").exists()

    verify_summary = manager.verify_backup(
        archive_path=summary.archive_path,
        identity_path=identity,
    )
    assert verify_summary.archive_sha256 == summary.archive_sha256
    assert verify_summary.component_count == 3
    assert any("pg_dump" in call for call, _ in runner.calls)
    assert any("tar -czf - storage" in " ".join(call) for call, _ in runner.calls)


def test_backup_retention_prunes_old_archives(tmp_path: Path) -> None:
    runner = FakeRunner()
    manager = _build_manager(tmp_path, runner=runner, retention_count=2)

    archives: list[Path] = []
    for index in range(3):
        summary = manager.create_backup(age_recipient="age1examplepublickey")
        archives.append(summary.archive_path)
        # Stabilize mtime ordering for deterministic prune behavior.
        ts = float(1000 + index)
        os.utime(summary.archive_path, (ts, ts))

    remaining_archives = sorted(manager.backup_dir.glob(f"*{BACKUP_ARCHIVE_SUFFIX}"))
    assert len(remaining_archives) == 2

    oldest_backup_id = _backup_id_for_archive(archives[0])
    assert not (manager.backup_dir / f"{oldest_backup_id}.manifest.json").exists()
    assert not (manager.backup_dir / f"{oldest_backup_id}.sha256").exists()


def test_restore_applies_state_and_runs_smoke_checks(tmp_path: Path) -> None:
    runner = FakeRunner()
    manager = _build_manager(tmp_path, runner=runner)
    identity = tmp_path / "age-identity.txt"
    identity.write_text("AGE-SECRET-KEY-TEST", encoding="utf-8")

    summary = manager.create_backup(age_recipient="age1examplepublickey")

    # Simulate fresh install / accidental deletion before restore.
    shutil.rmtree(manager.state_dir)
    restore_summary = manager.restore_backup(
        archive_path=summary.archive_path,
        identity_path=identity,
    )

    assert restore_summary.component_count == 3
    assert (manager.state_dir / "updater-state.json").exists()
    restored_content = (manager.state_dir / "updater-state.json").read_text(encoding="utf-8")
    assert '"version":"1.0.0"' in restored_content

    command_texts = [" ".join(command) for command, _ in runner.calls]
    assert any("psql -v ON_ERROR_STOP=1" in text for text in command_texts)
    assert any("compose -f" in text and "stop qdrant" in text for text in command_texts)
    assert any("run --rm --no-deps -T qdrant" in text for text in command_texts)
    assert any("SELECT 1;" in text for text in command_texts)


def test_verify_fails_when_sidecar_checksum_mismatch(tmp_path: Path) -> None:
    runner = FakeRunner()
    manager = _build_manager(tmp_path, runner=runner)
    identity = tmp_path / "age-identity.txt"
    identity.write_text("AGE-SECRET-KEY-TEST", encoding="utf-8")

    summary = manager.create_backup(age_recipient="age1examplepublickey")
    backup_id = _backup_id_for_archive(summary.archive_path)
    checksum_file = manager.backup_dir / f"{backup_id}.sha256"
    checksum_file.write_text(
        f"{'0' * 64}  {summary.archive_path.name}\n",
        encoding="utf-8",
    )

    with pytest.raises(BackupError, match="Archive checksum mismatch"):
        manager.verify_backup(
            archive_path=summary.archive_path,
            identity_path=identity,
        )


def test_domain_aware_qdrant_backup_and_restore(tmp_path: Path) -> None:
    runner = FakeRunner()
    manager = _build_manager(tmp_path, runner=runner)
    manager.qdrant_services_by_domain = {
        "owner_personal": "qdrant-owner",
        "tenant_raw": "qdrant-tenant",
    }
    manager.__post_init__()
    identity = tmp_path / "age-identity.txt"
    identity.write_text("AGE-SECRET-KEY-TEST", encoding="utf-8")

    summary = manager.create_backup(age_recipient="age1examplepublickey")
    restore_summary = manager.restore_backup(
        archive_path=summary.archive_path,
        identity_path=identity,
    )

    assert restore_summary.component_count == 4
    command_texts = [" ".join(command) for command, _ in runner.calls]
    assert any(
        "exec -T qdrant-owner" in text and "tar -czf - storage" in text for text in command_texts
    )
    assert any(
        "exec -T qdrant-tenant" in text and "tar -czf - storage" in text for text in command_texts
    )
    assert any("compose -f" in text and "stop qdrant-owner" in text for text in command_texts)
    assert any("compose -f" in text and "stop qdrant-tenant" in text for text in command_texts)
