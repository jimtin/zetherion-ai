#!/usr/bin/env python3
"""CLI for local encrypted backup, verification, and restore."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from zetherion_ai.backup.local_backup import BackupError, BackupManager


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--backup-dir",
        default=os.getenv("BACKUP_DIRECTORY", str(Path.home() / ".zetherion-backups")),
        help="Directory for encrypted backup archives (default: %(default)s).",
    )
    parser.add_argument(
        "--retention-count",
        type=int,
        default=int(os.getenv("BACKUP_RETENTION_COUNT", "14")),
        help="Number of archives to retain (default: %(default)s).",
    )
    parser.add_argument(
        "--state-dir",
        default=os.getenv("BACKUP_STATE_DIR", "data"),
        help="Persistent host state path to archive/restore (default: %(default)s).",
    )
    parser.add_argument(
        "--compose-file",
        default=os.getenv("BACKUP_COMPOSE_FILE", "docker-compose.yml"),
        help="Docker compose file used for service access (default: %(default)s).",
    )
    parser.add_argument(
        "--postgres-service",
        default=os.getenv("BACKUP_POSTGRES_SERVICE", "postgres"),
        help="PostgreSQL service name in docker compose (default: %(default)s).",
    )
    parser.add_argument(
        "--postgres-user",
        default=os.getenv("BACKUP_POSTGRES_USER", "zetherion"),
        help="PostgreSQL user used for dump/restore (default: %(default)s).",
    )
    parser.add_argument(
        "--postgres-db",
        default=os.getenv("BACKUP_POSTGRES_DB", "zetherion"),
        help="PostgreSQL database used for dump/restore (default: %(default)s).",
    )
    parser.add_argument(
        "--qdrant-service",
        default=os.getenv("BACKUP_QDRANT_SERVICE", "qdrant"),
        help="Legacy/default Qdrant service name in docker compose (default: %(default)s).",
    )
    parser.add_argument(
        "--qdrant-owner-service",
        default=os.getenv("BACKUP_QDRANT_OWNER_SERVICE", ""),
        help="Optional owner-domain Qdrant service name for domain-aware backups.",
    )
    parser.add_argument(
        "--qdrant-tenant-service",
        default=os.getenv("BACKUP_QDRANT_TENANT_SERVICE", ""),
        help="Optional tenant-domain Qdrant service name for domain-aware backups.",
    )
    return parser


def _build_manager(args: argparse.Namespace) -> BackupManager:
    qdrant_services_by_domain: dict[str, str] = {}
    if str(getattr(args, "qdrant_owner_service", "") or "").strip():
        qdrant_services_by_domain["owner_personal"] = str(args.qdrant_owner_service).strip()
    if str(getattr(args, "qdrant_tenant_service", "") or "").strip():
        qdrant_services_by_domain["tenant_raw"] = str(args.qdrant_tenant_service).strip()

    return BackupManager(
        backup_dir=Path(args.backup_dir),
        state_dir=Path(args.state_dir),
        compose_file=Path(args.compose_file),
        postgres_service=args.postgres_service,
        postgres_user=args.postgres_user,
        postgres_db=args.postgres_db,
        qdrant_service=args.qdrant_service,
        qdrant_services_by_domain=qdrant_services_by_domain,
        retention_count=args.retention_count,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage local encrypted backups for Zetherion.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", parents=[_common_parser()])
    create_parser.add_argument(
        "--age-recipient",
        default=os.getenv("BACKUP_AGE_RECIPIENT", ""),
        help="age recipient public key (default: BACKUP_AGE_RECIPIENT env var).",
    )

    verify_parser = subparsers.add_parser("verify", parents=[_common_parser()])
    verify_parser.add_argument(
        "--archive",
        required=True,
        help="Path to encrypted backup archive (*.tar.gz.age).",
    )
    verify_parser.add_argument(
        "--age-identity",
        default=os.getenv("BACKUP_AGE_IDENTITY", ""),
        help="Path to age private identity key (default: BACKUP_AGE_IDENTITY env var).",
    )

    restore_parser = subparsers.add_parser("restore", parents=[_common_parser()])
    restore_parser.add_argument(
        "--archive",
        required=True,
        help="Path to encrypted backup archive (*.tar.gz.age).",
    )
    restore_parser.add_argument(
        "--age-identity",
        default=os.getenv("BACKUP_AGE_IDENTITY", ""),
        help="Path to age private identity key (default: BACKUP_AGE_IDENTITY env var).",
    )
    restore_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate archive and checksums without applying restore actions.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    manager = _build_manager(args)

    try:
        if args.command == "create":
            if not args.age_recipient.strip():
                raise BackupError(
                    "Missing age recipient. Set BACKUP_AGE_RECIPIENT or pass --age-recipient."
                )
            summary = manager.create_backup(age_recipient=args.age_recipient)
        elif args.command == "verify":
            if not args.age_identity.strip():
                raise BackupError(
                    "Missing age identity. Set BACKUP_AGE_IDENTITY or pass --age-identity."
                )
            summary = manager.verify_backup(
                archive_path=Path(args.archive),
                identity_path=Path(args.age_identity),
            )
        else:
            if not args.age_identity.strip():
                raise BackupError(
                    "Missing age identity. Set BACKUP_AGE_IDENTITY or pass --age-identity."
                )
            summary = manager.restore_backup(
                archive_path=Path(args.archive),
                identity_path=Path(args.age_identity),
                dry_run=bool(args.dry_run),
            )
    except BackupError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
