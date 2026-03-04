"""Local encrypted backup manager for PostgreSQL, Qdrant, and host state."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess  # nosec B404
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

INTERNAL_MANIFEST_NAME = "manifest.json"
CHECKSUMS_NAME = "checksums.txt"
POSTGRES_DUMP_NAME = "postgres.sql"
QDRANT_ARCHIVE_NAME = "qdrant-storage.tar.gz"
STATE_ARCHIVE_NAME = "state.tar.gz"
BACKUP_ARCHIVE_SUFFIX = ".tar.gz.age"


class BackupError(RuntimeError):
    """Raised when backup operations fail."""


@dataclass(frozen=True)
class CommandResult:
    """Result from a command execution."""

    returncode: int
    stdout: bytes
    stderr: bytes


class CommandRunner(Protocol):
    """Protocol for command execution."""

    def run(self, command: list[str], *, input_bytes: bytes | None = None) -> CommandResult:
        """Run a command and return the process result."""


class CryptoAdapter(Protocol):
    """Protocol for archive encryption/decryption."""

    def encrypt(self, input_path: Path, output_path: Path, recipient: str) -> None:
        """Encrypt the input file to output path."""

    def decrypt(self, input_path: Path, output_path: Path, identity_path: Path) -> None:
        """Decrypt the input file to output path."""


class SubprocessCommandRunner:
    """Executes subprocess commands with captured output."""

    def run(self, command: list[str], *, input_bytes: bytes | None = None) -> CommandResult:
        """Run a command and return command output.

        Raises:
            BackupError: If command execution fails or command is unavailable.
        """
        try:
            completed = subprocess.run(  # nosec B603
                command,
                input=input_bytes,
                capture_output=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise BackupError(f"Required command not found: {command[0]}") from exc

        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise BackupError(
                f"Command failed ({completed.returncode}): {' '.join(command)}"
                + (f"\n{stderr}" if stderr else "")
            )

        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


@dataclass
class AgeCryptoAdapter:
    """Encrypt/decrypt backups using age CLI."""

    runner: CommandRunner

    def encrypt(self, input_path: Path, output_path: Path, recipient: str) -> None:
        """Encrypt file with age recipient public key."""
        if not recipient.strip():
            raise BackupError("Age recipient is required for encryption.")

        self.runner.run(
            [
                "age",
                "--encrypt",
                "--recipient",
                recipient,
                "--output",
                str(output_path),
                str(input_path),
            ]
        )

    def decrypt(self, input_path: Path, output_path: Path, identity_path: Path) -> None:
        """Decrypt file with age private identity key."""
        if not identity_path.exists():
            raise BackupError(f"Age identity file does not exist: {identity_path}")

        self.runner.run(
            [
                "age",
                "--decrypt",
                "--identity",
                str(identity_path),
                "--output",
                str(output_path),
                str(input_path),
            ]
        )


@dataclass(frozen=True)
class BackupSummary:
    """Summary metadata returned by backup operations."""

    backup_id: str
    archive_path: Path
    archive_sha256: str
    component_count: int
    created_at: str
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert summary to serializable dictionary."""
        return {
            "backup_id": self.backup_id,
            "archive_path": str(self.archive_path),
            "archive_sha256": self.archive_sha256,
            "component_count": self.component_count,
            "created_at": self.created_at,
            "dry_run": self.dry_run,
        }


