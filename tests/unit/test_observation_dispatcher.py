"""Unit tests for the observation dispatcher module."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from zetherion_ai.observation.dispatcher import (
    DEFAULT_ROUTES,
    MIN_DISPATCH_CONFIDENCE,
    ActionHandler,
    ActionTarget,
    DefaultMode,
    Dispatcher,
    DispatchResult,
    PolicyProvider,
)
from zetherion_ai.observation.models import (
    ExtractedItem,
    ItemType,
    ObservationEvent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(content: str = "Test content") -> ObservationEvent:
    """Create a minimal ObservationEvent for testing."""
    return ObservationEvent(
        source="test",
        source_id="msg-1",
        user_id=12345,
        author="test",
        author_is_owner=True,
        content=content,
    )


def _make_item(
    item_type: ItemType = ItemType.TASK,
    confidence: float = 0.8,
    content: str = "Do something",
) -> ExtractedItem:
    """Create a minimal ExtractedItem for testing."""
    return ExtractedItem(
        item_type=item_type,
        content=content,
        confidence=confidence,
        source_event=_make_event(),
    )


def _mock_handler(*, success: bool = True, message: str = "OK") -> AsyncMock:
    """Return an AsyncMock that satisfies ActionHandler."""
    handler = AsyncMock(spec=ActionHandler)

    async def _handle(item: ExtractedItem, *, user_id: int, mode: str) -> DispatchResult:
        return DispatchResult(
            item=item,
            target=ActionTarget.TASK_MANAGER,
            mode=mode,
            success=success,
            message=message,
        )

    handler.handle_dispatch = AsyncMock(side_effect=_handle)
    return handler


def _mock_policy(
    return_value: str | None = None,
) -> AsyncMock:
    """Return an AsyncMock that satisfies PolicyProvider."""
    policy = AsyncMock(spec=PolicyProvider)
    policy.get_mode = AsyncMock(return_value=return_value)
    return policy


# ---------------------------------------------------------------------------
# DEFAULT_ROUTES tests
# ---------------------------------------------------------------------------


class TestDefaultRoutes:
    """Tests for the DEFAULT_ROUTES routing table."""

    def test_all_item_types_have_route(self):
        """Every ItemType member must appear in DEFAULT_ROUTES."""
        for item_type in ItemType:
            assert item_type in DEFAULT_ROUTES, (
                f"ItemType.{item_type.name} missing from DEFAULT_ROUTES"
            )

    def test_routes_only_contain_valid_item_types(self):
        """DEFAULT_ROUTES keys must all be valid ItemType members."""
        for key in DEFAULT_ROUTES:
            assert isinstance(key, ItemType)

    def test_routes_values_are_target_mode_tuples(self):
        """Each route value must be a (ActionTarget, DefaultMode) pair."""
        for item_type, route in DEFAULT_ROUTES.items():
            target, mode = route
            assert isinstance(target, ActionTarget), (
                f"Route for {item_type}: target is not ActionTarget"
            )
            assert isinstance(mode, DefaultMode), f"Route for {item_type}: mode is not DefaultMode"

    def test_task_route(self):
        """TASK routes to TASK_MANAGER with DRAFT mode."""
        target, mode = DEFAULT_ROUTES[ItemType.TASK]
        assert target == ActionTarget.TASK_MANAGER
        assert mode == DefaultMode.DRAFT

    def test_deadline_route(self):
        """DEADLINE routes to CALENDAR with DRAFT mode."""
        target, mode = DEFAULT_ROUTES[ItemType.DEADLINE]
        assert target == ActionTarget.CALENDAR
        assert mode == DefaultMode.DRAFT

    def test_commitment_route(self):
        """COMMITMENT routes to TASK_MANAGER with DRAFT mode."""
        target, mode = DEFAULT_ROUTES[ItemType.COMMITMENT]
        assert target == ActionTarget.TASK_MANAGER
        assert mode == DefaultMode.DRAFT

    def test_contact_route(self):
        """CONTACT routes to CONTACT_GRAPH with AUTO mode."""
        target, mode = DEFAULT_ROUTES[ItemType.CONTACT]
        assert target == ActionTarget.CONTACT_GRAPH
        assert mode == DefaultMode.AUTO

    def test_fact_route(self):
        """FACT routes to PERSONAL_MODEL with AUTO mode."""
        target, mode = DEFAULT_ROUTES[ItemType.FACT]
        assert target == ActionTarget.PERSONAL_MODEL
        assert mode == DefaultMode.AUTO

    def test_meeting_route(self):
        """MEETING routes to CALENDAR with ASK mode."""
        target, mode = DEFAULT_ROUTES[ItemType.MEETING]
        assert target == ActionTarget.CALENDAR
        assert mode == DefaultMode.ASK

    def test_reminder_route(self):
        """REMINDER routes to CALENDAR with DRAFT mode."""
        target, mode = DEFAULT_ROUTES[ItemType.REMINDER]
        assert target == ActionTarget.CALENDAR
        assert mode == DefaultMode.DRAFT

    def test_action_item_route(self):
        """ACTION_ITEM routes to TASK_MANAGER with DRAFT mode."""
        target, mode = DEFAULT_ROUTES[ItemType.ACTION_ITEM]
        assert target == ActionTarget.TASK_MANAGER
        assert mode == DefaultMode.DRAFT

    def test_route_count_matches_item_type_count(self):
        """Number of routes must match number of ItemType members."""
        assert len(DEFAULT_ROUTES) == len(ItemType)


# ---------------------------------------------------------------------------
# ActionTarget and DefaultMode enum tests
# ---------------------------------------------------------------------------


class TestEnums:
    """Tests for ActionTarget and DefaultMode enums."""

    def test_action_target_values(self):
        """Verify ActionTarget string values."""
        assert ActionTarget.TASK_MANAGER == "task_manager"
        assert ActionTarget.CALENDAR == "calendar"
        assert ActionTarget.PERSONAL_MODEL == "personal_model"
        assert ActionTarget.CONTACT_GRAPH == "contact_graph"
        assert ActionTarget.MEMORY == "memory"
        assert ActionTarget.NOTIFICATION == "notification"
        assert ActionTarget.DEV_JOURNAL == "dev_journal"
        assert ActionTarget.MILESTONE_TRACKER == "milestone_tracker"

    def test_default_mode_values(self):
        """Verify DefaultMode string values."""
        assert DefaultMode.AUTO == "auto"
        assert DefaultMode.DRAFT == "draft"
        assert DefaultMode.ASK == "ask"
        assert DefaultMode.NEVER == "never"


# ---------------------------------------------------------------------------
# DispatchResult tests
# ---------------------------------------------------------------------------


class TestDispatchResult:
    """Tests for the DispatchResult dataclass."""

    def test_creation_with_required_fields(self):
        """DispatchResult can be created with required fields only."""
        item = _make_item()
        result = DispatchResult(
            item=item,
            target=ActionTarget.TASK_MANAGER,
            mode="draft",
            success=True,
        )
        assert result.item is item
        assert result.target == ActionTarget.TASK_MANAGER
        assert result.mode == "draft"
        assert result.success is True
        assert result.message == ""
        assert result.data == {}

    def test_creation_with_all_fields(self):
        """DispatchResult can be created with all optional fields."""
        item = _make_item()
        result = DispatchResult(
            item=item,
            target=ActionTarget.CALENDAR,
            mode="auto",
            success=False,
            message="Something went wrong",
            data={"retry": True},
        )
        assert result.message == "Something went wrong"
        assert result.data == {"retry": True}
        assert result.success is False


# ---------------------------------------------------------------------------
# Dispatcher constructor tests
# ---------------------------------------------------------------------------


class TestDispatcherConstructor:
    """Tests for Dispatcher.__init__."""

    def test_default_constructor(self):
        """Dispatcher can be created with no arguments."""
        dispatcher = Dispatcher()
        assert dispatcher._handlers == {}
        assert dispatcher._policy_provider is None

    def test_constructor_with_handlers(self):
        """Dispatcher accepts a handlers dict."""
        handler = _mock_handler()
        handlers = {ActionTarget.TASK_MANAGER: handler}
        dispatcher = Dispatcher(handlers=handlers)
        assert dispatcher._handlers == handlers
        assert dispatcher._policy_provider is None

    def test_constructor_with_policy_provider(self):
        """Dispatcher accepts a policy provider."""
        policy = _mock_policy()
        dispatcher = Dispatcher(policy_provider=policy)
        assert dispatcher._policy_provider is policy
        assert dispatcher._handlers == {}

    def test_constructor_with_handlers_and_policy(self):
        """Dispatcher accepts both handlers and policy provider."""
        handler = _mock_handler()
        policy = _mock_policy()
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
            policy_provider=policy,
        )
        assert ActionTarget.TASK_MANAGER in dispatcher._handlers
        assert dispatcher._policy_provider is policy


# ---------------------------------------------------------------------------
# Dispatcher.dispatch() tests
# ---------------------------------------------------------------------------


class TestDispatchMethod:
    """Tests for Dispatcher.dispatch()."""

    @pytest.mark.asyncio
    async def test_empty_items_returns_empty_list(self):
        """dispatch() with empty list returns empty results."""
        dispatcher = Dispatcher()
        results = await dispatcher.dispatch([], user_id=12345)
        assert results == []

    @pytest.mark.asyncio
    async def test_single_item_with_handler(self):
        """dispatch() with one item and a matching handler succeeds."""
        handler = _mock_handler(success=True, message="Created")
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        item = _make_item(item_type=ItemType.TASK)
        results = await dispatcher.dispatch([item], user_id=12345)

        assert len(results) == 1
        assert results[0].success is True
        handler.handle_dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multiple_items_all_dispatched(self):
        """dispatch() processes all items and returns matching count."""
        tm_handler = _mock_handler()
        cal_handler = _mock_handler()
        dispatcher = Dispatcher(
            handlers={
                ActionTarget.TASK_MANAGER: tm_handler,
                ActionTarget.CALENDAR: cal_handler,
            },
        )
        items = [
            _make_item(item_type=ItemType.TASK),
            _make_item(item_type=ItemType.MEETING),
            _make_item(item_type=ItemType.DEADLINE),
        ]
        results = await dispatcher.dispatch(items, user_id=12345)

        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_dispatch_logs_counts(self):
        """dispatch() logs dispatched and skipped counts."""
        handler = _mock_handler()
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        items = [
            _make_item(item_type=ItemType.TASK, confidence=0.9),
            _make_item(item_type=ItemType.TASK, confidence=0.1),
        ]

        with patch("zetherion_ai.observation.dispatcher.log") as mock_log:
            await dispatcher.dispatch(items, user_id=12345)
            mock_log.info.assert_any_call(
                "dispatch_complete",
                user_id=12345,
                total=2,
                dispatched=1,
                skipped=1,
            )

    @pytest.mark.asyncio
    async def test_dispatch_returns_results_in_order(self):
        """Results are returned in the same order as input items."""
        tm_handler = _mock_handler()
        cal_handler = _mock_handler()
        dispatcher = Dispatcher(
            handlers={
                ActionTarget.TASK_MANAGER: tm_handler,
                ActionTarget.CALENDAR: cal_handler,
            },
        )
        task_item = _make_item(item_type=ItemType.TASK, content="Task 1")
        meeting_item = _make_item(item_type=ItemType.MEETING, content="Meeting 1")
        results = await dispatcher.dispatch([task_item, meeting_item], user_id=12345)
        assert results[0].item is task_item
        assert results[1].item is meeting_item


# ---------------------------------------------------------------------------
# _dispatch_single() behaviour (tested through dispatch())
# ---------------------------------------------------------------------------


class TestDispatchSingle:
    """Tests for _dispatch_single() logic via dispatch()."""

    @pytest.mark.asyncio
    async def test_known_route_and_handler_succeeds(self):
        """Item with known route + registered handler dispatches OK."""
        handler = _mock_handler(success=True, message="Done")
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        item = _make_item(item_type=ItemType.TASK, confidence=0.9)
        results = await dispatcher.dispatch([item], user_id=1)

        assert len(results) == 1
        assert results[0].success is True
        handler.handle_dispatch.assert_awaited_once_with(
            item,
            user_id=1,
            mode="draft",
        )

    @pytest.mark.asyncio
    async def test_below_min_confidence_skipped(self):
        """Item below MIN_DISPATCH_CONFIDENCE is skipped."""
        handler = _mock_handler()
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        item = _make_item(confidence=0.2)
        results = await dispatcher.dispatch([item], user_id=1)

        assert len(results) == 1
        assert results[0].success is False
        assert "Confidence" in results[0].message
        assert "0.20" in results[0].message
        assert str(MIN_DISPATCH_CONFIDENCE) in results[0].message
        handler.handle_dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exactly_at_min_confidence_dispatched(self):
        """Item at exactly MIN_DISPATCH_CONFIDENCE passes."""
        handler = _mock_handler()
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        item = _make_item(confidence=MIN_DISPATCH_CONFIDENCE)
        results = await dispatcher.dispatch([item], user_id=1)

        assert results[0].success is True
        handler.handle_dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_just_below_min_confidence_skipped(self):
        """Item just below MIN_DISPATCH_CONFIDENCE is skipped."""
        handler = _mock_handler()
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        item = _make_item(
            confidence=MIN_DISPATCH_CONFIDENCE - 0.01,
        )
        results = await dispatcher.dispatch([item], user_id=1)

        assert results[0].success is False
        handler.handle_dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_policy_mode_never_blocks_dispatch(self):
        """Policy returning 'never' blocks dispatch."""
        handler = _mock_handler()
        policy = _mock_policy(return_value="never")
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
            policy_provider=policy,
        )
        item = _make_item(confidence=0.9)
        results = await dispatcher.dispatch([item], user_id=42)

        assert results[0].success is False
        assert results[0].mode == "never"
        assert "Blocked by user policy" in results[0].message
        handler.handle_dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_handler_registered_for_target(self):
        """Missing handler for the item's target returns failure."""
        dispatcher = Dispatcher(handlers={})
        item = _make_item(
            item_type=ItemType.TASK,
            confidence=0.9,
        )
        results = await dispatcher.dispatch([item], user_id=1)

        assert results[0].success is False
        assert "No handler registered" in results[0].message
        assert "task_manager" in results[0].message

    @pytest.mark.asyncio
    async def test_handler_raises_exception_caught(self):
        """Handler raising an exception is caught gracefully."""
        handler = AsyncMock(spec=ActionHandler)
        handler.handle_dispatch = AsyncMock(
            side_effect=RuntimeError("DB down"),
        )
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        item = _make_item(confidence=0.9)
        results = await dispatcher.dispatch([item], user_id=1)

        assert results[0].success is False
        assert "Handler error" in results[0].message
        assert "DB down" in results[0].message

    @pytest.mark.asyncio
    async def test_handler_raises_value_error_caught(self):
        """Handler raising ValueError is caught gracefully."""
        handler = AsyncMock(spec=ActionHandler)
        handler.handle_dispatch = AsyncMock(
            side_effect=ValueError("bad input"),
        )
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        item = _make_item(confidence=0.9)
        results = await dispatcher.dispatch([item], user_id=1)

        assert results[0].success is False
        assert "bad input" in results[0].message

    @pytest.mark.asyncio
    async def test_default_mode_used_when_no_policy_provider(self):
        """Without a policy provider the DEFAULT_ROUTES mode is used."""
        handler = _mock_handler()
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        item = _make_item(item_type=ItemType.TASK, confidence=0.9)
        await dispatcher.dispatch([item], user_id=1)

        # TASK default mode is 'draft'
        call_kwargs = handler.handle_dispatch.call_args
        assert call_kwargs.kwargs["mode"] == "draft"

    @pytest.mark.asyncio
    async def test_default_mode_used_when_policy_returns_none(self):
        """Policy returning None falls back to the default mode."""
        handler = _mock_handler()
        policy = _mock_policy(return_value=None)
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
            policy_provider=policy,
        )
        item = _make_item(item_type=ItemType.TASK, confidence=0.9)
        await dispatcher.dispatch([item], user_id=1)

        call_kwargs = handler.handle_dispatch.call_args
        assert call_kwargs.kwargs["mode"] == "draft"

    @pytest.mark.asyncio
    async def test_policy_overrides_default_mode(self):
        """Policy returning a mode string overrides the default."""
        handler = _mock_handler()
        policy = _mock_policy(return_value="auto")
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
            policy_provider=policy,
        )
        item = _make_item(item_type=ItemType.TASK, confidence=0.9)
        await dispatcher.dispatch([item], user_id=99)

        call_kwargs = handler.handle_dispatch.call_args
        assert call_kwargs.kwargs["mode"] == "auto"
        # Verify policy was queried with correct arguments
        policy.get_mode.assert_awaited_once_with(
            99,
            "task_manager",
            "task",
        )

    @pytest.mark.asyncio
    async def test_low_confidence_result_target_matches_route(self):
        """Skipped-for-confidence result still reports the route target."""
        dispatcher = Dispatcher()
        item = _make_item(
            item_type=ItemType.MEETING,
            confidence=0.1,
        )
        results = await dispatcher.dispatch([item], user_id=1)

        assert results[0].target == ActionTarget.CALENDAR
        assert results[0].mode == "never"

    @pytest.mark.asyncio
    async def test_dispatch_passes_correct_user_id_to_handler(self):
        """Handler receives the user_id supplied to dispatch()."""
        handler = _mock_handler()
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        item = _make_item(confidence=0.9)
        await dispatcher.dispatch([item], user_id=77777)

        call_kwargs = handler.handle_dispatch.call_args
        assert call_kwargs.kwargs["user_id"] == 77777

    @pytest.mark.asyncio
    async def test_dispatch_item_passed_to_handler(self):
        """Handler receives the exact ExtractedItem object."""
        handler = _mock_handler()
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        item = _make_item(confidence=0.9)
        await dispatcher.dispatch([item], user_id=1)

        call_args = handler.handle_dispatch.call_args
        assert call_args.args[0] is item


