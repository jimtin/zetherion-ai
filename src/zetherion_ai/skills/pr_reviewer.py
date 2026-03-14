"""Owner-scoped CI reviewer skill."""

from __future__ import annotations

from typing import Any

from zetherion_ai.logging import get_logger
from zetherion_ai.owner_ci import OwnerCiStorage
from zetherion_ai.skills.base import Skill, SkillMetadata, SkillRequest, SkillResponse
from zetherion_ai.skills.permissions import Permission, PermissionSet

log = get_logger("zetherion_ai.skills.pr_reviewer")
_REQUIRED_CERTIFICATION_GATE_CATEGORIES = {"static", "security", "unit", "integration", "e2e"}


def _normalize_owner_id(request: SkillRequest) -> str:
    for candidate in (
        request.context.get("owner_id"),
        request.context.get("operator_id"),
        request.context.get("actor_sub"),
        request.user_id,
    ):
        value = str(candidate or "").strip()
        if value:
            return value
    return "owner"


def _infer_gate_family(shard: dict[str, Any]) -> str | None:
    metadata = dict(shard.get("metadata") or {})
    explicit = str(metadata.get("gate_family") or metadata.get("gate_kind") or "").strip().lower()
    if explicit:
        return explicit
    lane_id = str(shard.get("lane_id") or "").strip().lower()
    if not lane_id:
        return None
    if "release" in lane_id or "golive" in lane_id:
        return "release"
    if "e2e" in lane_id or "playwright" in lane_id:
        return "e2e"
    if lane_id.startswith(("z-unit", "c-unit")) or "unit" in lane_id:
        return "unit"
    if lane_id.startswith(("z-int", "c-int")) or "integration" in lane_id:
        return "integration"
    return "integration"


