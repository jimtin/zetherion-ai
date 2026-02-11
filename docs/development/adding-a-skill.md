# Adding a Skill

This tutorial walks through creating a custom skill for Zetherion AI, from planning through testing. By the end, you will have a fully functional skill that handles multiple intents, integrates with the skills framework, and includes comprehensive tests.

For the skills framework internals, see [`../technical/skills-framework.md`](../technical/skills-framework.md). For the development environment setup, see [`setup.md`](setup.md).

---

## Overview

A **skill** is a modular capability that the bot can invoke to perform a specific category of tasks. Skills are routed to by intent: when the message router classifies a user message as matching one of a skill's declared intents, the framework dispatches the request to that skill.

Each skill:

- Declares its **metadata** (name, version, intents, permissions, Qdrant collections)
- Implements an **initialize** method for setup (creating collections, validating config)
- Implements a **handle** method that receives a `SkillRequest` and returns a `SkillResponse`
- Optionally implements **heartbeat** behavior for proactive actions (reminders, digests)
- Optionally contributes a **system prompt fragment** to inject context into the agent's prompt

Skills run inside the `zetherion-ai-skills` Docker service and communicate with the bot container over a REST API.

---

## Prerequisites

- Working development environment (see [`setup.md`](setup.md))
- Familiarity with async Python (`async`/`await`, `asyncio`)
- Understanding of the skills framework classes (covered below)

---

## Key Classes

Before writing code, familiarize yourself with the framework types defined in `src/zetherion_ai/skills/base.py` and `src/zetherion_ai/skills/permissions.py`.

### Skill (abstract base class)

```python
class Skill(ABC):
    def __init__(self, memory: QdrantMemory | None = None): ...

    @property
    @abstractmethod
    def metadata(self) -> SkillMetadata: ...

    @abstractmethod
    async def initialize(self) -> bool: ...

    @abstractmethod
    async def handle(self, request: SkillRequest) -> SkillResponse: ...

    # Optional overrides
    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]: ...
    def get_system_prompt_fragment(self, user_id: str) -> str | None: ...
    async def cleanup(self) -> None: ...
```

### SkillRequest

```python
@dataclass
class SkillRequest:
    id: UUID                          # Unique request identifier
    user_id: str                      # Discord user ID
    intent: str                       # Classified intent (e.g., "weather_current")
    message: str                      # Original user message
    context: dict[str, Any]           # Additional context from the router
    timestamp: datetime               # When the request was created
```

### SkillResponse

```python
@dataclass
class SkillResponse:
    request_id: UUID                  # Matches the SkillRequest.id
    success: bool                     # Whether the operation succeeded
    message: str                      # Human-readable response for the user
    data: dict[str, Any]              # Structured data (for programmatic use)
    error: str | None                 # Error message if success is False
    actions: list[dict[str, Any]]     # Follow-up actions to take
```

Use `SkillResponse.error_response(request_id, error_message)` as a shortcut for error cases.

### SkillMetadata

```python
@dataclass
class SkillMetadata:
    name: str                         # Unique skill identifier (snake_case)
    description: str                  # Human-readable description
    version: str                      # Semantic version (e.g., "1.0.0")
    author: str                       # Defaults to "Zetherion AI"
    permissions: PermissionSet        # Required permissions
    collections: list[str]            # Qdrant collections this skill uses
    intents: list[str]                # Intents this skill handles
```

### HeartbeatAction

```python
@dataclass
class HeartbeatAction:
    skill_name: str                   # Which skill generated the action
    action_type: str                  # "send_message", "update_memory", etc.
    user_id: str                      # Target user
    data: dict[str, Any]              # Payload for the action
    priority: int                     # Higher values = more important
```

### Permission

The `Permission` enum in `zetherion_ai.skills.permissions` defines what resources a skill can access:

| Permission | Description |
|------------|-------------|
| `READ_PROFILE` | Read user profile entries |
| `WRITE_PROFILE` | Create or update profile entries |
| `DELETE_PROFILE` | Delete profile entries |
| `READ_MEMORIES` | Read from conversation memory |
| `WRITE_MEMORIES` | Store new memories |
| `DELETE_MEMORIES` | Delete memories |
| `SEND_MESSAGES` | Send proactive messages |
| `SEND_DM` | Send direct messages |
| `SCHEDULE_TASKS` | Schedule future actions |
| `READ_SCHEDULE` | Read scheduled events |
| `READ_OWN_COLLECTION` | Read from the skill's Qdrant collection |
| `WRITE_OWN_COLLECTION` | Write to the skill's Qdrant collection |
| `INVOKE_OTHER_SKILLS` | Call other skills |
| `READ_CONFIG` | Read configuration values |
| `ADMIN` | Full administrative access (rarely granted) |

Pre-built permission sets are available: `READONLY_PERMISSIONS`, `STANDARD_PERMISSIONS`, `PROACTIVE_PERMISSIONS`.

---

## Step 1: Plan Your Skill

Before writing code, decide on these three things.

### Choose Intents

Intents follow the `verb_noun` naming convention. Each intent maps to one handler method. Pick names that are specific enough to avoid collisions with other skills.

For a weather skill, good intents would be:

- `weather_current` -- get current weather for a location
- `weather_forecast` -- get a multi-day forecast

Avoid generic names like `get_data` or `check_status` that could conflict.

### Define Permissions

Choose the minimum set of permissions your skill needs. For a weather skill that stores cached results in its own Qdrant collection and can send proactive weather alerts:

- `READ_OWN_COLLECTION` -- read cached weather data
- `WRITE_OWN_COLLECTION` -- write cached weather data
- `SEND_MESSAGES` -- send proactive weather alerts

### Plan Data Storage

If your skill stores data in Qdrant, decide on a collection name. Use the `skill_` prefix convention:

- `skill_weather` -- weather cache collection

---

## Step 2: Create the Skill Module

Create the directory structure:

```bash
mkdir -p src/zetherion_ai/skills/weather
touch src/zetherion_ai/skills/weather/__init__.py
touch src/zetherion_ai/skills/weather/skill.py
```

Add the package export in `__init__.py`:

```python
"""Weather skill for Zetherion AI."""

from zetherion_ai.skills.weather.skill import WeatherSkill

__all__ = ["WeatherSkill"]
```

---

## Step 3: Implement the Skill Class

This is the full annotated implementation of a weather skill with two intents.