# ---------------------------------------------------------------------------
# PolicyProvider interaction tests
# ---------------------------------------------------------------------------


class TestPolicyProvider:
    """Tests for PolicyProvider protocol interactions."""

    @pytest.mark.asyncio
    async def test_policy_returns_auto(self):
        """Policy returning 'auto' sets mode to 'auto'."""
        handler = _mock_handler()
        policy = _mock_policy(return_value="auto")
        dispatcher = Dispatcher(
            handlers={ActionTarget.CALENDAR: handler},
            policy_provider=policy,
        )
        item = _make_item(
            item_type=ItemType.MEETING,
            confidence=0.9,
        )
        await dispatcher.dispatch([item], user_id=1)

        call_kwargs = handler.handle_dispatch.call_args
        assert call_kwargs.kwargs["mode"] == "auto"

    @pytest.mark.asyncio
    async def test_policy_returns_never_blocks(self):
        """Policy returning 'never' prevents handler invocation."""
        handler = _mock_handler()
        policy = _mock_policy(return_value="never")
        dispatcher = Dispatcher(
            handlers={ActionTarget.CALENDAR: handler},
            policy_provider=policy,
        )
        item = _make_item(
            item_type=ItemType.MEETING,
            confidence=0.9,
        )
        results = await dispatcher.dispatch([item], user_id=1)

        assert results[0].success is False
        assert results[0].message == "Blocked by user policy"
        handler.handle_dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_policy_returns_none_uses_default(self):
        """Policy returning None falls through to the route default."""
        handler = _mock_handler()
        policy = _mock_policy(return_value=None)
        dispatcher = Dispatcher(
            handlers={ActionTarget.CALENDAR: handler},
            policy_provider=policy,
        )
        item = _make_item(
            item_type=ItemType.MEETING,
            confidence=0.9,
        )
        await dispatcher.dispatch([item], user_id=1)

        # MEETING default is 'ask'
        call_kwargs = handler.handle_dispatch.call_args
        assert call_kwargs.kwargs["mode"] == "ask"

    @pytest.mark.asyncio
    async def test_policy_returns_draft(self):
        """Policy returning 'draft' overrides default for CONTACT."""
        handler = _mock_handler()
        policy = _mock_policy(return_value="draft")
        dispatcher = Dispatcher(
            handlers={ActionTarget.CONTACT_GRAPH: handler},
            policy_provider=policy,
        )
        # CONTACT default is 'auto', policy overrides to 'draft'
        item = _make_item(
            item_type=ItemType.CONTACT,
            confidence=0.9,
        )
        await dispatcher.dispatch([item], user_id=1)

        call_kwargs = handler.handle_dispatch.call_args
        assert call_kwargs.kwargs["mode"] == "draft"

    @pytest.mark.asyncio
    async def test_policy_called_with_correct_domain_and_action(self):
        """Policy get_mode receives target value and item_type value."""
        handler = _mock_handler()
        policy = _mock_policy(return_value=None)
        dispatcher = Dispatcher(
            handlers={ActionTarget.PERSONAL_MODEL: handler},
            policy_provider=policy,
        )
        item = _make_item(
            item_type=ItemType.FACT,
            confidence=0.9,
        )
        await dispatcher.dispatch([item], user_id=555)

        policy.get_mode.assert_awaited_once_with(
            555,
            "personal_model",
            "fact",
        )

    @pytest.mark.asyncio
    async def test_policy_not_called_when_below_confidence(self):
        """Policy is not queried if item is below confidence."""
        policy = _mock_policy(return_value="auto")
        dispatcher = Dispatcher(policy_provider=policy)
        item = _make_item(confidence=0.1)
        await dispatcher.dispatch([item], user_id=1)

        policy.get_mode.assert_not_awaited()