@dataclass
class BackupManager:
    """Handles backup creation, verification, and restore workflows."""

    backup_dir: Path = field(default_factory=lambda: Path.home() / ".zetherion-backups")
    state_dir: Path = field(default_factory=lambda: Path("data"))
    compose_file: Path = field(default_factory=lambda: Path("docker-compose.yml"))
    postgres_service: str = "postgres"
    postgres_user: str = "zetherion"
    postgres_db: str = "zetherion"
    qdrant_service: str = "qdrant"
    retention_count: int = 14
    runner: CommandRunner = field(default_factory=SubprocessCommandRunner)
    crypto: CryptoAdapter | None = None

    def __post_init__(self) -> None:
        """Initialize dependent defaults."""
        self.backup_dir = self.backup_dir.expanduser().resolve()
        self.state_dir = self.state_dir.expanduser()
        self.compose_file = self.compose_file.expanduser()
        if self.retention_count < 1:
            raise BackupError("Retention count must be >= 1.")
        if self.crypto is None:
            self.crypto = AgeCryptoAdapter(self.runner)

    def create_backup(self, *, age_recipient: str) -> BackupSummary:
        """Create encrypted backup archive.

        The archive contains:
        - PostgreSQL SQL dump
        - Qdrant storage tarball
        - Host state tarball (defaults to ./data)
        - Internal manifest and checksums
        """
        created_at = datetime.now(UTC)
        backup_id = created_at.strftime("backup-%Y%m%dT%H%M%S%fZ")
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="zetherion-backup-create-") as temp_dir:
            staging_dir = Path(temp_dir)

            postgres_dump_path = staging_dir / POSTGRES_DUMP_NAME
            postgres_dump_path.write_bytes(self._collect_postgres_dump())

            qdrant_archive_path = staging_dir / QDRANT_ARCHIVE_NAME
            qdrant_archive_path.write_bytes(self._collect_qdrant_archive())

            state_archive_path = staging_dir / STATE_ARCHIVE_NAME
            self._archive_state(state_archive_path)

            components = [
                postgres_dump_path,
                qdrant_archive_path,
                state_archive_path,
            ]
            checksums = {path.name: _sha256_file(path) for path in components}
            manifest = self._build_internal_manifest(
                backup_id=backup_id,
                created_at=created_at,
                components=components,
                checksums=checksums,
            )

            manifest_path = staging_dir / INTERNAL_MANIFEST_NAME
            checksums_path = staging_dir / CHECKSUMS_NAME
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            _write_checksums_file(checksums_path, checksums)

            payload_path = staging_dir / "payload.tar.gz"
            with tarfile.open(payload_path, mode="w:gz") as payload_tar:
                for artifact in [*components, manifest_path, checksums_path]:
                    payload_tar.add(artifact, arcname=artifact.name)

            archive_path = self.backup_dir / f"{backup_id}{BACKUP_ARCHIVE_SUFFIX}"
            assert self.crypto is not None
            self.crypto.encrypt(payload_path, archive_path, age_recipient)

        archive_sha256 = _sha256_file(archive_path)
        sidecar_manifest_path = self._sidecar_manifest_path(backup_id)
        sidecar_checksum_path = self._sidecar_checksum_path(backup_id)
        sidecar_manifest_path.write_text(
            json.dumps(
                {
                    "backup_id": backup_id,
                    "created_at": created_at.isoformat(),
                    "archive_name": archive_path.name,
                    "archive_sha256": archive_sha256,
                    "retention_count": self.retention_count,
                    "component_count": len(manifest["components"]),
                    "state_dir": str(self.state_dir),
                    "compose_file": str(self.compose_file),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        sidecar_checksum_path.write_text(
            f"{archive_sha256}  {archive_path.name}\n",
            encoding="utf-8",
        )

        self._prune_old_archives()

        return BackupSummary(
            backup_id=backup_id,
            archive_path=archive_path,
            archive_sha256=archive_sha256,
            component_count=len(manifest["components"]),
            created_at=created_at.isoformat(),
        )

    def verify_backup(self, *, archive_path: Path, identity_path: Path) -> BackupSummary:
        """Decrypt and validate backup archive integrity."""
        archive_path = archive_path.expanduser().resolve()
        identity_path = identity_path.expanduser().resolve()
        backup_id = _backup_id_from_archive_name(archive_path.name)

        with tempfile.TemporaryDirectory(prefix="zetherion-backup-verify-") as temp_dir:
            temp_root = Path(temp_dir)
            payload_path = temp_root / "payload.tar.gz"
            extract_dir = temp_root / "payload"
            extract_dir.mkdir(parents=True, exist_ok=True)

            assert self.crypto is not None
            self.crypto.decrypt(archive_path, payload_path, identity_path)
            _safe_unpack_tar(payload_path, extract_dir)

            manifest = self._validate_payload_integrity(extract_dir)
            archive_sha256 = _sha256_file(archive_path)
            self._validate_sidecar_checksum(backup_id, archive_path, archive_sha256)

            return BackupSummary(
                backup_id=backup_id,
                archive_path=archive_path,
                archive_sha256=archive_sha256,
                component_count=len(manifest["components"]),
                created_at=str(manifest["created_at"]),
            )

    def restore_backup(
        self,
        *,
        archive_path: Path,
        identity_path: Path,
        dry_run: bool = False,
    ) -> BackupSummary:
        """Restore backup archive into active services and state paths."""
        archive_path = archive_path.expanduser().resolve()
        identity_path = identity_path.expanduser().resolve()
        backup_id = _backup_id_from_archive_name(archive_path.name)

        with tempfile.TemporaryDirectory(prefix="zetherion-backup-restore-") as temp_dir:
            temp_root = Path(temp_dir)
            payload_path = temp_root / "payload.tar.gz"
            extract_dir = temp_root / "payload"
            extract_dir.mkdir(parents=True, exist_ok=True)

            assert self.crypto is not None
            self.crypto.decrypt(archive_path, payload_path, identity_path)
            _safe_unpack_tar(payload_path, extract_dir)
            manifest = self._validate_payload_integrity(extract_dir)

            if dry_run:
                archive_sha256 = _sha256_file(archive_path)
                return BackupSummary(
                    backup_id=backup_id,
                    archive_path=archive_path,
                    archive_sha256=archive_sha256,
                    component_count=len(manifest["components"]),
                    created_at=str(manifest["created_at"]),
                    dry_run=True,
                )

            self._restore_postgres(extract_dir / POSTGRES_DUMP_NAME)
            self._restore_state(extract_dir / STATE_ARCHIVE_NAME)
            self._restore_qdrant(extract_dir / QDRANT_ARCHIVE_NAME)
            self._run_restore_smoke_checks()

            archive_sha256 = _sha256_file(archive_path)
            self._validate_sidecar_checksum(backup_id, archive_path, archive_sha256)
            return BackupSummary(
                backup_id=backup_id,
                archive_path=archive_path,
                archive_sha256=archive_sha256,
                component_count=len(manifest["components"]),
                created_at=str(manifest["created_at"]),
            )

    def _collect_postgres_dump(self) -> bytes:
        command = self._compose_command(
            [
                "exec",
                "-T",
                self.postgres_service,
                "pg_dump",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                "-U",
                self.postgres_user,
                self.postgres_db,
            ]
        )
        return self.runner.run(command).stdout

    def _collect_qdrant_archive(self) -> bytes:
        command = self._compose_command(
            [
                "exec",
                "-T",
                self.qdrant_service,
                "sh",
                "-lc",
                "cd /qdrant && tar -czf - storage",
            ]
        )
        return self.runner.run(command).stdout

    def _archive_state(self, destination: Path) -> None:
        with tempfile.TemporaryDirectory(prefix="zetherion-state-archive-") as temp_dir:
            temp_root = Path(temp_dir)
            with tarfile.open(destination, mode="w:gz") as state_tar:
                if self.state_dir.exists():
                    state_tar.add(self.state_dir, arcname=self.state_dir.name)
                else:
                    marker = temp_root / "state-missing.txt"
                    marker.write_text(
                        f"State directory did not exist at backup time: {self.state_dir}\n",
                        encoding="utf-8",
                    )
                    state_tar.add(marker, arcname=marker.name)

    def _build_internal_manifest(
        self,
        *,
        backup_id: str,
        created_at: datetime,
        components: list[Path],
        checksums: dict[str, str],
    ) -> dict[str, Any]:
        return {
            "version": 1,
            "backup_id": backup_id,
            "created_at": created_at.isoformat(),
            "components": [
                {
                    "name": component.name,
                    "sha256": checksums[component.name],
                    "size_bytes": component.stat().st_size,
                }
                for component in components
            ],
            "services": {
                "postgres": {
                    "service": self.postgres_service,
                    "database": self.postgres_db,
                    "user": self.postgres_user,
                },
                "qdrant": {
                    "service": self.qdrant_service,
                },
            },
            "host_state": {
                "source_path": str(self.state_dir),
                "archive_name": STATE_ARCHIVE_NAME,
            },
        }

    def _validate_payload_integrity(self, extract_dir: Path) -> dict[str, Any]:
        manifest_path = extract_dir / INTERNAL_MANIFEST_NAME
        checksums_path = extract_dir / CHECKSUMS_NAME
        if not manifest_path.exists():
            raise BackupError(f"Missing manifest in payload: {manifest_path}")
        if not checksums_path.exists():
            raise BackupError(f"Missing checksums in payload: {checksums_path}")

        try:
            manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BackupError(f"Invalid internal manifest JSON: {manifest_path}") from exc

        if not isinstance(manifest_raw, dict):
            raise BackupError("Internal manifest root must be a JSON object.")

        manifest: dict[str, Any] = manifest_raw
        components = manifest.get("components")
        if not isinstance(components, list) or not components:
            raise BackupError("Manifest components are missing or invalid.")

        expected_checksums = _read_checksums_file(checksums_path)
        for component in components:
            name = str(component.get("name", "")).strip()
            expected = str(component.get("sha256", "")).strip()
            if not name or not expected:
                raise BackupError("Manifest component entry is incomplete.")
            if name not in expected_checksums:
                raise BackupError(f"Missing checksum entry for component: {name}")
            if expected_checksums[name] != expected:
                raise BackupError(f"Checksum mismatch between manifest and checksums file: {name}")
            component_path = extract_dir / name
            if not component_path.exists():
                raise BackupError(f"Missing payload component: {component_path}")
            actual = _sha256_file(component_path)
            if actual != expected:
                raise BackupError(
                    f"Checksum verification failed for {name}. Expected {expected}, got {actual}."
                )

        return manifest

    def _validate_sidecar_checksum(
        self,
        backup_id: str,
        archive_path: Path,
        archive_sha256: str,
    ) -> None:
        checksum_path = self._sidecar_checksum_path(backup_id)
        if not checksum_path.exists():
            return
        checksums = _read_checksums_file(checksum_path)
        if archive_path.name not in checksums:
            raise BackupError(
                f"Sidecar checksum file does not include archive entry: {checksum_path}"
            )
        expected = checksums[archive_path.name]
        if expected != archive_sha256:
            raise BackupError(
                f"Archive checksum mismatch for {archive_path.name}. "
                f"Expected {expected}, got {archive_sha256}."
            )

    def _restore_postgres(self, dump_path: Path) -> None:
        if not dump_path.exists():
            raise BackupError(f"PostgreSQL dump not found in payload: {dump_path}")
        command = self._compose_command(
            [
                "exec",
                "-T",
                self.postgres_service,
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                self.postgres_user,
                self.postgres_db,
            ]
        )
        self.runner.run(command, input_bytes=dump_path.read_bytes())

    def _restore_qdrant(self, qdrant_archive_path: Path) -> None:
        if not qdrant_archive_path.exists():
            raise BackupError(f"Qdrant archive not found in payload: {qdrant_archive_path}")

        self.runner.run(self._compose_command(["stop", self.qdrant_service]))
        try:
            restore_cmd = self._compose_command(
                [
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    self.qdrant_service,
                    "sh",
                    "-lc",
                    "mkdir -p /qdrant/storage && rm -rf /qdrant/storage/* && tar -xzf - -C /qdrant",
                ]
            )
            self.runner.run(restore_cmd, input_bytes=qdrant_archive_path.read_bytes())
        finally:
            self.runner.run(self._compose_command(["up", "-d", self.qdrant_service]))

    def _restore_state(self, state_archive_path: Path) -> None:
        if not state_archive_path.exists():
            raise BackupError(f"State archive not found in payload: {state_archive_path}")
        target_parent = self.state_dir.parent.resolve()
        target_parent.mkdir(parents=True, exist_ok=True)

        if self.state_dir.exists():
            if self.state_dir.is_dir():
                shutil.rmtree(self.state_dir)
            else:
                self.state_dir.unlink()

        _safe_unpack_tar(state_archive_path, target_parent)

    def _run_restore_smoke_checks(self) -> None:
        postgres_check = self._compose_command(
            [
                "exec",
                "-T",
                self.postgres_service,
                "psql",
                "-U",
                self.postgres_user,
                self.postgres_db,
                "-c",
                "SELECT 1;",
            ]
        )
        self.runner.run(postgres_check)

        qdrant_check = self._compose_command(
            [
                "exec",
                "-T",
                self.qdrant_service,
                "sh",
                "-lc",
                "test -d /qdrant/storage",
            ]
        )
        self.runner.run(qdrant_check)

        if not self.state_dir.exists():
            raise BackupError(f"State directory restore smoke check failed: {self.state_dir}")

    def _compose_command(self, extra_args: list[str]) -> list[str]:
        command = ["docker", "compose", "-f", str(self.compose_file)]
        command.extend(extra_args)
        return command

    def _prune_old_archives(self) -> None:
        archives = sorted(
            self.backup_dir.glob(f"*{BACKUP_ARCHIVE_SUFFIX}"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        stale_archives = archives[self.retention_count :]
        for archive_path in stale_archives:
            backup_id = _backup_id_from_archive_name(archive_path.name)
            archive_path.unlink(missing_ok=True)
            self._sidecar_manifest_path(backup_id).unlink(missing_ok=True)
            self._sidecar_checksum_path(backup_id).unlink(missing_ok=True)

    def _sidecar_manifest_path(self, backup_id: str) -> Path:
        return self.backup_dir / f"{backup_id}.manifest.json"

    def _sidecar_checksum_path(self, backup_id: str) -> Path:
        return self.backup_dir / f"{backup_id}.sha256"


def _safe_unpack_tar(archive_path: Path, target_dir: Path) -> None:
    """Unpack tar archive with path traversal protection."""
    target_dir.mkdir(parents=True, exist_ok=True)
    target_root = target_dir.resolve()

    with tarfile.open(archive_path, mode="r:*") as archive:
        for member in archive.getmembers():
            member_path = (target_dir / member.name).resolve()
            if not str(member_path).startswith(str(target_root)):
                raise BackupError(f"Unsafe archive member path detected: {member.name}")
            if member.issym() or member.islnk():
                raise BackupError(f"Symlink entries are not supported in archives: {member.name}")

            if member.isdir():
                member_path.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue

            member_path.parent.mkdir(parents=True, exist_ok=True)
            extracted = archive.extractfile(member)
            if extracted is None:
                raise BackupError(f"Failed to extract archive member: {member.name}")
            member_path.write_bytes(extracted.read())


def _write_checksums_file(path: Path, checksums: dict[str, str]) -> None:
    lines = [f"{digest}  {filename}" for filename, digest in sorted(checksums.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_checksums_file(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        digest, filename = line.split(maxsplit=1)
        checksums[filename.strip()] = digest.strip()
    return checksums


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_id_from_archive_name(name: str) -> str:
    if not name.endswith(BACKUP_ARCHIVE_SUFFIX):
        raise BackupError(f"Backup archive filename must end with {BACKUP_ARCHIVE_SUFFIX}: {name}")
    return name[: -len(BACKUP_ARCHIVE_SUFFIX)]