```python
"""Weather Skill for Zetherion AI.

Provides weather information capabilities:
- Current weather conditions for a location
- Multi-day weather forecasts
- Proactive severe weather alerts via heartbeat
"""

from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import (
    HeartbeatAction,
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
)
from zetherion_ai.skills.permissions import Permission, PermissionSet

if TYPE_CHECKING:
    from zetherion_ai.memory.qdrant import QdrantMemory

log = get_logger("zetherion_ai.skills.weather")

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

WEATHER_COLLECTION = "skill_weather"

# Intent constants -- define these to avoid typos and enable reuse
INTENT_WEATHER_CURRENT = "weather_current"
INTENT_WEATHER_FORECAST = "weather_forecast"


class WeatherSkill(Skill):
    """Skill for providing weather information.

    Intents handled:
    - weather_current: Get current weather conditions
    - weather_forecast: Get a multi-day forecast

    Heartbeat actions:
    - severe_weather_alert: Warn about severe weather conditions
    """

    INTENTS = [INTENT_WEATHER_CURRENT, INTENT_WEATHER_FORECAST]

    def __init__(self, memory: "QdrantMemory | None" = None) -> None:
        """Initialize the weather skill.

        Args:
            memory: Optional Qdrant memory for caching weather data.
        """
        super().__init__(memory=memory)
        # Internal cache: user_id -> last known location
        self._user_locations: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def metadata(self) -> SkillMetadata:
        """Return skill metadata."""
        return SkillMetadata(
            name="weather",
            description="Provide current weather and forecasts for any location",
            version="1.0.0",
            author="Zetherion AI",
            permissions=PermissionSet(
                {
                    Permission.READ_OWN_COLLECTION,
                    Permission.WRITE_OWN_COLLECTION,
                    Permission.SEND_MESSAGES,
                }
            ),
            collections=[WEATHER_COLLECTION],
            intents=self.INTENTS,
        )

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    async def initialize(self) -> bool:
        """Initialize the skill and create the Qdrant collection if needed.

        Returns:
            True if initialization succeeded.
        """
        if not self._memory:
            log.warning("weather_no_memory", msg="No memory provided, using in-memory only")
            return True

        try:
            await self._memory.ensure_collection(
                WEATHER_COLLECTION,
                vector_size=768,  # Gemini embedding dimensions
            )
            log.info("weather_initialized", collection=WEATHER_COLLECTION)
            return True
        except Exception as e:
            log.error("weather_init_failed", error=str(e))
            return False

    # ------------------------------------------------------------------
    # Request handling
    # ------------------------------------------------------------------

    async def handle(self, request: SkillRequest) -> SkillResponse:
        """Route the request to the appropriate handler.

        Args:
            request: The incoming skill request.

        Returns:
            Response from the matched handler.
        """
        match request.intent:
            case "weather_current":
                return await self._handle_current(request)
            case "weather_forecast":
                return await self._handle_forecast(request)
            case _:
                return SkillResponse.error_response(
                    request.id,
                    f"Unknown intent: {request.intent}",
                )

    async def _handle_current(self, request: SkillRequest) -> SkillResponse:
        """Handle a current weather request.

        Args:
            request: The incoming skill request.
                Expected context keys:
                    - location (str): City name or coordinates.

        Returns:
            Response with current weather data.
        """
        location = request.context.get("location", "")
        if not location:
            return SkillResponse.error_response(
                request.id,
                "No location provided. Please specify a city or address.",
            )

        # Remember the user's location for future requests
        self._user_locations[request.user_id] = location

        # In a real implementation, call a weather API here.
        # For this example, return placeholder data.
        weather_data = {
            "location": location,
            "temperature_c": 22,
            "condition": "Partly cloudy",
            "humidity": 65,
            "wind_kph": 12,
        }

        log.info(
            "weather_current_fetched",
            user_id=request.user_id,
            location=location,
        )

        return SkillResponse(
            request_id=request.id,
            message=f"Current weather in {location}: 22C, partly cloudy, 65% humidity.",
            data={"weather": weather_data},
        )

    async def _handle_forecast(self, request: SkillRequest) -> SkillResponse:
        """Handle a weather forecast request.

        Args:
            request: The incoming skill request.
                Expected context keys:
                    - location (str): City name or coordinates.
                    - days (int): Number of forecast days (default 3).

        Returns:
            Response with forecast data.
        """
        location = request.context.get("location", "")
        days = request.context.get("days", 3)

        if not location:
            # Fall back to the user's last known location
            location = self._user_locations.get(request.user_id, "")

        if not location:
            return SkillResponse.error_response(
                request.id,
                "No location provided and no previous location on record.",
            )

        # In a real implementation, call a weather API here.
        forecast = [
            {"day": i + 1, "high_c": 22 + i, "low_c": 14 + i, "condition": "Sunny"}
            for i in range(days)
        ]

        log.info(
            "weather_forecast_fetched",
            user_id=request.user_id,
            location=location,
            days=days,
        )

        return SkillResponse(
            request_id=request.id,
            message=f"{days}-day forecast for {location} retrieved.",
            data={"forecast": forecast, "location": location, "days": days},
        )

    # ------------------------------------------------------------------
    # Heartbeat (optional)
    # ------------------------------------------------------------------

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Check for severe weather in users' locations.

        Called periodically by the heartbeat scheduler. Returns actions
        for any users in locations with severe weather.

        Args:
            user_ids: List of user IDs to check.

        Returns:
            List of heartbeat actions for severe weather alerts.
        """
        actions: list[HeartbeatAction] = []

        for user_id in user_ids:
            location = self._user_locations.get(user_id)
            if not location:
                continue

            # In a real implementation, check a weather API for alerts.
            # This example shows the structure only.
            has_severe_weather = False  # Replace with actual API check

            if has_severe_weather:
                actions.append(
                    HeartbeatAction(
                        skill_name=self.name,
                        action_type="severe_weather_alert",
                        user_id=user_id,
                        data={
                            "location": location,
                            "alert_type": "thunderstorm",
                            "message": f"Severe thunderstorm warning for {location}.",
                        },
                        priority=9,
                    )
                )

        return actions

    # ------------------------------------------------------------------
    # System prompt fragment (optional)
    # ------------------------------------------------------------------

    def get_system_prompt_fragment(self, user_id: str) -> str | None:
        """Inject the user's known location into the agent prompt.

        Args:
            user_id: The user ID for personalization.

        Returns:
            A sentence about the user's location, or None.
        """
        location = self._user_locations.get(user_id)
        if location:
            return f"The user's last known location is {location}."
        return None

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup(self) -> None:
        """Release resources when the skill is shut down."""
        self._user_locations.clear()
        log.info("weather_cleanup_complete")
```

