"""Focused coverage for GitHub repo enrollment and publish flows."""

from __future__ import annotations

import base64
import io
import tarfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import zetherion_ai.skills.agent_bootstrap as agent_bootstrap
from zetherion_ai.skills.agent_bootstrap import AgentBootstrapSkill
from zetherion_ai.skills.github.client import GitHubAPIError, GitHubValidationError
from zetherion_ai.skills.github.models import PullRequest, Repository


def _storage() -> MagicMock:
    storage = MagicMock()
    storage.get_external_service_connector_with_secret = AsyncMock(
        return_value={
            "connector_id": "github-primary",
            "service_kind": "github",
            "active": True,
            "secret_value": "token-1",
            "auth_kind": "github_app_installation",
            "policy": {},
        }
    )
    storage.list_agent_docs_manifests = AsyncMock(return_value=[])
    storage.upsert_repo_profile = AsyncMock(side_effect=lambda owner_id, profile: dict(profile))
    storage.upsert_agent_app_profile = AsyncMock(
        side_effect=lambda owner_id, **kwargs: {
            "app_id": kwargs["app_id"],
            "display_name": kwargs["display_name"],
            "profile": dict(kwargs["profile"]),
            "active": kwargs["active"],
        }
    )
    storage.upsert_agent_knowledge_pack = AsyncMock(
        side_effect=lambda owner_id, **kwargs: {
            "app_id": kwargs["app_id"],
            "version": kwargs["version"],
            "pack": dict(kwargs["pack"]),
            "current": kwargs["current"],
        }
    )
    storage.record_agent_audit_event = AsyncMock(return_value={"audit_id": "audit-1"})
    storage.list_agent_app_profiles = AsyncMock(return_value=[])
    storage.list_external_access_grants = AsyncMock(return_value=[])
    storage.get_agent_app_profile = AsyncMock(return_value=None)
    storage.get_repo_profile = AsyncMock(return_value=None)
    storage.create_managed_operation = AsyncMock(return_value={"operation_id": "op-1"})
    storage.get_publish_candidate = AsyncMock(return_value=None)
    storage.find_managed_operation_by_ref = AsyncMock(return_value=None)
    storage.update_publish_candidate_review = AsyncMock(
        return_value={"candidate_id": "candidate-1", "status": "github_pr_open"}
    )
    storage.update_managed_operation = AsyncMock()
    storage.upsert_operation_ref = AsyncMock()
    storage.record_operation_evidence = AsyncMock(return_value={"evidence_id": "evidence-1"})
    storage.record_operation_incident = AsyncMock(return_value={"incident_id": "incident-1"})
    return storage


class _FakeDiscoveryClient:
    instances: list[_FakeDiscoveryClient] = []

    def __init__(self, token: str) -> None:
        self.token = token
        self.closed = False
        self.installation_pages: list[tuple[int, int]] = []
        self.repository_pages: list[tuple[int, int]] = []
        self._repositories = [
            Repository(
                owner="jimtin",
                name="alpha-private",
                description="AI control plane",
                private=True,
            ),
            Repository(
                owner="jimtin",
                name="beta-public",
                description="Public demo",
                private=False,
            ),
            Repository(
                owner="someone-else",
                name="foreign-private",
                description="Other owner",
                private=True,
            ),
            Repository(
                owner="jimtin",
                name="archived-private",
                description="Archived repo",
                private=True,
                archived=True,
            ),
        ]
        self.__class__.instances.append(self)

    async def list_installation_repositories(self, *, per_page: int, page: int) -> list[Repository]:
        self.installation_pages.append((per_page, page))
        return list(self._repositories) if page == 1 else []

    async def list_repositories(self, *, per_page: int, page: int) -> list[Repository]:
        self.repository_pages.append((per_page, page))
        return list(self._repositories) if page == 1 else []

    async def close(self) -> None:
        self.closed = True


class _FakeRepositoryClient:
    def __init__(self, token: str) -> None:
        self.token = token
        self.closed = False

    async def get_repository(self, owner: str, repo: str) -> Repository:
        return Repository(
            owner=owner,
            name=repo,
            description="Managed repo",
            private=True,
            html_url=f"https://github.com/{owner}/{repo}",
            default_branch="main",
        )

    async def close(self) -> None:
        self.closed = True


