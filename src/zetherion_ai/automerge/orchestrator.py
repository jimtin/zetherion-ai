"""Autonomous PR orchestration with deterministic guardrails."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.github.models import PullRequest

log = get_logger("zetherion_ai.automerge.orchestrator")

_MERGE_METHODS = {"merge", "squash", "rebase"}
_GREEN_CHECK_CONCLUSIONS = {"success", "neutral", "skipped"}


class GitHubAutomergeAPI(Protocol):
    """GitHub API protocol used by the automerge orchestrator."""

    async def ensure_branch(
        self,
        owner: str,
        repo: str,
        *,
        branch: str,
        source_ref: str,
    ) -> dict[str, Any]:
        """Ensure ``branch`` exists, creating from ``source_ref`` when missing."""

    async def find_open_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        head: str,
        base: str,
    ) -> PullRequest | None:
        """Find an open pull request for ``head`` -> ``base``."""

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        head: str,
        base: str,
        body: str = "",
        draft: bool = False,
    ) -> PullRequest:
        """Create a pull request."""

    async def get_pull_request(self, owner: str, repo: str, pr_number: int) -> PullRequest:
        """Fetch one pull request by number."""

    async def list_pull_request_files(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        per_page: int = 100,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """List changed files for one pull request."""

    async def list_check_runs(
        self,
        owner: str,
        repo: str,
        *,
        ref: str,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """List check runs for a ref (branch or sha)."""

    async def merge_pull_request(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        commit_title: str | None = None,
        commit_message: str | None = None,
        merge_method: str = "squash",
    ) -> dict[str, Any]:
        """Merge one pull request."""


@dataclass(frozen=True)
class AutomergeGuardrails:
    """Hard guardrails for autonomous PR promotion."""

    allowed_paths: tuple[str, ...] = ()
    max_changed_files: int = 120
    max_additions: int = 6000
    max_deletions: int = 3000
    required_checks: tuple[str, ...] = ("CI/CD Pipeline",)
    forbidden_actions: tuple[str, ...] = (
        "git.force_push",
        "git.reset.hard",
        "git.delete_branch",
        "git.rewrite_history",
    )


@dataclass(frozen=True)
class AutomergeExecutionRequest:
    """Automerge orchestration input payload."""

    tenant_id: str
    repository: str
    base_branch: str = "main"
    source_ref: str | None = None
    head_branch: str | None = None
    pr_title: str | None = None
    pr_body: str = ""
    merge_method: str = "squash"
    commit_title: str | None = None
    commit_message: str | None = None
    requested_actions: tuple[str, ...] = ()
    guardrails: AutomergeGuardrails = field(default_factory=AutomergeGuardrails)
    post_merge_validation_passed: bool = True


@dataclass(frozen=True)
class AutomergeExecutionResult:
    """Structured orchestration result."""

    status: str
    tenant_id: str
    repository: str
    base_branch: str
    head_branch: str
    pr_number: int | None
    pr_url: str | None
    branch_created: bool
    decision: str
    reason: str
    guardrail_report: dict[str, Any]
    merge_result: dict[str, Any] = field(default_factory=dict)
    escalation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict representation."""
        return {
            "status": self.status,
            "tenant_id": self.tenant_id,
            "repository": self.repository,
            "base_branch": self.base_branch,
            "head_branch": self.head_branch,
            "pr_number": self.pr_number,
            "pr_url": self.pr_url,
            "branch_created": self.branch_created,
            "decision": self.decision,
            "reason": self.reason,
            "guardrail_report": self.guardrail_report,
            "merge_result": self.merge_result,
            "escalation": self.escalation,
        }