class PrReviewerSkill(Skill):
    """Deterministic review gate for owner-scoped CI runs."""

    def __init__(self, *, storage: OwnerCiStorage) -> None:
        super().__init__(memory=None)
        self._storage = storage

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="pr_reviewer",
            description="Deterministic CI review policy for owner-scoped runs",
            version="0.2.0",
            permissions=PermissionSet({Permission.ADMIN, Permission.READ_CONFIG}),
            intents=["ci_run_review"],
        )

    async def initialize(self) -> bool:
        log.info("pr_reviewer_initialized")
        return True

    async def handle(self, request: SkillRequest) -> SkillResponse:
        if request.intent != "ci_run_review":
            return SkillResponse.error_response(
                request.id,
                f"Unknown reviewer intent: {request.intent}",
            )
        try:
            owner_id = _normalize_owner_id(request)
            run_id = str(request.context.get("run_id") or "").strip()
            if not run_id:
                raise ValueError("run_id is required")
            run = await self._storage.get_run(owner_id, run_id)
            if run is None:
                raise ValueError(f"Run `{run_id}` not found")
            review = self._review_run(run)
            stored = await self._storage.store_run_review(owner_id, run_id, review)
            return SkillResponse(
                request_id=request.id,
                message=f"Reviewed run `{run_id}` with verdict `{review['verdict']}`.",
                data={"review": review, "run": stored or run},
            )
        except ValueError as exc:
            return SkillResponse.error_response(request.id, str(exc))

    def _review_run(self, run: dict[str, Any]) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        shards = list(run.get("shards") or [])
        repo_id = str(run.get("repo_id") or "")
        metadata = dict(run.get("metadata") or {})

        required_static_gates = {
            str(item).strip()
            for item in list(metadata.get("required_static_gates") or [])
            if str(item).strip()
        }
        required_security_gates = {
            str(item).strip()
            for item in list(metadata.get("required_security_gates") or [])
            if str(item).strip()
        }
        completed_static_gates = {
            str(shard.get("lane_id") or "").strip()
            for shard in shards
            if str(shard.get("status") or "").strip().lower() == "succeeded"
        }
        completed_security_gates = {
            str(shard.get("lane_id") or "").strip()
            for shard in shards
            if str(shard.get("status") or "").strip().lower() == "succeeded"
        }
        missing_static_gates = sorted(required_static_gates - completed_static_gates)
        if missing_static_gates:
            findings.append(
                {
                    "severity": "high",
                    "code": "mandatory_static_gates_missing",
                    "summary": "Mandatory static gates are missing or not green",
                    "details": {"missing": missing_static_gates},
                }
            )
        missing_security_gates = sorted(required_security_gates - completed_security_gates)
        if missing_security_gates:
            findings.append(
                {
                    "severity": "high",
                    "code": "mandatory_security_gates_missing",
                    "summary": "Mandatory security gates are missing or not green",
                    "details": {"missing": missing_security_gates},
                }
            )

        required_gate_categories = {
            str(item).strip().lower()
            for item in list(metadata.get("required_gate_categories") or [])
            if str(item).strip()
        }
        if not required_gate_categories and bool(metadata.get("certification_required")):
            required_gate_categories = set(_REQUIRED_CERTIFICATION_GATE_CATEGORIES)
        completed_gate_categories = {
            category
            for shard in shards
            if str(shard.get("status") or "").strip().lower() in {"succeeded", "skipped"}
            and bool((shard.get("metadata") or {}).get("blocking", True))
            for category in [
                (
                    "static"
                    if str(shard.get("lane_id") or "").strip() in required_static_gates
                    else (
                        "security"
                        if str(shard.get("lane_id") or "").strip() in required_security_gates
                        else _infer_gate_family(shard)
                    )
                )
            ]
            if category in required_gate_categories
        }
        missing_gate_categories = sorted(required_gate_categories - completed_gate_categories)
        if missing_gate_categories:
            findings.append(
                {
                    "severity": "high",
                    "code": "required_gate_categories_missing",
                    "summary": "Required gate categories are incomplete",
                    "details": {"missing": missing_gate_categories},
                }
            )

        for shard in shards:
            status = str(shard.get("status") or "").strip().lower()
            if status == "failed":
                findings.append(
                    {
                        "severity": "high",
                        "code": "shard_failed",
                        "summary": f"Shard `{shard.get('lane_id')}` failed",
                        "details": shard.get("error") or shard.get("result") or {},
                    }
                )
            elif status in {"queued_local", "running", "running_disconnected", "awaiting_sync"}:
                findings.append(
                    {
                        "severity": "medium",
                        "code": "shard_pending",
                        "summary": f"Shard `{shard.get('lane_id')}` is not complete",
                        "details": {"status": status},
                    }
                )

        certification_required = bool(metadata.get("certification_required"))
        certification_requirements = {
            str(item).strip()
            for item in list(metadata.get("certification_requirements") or [])
            if str(item).strip()
        }

        if certification_required:
            windows_shards = [
                shard
                for shard in shards
                if str(shard.get("execution_target") or "").strip().lower() == "windows_local"
            ]
            if any(
                str(shard.get("status") or "").strip().lower() != "succeeded"
                for shard in windows_shards
            ):
                findings.append(
                    {
                        "severity": "high",
                        "code": "certification_incomplete",
                        "summary": "Certification run is missing successful Windows evidence",
                        "details": {"repo_id": repo_id},
                    }
                )

            if "discord_roundtrip" in certification_requirements:
                discord_shard = next(
                    (
                        shard
                        for shard in windows_shards
                        if str(shard.get("lane_id") or "").strip() == "discord-required-e2e"
                    ),
                    None,
                )
                if discord_shard is None or (
                    str(discord_shard.get("status") or "").strip().lower() != "succeeded"
                ):
                    findings.append(
                        {
                            "severity": "high",
                            "code": "discord_roundtrip_missing",
                            "summary": "Discord -> AI -> Discord certification receipt is missing",
                            "details": {"repo_id": repo_id},
                        }
                    )

            for shard in windows_shards:
                if str(shard.get("status") or "").strip().lower() != "succeeded":
                    continue
                result = dict(shard.get("result") or {})
                if (
                    not list(result.get("log_chunks") or [])
                    and not str(result.get("stdout") or "").strip()
                ):
                    findings.append(
                        {
                            "severity": "medium",
                            "code": "observability_logs_missing",
                            "summary": f"Shard `{shard.get('lane_id')}` is missing stored logs",
                            "details": {"shard_id": shard.get("shard_id")},
                        }
                    )
                if not list(result.get("resource_samples") or []):
                    findings.append(
                        {
                            "severity": "medium",
                            "code": "resource_samples_missing",
                            "summary": (
                                f"Shard `{shard.get('lane_id')}` " "is missing resource telemetry"
                            ),
                            "details": {"shard_id": shard.get("shard_id")},
                        }
                    )

        if repo_id in {"catalyst-group-solutions", "zetherion-ai"} and not metadata.get(
            "platform_canary", False
        ):
            findings.append(
                {
                    "severity": "medium",
                    "code": "platform_canary_missing",
                    "summary": "Platform canary metadata is missing for a certification repo",
                    "details": {"repo_id": repo_id},
                }
            )

        merge_blocked = any(finding["severity"] == "high" for finding in findings)
        if not merge_blocked and any(finding["severity"] == "medium" for finding in findings):
            verdict = "needs_sync"
            merge_blocked = True
        elif merge_blocked:
            verdict = "blocked"
        else:
            verdict = "approved"

        summary_lines = [f"Verdict: {verdict}"]
        for finding in findings:
            summary_lines.append(f"- [{finding['severity']}] {finding['summary']}")
        if not findings:
            summary_lines.append("- No blocking findings.")

        return {
            "verdict": verdict,
            "severity": "high" if merge_blocked else "none",
            "findings": findings,
            "merge_blocked": merge_blocked,
            "summary_markdown": "\n".join(summary_lines),
        }