class _FakeGovernanceClient:
    def __init__(self, token: str) -> None:
        self.token = token
        self.closed = False
        self.updated_payload: dict[str, Any] | None = None

    async def get_repository(self, owner: str, repo: str) -> Repository:
        return Repository(owner=owner, name=repo, default_branch="main", private=True)

    async def update_branch_protection(
        self,
        owner: str,
        repo: str,
        *,
        branch: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.updated_payload = {
            "owner": owner,
            "repo": repo,
            "branch": branch,
            "payload": payload,
        }
        return {"branch": branch, "protection": "updated"}

    async def close(self) -> None:
        self.closed = True


class _FakeFailingGovernanceClient(_FakeGovernanceClient):
    async def update_branch_protection(
        self,
        owner: str,
        repo: str,
        *,
        branch: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        raise GitHubValidationError("branch protection rejected")


class _FakePullRequestClient:
    def __init__(self, token: str) -> None:
        self.token = token
        self.closed = False
        self.created: list[dict[str, Any]] = []

    async def find_open_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        head: str,
        base: str,
    ) -> PullRequest | None:
        return None

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        head: str,
        base: str,
        body: str,
        draft: bool,
    ) -> PullRequest:
        self.created.append(
            {
                "owner": owner,
                "repo": repo,
                "title": title,
                "head": head,
                "base": base,
                "body": body,
                "draft": draft,
            }
        )
        return PullRequest(
            number=164,
            title=title,
            body=body,
            head_ref=head,
            base_ref=base,
            repository=f"{owner}/{repo}",
        )

    async def close(self) -> None:
        self.closed = True


class _FakeArchiveClient:
    def __init__(self, token: str) -> None:
        self.token = token
        self.closed = False

    async def get_repository(self, owner: str, repo: str) -> Repository:
        return Repository(
            owner=owner,
            name=repo,
            private=True,
            default_branch="main",
            html_url=f"https://github.com/{owner}/{repo}",
        )

    async def get_repository_archive(self, owner: str, repo: str, *, ref: str) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            file_bytes = b"print('ok')\n"
            info = tarfile.TarInfo(name=f"{repo}-{ref}/src/main.py")
            info.size = len(file_bytes)
            archive.addfile(info, io.BytesIO(file_bytes))
        return buffer.getvalue()

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_discover_github_repositories_filters_installation_results(monkeypatch) -> None:
    storage = _storage()
    storage.get_external_service_connector_with_secret.return_value = {
        "connector_id": "github-primary",
        "service_kind": "github",
        "active": True,
        "secret_value": "token-1",
        "auth_kind": "github_app_installation",
        "policy": {
            "allowed_repositories": ["jimtin/alpha-private", "jimtin/beta-public"],
            "allowed_owners": ["jimtin"],
        },
    }
    monkeypatch.setattr(agent_bootstrap, "GitHubClient", _FakeDiscoveryClient)

    skill = AgentBootstrapSkill(storage=storage)
    repositories = await skill._discover_github_repositories(
        "owner-1",
        connector_id="github-primary",
        query="alpha",
        limit=3,
        private_only=True,
    )

    assert repositories == [
        {
            "owner": "jimtin",
            "name": "alpha-private",
            "full_name": "jimtin/alpha-private",
            "description": "AI control plane",
            "html_url": "",
            "default_branch": "main",
            "private": True,
            "fork": False,
            "archived": False,
            "open_issues_count": 0,
            "stargazers_count": 0,
            "forks_count": 0,
            "connector_id": "github-primary",
        }
    ]
    client = _FakeDiscoveryClient.instances[-1]
    assert client.installation_pages == [(3, 1), (3, 2)]
    assert client.repository_pages == []
    assert client.closed is True