---

## Step 4: Register the Skill

Skills are registered in the `main()` function of `src/zetherion_ai/skills/server.py`. This is the entry point for the `zetherion-ai-skills` Docker service.

Add your skill's import and registration call:

```python
# In src/zetherion_ai/skills/server.py, inside the main() function:

from zetherion_ai.skills.calendar import CalendarSkill
from zetherion_ai.skills.profile_skill import ProfileSkill
from zetherion_ai.skills.task_manager import TaskManagerSkill
from zetherion_ai.skills.weather import WeatherSkill  # <-- Add import

registry.register(TaskManagerSkill())
registry.register(CalendarSkill())
registry.register(ProfileSkill())
registry.register(WeatherSkill())  # <-- Add registration
```

The `SkillRegistry` will:

1. Validate that the skill's permissions do not exceed the maximum allowed set
2. Map each of the skill's declared intents to the skill name
3. Call `safe_initialize()` during the startup sequence

---

## Step 5: Add Heartbeat Support (Optional)

If your skill needs to perform proactive actions (reminders, alerts, periodic checks), override `on_heartbeat()`. The heartbeat scheduler calls this method periodically for all registered skills that have the `SEND_MESSAGES` permission.

The example in Step 3 already demonstrates this pattern. Key points:

- Return an empty list if there is nothing to do
- Set `priority` appropriately: 9 for critical alerts, 3 for low-priority background checks
- The `action_type` string is freeform but should be descriptive (e.g., `"severe_weather_alert"`, `"deadline_reminder"`, `"stale_task_check"`)
- The `data` dictionary carries whatever payload the notification dispatcher needs

The heartbeat scheduler collects actions from all skills, sorts them by priority (highest first), and dispatches them through the notification system.

---

## Step 6: Write Tests

Create the test file at `tests/unit/test_weather_skill.py`. Follow the project's existing test patterns: class-based grouping, `pytest.mark.asyncio` for async tests, and the Arrange-Act-Assert structure.

