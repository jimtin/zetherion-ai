"""Canonical run-report and coaching builders for owner-CI."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from zetherion_ai.owner_ci.diagnostics import build_run_diagnostics
from zetherion_ai.owner_ci.models import (
    AgentCoachingFeedback,
    AgentCoachingFinding,
    AgentInstructionRecommendation,
    AgentRuleViolation,
    CorrelationContext,
    EvidenceReference,
    RunGraph,
    RunGraphArtifactRef,
    RunGraphDiagnosticRef,
    RunGraphNode,
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalized_state(value: str | None) -> str:
    candidate = str(value or "").strip().lower()
    if candidate in {"succeeded", "success", "healthy", "ready", "passed", "merged"}:
        return "succeeded"
    if candidate in {"failed", "failure", "blocked", "cancelled", "error"}:
        return "failed"
    if candidate in {"running", "claimed", "awaiting_sync", "running_disconnected"}:
        return "running"
    if candidate in {"planned", "queued_local", "pending"}:
        return "queued"
    return candidate or "unknown"


def _dedupe_strings(values: list[Any]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _normalize_mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _normalize_time_range(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        start = str(value.get("start") or value.get("from") or "").strip() or None
        end = str(value.get("end") or value.get("to") or "").strip() or None
        result = {}
        if start:
            result["start"] = start
        if end:
            result["end"] = end
        return result
    return {}


def _run_node_id(run_id: str) -> str:
    return f"run:{run_id}"


def _shard_node_id(run_id: str, shard_id: str) -> str:
    return f"shard:{run_id}:{shard_id}"


def _step_node_id(run_id: str, shard_id: str, step_id: str) -> str:
    return f"step:{run_id}:{shard_id}:{step_id}"


def _artifact_id(run_id: str, shard_id: str, artifact_key: str) -> str:
    return f"artifact:{run_id}:{shard_id}:{artifact_key}"


def _diagnostic_id(run_id: str, code: str, shard_id: str | None, index: int) -> str:
    shard_fragment = shard_id or "run"
    return f"diagnostic:{run_id}:{shard_fragment}:{code}:{index}"


def _step_artifacts(
    *,
    run_id: str,
    shard_id: str,
    artifacts: list[dict[str, Any]],
) -> list[RunGraphArtifactRef]:
    output: list[RunGraphArtifactRef] = []
    for index, artifact in enumerate(artifacts):
        artifact_key = str(
            artifact.get("artifact_id")
            or artifact.get("id")
            or artifact.get("path")
            or artifact.get("title")
            or f"artifact-{index + 1}"
        ).strip()
        if not artifact_key:
            artifact_key = f"artifact-{index + 1}"
        node_id = str(artifact.get("node_id") or "").strip() or None
        output.append(
            RunGraphArtifactRef(
                artifact_id=_artifact_id(run_id, shard_id, artifact_key),
                node_id=node_id,
                kind=str(artifact.get("kind") or "artifact").strip() or "artifact",
                title=str(artifact.get("title") or artifact_key).strip() or artifact_key,
                path=str(artifact.get("path") or "").strip() or None,
                state=_normalized_state(str(artifact.get("state") or "ready")),
                created_at=str(artifact.get("created_at") or "").strip() or None,
                metadata=dict(artifact.get("metadata") or {}),
            )
        )
    return output


def _debug_bundle_artifacts(
    *,
    run_id: str,
    shard_id: str,
    debug_bundle: dict[str, Any] | None,
) -> list[RunGraphArtifactRef]:
    bundle = dict((debug_bundle or {}).get("bundle") or {})
    artifact_paths = dict(bundle.get("artifact_receipt_paths") or {})
    output: list[RunGraphArtifactRef] = []
    for key, raw_path in artifact_paths.items():
        path = str(raw_path or "").strip()
        if not path:
            continue
        output.append(
            RunGraphArtifactRef(
                artifact_id=_artifact_id(run_id, shard_id, str(key)),
                node_id=_shard_node_id(run_id, shard_id),
                kind="artifact_receipt",
                title=str(key),
                path=path,
                state="ready",
                metadata={"source": "debug_bundle"},
            )
        )
    return output


def _submitted_evidence_references(
    *,
    shard_id: str,
    result: dict[str, Any],
) -> list[EvidenceReference]:
    references: list[EvidenceReference] = []
    for index, raw_ref in enumerate(_normalize_mapping_list(result.get("evidence_references"))):
        evidence_ref_id = str(
            raw_ref.get("evidence_ref_id") or raw_ref.get("id") or f"evidence-{index + 1}"
        ).strip()
        if not evidence_ref_id:
            evidence_ref_id = f"evidence-{index + 1}"
        references.append(
            EvidenceReference(
                evidence_ref_id=f"{shard_id}:{evidence_ref_id}",
                node_id=str(raw_ref.get("node_id") or "").strip() or None,
                provider=str(raw_ref.get("provider") or "unknown").strip() or "unknown",
                service=str(raw_ref.get("service") or "").strip() or None,
                query=str(raw_ref.get("query") or "").strip() or None,
                time_range=_normalize_time_range(raw_ref.get("time_range")),
                trace_id=str(raw_ref.get("trace_id") or "").strip() or None,
                request_id=str(raw_ref.get("request_id") or "").strip() or None,
                artifact_ref=str(raw_ref.get("artifact_ref") or "").strip() or None,
                metadata=dict(raw_ref.get("metadata") or {}),
            )
        )
    return references


def build_correlation_context(run: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(run.get("metadata") or {})
    trace_ids: list[str] = []
    request_ids: list[str] = []
    services: list[str] = []
    containers: list[str] = []
    for shard in list(run.get("shards") or []):
        result = dict(shard.get("result") or {})
        correlation = dict(result.get("correlation_context") or {})
        trace_ids.extend(list(correlation.get("trace_ids") or []))
        request_ids.extend(list(correlation.get("request_ids") or []))
        services.extend(list(correlation.get("services") or []))
        containers.extend(list(correlation.get("containers") or []))
        for evidence_ref in _normalize_mapping_list(result.get("evidence_references")):
            if evidence_ref.get("trace_id"):
                trace_ids.append(str(evidence_ref.get("trace_id")))
            if evidence_ref.get("request_id"):
                request_ids.append(str(evidence_ref.get("request_id")))
            if evidence_ref.get("service"):
                services.append(str(evidence_ref.get("service")))
    context = CorrelationContext(
        run_id=str(run.get("run_id") or ""),
        commit_sha=str(
            metadata.get("git_sha") or metadata.get("head_sha") or metadata.get("commit_sha") or ""
        ).strip()
        or None,
        environment=(
            str(metadata.get("environment") or metadata.get("target_environment") or "").strip()
            or None
        ),
        trace_ids=_dedupe_strings(trace_ids),
        request_ids=_dedupe_strings(request_ids),
        services=_dedupe_strings(services),
        containers=_dedupe_strings(containers),
        metadata={
            "repo_id": str(run.get("repo_id") or "").strip() or None,
            "trigger": str(run.get("trigger") or "").strip() or None,
        },
    )
    return context.model_dump(mode="json")


def build_agent_coaching_feedback(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    coaching_feedback: list[dict[str, Any]] = []
    for gap in gaps:
        metadata = dict(gap.get("metadata") or {})
        if str(metadata.get("record_kind") or "").strip() != "agent_coaching":
            continue
        findings_payload = _normalize_mapping_list(metadata.get("findings"))
        recommendations_payload = _normalize_mapping_list(metadata.get("recommendations"))
        evidence_payload = _normalize_mapping_list(metadata.get("evidence_references"))
        feedback = AgentCoachingFeedback(
            feedback_id=str(gap.get("gap_id") or ""),
            principal_id=str(gap.get("principal_id") or "").strip() or None,
            repo_id=str(gap.get("repo_id") or "").strip() or None,
            run_id=str(gap.get("run_id") or "").strip() or None,
            commit_sha=str(metadata.get("commit_sha") or "").strip() or None,
            scope=str(metadata.get("scope") or "principal").strip() or "principal",
            status=str(gap.get("status") or "open").strip() or "open",
            blocking=bool(gap.get("blocker")),
            recurrence_count=max(1, int(gap.get("occurrence_count") or 1)),
            confidence=(
                float(metadata.get("confidence"))
                if metadata.get("confidence") is not None
                else None
            ),
            summary=str(
                metadata.get("summary") or gap.get("suggested_fix") or gap.get("gap_type") or ""
            ),
            findings=[
                AgentCoachingFinding(
                    finding_id=str(item.get("finding_id") or f"{gap.get('gap_id')}:finding"),
                    coaching_kind=str(
                        item.get("coaching_kind") or metadata.get("coaching_kind") or "diagnostic"
                    ),
                    rule_code=str(
                        item.get("rule_code")
                        or metadata.get("rule_code")
                        or gap.get("gap_type")
                        or ""
                    ),
                    summary=str(item.get("summary") or gap.get("suggested_fix") or ""),
                    remediation=str(item.get("remediation") or gap.get("suggested_fix") or ""),
                    blocking=bool(item.get("blocking", gap.get("blocker", False))),
                    recurrence_count=max(
                        1,
                        int(item.get("recurrence_count") or gap.get("occurrence_count") or 1),
                    ),
                    confidence=(
                        float(item.get("confidence"))
                        if item.get("confidence") is not None
                        else (
                            float(metadata.get("confidence"))
                            if metadata.get("confidence") is not None
                            else None
                        )
                    ),
                    evidence_ref_ids=[
                        str(entry).strip()
                        for entry in list(item.get("evidence_ref_ids") or [])
                        if str(entry).strip()
                    ],
                    metadata=dict(item.get("metadata") or {}),
                )
                for item in findings_payload
            ],
            recommendations=[
                AgentInstructionRecommendation(
                    title=str(item.get("title") or "Update AGENTS.md").strip()
                    or "Update AGENTS.md",
                    instructions=[
                        str(entry).strip()
                        for entry in list(item.get("instructions") or [])
                        if str(entry).strip()
                    ],
                    agents_md_update=str(item.get("agents_md_update") or "").strip() or None,
                    patch_guidance=dict(item.get("patch_guidance") or {}),
                )
                for item in recommendations_payload
            ],
            rule_violations=[
                AgentRuleViolation(
                    rule_code=str(metadata.get("rule_code") or gap.get("gap_type") or ""),
                    summary=str(metadata.get("summary") or gap.get("suggested_fix") or ""),
                    blocking=bool(gap.get("blocker")),
                    evidence_ref_ids=[
                        str(entry).strip()
                        for entry in list(metadata.get("evidence_ref_ids") or [])
                        if str(entry).strip()
                    ],
                    metadata={"detected_from": gap.get("detected_from")},
                )
            ],
            evidence_references=[
                EvidenceReference.model_validate(item) for item in evidence_payload
            ],
            metadata=metadata,
            created_at=str(gap.get("first_seen_at") or "").strip() or None,
            updated_at=str(gap.get("updated_at") or "").strip() or None,
        )
        coaching_feedback.append(feedback.model_dump(mode="json", exclude_none=True))
    return coaching_feedback


def _agents_md_update_for_rule(rule_code: str, *, repo_id: str | None) -> str:
    repo_label = repo_id or "this repo"
    snippets = {
        "missing_preflight_check": (
            f"Before asking Zetherion to start certification for {repo_label}, run the mandatory "
            "static and security checks locally or in-container first, then include a "
            "`preflight_checks` attestation payload listing each completed check id and status."
        ),
        "tool_version_mismatch": (
            f"Pin the locally executed tooling for {repo_label} to the CI-approved version and "
            "record that version in the `preflight_checks.tool_versions` attestation before "
            "requesting certification."
        ),
        "missing_preflight_attestation": (
            "Do not start certification without a machine-readable `preflight_checks` attestation "
            "that records completed mandatory checks, tool versions, and gate categories."
        ),
        "connector_auth_failed": (
            "Add an explicit auth verification step to AGENTS.md before triggering CI so connector "
            "health is validated before the main run."
        ),
        "artifact_contract_failed": (
            "Update AGENTS.md so shards are not marked complete until required artifacts and "
            "receipts are written."
        ),
        "shard_contract_invalid": (
            "Update AGENTS.md to validate shard structure, test selection, and artifact contracts "
            "before asking Zetherion to certify the change."
        ),
        "coverage_gate_failed": (
            "Update AGENTS.md to require reviewing coverage-summary.json and coverage-gaps.json "
            "before rerunning the full gate."
        ),
    }
    return snippets.get(
        rule_code,
        (
            "Add the missing deterministic check to AGENTS.md so this failure mode "
            "is prevented before the next run."
        ),
    )


def build_preflight_coaching_payloads(
    *,
    principal_id: str | None,
    repo_id: str,
    commit_sha: str | None,
    violations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for violation in violations:
        rule_code = str(violation.get("rule_code") or "").strip() or "missing_preflight_check"
        summary = str(
            violation.get("summary") or "Preflight certification requirements were not met."
        ).strip()
        remediation = str(violation.get("remediation") or summary).strip()
        evidence_refs = _normalize_mapping_list(violation.get("evidence_references"))
        payloads.append(
            {
                "principal_id": principal_id,
                "repo_id": repo_id,
                "gap_type": f"agent_preflight_{rule_code}",
                "severity": "high",
                "blocker": True,
                "detected_from": "ci_run_preflight",
                "required_capability": "certification_preflight",
                "observed_request": {
                    "rule_code": rule_code,
                    "repo_id": repo_id,
                    "commit_sha": commit_sha,
                },
                "suggested_fix": remediation,
                "metadata": {
                    "record_kind": "agent_coaching",
                    "coaching_kind": "preflight",
                    "rule_code": rule_code,
                    "scope": "principal",
                    "confidence": 0.99,
                    "summary": summary,
                    "commit_sha": commit_sha,
                    "findings": [
                        {
                            "finding_id": f"{rule_code}:preflight",
                            "coaching_kind": "preflight",
                            "rule_code": rule_code,
                            "summary": summary,
                            "remediation": remediation,
                            "blocking": True,
                            "confidence": 0.99,
                            "evidence_ref_ids": [
                                str(item.get("evidence_ref_id") or "").strip()
                                for item in evidence_refs
                                if str(item.get("evidence_ref_id") or "").strip()
                            ],
                        }
                    ],
                    "recommendations": [
                        {
                            "title": "Update AGENTS.md preflight rules",
                            "instructions": [
                                remediation,
                                (
                                    "Do not ask Zetherion to start certification until "
                                    "the attestation is complete."
                                ),
                            ],
                            "agents_md_update": _agents_md_update_for_rule(
                                rule_code,
                                repo_id=repo_id,
                            ),
                        }
                    ],
                    "evidence_references": evidence_refs,
                },
            }
        )
    return payloads


def build_recurring_diagnostic_coaching_payloads(
    *,
    report: dict[str, Any],
    principal_id: str | None,
    historical_occurrences: dict[str, int],
) -> list[dict[str, Any]]:
    repo_id = str(report.get("repo_id") or "").strip()
    correlation = dict(report.get("correlation_context") or {})
    commit_sha = str(correlation.get("commit_sha") or "").strip() or None
    payloads: list[dict[str, Any]] = []
    for finding in _normalize_mapping_list(report.get("diagnostic_findings")):
        rule_code = str(finding.get("code") or finding.get("type") or "").strip()
        if not rule_code:
            continue
        recurrence_count = int(historical_occurrences.get(rule_code) or 0) + 1
        if recurrence_count < 2:
            continue
        summary = (
            str(
                finding.get("root_cause_summary") or finding.get("summary") or "Recurring CI issue"
            ).strip()
            or "Recurring CI issue"
        )
        remediation = str(
            finding.get("recommended_fix")
            or "Add a preventative instruction to AGENTS.md before rerunning certification."
        ).strip()
        payloads.append(
            {
                "principal_id": principal_id,
                "repo_id": repo_id,
                "run_id": str(report.get("run_id") or "").strip() or None,
                "gap_type": f"agent_recurring_{rule_code}",
                "severity": str(finding.get("severity") or "medium").strip() or "medium",
                "blocker": bool(finding.get("blocking", False)),
                "detected_from": "ci_run_diagnostics",
                "required_capability": "agents_md_prevention",
                "observed_request": {
                    "rule_code": rule_code,
                    "repo_id": repo_id,
                    "commit_sha": commit_sha,
                    "recurrence_count": recurrence_count,
                },
                "suggested_fix": remediation,
                "metadata": {
                    "record_kind": "agent_coaching",
                    "coaching_kind": "recurring_issue",
                    "rule_code": rule_code,
                    "scope": "principal",
                    "confidence": 0.9,
                    "summary": summary,
                    "commit_sha": commit_sha,
                    "findings": [
                        {
                            "finding_id": f"{rule_code}:recurring",
                            "coaching_kind": "recurring_issue",
                            "rule_code": rule_code,
                            "summary": summary,
                            "remediation": remediation,
                            "blocking": bool(finding.get("blocking", False)),
                            "recurrence_count": recurrence_count,
                            "confidence": 0.9,
                            "evidence_ref_ids": [
                                str(entry).strip()
                                for entry in list(finding.get("evidence_ref_ids") or [])
                                if str(entry).strip()
                            ],
                        }
                    ],
                    "recommendations": [
                        {
                            "title": "Update AGENTS.md to prevent recurrence",
                            "instructions": [
                                remediation,
                                "Add this prevention rule to AGENTS.md before the next run.",
                            ],
                            "agents_md_update": _agents_md_update_for_rule(
                                rule_code,
                                repo_id=repo_id,
                            ),
                        }
                    ],
                    "evidence_references": [
                        item
                        for item in _normalize_mapping_list(report.get("all_evidence_references"))
                        if str(item.get("node_id") or "").strip()
                        == str(finding.get("node_id") or "").strip()
                    ],
                },
            }
        )
    return payloads


def build_run_report(
    *,
    run: dict[str, Any],
    logs: list[dict[str, Any]],
    debug_bundle: dict[str, Any] | None,
    coaching_feedback: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    run_id = str(run.get("run_id") or "").strip()
    repo_id = str(run.get("repo_id") or "").strip()
    generated_at = _utc_now_iso()
    diagnostic_summary, diagnostic_findings = build_run_diagnostics(
        run=run,
        logs=logs,
        debug_bundle=debug_bundle,
    )
    correlation_context = build_correlation_context(run)
    nodes: list[RunGraphNode] = []
    artifacts: list[RunGraphArtifactRef] = []
    evidence_refs: list[EvidenceReference] = []
    diagnostics: list[RunGraphDiagnosticRef] = []

    run_node_id = _run_node_id(run_id)
    nodes.append(
        RunGraphNode(
            node_id=run_node_id,
            kind="run",
            label=repo_id or run_id,
            state=_normalized_state(str(run.get("status") or "")),
            run_id=run_id,
            started_at=str(run.get("created_at") or "").strip() or None,
            completed_at=str(run.get("updated_at") or "").strip() or None,
            metadata={
                "repo_id": repo_id,
                "trigger": str(run.get("trigger") or "").strip() or None,
            },
        )
    )

    artifact_ids_by_node: dict[str, list[str]] = {}
    evidence_ids_by_node: dict[str, list[str]] = {}
    diagnostic_ids_by_node: dict[str, list[str]] = {}

    for shard in list(run.get("shards") or []):
        shard_id = str(shard.get("shard_id") or shard.get("lane_id") or "").strip()
        if not shard_id:
            continue
        shard_node_id = _shard_node_id(run_id, shard_id)
        metadata = dict(shard.get("metadata") or {})
        shard_node = RunGraphNode(
            node_id=shard_node_id,
            kind="shard",
            label=(
                str(shard.get("lane_label") or shard.get("lane_id") or shard_id).strip() or shard_id
            ),
            parent_id=run_node_id,
            dependency_ids=[
                _shard_node_id(run_id, dependency)
                for dependency in _dedupe_strings(list(metadata.get("depends_on") or []))
            ],
            state=_normalized_state(str(shard.get("status") or "")),
            run_id=run_id,
            shard_id=shard_id,
            started_at=str(shard.get("started_at") or "").strip() or None,
            completed_at=str(shard.get("completed_at") or "").strip() or None,
            metadata={
                "lane_id": str(shard.get("lane_id") or "").strip() or None,
                "execution_target": str(shard.get("execution_target") or "").strip() or None,
                "resource_class": str(metadata.get("resource_class") or "").strip() or None,
            },
        )
        nodes.append(shard_node)

        result = dict(shard.get("result") or {})
        steps = _normalize_mapping_list(result.get("steps"))
        for index, step in enumerate(steps):
            step_id = str(step.get("step_id") or step.get("id") or f"step-{index + 1}").strip()
            if not step_id:
                step_id = f"step-{index + 1}"
            step_node_id = _step_node_id(run_id, shard_id, step_id)
            nodes.append(
                RunGraphNode(
                    node_id=step_node_id,
                    kind="step",
                    label=str(step.get("label") or step.get("name") or step_id).strip() or step_id,
                    parent_id=shard_node_id,
                    dependency_ids=[
                        _step_node_id(run_id, shard_id, dependency)
                        for dependency in _dedupe_strings(list(step.get("depends_on") or []))
                    ],
                    state=_normalized_state(str(step.get("state") or step.get("status") or "")),
                    run_id=run_id,
                    shard_id=shard_id,
                    step_id=step_id,
                    started_at=str(step.get("started_at") or "").strip() or None,
                    completed_at=str(step.get("completed_at") or "").strip() or None,
                    metadata=dict(step.get("metadata") or {}),
                )
            )

        shard_artifacts = _step_artifacts(
            run_id=run_id,
            shard_id=shard_id,
            artifacts=_normalize_mapping_list(result.get("artifacts")),
        )
        shard_debug_bundle = (
            {"bundle": dict(result.get("debug_bundle") or {}), "shard_id": shard_id}
            if isinstance(result.get("debug_bundle"), dict)
            else (
                debug_bundle
                if str((debug_bundle or {}).get("shard_id") or "").strip() == shard_id
                else None
            )
        )
        for artifact in [
            *shard_artifacts,
            *_debug_bundle_artifacts(
                run_id=run_id,
                shard_id=shard_id,
                debug_bundle=shard_debug_bundle,
            ),
        ]:
            artifacts.append(artifact)
            node_id = artifact.node_id or shard_node_id
            artifact_ids_by_node.setdefault(node_id, []).append(artifact.artifact_id)

        shard_evidence_refs = _submitted_evidence_references(shard_id=shard_id, result=result)
        for evidence_ref in shard_evidence_refs:
            node_id = evidence_ref.node_id or shard_node_id
            if evidence_ref.node_id is None:
                evidence_ref.node_id = node_id
            evidence_refs.append(evidence_ref)
            evidence_ids_by_node.setdefault(node_id, []).append(evidence_ref.evidence_ref_id)

    for index, finding in enumerate(diagnostic_findings):
        shard_id = str(finding.get("shard_id") or "").strip() or None
        node_id = _shard_node_id(run_id, shard_id) if shard_id else run_node_id
        diagnostic = RunGraphDiagnosticRef(
            diagnostic_id=_diagnostic_id(
                run_id,
                str(finding.get("code") or finding.get("type") or "diagnostic"),
                shard_id,
                index,
            ),
            node_id=node_id,
            code=str(finding.get("code") or finding.get("type") or "diagnostic").strip()
            or "diagnostic",
            summary=str(
                finding.get("summary") or finding.get("root_cause_summary") or "Diagnostic finding"
            ).strip()
            or "Diagnostic finding",
            blocking=bool(finding.get("blocking", False)),
            severity=str(finding.get("severity") or "medium").strip() or "medium",
            created_at=generated_at,
            metadata={
                "finding": dict(finding),
            },
        )
        diagnostics.append(diagnostic)
        diagnostic_ids_by_node.setdefault(node_id, []).append(diagnostic.diagnostic_id)

    for node in nodes:
        node.artifact_ids = artifact_ids_by_node.get(node.node_id, [])
        node.evidence_ref_ids = evidence_ids_by_node.get(node.node_id, [])
        node.diagnostic_ids = diagnostic_ids_by_node.get(node.node_id, [])

    graph = RunGraph(
        run_id=run_id,
        generated_at=generated_at,
        state=_normalized_state(str(run.get("status") or "")),
        nodes=nodes,
        artifacts=artifacts,
        diagnostics=diagnostics,
        evidence_references=evidence_refs,
        metadata={"repo_id": repo_id},
    ).model_dump(mode="json")

    coverage_summary = next(
        (
            dict((dict(shard.get("result") or {})).get("coverage_summary") or {})
            for shard in list(run.get("shards") or [])
            if isinstance(dict(shard.get("result") or {}).get("coverage_summary"), dict)
        ),
        {},
    )
    coverage_gaps = next(
        (
            dict((dict(shard.get("result") or {})).get("coverage_gaps") or {})
            for shard in list(run.get("shards") or [])
            if isinstance(dict(shard.get("result") or {}).get("coverage_gaps"), dict)
        ),
        {},
    )

    failing_node_ids = {
        diagnostic["node_id"]
        for diagnostic in graph["diagnostics"]
        if bool(diagnostic.get("blocking")) and diagnostic.get("node_id")
    }
    failing_evidence_refs = [
        reference
        for reference in graph["evidence_references"]
        if str(reference.get("node_id") or "") in failing_node_ids
    ]

    return {
        "run_id": run_id,
        "repo_id": repo_id,
        "generated_at": generated_at,
        "package": {
            "root": "run_report",
            "files": [
                {"kind": "run_graph", "path": "run_report/run_graph.json"},
                {"kind": "correlation_context", "path": "run_report/correlation_context.json"},
                {"kind": "diagnostic_summary", "path": "run_report/diagnostic_summary.json"},
                {"kind": "diagnostic_findings", "path": "run_report/diagnostic_findings.json"},
                {"kind": "coverage_summary", "path": "run_report/coverage_summary.json"},
                {"kind": "coverage_gaps", "path": "run_report/coverage_gaps.json"},
                {"kind": "artifacts_index", "path": "run_report/artifacts/index.json"},
                {"kind": "evidence_index", "path": "run_report/evidence/index.json"},
                {"kind": "coaching", "path": "run_report/coaching.json"},
            ],
        },
        "run_graph": graph,
        "correlation_context": correlation_context,
        "diagnostic_summary": diagnostic_summary,
        "diagnostic_findings": diagnostic_findings,
        "diagnostic_artifacts": list(diagnostic_summary.get("diagnostic_artifacts") or []),
        "coverage_summary": coverage_summary,
        "coverage_gaps": coverage_gaps,
        "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
        "evidence": failing_evidence_refs,
        "all_evidence_references": graph["evidence_references"],
        "coaching": list(coaching_feedback or []),
        "correlated_incidents": [
            {
                "type": str(finding.get("code") or finding.get("type") or "diagnostic"),
                "blocking": bool(finding.get("blocking", False)),
                "summary": str(
                    finding.get("root_cause_summary")
                    or finding.get("summary")
                    or "Diagnostic finding"
                ),
            }
            for finding in diagnostic_findings
        ],
    }