@pytest.mark.asyncio
async def test_enroll_github_repository_builds_repo_app_and_knowledge_pack(monkeypatch) -> None:
    storage = _storage()
    storage.list_agent_docs_manifests.return_value = [
        {
            "slug": "zetherion-docs-index",
            "manifest": {"title": "Docs Index"},
        }
    ]
    monkeypatch.setattr(agent_bootstrap, "GitHubClient", _FakeRepositoryClient)

    skill = AgentBootstrapSkill(storage=storage)
    skill._enforce_managed_repo = AsyncMock(  # type: ignore[method-assign]
        return_value={"governance": {"applied": True}}
    )

    result = await skill._enroll_github_repository(
        owner_id="owner-1",
        github_repo="jimtin/zetherion-ai",
        app_id=None,
        display_name="Zetherion AI",
        stack_kind="python",
        public_base_url="https://cgs.example.com",
        overrides={"knowledge_pack": {"custom_hint": True}},
        enforce_managed_repo=True,
        principal_id="codex-1",
    )

    assert result["repository"]["full_name"] == "jimtin/zetherion-ai"
    assert result["repo_profile"]["repo_id"] == "zetherion-ai"
    assert result["app"]["profile"]["runtime_routes"]["apps"] == (
        "https://cgs.example.com/service/ai/v1/agent/apps"
    )
    assert result["knowledge_pack"]["pack"]["custom_hint"] is True
    skill._enforce_managed_repo.assert_awaited_once()  # type: ignore[attr-defined]
    storage.record_agent_audit_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_enforce_managed_repo_updates_profiles_and_audits(monkeypatch) -> None:
    storage = _storage()
    storage.get_agent_app_profile.return_value = {
        "app_id": "zetherion-ai",
        "display_name": "Zetherion AI",
        "profile": {
            "github_repos": [],
        },
        "active": True,
    }
    storage.get_repo_profile.return_value = {
        "repo_id": "zetherion-ai",
        "display_name": "Zetherion AI",
        "metadata": {},
    }
    monkeypatch.setattr(agent_bootstrap, "GitHubClient", _FakeGovernanceClient)

    skill = AgentBootstrapSkill(storage=storage)
    result = await skill._enforce_managed_repo(
        owner_id="owner-1",
        app_id="zetherion-ai",
        github_repo="jimtin/zetherion-ai",
        default_branch=None,
        principal_id="codex-1",
    )

    assert result["review"]["status"] == "applied"
    assert result["governance"]["applied"] is True
    app_profile = storage.upsert_agent_app_profile.await_args.kwargs["profile"]
    repo_profile = storage.upsert_repo_profile.await_args.args[1]
    assert app_profile["github_governance"]["managed_repo"] is True
    assert app_profile["github_repos"] == ["jimtin/zetherion-ai"]
    assert repo_profile["metadata"]["github_governance"]["managed_repo"] is True
    storage.record_agent_audit_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_enforce_managed_repo_records_warning_on_branch_protection_failure(
    monkeypatch,
) -> None:
    storage = _storage()
    monkeypatch.setattr(agent_bootstrap, "GitHubClient", _FakeFailingGovernanceClient)

    skill = AgentBootstrapSkill(storage=storage)
    result = await skill._enforce_managed_repo(
        owner_id="owner-1",
        app_id="zetherion-ai",
        github_repo="jimtin/zetherion-ai",
        default_branch="main",
        principal_id="codex-1",
    )

    assert result["review"]["status"] == "failed"
    assert result["governance"]["applied"] is False
    assert "branch protection rejected" in result["governance"]["error"]
    storage.upsert_agent_app_profile.assert_not_awaited()
    storage.upsert_repo_profile.assert_not_awaited()
    assert storage.record_agent_audit_event.await_args.kwargs["decision"] == "warning"


@pytest.mark.asyncio
async def test_apply_candidate_payload_supports_diff_and_overlay_inputs(tmp_path: Path) -> None:
    skill = AgentBootstrapSkill(storage=_storage())
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    skill._run_command = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"returncode": 1, "stdout": "", "stderr": "3way failed"},
            {"returncode": 0, "stdout": "", "stderr": ""},
        ]
    )
    await skill._apply_candidate_payload(
        workspace=workspace,
        candidate_payload={"diff_text": "diff --git a/README.md b/README.md\n"},
        env={},
    )

    patch_file = workspace / ".zetherion-publish.patch"
    assert patch_file.read_text(encoding="utf-8").startswith("diff --git")
    assert skill._run_command.await_count == 2  # type: ignore[attr-defined]

    overlay_buffer = io.BytesIO()
    with tarfile.open(fileobj=overlay_buffer, mode="w:gz") as archive:
        file_bytes = b"hello from overlay\n"
        info = tarfile.TarInfo(name="overlay/README.md")
        info.size = len(file_bytes)
        archive.addfile(info, io.BytesIO(file_bytes))
    payload = base64.b64encode(overlay_buffer.getvalue()).decode("ascii")

    await skill._apply_candidate_payload(
        workspace=workspace,
        candidate_payload={"patch_bundle_base64": payload},
        env={},
    )

    assert (workspace / "README.md").read_text(encoding="utf-8") == "hello from overlay\n"


@pytest.mark.asyncio
async def test_apply_candidate_payload_requires_diff_or_bundle(tmp_path: Path) -> None:
    skill = AgentBootstrapSkill(storage=_storage())

    with pytest.raises(ValueError, match="missing diff_text and patch_bundle_base64"):
        await skill._apply_candidate_payload(
            workspace=tmp_path,
            candidate_payload={},
            env={},
        )