# ---------------------------------------------------------------------------
# ActionHandler interaction tests
# ---------------------------------------------------------------------------


class TestActionHandler:
    """Tests for ActionHandler protocol interactions."""

    @pytest.mark.asyncio
    async def test_handler_success_propagated(self):
        """Successful handler result is returned as-is."""
        item = _make_item(confidence=0.9)
        expected = DispatchResult(
            item=item,
            target=ActionTarget.TASK_MANAGER,
            mode="draft",
            success=True,
            message="Task created",
            data={"task_id": 42},
        )
        handler = AsyncMock(spec=ActionHandler)
        handler.handle_dispatch = AsyncMock(
            return_value=expected,
        )
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        results = await dispatcher.dispatch([item], user_id=1)

        assert results[0] is expected

    @pytest.mark.asyncio
    async def test_handler_failure_propagated(self):
        """Handler returning success=False is returned as-is."""
        item = _make_item(confidence=0.9)
        expected = DispatchResult(
            item=item,
            target=ActionTarget.TASK_MANAGER,
            mode="draft",
            success=False,
            message="Validation failed",
        )
        handler = AsyncMock(spec=ActionHandler)
        handler.handle_dispatch = AsyncMock(
            return_value=expected,
        )
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        results = await dispatcher.dispatch([item], user_id=1)

        assert results[0] is expected
        assert results[0].success is False

    @pytest.mark.asyncio
    async def test_handler_exception_returns_error_result(self):
        """Handler exception is caught and turned into an error result."""
        handler = AsyncMock(spec=ActionHandler)
        handler.handle_dispatch = AsyncMock(
            side_effect=ConnectionError("timeout"),
        )
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        item = _make_item(confidence=0.9)
        results = await dispatcher.dispatch([item], user_id=1)

        r = results[0]
        assert r.success is False
        assert r.target == ActionTarget.TASK_MANAGER
        assert "Handler error" in r.message
        assert "timeout" in r.message

    @pytest.mark.asyncio
    async def test_handler_exception_logs_error(self):
        """Handler exception is logged with structured context."""
        handler = AsyncMock(spec=ActionHandler)
        handler.handle_dispatch = AsyncMock(
            side_effect=RuntimeError("crash"),
        )
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        item = _make_item(confidence=0.9)

        with patch("zetherion_ai.observation.dispatcher.log") as mock_log:
            await dispatcher.dispatch([item], user_id=1)
            mock_log.error.assert_called_once_with(
                "dispatch_handler_error",
                item_type=ItemType.TASK,
                target=ActionTarget.TASK_MANAGER,
                error="crash",
                user_id=1,
            )


