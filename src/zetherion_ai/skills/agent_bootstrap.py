"""Owner-scoped agent bootstrap skill."""

from __future__ import annotations

from zetherion_ai.logging import get_logger
from zetherion_ai.owner_ci import OwnerCiStorage
from zetherion_ai.skills.base import Skill, SkillMetadata, SkillRequest, SkillResponse
from zetherion_ai.skills.permissions import Permission, PermissionSet

log = get_logger("zetherion_ai.skills.agent_bootstrap")

_DEFAULT_DOCS = [
    {
        "slug": "cgs-ai-api-quickstart",
        "title": "CGS AI API Quickstart",
        "path": "/docs/technical/cgs-ai-api-quickstart",
        "category": "quickstart",
    },
    {
        "slug": "cgs-ai-api-reference",
        "title": "CGS AI API Reference",
        "path": "/docs/technical/cgs-ai-api-reference",
        "category": "reference",
    },
    {
        "slug": "cgs-ai-integration-credentials-runbook",
        "title": "Integration Credentials Runbook",
        "path": "/docs/technical/cgs-ai-integration-credentials-runbook",
        "category": "runbook",
    },
    {
        "slug": "zetherion-docs-index",
        "title": "Zetherion Product Docs",
        "path": "/products/zetherion-ai/docs",
        "category": "product",
    },
]


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


class AgentBootstrapSkill(Skill):
    """Store agent setup manifests and expose machine-readable docs manifests."""

    def __init__(self, *, storage: OwnerCiStorage) -> None:
        super().__init__(memory=None)
        self._storage = storage

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="agent_bootstrap",
            description="Owner agent bootstrap receipts, manifests, and docs metadata",
            version="0.1.0",
            permissions=PermissionSet({Permission.ADMIN, Permission.READ_CONFIG}),
            intents=[
                "agent_client_bootstrap",
                "agent_client_manifest_get",
                "agent_docs_list",
                "agent_docs_get",
            ],
        )

    async def initialize(self) -> bool:
        log.info("agent_bootstrap_initialized")
        return True

    async def handle(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        await self._ensure_default_docs(
            owner_id,
            str(request.context.get("public_base_url") or "").strip(),
        )
        if request.intent == "agent_client_bootstrap":
            client_id = str(request.context.get("client_id") or "").strip()
            if not client_id:
                return SkillResponse.error_response(request.id, "client_id is required")
            manifest_payload = dict(request.context.get("manifest") or {})
            if not manifest_payload:
                return SkillResponse.error_response(request.id, "manifest is required")
            stored_manifest = await self._storage.store_agent_bootstrap_manifest(
                owner_id,
                client_id,
                manifest_payload,
            )
            receipt = await self._storage.store_agent_setup_receipt(
                owner_id,
                client_id=client_id,
                receipt={
                    "status": "stored",
                    "steps": list(request.context.get("steps") or []),
                    "stored_manifest_version": manifest_payload.get("version") or "v1",
                },
            )
            return SkillResponse(
                request_id=request.id,
                message=f"Stored bootstrap manifest for `{client_id}`.",
                data={"manifest": stored_manifest, "receipt": receipt},
            )
        if request.intent == "agent_client_manifest_get":
            client_id = str(request.context.get("client_id") or "").strip()
            if not client_id:
                return SkillResponse.error_response(request.id, "client_id is required")
            stored_manifest_record = await self._storage.get_agent_bootstrap_manifest(
                owner_id, client_id
            )
            if stored_manifest_record is None:
                return SkillResponse.error_response(request.id, f"Manifest `{client_id}` not found")
            return SkillResponse(
                request_id=request.id,
                message=f"Loaded bootstrap manifest for `{client_id}`.",
                data={"manifest": stored_manifest_record},
            )
        if request.intent == "agent_docs_get":
            slug = str(request.context.get("slug") or "").strip()
            if not slug:
                return SkillResponse.error_response(request.id, "slug is required")
            docs_manifest = await self._storage.get_agent_docs_manifest(owner_id, slug)
            if docs_manifest is None:
                return SkillResponse.error_response(request.id, f"Docs manifest `{slug}` not found")
            return SkillResponse(
                request_id=request.id,
                message=f"Loaded docs manifest `{slug}`.",
                data={"doc": docs_manifest},
            )
        if request.intent == "agent_docs_list":
            docs = await self._storage.list_agent_docs_manifests(owner_id)
            return SkillResponse(
                request_id=request.id,
                message=f"Loaded {len(docs)} docs manifests.",
                data={"docs": docs},
            )
        return SkillResponse.error_response(
            request.id,
            f"Unknown agent bootstrap intent: {request.intent}",
        )

    async def _ensure_default_docs(self, owner_id: str, public_base_url: str) -> None:
        existing = await self._storage.list_agent_docs_manifests(owner_id)
        if existing:
            return
        base = public_base_url.rstrip("/")
        for doc in _DEFAULT_DOCS:
            await self._storage.upsert_agent_docs_manifest(
                owner_id,
                slug=str(doc["slug"]),
                title=str(doc["title"]),
                manifest={
                    **doc,
                    "url": f"{base}{doc['path']}" if base else str(doc["path"]),
                    "version": "current",
                },
            )