@pytest.mark.asyncio
async def test_apply_candidate_payload_uses_patch_file_from_overlay_bundle(tmp_path: Path) -> None:
    skill = AgentBootstrapSkill(storage=_storage())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill._run_command = AsyncMock(  # type: ignore[method-assign]
        return_value={"returncode": 0, "stdout": "", "stderr": ""}
    )

    overlay_buffer = io.BytesIO()
    with tarfile.open(fileobj=overlay_buffer, mode="w:gz") as archive:
        patch_bytes = b"diff --git a/README.md b/README.md\n"
        info = tarfile.TarInfo(name="patch.diff")
        info.size = len(patch_bytes)
        archive.addfile(info, io.BytesIO(patch_bytes))
    payload = base64.b64encode(overlay_buffer.getvalue()).decode("ascii")

    await skill._apply_candidate_payload(
        workspace=workspace,
        candidate_payload={"patch_bundle_base64": payload},
        env={"GIT_TERMINAL_PROMPT": "0"},
    )

    assert skill._run_command.await_count == 1  # type: ignore[attr-defined]
    command = skill._run_command.await_args.args[0]  # type: ignore[attr-defined]
    assert command[:5] == ["git", "-C", str(workspace), "apply", "--index"]


@pytest.mark.asyncio
async def test_run_fast_validation_lanes_tracks_skips_and_failures(
    monkeypatch,
    tmp_path: Path,
) -> None:
    skill = AgentBootstrapSkill(storage=_storage())
    monkeypatch.setattr(
        agent_bootstrap.shutil,
        "which",
        lambda command: None if command == "missingcmd" else f"/bin/{command}",
    )
    skill._run_command = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"returncode": 0, "stdout": "ok", "stderr": ""},
            {"returncode": 1, "stdout": "", "stderr": "failed"},
        ]
    )

    result = await skill._run_fast_validation_lanes(
        repo={
            "mandatory_static_gates": [
                {"lane_id": "empty", "command": []},
                {"lane_id": "missing", "command": ["missingcmd", "--version"]},
                {"lane_id": "passed", "command": ["python", "--version"]},
            ],
            "local_fast_lanes": [
                {"lane_id": "failed", "command": ["pytest", "-q"]},
                {"lane_id": "never-run", "command": ["echo", "later"]},
            ],
        },
        workspace=tmp_path,
    )

    assert result["status"] == "failed"
    assert [receipt["status"] for receipt in result["receipts"]] == [
        "skipped",
        "skipped",
        "passed",
        "failed",
    ]


@pytest.mark.asyncio
async def test_run_command_returns_stdout_and_raises_on_failure(tmp_path: Path) -> None:
    skill = AgentBootstrapSkill(storage=_storage())

    success = await skill._run_command(
        ["/bin/sh", "-c", "printf 'ok'"],
        cwd=tmp_path,
        check=True,
    )

    assert success["returncode"] == 0
    assert success["stdout"] == "ok"

    with pytest.raises(ValueError, match="Command failed"):
        await skill._run_command(
            ["/bin/sh", "-c", "echo boom >&2; exit 3"],
            cwd=tmp_path,
            check=True,
        )


