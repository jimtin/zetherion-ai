"""Skill registry for managing and routing to skills.

The registry is responsible for:
- Discovering and loading skills
- Routing requests to the appropriate skill based on intent
- Coordinating heartbeat cycles across all skills
- Enforcing permission boundaries
"""

from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import (
    HeartbeatAction,
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
    SkillStatus,
)
from zetherion_ai.skills.permissions import Permission, PermissionSet

if TYPE_CHECKING:
    from zetherion_ai.memory.qdrant import QdrantMemory

log = get_logger("zetherion_ai.skills.registry")


class SkillNotFoundError(Exception):
    """Raised when a skill is not found."""

    pass


class SkillPermissionError(Exception):
    """Raised when a skill lacks required permissions."""

    pass


class SkillRegistry:
    """Registry for managing skills.

    The registry maintains a collection of skills and provides:
    - Registration and initialization of skills
    - Intent-based routing to skills
    - Heartbeat coordination
    - Skill listing and metadata
    """

    def __init__(
        self,
        memory: "QdrantMemory | None" = None,
        max_permissions: PermissionSet | None = None,
    ):
        """Initialize the skill registry.

        Args:
            memory: Optional Qdrant memory for skill storage.
            max_permissions: Maximum permissions any skill can have.
                            Skills requesting more permissions will be rejected.
        """
        self._memory = memory
        self._skills: dict[str, Skill] = {}
        self._intent_map: dict[str, str] = {}  # intent -> skill_name
        self._max_permissions = max_permissions

        log.info("skill_registry_initialized")

    @property
    def skill_count(self) -> int:
        """Return the number of registered skills."""
        return len(self._skills)

    def get_skill(self, name: str) -> Skill | None:
        """Get a skill by name.

        Args:
            name: The skill name.

        Returns:
            The skill, or None if not found.
        """
        return self._skills.get(name)

    def get_skill_for_intent(self, intent: str) -> Skill | None:
        """Get the skill that handles a specific intent.

        Args:
            intent: The intent to look up.

        Returns:
            The skill that handles this intent, or None.
        """
        skill_name = self._intent_map.get(intent)
        if skill_name:
            return self._skills.get(skill_name)
        return None

    def register(self, skill: Skill) -> bool:
        """Register a skill with the registry.

        Args:
            skill: The skill to register.

        Returns:
            True if registration succeeded.

        Raises:
            SkillPermissionError: If skill requests excessive permissions.
        """
        metadata = skill.metadata

        # Check if skill name is already registered
        if metadata.name in self._skills:
            log.warning("skill_already_registered", skill=metadata.name)
            return False

        # Validate permissions against max allowed
        if self._max_permissions and not metadata.permissions.is_subset_of(self._max_permissions):
            excess = [p.name for p in metadata.permissions if p not in self._max_permissions]
            raise SkillPermissionError(
                f"Skill '{metadata.name}' requests excessive permissions: {excess}"
            )

        # Register the skill
        self._skills[metadata.name] = skill

        # Map intents to this skill
        for intent in metadata.intents:
            if intent in self._intent_map:
                log.warning(
                    "intent_already_mapped",
                    intent=intent,
                    existing_skill=self._intent_map[intent],
                    new_skill=metadata.name,
                )
            self._intent_map[intent] = metadata.name

        log.info(
            "skill_registered",
            skill=metadata.name,
            version=metadata.version,
            intents=metadata.intents,
        )

        return True

    def unregister(self, name: str) -> bool:
        """Unregister a skill.

        Args:
            name: The skill name.

        Returns:
            True if the skill was unregistered.
        """
        skill = self._skills.pop(name, None)
        if skill is None:
            return False

        # Remove intent mappings
        intents_to_remove = [
            intent for intent, skill_name in self._intent_map.items() if skill_name == name
        ]
        for intent in intents_to_remove:
            del self._intent_map[intent]

        log.info("skill_unregistered", skill=name)
        return True

    async def initialize_all(self) -> dict[str, bool]:
        """Initialize all registered skills.

        Returns:
            Dictionary mapping skill names to initialization success.
        """
        results = {}
        for name, skill in self._skills.items():
            success = await skill.safe_initialize()
            results[name] = success
            if not success:
                log.error("skill_init_failed", skill=name, error=skill.error)

        ready_count = sum(1 for s in results.values() if s)
        log.info(
            "skills_initialized",
            total=len(results),
            ready=ready_count,
            failed=len(results) - ready_count,
        )

        return results

    async def handle_request(self, request: SkillRequest) -> SkillResponse:
        """Route a request to the appropriate skill.

        Args:
            request: The incoming request.

        Returns:
            Response from the skill.
        """
        # Find skill by intent
        skill = self.get_skill_for_intent(request.intent)

        if skill is None:
            # Try to find by explicit skill name in context
            skill_name = request.context.get("skill_name")
            if skill_name:
                skill = self.get_skill(skill_name)

        if skill is None:
            log.warning("no_skill_for_intent", intent=request.intent)
            return SkillResponse.error_response(
                request.id,
                f"No skill found for intent: {request.intent}",
            )

        log.debug(
            "routing_to_skill",
            skill=skill.name,
            intent=request.intent,
            user_id=request.user_id,
        )

        return await skill.safe_handle(request)

    async def run_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Run heartbeat on all ready skills.

        Args:
            user_ids: List of user IDs to check.

        Returns:
            List of all actions from all skills, sorted by priority.
        """
        all_actions: list[HeartbeatAction] = []

        for name, skill in self._skills.items():
            if skill.status != SkillStatus.READY:
                continue

            if not skill.has_permission(Permission.SEND_MESSAGES):
                continue

            try:
                actions = await skill.on_heartbeat(user_ids)
                all_actions.extend(actions)
            except Exception as e:
                log.error("heartbeat_failed", skill=name, error=str(e))

        # Sort by priority (highest first)
        all_actions.sort(key=lambda a: a.priority, reverse=True)

        log.debug("heartbeat_complete", action_count=len(all_actions))
        return all_actions

    def get_system_prompt_fragments(self, user_id: str) -> list[str]:
        """Get system prompt fragments from all ready skills.

        Args:
            user_id: The user ID for personalization.

        Returns:
            List of prompt fragments to include.
        """
        fragments = []

        for skill in self._skills.values():
            if skill.status != SkillStatus.READY:
                continue

            try:
                fragment = skill.get_system_prompt_fragment(user_id)
                if fragment:
                    fragments.append(fragment)
            except Exception as e:
                log.error("prompt_fragment_failed", skill=skill.name, error=str(e))

        return fragments

    def list_skills(self) -> list[SkillMetadata]:
        """List all registered skills.

        Returns:
            List of skill metadata.
        """
        return [skill.metadata for skill in self._skills.values()]

    def list_ready_skills(self) -> list[SkillMetadata]:
        """List only ready skills.

        Returns:
            List of ready skill metadata.
        """
        return [
            skill.metadata for skill in self._skills.values() if skill.status == SkillStatus.READY
        ]

    def list_intents(self) -> dict[str, str]:
        """List all registered intents and their skills.

        Returns:
            Dictionary mapping intents to skill names.
        """
        return dict(self._intent_map)

    def get_status_summary(self) -> dict[str, Any]:
        """Get a summary of registry status.

        Returns:
            Dictionary with registry status information.
        """
        by_status: dict[str, list[str]] = {}
        for skill in self._skills.values():
            status = skill.status.value
            if status not in by_status:
                by_status[status] = []
            by_status[status].append(skill.name)

        return {
            "total_skills": len(self._skills),
            "total_intents": len(self._intent_map),
            "by_status": by_status,
            "ready_count": len(by_status.get("ready", [])),
            "error_count": len(by_status.get("error", [])),
        }

    async def cleanup_all(self) -> None:
        """Clean up all skills."""
        for name, skill in self._skills.items():
            try:
                await skill.cleanup()
            except Exception as e:
                log.error("skill_cleanup_failed", skill=name, error=str(e))

        log.info("all_skills_cleaned_up")