class AutomergeOrchestrator:
    """Create branch/PR, enforce guardrails, then merge when safe."""

    async def execute(
        self,
        *,
        request: AutomergeExecutionRequest,
        api: GitHubAutomergeAPI,
    ) -> AutomergeExecutionResult:
        owner, repo = self._parse_repository(request.repository)
        base_branch = self._normalize_branch(request.base_branch or "main")
        source_ref = request.source_ref or base_branch
        head_branch = self._resolve_head_branch(request.head_branch, base_branch)
        merge_method = str(request.merge_method or "squash").strip().lower()
        if merge_method not in _MERGE_METHODS:
            raise ValueError(f"Unsupported merge_method '{request.merge_method}'")

        preflight_block = self._check_forbidden_actions(request)
        if preflight_block is not None:
            return preflight_block

        ensured_branch = await api.ensure_branch(
            owner,
            repo,
            branch=head_branch,
            source_ref=source_ref,
        )
        branch_created = bool(ensured_branch.get("created"))

        pr = await api.find_open_pull_request(
            owner,
            repo,
            head=head_branch,
            base=base_branch,
        )
        if pr is None:
            title = request.pr_title or f"Autonomous merge: {head_branch} -> {base_branch}"
            pr = await api.create_pull_request(
                owner,
                repo,
                title=title,
                head=head_branch,
                base=base_branch,
                body=request.pr_body,
                draft=False,
            )
            log.info(
                "automerge_pr_created",
                tenant_id=request.tenant_id,
                repository=request.repository,
                pr_number=pr.number,
            )

        pr = await api.get_pull_request(owner, repo, pr.number)
        pr_files = await api.list_pull_request_files(owner, repo, pr.number)

        guardrail_report = self._evaluate_guardrails(
            request=request,
            pull_request=pr,
            changed_files=pr_files,
        )
        if guardrail_report["blocked"]:
            return AutomergeExecutionResult(
                status="blocked",
                tenant_id=request.tenant_id,
                repository=request.repository,
                base_branch=base_branch,
                head_branch=head_branch,
                pr_number=pr.number,
                pr_url=pr.html_url or None,
                branch_created=branch_created,
                decision="deny",
                reason="guardrails_blocked",
                guardrail_report=guardrail_report,
                escalation={
                    "required": True,
                    "type": "manual_review",
                    "note": "Guardrail violations blocked merge execution.",
                },
            )

        checks = await api.list_check_runs(owner, repo, ref=pr.head_ref or head_branch)
        check_report = self._evaluate_required_checks(request.guardrails.required_checks, checks)
        guardrail_report["checks"] = check_report
        if check_report["blocked"]:
            return AutomergeExecutionResult(
                status="blocked",
                tenant_id=request.tenant_id,
                repository=request.repository,
                base_branch=base_branch,
                head_branch=head_branch,
                pr_number=pr.number,
                pr_url=pr.html_url or None,
                branch_created=branch_created,
                decision="deny",
                reason="required_checks_not_green",
                guardrail_report=guardrail_report,
                escalation={
                    "required": True,
                    "type": "checks_pending_or_failed",
                    "note": "Required checks were missing, pending, or failed.",
                },
            )

        merge_result = await api.merge_pull_request(
            owner,
            repo,
            pr.number,
            commit_title=request.commit_title,
            commit_message=request.commit_message,
            merge_method=merge_method,
        )
        merged = bool(merge_result.get("merged"))
        if not merged:
            return AutomergeExecutionResult(
                status="blocked",
                tenant_id=request.tenant_id,
                repository=request.repository,
                base_branch=base_branch,
                head_branch=head_branch,
                pr_number=pr.number,
                pr_url=pr.html_url or None,
                branch_created=branch_created,
                decision="deny",
                reason="merge_blocked",
                guardrail_report=guardrail_report,
                merge_result=merge_result,
                escalation={
                    "required": True,
                    "type": "merge_blocked",
                    "note": str(merge_result.get("message") or "GitHub merge blocked"),
                },
            )

        if not request.post_merge_validation_passed:
            return AutomergeExecutionResult(
                status="rollback_required",
                tenant_id=request.tenant_id,
                repository=request.repository,
                base_branch=base_branch,
                head_branch=head_branch,
                pr_number=pr.number,
                pr_url=pr.html_url or None,
                branch_created=branch_created,
                decision="allow",
                reason="merged_but_post_merge_validation_failed",
                guardrail_report=guardrail_report,
                merge_result=merge_result,
                escalation={
                    "required": True,
                    "type": "rollback_required",
                    "note": "Merge landed but post-merge validation failed.",
                },
            )

        return AutomergeExecutionResult(
            status="merged",
            tenant_id=request.tenant_id,
            repository=request.repository,
            base_branch=base_branch,
            head_branch=head_branch,
            pr_number=pr.number,
            pr_url=pr.html_url or None,
            branch_created=branch_created,
            decision="allow",
            reason="merged",
            guardrail_report=guardrail_report,
            merge_result=merge_result,
        )

    def _check_forbidden_actions(
        self,
        request: AutomergeExecutionRequest,
    ) -> AutomergeExecutionResult | None:
        forbidden = {
            self._normalize_action(value) for value in request.guardrails.forbidden_actions
        }
        requested = {self._normalize_action(value) for value in request.requested_actions}
        blocked_actions = sorted(action for action in requested if action and action in forbidden)
        if not blocked_actions:
            return None

        return AutomergeExecutionResult(
            status="blocked",
            tenant_id=request.tenant_id,
            repository=request.repository,
            base_branch=self._normalize_branch(request.base_branch or "main"),
            head_branch=self._resolve_head_branch(
                request.head_branch,
                request.base_branch or "main",
            ),
            pr_number=None,
            pr_url=None,
            branch_created=False,
            decision="deny",
            reason="forbidden_actions_requested",
            guardrail_report={
                "blocked": True,
                "violations": [
                    {
                        "kind": "forbidden_actions",
                        "blocked_actions": blocked_actions,
                    }
                ],
            },
            escalation={
                "required": True,
                "type": "two_person_required",
                "note": "Request includes forbidden autonomous actions.",
            },
        )

    def _evaluate_guardrails(
        self,
        *,
        request: AutomergeExecutionRequest,
        pull_request: PullRequest,
        changed_files: list[dict[str, Any]],
    ) -> dict[str, Any]:
        violations: list[dict[str, Any]] = []

        if pull_request.changed_files > int(request.guardrails.max_changed_files):
            violations.append(
                {
                    "kind": "max_changed_files",
                    "actual": pull_request.changed_files,
                    "limit": int(request.guardrails.max_changed_files),
                }
            )
        if pull_request.additions > int(request.guardrails.max_additions):
            violations.append(
                {
                    "kind": "max_additions",
                    "actual": pull_request.additions,
                    "limit": int(request.guardrails.max_additions),
                }
            )
        if pull_request.deletions > int(request.guardrails.max_deletions):
            violations.append(
                {
                    "kind": "max_deletions",
                    "actual": pull_request.deletions,
                    "limit": int(request.guardrails.max_deletions),
                }
            )

        allowed_paths = self._normalize_allowed_paths(request.guardrails.allowed_paths)
        if allowed_paths:
            disallowed_files = sorted(
                filename
                for filename in self._extract_filenames(changed_files)
                if not any(filename.startswith(prefix) for prefix in allowed_paths)
            )
            if disallowed_files:
                violations.append(
                    {
                        "kind": "allowed_paths",
                        "allowed_paths": list(allowed_paths),
                        "disallowed_files": disallowed_files,
                    }
                )

        return {
            "blocked": bool(violations),
            "violations": violations,
            "stats": {
                "changed_files": pull_request.changed_files,
                "additions": pull_request.additions,
                "deletions": pull_request.deletions,
            },
            "evaluated_at": datetime.now(UTC).isoformat(),
        }

    @staticmethod
    def _evaluate_required_checks(
        required_checks: tuple[str, ...],
        check_runs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        required = [name.strip() for name in required_checks if name.strip()]
        if not required:
            return {"blocked": False, "required": [], "missing": [], "pending": [], "failed": []}

        latest_by_name: dict[str, dict[str, Any]] = {}
        for check_run in check_runs:
            name = str(check_run.get("name") or "").strip()
            if not name:
                continue
            latest_by_name[name] = check_run

        missing: list[str] = []
        pending: list[str] = []
        failed: list[str] = []
        for required_name in required:
            required_run = latest_by_name.get(required_name)
            if required_run is None:
                missing.append(required_name)
                continue
            status = str(required_run.get("status") or "").strip().lower()
            conclusion = str(required_run.get("conclusion") or "").strip().lower()
            if status != "completed":
                pending.append(required_name)
                continue
            if conclusion not in _GREEN_CHECK_CONCLUSIONS:
                failed.append(required_name)

        return {
            "blocked": bool(missing or pending or failed),
            "required": required,
            "missing": missing,
            "pending": pending,
            "failed": failed,
        }

    @staticmethod
    def _extract_filenames(changed_files: list[dict[str, Any]]) -> list[str]:
        names: list[str] = []
        for row in changed_files:
            filename = str(row.get("filename") or "").strip().lstrip("/")
            if filename:
                names.append(filename)
        return names

    @staticmethod
    def _parse_repository(repository: str) -> tuple[str, str]:
        raw = str(repository or "").strip()
        parts = [part.strip() for part in raw.split("/", 1)]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError("repository must be in owner/repo format")
        return parts[0], parts[1]

    @staticmethod
    def _normalize_branch(branch: str) -> str:
        raw = str(branch or "").strip()
        if not raw:
            raise ValueError("base_branch cannot be empty")
        return raw.removeprefix("refs/heads/").removeprefix("heads/")

    @staticmethod
    def _resolve_head_branch(head_branch: str | None, base_branch: str) -> str:
        raw_head = str(head_branch or "").strip()
        if raw_head:
            normalized = raw_head.removeprefix("refs/heads/").removeprefix("heads/")
        else:
            suffix = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
            normalized = f"codex/automerge-{suffix}"
        if not normalized.startswith("codex/"):
            normalized = f"codex/{normalized}"
        if normalized == base_branch:
            raise ValueError("head_branch must differ from base_branch")
        return normalized

    @staticmethod
    def _normalize_allowed_paths(raw_paths: tuple[str, ...]) -> tuple[str, ...]:
        output: list[str] = []
        for value in raw_paths:
            normalized = str(value or "").strip().lstrip("/")
            if not normalized:
                continue
            if not normalized.endswith("/"):
                normalized = f"{normalized}/"
            output.append(normalized)
        return tuple(dict.fromkeys(output))

    @staticmethod
    def _normalize_action(value: str) -> str:
        return str(value or "").strip().lower().replace(" ", "_")