@pytest.mark.asyncio
async def test_apply_publish_candidate_opens_pull_request_after_local_validation(
    monkeypatch,
) -> None:
    storage = _storage()
    candidate = {
        "candidate_id": "candidate-1",
        "app_id": "zetherion-ai",
        "repo_id": "zetherion-ai",
        "base_sha": "a" * 40,
        "principal_id": "codex-1",
        "candidate": {
            "summary": "Apply DM queue fix",
            "intent": "Repair Discord DM delivery.",
            "target_branch": "zetherion/fix-dm",
        },
    }
    storage.get_publish_candidate.return_value = candidate
    storage.update_publish_candidate_review.return_value = {
        **candidate,
        "status": "github_pr_open",
    }
    monkeypatch.setattr(agent_bootstrap, "GitHubClient", _FakePullRequestClient)

    skill = AgentBootstrapSkill(storage=storage)
    skill._resolve_repo_profile = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "repo_id": "zetherion-ai",
            "app_id": "zetherion-ai",
            "github_repo": "jimtin/zetherion-ai",
            "default_branch": "main",
        }
    )
    skill._require_github_connector = AsyncMock(  # type: ignore[method-assign]
        return_value={"secret_value": "token-1"}
    )
    skill._find_or_create_operation = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "operation_id": "op-1",
            "summary": {},
            "metadata": {},
            "app_id": "zetherion-ai",
            "repo_id": "zetherion-ai",
        }
    )
    skill._apply_candidate_payload = AsyncMock()  # type: ignore[method-assign]
    skill._run_fast_validation_lanes = AsyncMock(  # type: ignore[method-assign]
        return_value={"status": "passed", "receipts": [{"lane_id": "z-unit-core"}]}
    )

    async def _fake_run_command(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> dict[str, Any]:
        if "status" in command and "--porcelain" in command:
            return {"command": command, "returncode": 0, "stdout": " M README.md\n", "stderr": ""}
        if "rev-parse" in command and "--verify" in command:
            return {"command": command, "returncode": 1, "stdout": "", "stderr": "missing"}
        if "rev-parse" in command and "HEAD" in command:
            return {"command": command, "returncode": 0, "stdout": "b" * 40 + "\n", "stderr": ""}
        return {"command": command, "returncode": 0, "stdout": "", "stderr": ""}

    skill._run_command = AsyncMock(side_effect=_fake_run_command)  # type: ignore[method-assign]

    result = await skill._apply_publish_candidate(
        owner_id="owner-1",
        candidate_id="candidate-1",
        target_branch=None,
        principal_id="codex-1",
    )

    assert result["candidate"]["status"] == "github_pr_open"
    assert result["pull_request"]["number"] == 164
    skill._apply_candidate_payload.assert_awaited_once()  # type: ignore[attr-defined]
    storage.record_operation_evidence.assert_awaited_once()
    storage.record_agent_audit_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_publish_candidate_records_validation_failure(monkeypatch) -> None:
    storage = _storage()
    candidate = {
        "candidate_id": "candidate-1",
        "app_id": "zetherion-ai",
        "repo_id": "zetherion-ai",
        "base_sha": "a" * 40,
        "principal_id": "codex-1",
        "candidate": {
            "summary": "Apply DM queue fix",
            "intent": "Repair Discord DM delivery.",
        },
    }
    storage.get_publish_candidate = AsyncMock(
        side_effect=[
            candidate,
            {**candidate, "status": "failed_validation"},
        ]
    )
    monkeypatch.setattr(agent_bootstrap, "GitHubClient", _FakePullRequestClient)

    skill = AgentBootstrapSkill(storage=storage)
    skill._resolve_repo_profile = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "repo_id": "zetherion-ai",
            "app_id": "zetherion-ai",
            "github_repo": "jimtin/zetherion-ai",
            "default_branch": "main",
        }
    )
    skill._require_github_connector = AsyncMock(  # type: ignore[method-assign]
        return_value={"secret_value": "token-1"}
    )
    skill._find_or_create_operation = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "operation_id": "op-1",
            "summary": {},
            "metadata": {},
            "app_id": "zetherion-ai",
            "repo_id": "zetherion-ai",
        }
    )
    skill._apply_candidate_payload = AsyncMock()  # type: ignore[method-assign]
    skill._run_fast_validation_lanes = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "status": "failed",
            "receipts": [{"lane_id": "z-unit-core", "status": "failed"}],
        }
    )

    async def _fake_run_command(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> dict[str, Any]:
        if "status" in command and "--porcelain" in command:
            return {"command": command, "returncode": 0, "stdout": " M README.md\n", "stderr": ""}
        if "rev-parse" in command and "--verify" in command:
            return {"command": command, "returncode": 0, "stdout": "a" * 40 + "\n", "stderr": ""}
        return {"command": command, "returncode": 0, "stdout": "", "stderr": ""}

    skill._run_command = AsyncMock(side_effect=_fake_run_command)  # type: ignore[method-assign]

    result = await skill._apply_publish_candidate(
        owner_id="owner-1",
        candidate_id="candidate-1",
        target_branch="zetherion/fix-dm",
        principal_id="codex-1",
    )

    assert result["candidate"]["status"] == "failed_validation"
    storage.record_operation_incident.assert_awaited_once()
    update_calls = storage.update_managed_operation.await_args_list
    assert any(call.kwargs["lifecycle_stage"] == "validation_failed" for call in update_calls)


@pytest.mark.asyncio
async def test_apply_publish_candidate_records_apply_failure_when_git_push_fails(
    monkeypatch,
) -> None:
    storage = _storage()
    candidate = {
        "candidate_id": "candidate-1",
        "app_id": "zetherion-ai",
        "repo_id": "zetherion-ai",
        "base_sha": "a" * 40,
        "principal_id": "codex-1",
        "candidate": {
            "summary": "Apply DM queue fix",
            "intent": "Repair Discord DM delivery.",
            "target_branch": "zetherion/fix-dm",
        },
    }
    storage.get_publish_candidate.return_value = candidate
    storage.update_publish_candidate_review.return_value = {
        **candidate,
        "status": "failed",
    }
    monkeypatch.setattr(agent_bootstrap, "GitHubClient", _FakePullRequestClient)

    skill = AgentBootstrapSkill(storage=storage)
    skill._resolve_repo_profile = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "repo_id": "zetherion-ai",
            "app_id": "zetherion-ai",
            "github_repo": "jimtin/zetherion-ai",
            "default_branch": "main",
        }
    )
    skill._require_github_connector = AsyncMock(  # type: ignore[method-assign]
        return_value={"secret_value": "token-1"}
    )
    skill._find_or_create_operation = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "operation_id": "op-1",
            "summary": {},
            "metadata": {},
            "app_id": "zetherion-ai",
            "repo_id": "zetherion-ai",
        }
    )
    skill._apply_candidate_payload = AsyncMock()  # type: ignore[method-assign]
    skill._run_fast_validation_lanes = AsyncMock(  # type: ignore[method-assign]
        return_value={"status": "passed", "receipts": [{"lane_id": "z-unit-core"}]}
    )

    async def _fake_run_command(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> dict[str, Any]:
        if "status" in command and "--porcelain" in command:
            return {"command": command, "returncode": 0, "stdout": " M README.md\n", "stderr": ""}
        if "rev-parse" in command and "--verify" in command:
            return {"command": command, "returncode": 0, "stdout": "a" * 40 + "\n", "stderr": ""}
        if "rev-parse" in command and "HEAD" in command:
            return {"command": command, "returncode": 0, "stdout": "b" * 40 + "\n", "stderr": ""}
        if "push" in command:
            raise ValueError("push failed")
        return {"command": command, "returncode": 0, "stdout": "", "stderr": ""}

    skill._run_command = AsyncMock(side_effect=_fake_run_command)  # type: ignore[method-assign]

    result = await skill._apply_publish_candidate(
        owner_id="owner-1",
        candidate_id="candidate-1",
        target_branch=None,
        principal_id="codex-1",
    )

    assert result["candidate"]["status"] == "failed"
    assert result["error"] == "push failed"
    storage.record_operation_incident.assert_awaited_once()
    assert storage.record_agent_audit_event.await_args.kwargs["decision"] == "blocked"


@pytest.mark.asyncio
async def test_require_connector_and_service_connector_for_validate_contracts() -> None:
    storage = _storage()
    skill = AgentBootstrapSkill(storage=storage)

    connector = await skill._require_connector(
        "owner-1",
        connector_id="github-primary",
        service_kind="github",
    )
    assert connector["connector_id"] == "github-primary"

    app_profile = {
        "app_id": "zetherion-ai",
        "profile": {
            "service_connector_map": {
                "github": {
                    "connector_id": "github-primary",
                    "read_access": ["branch_metadata"],
                    "write_access": [],
                }
            }
        },
    }
    github_connector = skill._service_connector_for(app_profile, service_kind="github")
    assert github_connector["available_views"] == ["compare", "overview", "pulls", "workflows"]
    assert github_connector["service_kind"] == "github"

    storage.get_external_service_connector_with_secret.return_value = None
    with pytest.raises(ValueError, match="not found"):
        await skill._require_connector(
            "owner-1",
            connector_id="missing",
            service_kind="github",
        )

    storage.get_external_service_connector_with_secret.return_value = {
        "connector_id": "github-primary",
        "service_kind": "vercel",
        "active": True,
        "secret_value": "token-1",
    }
    with pytest.raises(ValueError, match="not a `github` connector"):
        await skill._require_connector(
            "owner-1",
            connector_id="github-primary",
            service_kind="github",
        )

    storage.get_external_service_connector_with_secret.return_value = {
        "connector_id": "github-primary",
        "service_kind": "github",
        "active": False,
        "secret_value": "token-1",
    }
    with pytest.raises(ValueError, match="inactive"):
        await skill._require_connector(
            "owner-1",
            connector_id="github-primary",
            service_kind="github",
        )

    storage.get_external_service_connector_with_secret.return_value = {
        "connector_id": "github-primary",
        "service_kind": "github",
        "active": True,
        "secret_value": "",
    }
    with pytest.raises(ValueError, match="no secret configured"):
        await skill._require_connector(
            "owner-1",
            connector_id="github-primary",
            service_kind="github",
        )
    optional_secret = await skill._require_connector(
        "owner-1",
        connector_id="github-primary",
        service_kind="github",
        require_secret=False,
    )
    assert optional_secret["connector_id"] == "github-primary"

    with pytest.raises(ValueError, match="does not declare a `stripe` connector"):
        skill._service_connector_for(app_profile, service_kind="stripe")


@pytest.mark.asyncio
async def test_list_accessible_apps_and_require_app_access_follow_app_and_repo_grants() -> None:
    storage = _storage()
    storage.list_agent_app_profiles.return_value = [
        {
            "app_id": "zetherion-ai",
            "profile": {
                "repo_ids": ["zetherion-ai"],
            },
        },
        {
            "app_id": "catalyst-group-solutions",
            "profile": {
                "repo_ids": ["catalyst-group-solutions"],
            },
        },
    ]
    storage.list_external_access_grants.return_value = [
        {
            "resource_type": "app",
            "resource_id": "zetherion-ai",
            "active": True,
        },
        {
            "resource_type": "repo",
            "resource_id": "catalyst-group-solutions",
            "active": True,
        },
        {
            "resource_type": "app",
            "resource_id": "ignored-app",
            "active": False,
        },
    ]

    async def _get_app_profile(owner_id: str, app_id: str) -> dict[str, Any] | None:
        if app_id == "zetherion-ai":
            return {"app_id": "zetherion-ai", "profile": {}}
        if app_id == "missing-app":
            return None
        return {"app_id": app_id, "profile": {}}

    storage.get_agent_app_profile = AsyncMock(side_effect=_get_app_profile)

    skill = AgentBootstrapSkill(storage=storage)
    accessible = await skill._list_accessible_apps("owner-1", "codex-1")
    assert [app["app_id"] for app in accessible] == [
        "zetherion-ai",
        "catalyst-group-solutions",
    ]

    app = await skill._require_app_access(
        "owner-1",
        principal_id=None,
        app_id="zetherion-ai",
    )
    assert app["app_id"] == "zetherion-ai"

    app_with_principal = await skill._require_app_access(
        "owner-1",
        principal_id="codex-1",
        app_id="zetherion-ai",
    )
    assert app_with_principal["app_id"] == "zetherion-ai"

    with pytest.raises(ValueError, match="App `missing-app` not found"):
        await skill._require_app_access(
            "owner-1",
            principal_id="codex-1",
            app_id="missing-app",
        )

    with pytest.raises(ValueError, match="not allowed to access app `denied-app`"):
        await skill._require_app_access(
            "owner-1",
            principal_id="codex-1",
            app_id="denied-app",
        )


@pytest.mark.asyncio
async def test_resolve_repo_profile_and_find_or_create_operation_builtin_and_create_paths() -> None:
    storage = _storage()
    storage.get_repo_profile.side_effect = [None, None]
    storage.find_managed_operation_by_ref.side_effect = [
        {"operation_id": "existing-op"},
        None,
        None,
    ]
    storage.create_managed_operation.return_value = {"operation_id": "new-op"}
    storage.upsert_operation_ref = AsyncMock(return_value={"ref_id": "ref-1"})

    skill = AgentBootstrapSkill(storage=storage)

    resolved = await skill._resolve_repo_profile("owner-1", "zetherion-ai")
    assert resolved["repo_id"] == "zetherion-ai"

    with pytest.raises(ValueError, match="Repo profile `missing-repo` not found"):
        await skill._resolve_repo_profile("owner-1", "missing-repo")

    existing = await skill._find_or_create_operation(
        owner_id="owner-1",
        app_id="zetherion-ai",
        repo_id="zetherion-ai",
        refs={"git_sha": "a" * 40},
        request_context={"operation_kind": "publish_candidate"},
    )
    assert existing["operation_id"] == "existing-op"

    created = await skill._find_or_create_operation(
        owner_id="owner-1",
        app_id="zetherion-ai",
        repo_id="zetherion-ai",
        refs={
            "git_sha": "b" * 40,
            "branch": "zetherion/fix-dm",
        },
        request_context={},
    )
    assert created["operation_id"] == "new-op"
    assert storage.create_managed_operation.await_args.kwargs["operation_kind"] == (
        "service_evidence"
    )
    assert storage.upsert_operation_ref.await_count == 2


@pytest.mark.asyncio
async def test_workspace_bundle_payload_prefers_github_archive_and_falls_back_on_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    storage = _storage()
    skill = AgentBootstrapSkill(storage=storage)
    monkeypatch.setattr(agent_bootstrap, "GitHubClient", _FakeArchiveClient)

    repo = {
        "repo_id": "zetherion-ai",
        "github_repo": "jimtin/zetherion-ai",
        "allowed_paths": [str(tmp_path / "missing-worktree")],
    }
    knowledge_pack = {
        "workspace_manifest": {"repo_id": "zetherion-ai"},
        "service_connector_map": {
            "github": {
                "connector_id": "github-primary",
            }
        },
    }

    bundle, resolved_ref = await skill._create_workspace_bundle_payload(
        owner_id="owner-1",
        repo=repo,
        knowledge_pack=knowledge_pack,
        git_ref="HEAD",
    )
    assert bundle["source_kind"] == "github_archive"
    assert bundle["download_mode"] == "inline_base64"
    assert bundle["repository"]["full_name"] == "jimtin/zetherion-ai"
    assert resolved_ref == "main"

    skill._build_github_workspace_bundle = AsyncMock(  # type: ignore[method-assign]
        side_effect=GitHubAPIError("archive fetch failed")
    )
    fallback_bundle, fallback_ref = await skill._create_workspace_bundle_payload(
        owner_id="owner-1",
        repo=repo,
        knowledge_pack=knowledge_pack,
        git_ref="main",
    )
    assert fallback_bundle["source_kind"] == "metadata_only"
    assert fallback_ref is None

    no_repo_bundle, no_repo_ref = await skill._create_workspace_bundle_payload(
        owner_id="owner-1",
        repo={"repo_id": "zetherion-ai", "allowed_paths": []},
        knowledge_pack={"workspace_manifest": {"repo_id": "zetherion-ai"}},
        git_ref="main",
    )
    assert no_repo_bundle["source_kind"] == "metadata_only"
    assert no_repo_ref is None


@pytest.mark.asyncio
async def test_read_service_view_and_execute_service_action_record_gap_on_validation_errors() -> (
    None
):
    storage = _storage()
    storage.record_agent_audit_event = AsyncMock(return_value={"audit_id": "audit-1"})
    storage.create_agent_service_request = AsyncMock(return_value={"request_id": "req-1"})
    skill = AgentBootstrapSkill(storage=storage)
    skill._record_gap = AsyncMock(return_value={"gap_id": "gap-1"})  # type: ignore[method-assign]

    app_profile = {
        "app_id": "zetherion-ai",
        "profile": {
            "repo_ids": ["zetherion-ai"],
            "service_connector_map": {
                "github": {
                    "connector_id": "github-primary",
                    "read_access": ["overview_only"],
                    "write_access": [],
                },
                "stripe": {
                    "connector_id": "stripe-primary",
                    "read_access": [],
                    "write_access": ["product_ensure"],
                },
            },
        },
    }

    with pytest.raises(ValueError, match="does not allow `github` view `overview`"):
        await skill._read_service_view(
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="zetherion-ai",
            app_profile=app_profile,
            service_kind="github",
            view="overview",
            public_base_url="https://cgs.example.com",
            request_context={"session_id": "sess-1"},
        )
    assert skill._record_gap.await_args.kwargs["detected_from"] == "service_read"  # type: ignore[attr-defined]

    skill._record_gap.reset_mock()  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="Unsupported `stripe` service action `unknown.action`"):
        await skill._execute_service_action(
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="zetherion-ai",
            app_profile=app_profile,
            service_kind="stripe",
            action_id="unknown.action",
            request_context={"session_id": "sess-1", "input": {"name": "Gold"}},
        )
    assert skill._record_gap.await_args.kwargs["detected_from"] == (  # type: ignore[attr-defined]
        "service_request_validation"
    )


@pytest.mark.asyncio
async def test_execute_stripe_service_action_translates_api_errors() -> None:
    stripe_client = MagicMock()
    stripe_client.ensure_product = AsyncMock(side_effect=agent_bootstrap.StripeAPIError("boom"))
    stripe_client.close = AsyncMock()

    skill = AgentBootstrapSkill(storage=_storage())
    skill._require_connector = AsyncMock(  # type: ignore[method-assign]
        return_value={"secret_value": "stripe-secret"}
    )

    with patch("zetherion_ai.skills.agent_bootstrap.StripeClient", return_value=stripe_client):
        with pytest.raises(ValueError, match="boom"):
            await skill._execute_stripe_service_action(
                owner_id="owner-1",
                connector_id="stripe-primary",
                action_id="product.ensure",
                input_payload={"name": "Gold"},
            )