```python
"""Tests for the Weather Skill."""

from uuid import uuid4

import pytest

from zetherion_ai.skills.base import SkillRequest, SkillResponse, SkillStatus
from zetherion_ai.skills.permissions import Permission
from zetherion_ai.skills.weather import WeatherSkill


class TestWeatherSkillMetadata:
    """Tests for WeatherSkill metadata."""

    def test_metadata_name(self) -> None:
        """Skill name should be 'weather'."""
        skill = WeatherSkill()
        assert skill.metadata.name == "weather"

    def test_metadata_version(self) -> None:
        """Skill version should be '1.0.0'."""
        skill = WeatherSkill()
        assert skill.metadata.version == "1.0.0"

    def test_metadata_intents(self) -> None:
        """Skill should declare two intents."""
        skill = WeatherSkill()
        assert "weather_current" in skill.metadata.intents
        assert "weather_forecast" in skill.metadata.intents
        assert len(skill.metadata.intents) == 2

    def test_metadata_permissions(self) -> None:
        """Skill should request expected permissions."""
        skill = WeatherSkill()
        perms = skill.metadata.permissions
        assert Permission.READ_OWN_COLLECTION in perms
        assert Permission.WRITE_OWN_COLLECTION in perms
        assert Permission.SEND_MESSAGES in perms

    def test_metadata_collections(self) -> None:
        """Skill should declare its Qdrant collection."""
        skill = WeatherSkill()
        assert "skill_weather" in skill.metadata.collections


class TestWeatherSkillInitialization:
    """Tests for WeatherSkill initialization."""

    @pytest.mark.asyncio
    async def test_initialize_without_memory(self) -> None:
        """Skill should initialize successfully without Qdrant memory."""
        skill = WeatherSkill()
        result = await skill.initialize()
        assert result is True

    @pytest.mark.asyncio
    async def test_safe_initialize_sets_ready(self) -> None:
        """safe_initialize should set status to READY on success."""
        skill = WeatherSkill()
        await skill.safe_initialize()
        assert skill.status == SkillStatus.READY


class TestWeatherCurrent:
    """Tests for the weather_current intent handler."""

    @pytest.mark.asyncio
    async def test_current_weather_success(self) -> None:
        """Should return weather data for a valid location."""
        skill = WeatherSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="weather_current",
            message="What is the weather in London?",
            context={"location": "London"},
        )

        response = await skill.safe_handle(request)

        assert response.success is True
        assert "London" in response.message
        assert "weather" in response.data
        assert response.data["weather"]["location"] == "London"

    @pytest.mark.asyncio
    async def test_current_weather_no_location(self) -> None:
        """Should return an error when no location is provided."""
        skill = WeatherSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="weather_current",
            message="What is the weather?",
            context={},
        )

        response = await skill.safe_handle(request)

        assert response.success is False
        assert "No location" in (response.error or "")

    @pytest.mark.asyncio
    async def test_current_weather_remembers_location(self) -> None:
        """Should remember the user's location for future requests."""
        skill = WeatherSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="weather_current",
            message="Weather in Tokyo",
            context={"location": "Tokyo"},
        )

        await skill.safe_handle(request)

        # The skill should now know this user's location
        assert skill._user_locations["user123"] == "Tokyo"


class TestWeatherForecast:
    """Tests for the weather_forecast intent handler."""

    @pytest.mark.asyncio
    async def test_forecast_success(self) -> None:
        """Should return forecast data for a valid location."""
        skill = WeatherSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="weather_forecast",
            message="5-day forecast for Paris",
            context={"location": "Paris", "days": 5},
        )

        response = await skill.safe_handle(request)

        assert response.success is True
        assert len(response.data["forecast"]) == 5
        assert response.data["location"] == "Paris"

    @pytest.mark.asyncio
    async def test_forecast_uses_remembered_location(self) -> None:
        """Should fall back to the user's last known location."""
        skill = WeatherSkill()
        await skill.safe_initialize()

        # Set a known location
        skill._user_locations["user123"] = "Berlin"

        request = SkillRequest(
            user_id="user123",
            intent="weather_forecast",
            message="Give me the forecast",
            context={},
        )

        response = await skill.safe_handle(request)

        assert response.success is True
        assert response.data["location"] == "Berlin"

    @pytest.mark.asyncio
    async def test_forecast_no_location_at_all(self) -> None:
        """Should return an error when no location is available."""
        skill = WeatherSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user999",
            intent="weather_forecast",
            message="Give me the forecast",
            context={},
        )

        response = await skill.safe_handle(request)

        assert response.success is False
        assert "No location" in (response.error or "")


class TestWeatherUnknownIntent:
    """Tests for unknown intent handling."""

    @pytest.mark.asyncio
    async def test_unknown_intent(self) -> None:
        """Should return an error for an unrecognized intent."""
        skill = WeatherSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="weather_historical",
            message="What was the weather last week?",
            context={},
        )

        response = await skill.safe_handle(request)

        assert response.success is False
        assert "Unknown intent" in (response.error or "")


class TestWeatherHeartbeat:
    """Tests for heartbeat behavior."""

    @pytest.mark.asyncio
    async def test_heartbeat_no_users(self) -> None:
        """Should return empty list when no users have locations."""
        skill = WeatherSkill()
        await skill.safe_initialize()

        actions = await skill.on_heartbeat(["user123"])
        assert actions == []

    @pytest.mark.asyncio
    async def test_heartbeat_with_known_location(self) -> None:
        """Should check weather for users with known locations."""
        skill = WeatherSkill()
        await skill.safe_initialize()
        skill._user_locations["user123"] = "London"

        # In the stub implementation, no severe weather is reported,
        # so the result is still empty. In a real test, you would
        # mock the weather API to return a severe weather response.
        actions = await skill.on_heartbeat(["user123"])
        assert isinstance(actions, list)


class TestWeatherSystemPrompt:
    """Tests for system prompt fragment generation."""

    def test_prompt_fragment_with_location(self) -> None:
        """Should return location info when known."""
        skill = WeatherSkill()
        skill._user_locations["user123"] = "Sydney"

        fragment = skill.get_system_prompt_fragment("user123")
        assert fragment is not None
        assert "Sydney" in fragment

    def test_prompt_fragment_without_location(self) -> None:
        """Should return None when location is unknown."""
        skill = WeatherSkill()

        fragment = skill.get_system_prompt_fragment("user999")
        assert fragment is None


class TestWeatherCleanup:
    """Tests for skill cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_clears_locations(self) -> None:
        """Cleanup should clear the location cache."""
        skill = WeatherSkill()
        skill._user_locations["user123"] = "London"

        await skill.cleanup()

        assert len(skill._user_locations) == 0
```