# ---------------------------------------------------------------------------
# MIN_DISPATCH_CONFIDENCE constant
# ---------------------------------------------------------------------------


class TestMinDispatchConfidence:
    """Tests for the MIN_DISPATCH_CONFIDENCE constant."""

    def test_min_dispatch_confidence_value(self):
        """MIN_DISPATCH_CONFIDENCE should be 0.4."""
        assert MIN_DISPATCH_CONFIDENCE == 0.4

    def test_min_dispatch_confidence_is_float(self):
        """MIN_DISPATCH_CONFIDENCE should be a float."""
        assert isinstance(MIN_DISPATCH_CONFIDENCE, float)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge-case and integration-style tests."""

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure_items(self):
        """Mix of passing and failing items returns correct counts."""
        handler = _mock_handler()
        dispatcher = Dispatcher(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        items = [
            _make_item(confidence=0.9),  # passes
            _make_item(confidence=0.1),  # below threshold
            _make_item(confidence=0.5),  # passes
        ]
        results = await dispatcher.dispatch(items, user_id=1)

        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]
        assert len(successes) == 2
        assert len(failures) == 1

    @pytest.mark.asyncio
    async def test_different_handlers_per_target(self):
        """Each target can have its own independent handler."""
        tm_handler = _mock_handler(message="task done")
        cal_handler = _mock_handler(message="cal done")
        dispatcher = Dispatcher(
            handlers={
                ActionTarget.TASK_MANAGER: tm_handler,
                ActionTarget.CALENDAR: cal_handler,
            },
        )
        items = [
            _make_item(item_type=ItemType.TASK, confidence=0.9),
            _make_item(
                item_type=ItemType.MEETING,
                confidence=0.9,
            ),
        ]
        results = await dispatcher.dispatch(items, user_id=1)

        assert len(results) == 2
        tm_handler.handle_dispatch.assert_awaited_once()
        cal_handler.handle_dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_item_types_can_be_dispatched(self):
        """Every ItemType can be dispatched when handlers exist."""
        handlers = {target: _mock_handler() for target in ActionTarget}
        dispatcher = Dispatcher(handlers=handlers)

        items = [_make_item(item_type=it, confidence=0.9) for it in ItemType]
        results = await dispatcher.dispatch(
            items,
            user_id=1,
        )
        assert all(r.success for r in results)
        assert len(results) == len(ItemType)

    @pytest.mark.asyncio
    async def test_handler_called_with_policy_mode_auto(self):
        """Full path: policy=auto, handler invoked with mode='auto'."""
        handler = _mock_handler()
        policy = _mock_policy(return_value="auto")
        dispatcher = Dispatcher(
            handlers={ActionTarget.CALENDAR: handler},
            policy_provider=policy,
        )
        item = _make_item(
            item_type=ItemType.MEETING,
            confidence=0.9,
        )
        results = await dispatcher.dispatch([item], user_id=10)

        assert results[0].success is True
        call_kwargs = handler.handle_dispatch.call_args
        assert call_kwargs.kwargs["mode"] == "auto"
        assert call_kwargs.kwargs["user_id"] == 10
