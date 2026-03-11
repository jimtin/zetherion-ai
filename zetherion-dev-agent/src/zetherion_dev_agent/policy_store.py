"""SQLite-backed policy and cleanup history store for dev autopilot."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

PolicyMode = Literal["ask", "auto_clean", "never_clean"]
PendingStatus = Literal["pending", "approved", "denied", "expired"]


@dataclass(frozen=True)
class PendingApproval:
    """Pending approval record for a discovered project."""

    project_id: str
    first_seen_at: str
    last_prompted_at: str | None
    prompt_count: int
    status: PendingStatus

    @property
    def first_seen(self) -> datetime:
        return datetime.fromisoformat(self.first_seen_at)

    @property
    def last_prompted(self) -> datetime | None:
        if not self.last_prompted_at:
            return None
        return datetime.fromisoformat(self.last_prompted_at)


class PolicyStore:
    """Durable policy memory for per-project cleanup decisions."""

    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()

    def close(self) -> None:
        """Close the SQLite connection."""
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS project_policies (
                project_id   TEXT PRIMARY KEY,
                mode         TEXT NOT NULL CHECK(mode IN ('ask','auto_clean','never_clean')),
                approved_at  TEXT,
                source       TEXT NOT NULL DEFAULT 'unknown',
                updated_at   TEXT NOT NULL,
                notes        TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS pending_approvals (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id       TEXT NOT NULL UNIQUE,
                first_seen_at    TEXT NOT NULL,
                last_prompted_at TEXT,
                prompt_count     INTEGER NOT NULL DEFAULT 0,
                status           TEXT NOT NULL DEFAULT 'pending'
                               CHECK(status IN ('pending','approved','denied','expired'))
            );

            CREATE TABLE IF NOT EXISTS cleanup_runs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at       TEXT NOT NULL,
                project_id   TEXT NOT NULL,
                actions_json TEXT NOT NULL,
                dry_run      INTEGER NOT NULL,
                success      INTEGER NOT NULL,
                error        TEXT
            );

            CREATE TABLE IF NOT EXISTS runtime_meta (
                key          TEXT PRIMARY KEY,
                value        TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pending_worker_results (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          TEXT NOT NULL UNIQUE,
                payload_json    TEXT NOT NULL,
                relay_mode      TEXT NOT NULL DEFAULT 'direct_then_relay',
                status          TEXT NOT NULL DEFAULT 'pending'
                               CHECK(status IN ('pending','sent','failed')),
                attempt_count   INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TEXT,
                next_attempt_at TEXT,
                sent_at         TEXT,
                error_message   TEXT,
                created_at      TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat()

    # ------------------------------------------------------------------
    # Policy CRUD
    # ------------------------------------------------------------------

    def get_policy(self, project_id: str) -> PolicyMode | None:
        """Return policy mode for project, if configured."""
        row = self._conn.execute(
            "SELECT mode FROM project_policies WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if row is None:
            return None
        return str(row["mode"])  # type: ignore[return-value]

    def set_policy(
        self,
        project_id: str,
        mode: PolicyMode,
        *,
        source: str = "manual",
        notes: str = "",
    ) -> None:
        """Upsert a project policy and transition pending state."""
        now = self._now_iso()
        approved_at = now if mode == "auto_clean" else None
        self._conn.execute(
            """
            INSERT INTO project_policies (project_id, mode, approved_at, source, updated_at, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                mode = excluded.mode,
                approved_at = excluded.approved_at,
                source = excluded.source,
                updated_at = excluded.updated_at,
                notes = excluded.notes
            """,
            (project_id, mode, approved_at, source, now, notes),
        )
        pending_status: PendingStatus = "pending"
        if mode == "auto_clean":
            pending_status = "approved"
        elif mode == "never_clean":
            pending_status = "denied"
        self._conn.execute(
            """
            INSERT INTO pending_approvals (project_id, first_seen_at, status)
            VALUES (?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET status = excluded.status
            """,
            (project_id, now, pending_status),
        )
        self._conn.commit()

    def list_policies(self, *, mode: PolicyMode | None = None) -> list[dict[str, Any]]:
        """List policies, optionally filtered by mode."""
        if mode is None:
            rows = self._conn.execute(
                """
                SELECT project_id, mode, approved_at, source, updated_at, notes
                FROM project_policies
                ORDER BY project_id
                """
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT project_id, mode, approved_at, source, updated_at, notes
                FROM project_policies
                WHERE mode = ?
                ORDER BY project_id
                """,
                (mode,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Pending approvals
    # ------------------------------------------------------------------

    def record_project_discovery(self, project_id: str) -> bool:
        """Ensure project has a pending approval when no policy exists.

        Returns True when this discovery should trigger a fresh prompt.
        """
        if self.get_policy(project_id) is not None:
            return False

        now = self._now_iso()
        row = self._conn.execute(
            "SELECT status FROM pending_approvals WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if row is None:
            self._conn.execute(
                """
                INSERT INTO pending_approvals (
                    project_id, first_seen_at, last_prompted_at, prompt_count, status
                )
                VALUES (?, ?, NULL, 0, 'pending')
                """,
                (project_id, now),
            )
            self._conn.commit()
            return True

        if str(row["status"]) != "pending":
            self._conn.execute(
                """
                UPDATE pending_approvals
                SET status = 'pending',
                    first_seen_at = ?,
                    last_prompted_at = NULL,
                    prompt_count = 0
                WHERE project_id = ?
                """,
                (now, project_id),
            )
            self._conn.commit()
            return True
        return False

    def mark_prompted(self, project_id: str) -> None:
        """Record that a prompt was sent for this project."""
        now = self._now_iso()
        self._conn.execute(
            """
            UPDATE pending_approvals
            SET last_prompted_at = ?, prompt_count = prompt_count + 1
            WHERE project_id = ? AND status = 'pending'
            """,
            (now, project_id),
        )
        self._conn.commit()

    def list_pending_approvals(self) -> list[PendingApproval]:
        """Return pending approvals in deterministic order."""
        rows = self._conn.execute(
            """
            SELECT project_id, first_seen_at, last_prompted_at, prompt_count, status
            FROM pending_approvals
            WHERE status = 'pending'
            ORDER BY first_seen_at ASC, project_id ASC
            """
        ).fetchall()
        return [
            PendingApproval(
                project_id=str(row["project_id"]),
                first_seen_at=str(row["first_seen_at"]),
                last_prompted_at=str(row["last_prompted_at"]) if row["last_prompted_at"] else None,
                prompt_count=int(row["prompt_count"]),
                status=str(row["status"]),  # type: ignore[arg-type]
            )
            for row in rows
        ]

    def list_reprompt_due(self, *, reprompt_hours: int) -> list[PendingApproval]:
        """Return pending approvals that should be re-prompted."""
        threshold = datetime.now(UTC) - timedelta(hours=max(1, reprompt_hours))
        due: list[PendingApproval] = []
        for pending in self.list_pending_approvals():
            last_prompted = pending.last_prompted
            if last_prompted is None or last_prompted <= threshold:
                due.append(pending)
        return due

    # ------------------------------------------------------------------
    # Cleanup run history
    # ------------------------------------------------------------------

    def record_cleanup_run(
        self,
        *,
        project_id: str,
        actions: list[dict[str, Any]],
        dry_run: bool,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Persist a cleanup run record."""
        self._conn.execute(
            """
            INSERT INTO cleanup_runs (run_at, project_id, actions_json, dry_run, success, error)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                self._now_iso(),
                project_id,
                json.dumps(actions),
                int(dry_run),
                int(success),
                error,
            ),
        )
        self._conn.commit()

    def list_cleanup_runs(
        self,
        *,
        limit: int = 50,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return cleanup history, newest first."""
        if project_id:
            rows = self._conn.execute(
                """
                SELECT id, run_at, project_id, actions_json, dry_run, success, error
                FROM cleanup_runs
                WHERE project_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (project_id, max(1, limit)),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT id, run_at, project_id, actions_json, dry_run, success, error
                FROM cleanup_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            actions: list[dict[str, Any]] = []
            raw_actions = str(row["actions_json"] or "[]")
            try:
                parsed = json.loads(raw_actions)
                if isinstance(parsed, list):
                    actions = [dict(item) for item in parsed if isinstance(item, dict)]
            except json.JSONDecodeError:
                actions = []
            records.append(
                {
                    "id": int(row["id"]),
                    "run_at": str(row["run_at"]),
                    "project_id": str(row["project_id"]),
                    "actions": actions,
                    "dry_run": bool(row["dry_run"]),
                    "success": bool(row["success"]),
                    "error": str(row["error"]) if row["error"] else None,
                }
            )
        return records

    # ------------------------------------------------------------------
    # Runtime metadata
    # ------------------------------------------------------------------

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        """Fetch a runtime metadata value by key."""
        row = self._conn.execute(
            "SELECT value FROM runtime_meta WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return default
        return str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        """Persist a runtime metadata value."""
        self._conn.execute(
            """
            INSERT INTO runtime_meta (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, self._now_iso()),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Pending worker result spool
    # ------------------------------------------------------------------

    def enqueue_worker_result(
        self,
        *,
        job_id: str,
        payload: dict[str, Any],
        relay_mode: str = "direct_then_relay",
    ) -> None:
        now = self._now_iso()
        self._conn.execute(
            """
            INSERT INTO pending_worker_results (
                job_id,
                payload_json,
                relay_mode,
                status,
                attempt_count,
                last_attempt_at,
                next_attempt_at,
                sent_at,
                error_message,
                created_at
            ) VALUES (?, ?, ?, 'pending', 0, NULL, NULL, NULL, NULL, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                relay_mode = excluded.relay_mode,
                status = 'pending',
                error_message = NULL
            """,
            (job_id, json.dumps(payload), relay_mode, now),
        )
        self._conn.commit()

    def list_pending_worker_results(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT *
            FROM pending_worker_results
            WHERE status = 'pending'
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (self._now_iso(), max(1, limit)),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            payload: dict[str, Any] = {}
            try:
                parsed = json.loads(str(row["payload_json"] or "{}"))
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = {}
            results.append(
                {
                    "id": int(row["id"]),
                    "job_id": str(row["job_id"]),
                    "payload": payload,
                    "relay_mode": str(row["relay_mode"]),
                    "attempt_count": int(row["attempt_count"]),
                    "status": str(row["status"]),
                    "error_message": str(row["error_message"]) if row["error_message"] else None,
                }
            )
        return results

    def mark_worker_result_sent(self, row_id: int) -> None:
        now = self._now_iso()
        self._conn.execute(
            """
            UPDATE pending_worker_results
            SET status = 'sent',
                sent_at = ?,
                last_attempt_at = ?,
                attempt_count = attempt_count + 1,
                error_message = NULL
            WHERE id = ?
            """,
            (now, now, row_id),
        )
        self._conn.commit()

    def mark_worker_result_failed(
        self,
        row_id: int,
        error_message: str,
        retry_at: datetime,
    ) -> None:
        current = self._conn.execute(
            "SELECT attempt_count FROM pending_worker_results WHERE id = ? LIMIT 1",
            (row_id,),
        ).fetchone()
        next_attempt_count = int(current["attempt_count"] or 0) + 1 if current else 1
        status = "failed" if next_attempt_count >= 10 else "pending"
        self._conn.execute(
            """
            UPDATE pending_worker_results
            SET status = ?,
                error_message = ?,
                last_attempt_at = ?,
                next_attempt_at = ?,
                attempt_count = attempt_count + 1
            WHERE id = ?
            """,
            (
                status,
                error_message,
                self._now_iso(),
                retry_at.astimezone(UTC).isoformat(),
                row_id,
            ),
        )
        self._conn.commit()