Run the tests:

```bash
pytest tests/unit/test_weather_skill.py -v
```

---

## Step 7: Update Router Prompts

For the router to classify user messages as your skill's intents, you need to add examples to the router's classification prompts.

The router prompt is built in `src/zetherion_ai/agent/prompts.py`. Add your intents to the intent classification examples:

```python
# In the router classification prompt, add examples like:

# Weather intents
# "What's the weather in London?" -> weather_current
# "Give me a 5-day forecast for Tokyo" -> weather_forecast
# "Will it rain tomorrow in Berlin?" -> weather_forecast
# "Current temperature in New York" -> weather_current
```

The exact location and format depends on how the router prompt is structured. The key requirement is that the router model (`gemini-2.5-flash` or `llama3.2:3b`) sees enough examples to reliably classify weather-related messages to your intents.

---

## Checklist

Before opening a pull request for a new skill, verify that all of the following are complete:

- [ ] Skill class extends `Skill` and implements all three required methods (`metadata`, `initialize`, `handle`)
- [ ] Intents follow the `verb_noun` naming convention
- [ ] Permissions are the minimum required set
- [ ] Qdrant collection name uses the `skill_` prefix
- [ ] Skill is registered in `src/zetherion_ai/skills/server.py`
- [ ] Router prompts updated with intent classification examples
- [ ] `__init__.py` created with proper exports
- [ ] Tests cover all intents (success and error cases)
- [ ] Tests cover initialization (with and without memory)
- [ ] Tests cover heartbeat behavior (if implemented)
- [ ] Tests cover system prompt fragments (if implemented)
- [ ] Tests cover cleanup
- [ ] All tests pass: `pytest tests/ -m "not discord_e2e"`
- [ ] Type checking passes: `mypy src/zetherion_ai`
- [ ] Pre-commit hooks pass: `pre-commit run --all-files`
- [ ] Conventional commit message used (e.g., `feat: add weather skill`)

---

## Further Reading

- [`../technical/skills-framework.md`](../technical/skills-framework.md) -- Skills framework architecture and internals
- [`../technical/architecture.md`](../technical/architecture.md) -- Overall system architecture
- [Testing](testing.md) -- Testing patterns and strategies
- [`setup.md`](setup.md) -- Development environment setup and contributing guidelines
