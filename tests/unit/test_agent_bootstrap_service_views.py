"""Coverage for brokered service views and actions in agent bootstrap."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.skills.agent_bootstrap import AgentBootstrapSkill
from zetherion_ai.skills.stripe.client import StripeAPIError


def _app_profile() -> dict[str, object]:
    return {
        "app_id": "catalyst-group-solutions",
        "profile": {
            "repo_ids": ["catalyst-group-solutions"],
            "github_repos": ["jimtin/catalyst-group-solutions"],
            "service_connector_map": {
                "github": {
                    "connector_id": "github-primary",
                    "read_access": [],
                    "write_access": [],
                },
                "vercel": {
                    "connector_id": "vercel-primary",
                    "read_access": [],
                    "write_access": [],
                },
                "clerk": {
                    "connector_id": "clerk-primary",
                    "read_access": [],
                    "write_access": [],
                },
                "stripe": {
                    "connector_id": "stripe-primary",
                    "read_access": [],
                    "write_access": [],
                },
                "docker": {
                    "connector_id": "docker-primary",
                    "read_access": [],
                    "write_access": [],
                },
            },
        },
    }


def _skill(storage: MagicMock | None = None) -> AgentBootstrapSkill:
    skill = AgentBootstrapSkill(storage=storage or MagicMock())
    skill._record_gap = AsyncMock(return_value={"gap_id": "gap-1"})  # type: ignore[method-assign]
    return skill


@pytest.mark.asyncio
async def test_read_github_service_view_covers_supported_views() -> None:
    skill = _skill()
    skill._require_github_connector = AsyncMock(return_value={"secret_value": "gh-secret"})  # type: ignore[method-assign]

    repository = SimpleNamespace(
        default_branch="main",
        to_dict=lambda: {"name": "catalyst-group-solutions"},
    )
    pull_request = SimpleNamespace(to_dict=lambda: {"number": 7, "title": "Fix DM"})
    workflow_run = SimpleNamespace(to_dict=lambda: {"id": 101, "status": "completed"})

    client = MagicMock()
    client.get_repository = AsyncMock(return_value=repository)
    client.get_branch_protection = AsyncMock(return_value={"required_status_checks": {}})
    client.list_pull_requests = AsyncMock(return_value=[pull_request])
    client.list_workflow_runs = AsyncMock(return_value=[workflow_run])
    client.compare_commits = AsyncMock(return_value={"ahead_by": 1})
    client.close = AsyncMock()

    with patch("zetherion_ai.skills.agent_bootstrap.GitHubClient", return_value=client):
        overview = await skill._read_github_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="github-primary",
            view="overview",
            request_context={"branch": "main"},
        )
        compare = await skill._read_github_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="github-primary",
            view="compare",
            request_context={"base": "main", "head": "feature/test"},
        )
        pulls = await skill._read_github_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="github-primary",
            view="pulls",
            request_context={"state": "open"},
        )
        workflows = await skill._read_github_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="github-primary",
            view="workflows",
            request_context={"status": "completed"},
        )

    assert overview["default_branch"] == "main"
    assert compare["comparison"]["ahead_by"] == 1
    assert pulls["pull_requests"][0]["number"] == 7
    assert workflows["workflow_runs"][0]["id"] == 101
    assert client.close.await_count == 4


@pytest.mark.asyncio
async def test_read_vercel_clerk_and_generic_service_views_cover_supported_paths() -> None:
    skill = _skill()
    skill._require_connector = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"secret_value": "vercel-secret", "metadata": {"team_id": "team-1"}},
            {"secret_value": "vercel-secret", "metadata": {"team_id": "team-1"}},
            {"secret_value": "vercel-secret", "metadata": {"team_id": "team-1"}},
            {"secret_value": "vercel-secret", "metadata": {"team_id": "team-1"}},
            {
                "metadata": {
                    "issuer": "https://clerk.example.com",
                    "frontend_api_url": "https://clerk.example.com",
                    "jwks_url": "https://clerk.example.com/jwks",
                }
            },
            {"metadata": {"issuer": "https://clerk.example.com"}},
            {"metadata": {"jwks_url": "https://clerk.example.com/jwks"}},
            {"connector_id": "docker-primary", "metadata": {"scope": "local"}, "policy": {}},
        ]
    )

    vercel_client = MagicMock()
    vercel_client.get_project = AsyncMock(
        return_value={"id": "proj_1", "name": "cgs", "framework": "nextjs"}
    )
    vercel_client.list_deployments = AsyncMock(
        return_value=[{"uid": "dep_1", "readyState": "READY"}]
    )
    vercel_client.list_domains = AsyncMock(return_value=[{"name": "cgs.example.com"}])
    vercel_client.list_env_vars = AsyncMock(return_value=[{"id": "env_1", "key": "CLERK_KEY"}])
    vercel_client.close = AsyncMock()

    clerk_client = MagicMock()
    clerk_client.get_jwks = AsyncMock(return_value={"keys": [{"kid": "kid-1"}]})
    clerk_client.get_openid_configuration = AsyncMock(
        return_value={"issuer": "https://clerk.example.com"}
    )
    clerk_client.close = AsyncMock()

    with (
        patch("zetherion_ai.skills.agent_bootstrap.VercelClient", return_value=vercel_client),
        patch(
            "zetherion_ai.skills.agent_bootstrap.ClerkMetadataClient",
            return_value=clerk_client,
        ),
    ):
        overview = await skill._read_vercel_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="vercel-primary",
            view="overview",
            request_context={"project_ref": "cgs"},
        )
        deployments = await skill._read_vercel_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="vercel-primary",
            view="deployments",
            request_context={"project_ref": "cgs"},
        )
        domains = await skill._read_vercel_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="vercel-primary",
            view="domains",
            request_context={"project_ref": "cgs"},
        )
        envs = await skill._read_vercel_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="vercel-primary",
            view="envs",
            request_context={"project_ref": "cgs"},
        )
        clerk_overview = await skill._read_clerk_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="clerk-primary",
            view="overview",
            request_context={},
        )
        clerk_openid = await skill._read_clerk_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="clerk-primary",
            view="openid",
            request_context={"issuer": "https://clerk.example.com"},
        )
        clerk_jwks = await skill._read_clerk_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="clerk-primary",
            view="jwks",
            request_context={"jwks_url": "https://clerk.example.com/jwks"},
        )
        generic = await skill._read_generic_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="docker-primary",
            service_kind="docker",
            view="overview",
        )

    assert overview["deployments"][0]["uid"] == "dep_1"
    assert deployments["deployments"][0]["readyState"] == "READY"
    assert domains["domains"][0]["name"] == "cgs.example.com"
    assert envs["envs"][0]["key"] == "CLERK_KEY"
    assert clerk_overview["instance"]["issuer"] == "https://clerk.example.com"
    assert clerk_openid["openid_configuration"]["issuer"] == "https://clerk.example.com"
    assert clerk_jwks["jwks"]["keys"][0]["kid"] == "kid-1"
    assert generic["connector"]["connector_id"] == "docker-primary"
    assert vercel_client.close.await_count == 4
    assert clerk_client.close.await_count == 2


@pytest.mark.asyncio
async def test_read_stripe_service_view_covers_supported_views_and_error_translation() -> None:
    skill = _skill()
    skill._require_connector = AsyncMock(return_value={"secret_value": "stripe-secret"})  # type: ignore[method-assign]

    stripe_client = MagicMock()
    stripe_client.get_account = AsyncMock(return_value={"id": "acct_1"})
    stripe_client.list_products = AsyncMock(
        side_effect=[[{"id": "prod_1"}], [{"id": "prod_1"}], StripeAPIError("boom")]
    )
    stripe_client.list_subscriptions = AsyncMock(
        return_value=[{"id": "sub_1"}]
    )
    stripe_client.list_invoices = AsyncMock(return_value=[{"id": "inv_1"}])
    stripe_client.list_prices = AsyncMock(return_value=[{"id": "price_1"}])
    stripe_client.list_customers = AsyncMock(return_value=[{"id": "cus_1"}])
    stripe_client.list_webhook_endpoints = AsyncMock(return_value=[{"id": "wh_1"}])
    stripe_client.close = AsyncMock()

    with patch("zetherion_ai.skills.agent_bootstrap.StripeClient", return_value=stripe_client):
        overview = await skill._read_stripe_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="stripe-primary",
            view="overview",
            request_context={},
        )
        products = await skill._read_stripe_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="stripe-primary",
            view="products",
            request_context={"limit": 5},
        )
        prices = await skill._read_stripe_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="stripe-primary",
            view="prices",
            request_context={"product_id": "prod_1"},
        )
        customers = await skill._read_stripe_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="stripe-primary",
            view="customers",
            request_context={"email": "test@example.com"},
        )
        subscriptions = await skill._read_stripe_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="stripe-primary",
            view="subscriptions",
            request_context={"customer_id": "cus_1"},
        )
        invoices = await skill._read_stripe_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="stripe-primary",
            view="invoices",
            request_context={"customer_id": "cus_1"},
        )
        webhook_health = await skill._read_stripe_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=_app_profile(),
            connector_id="stripe-primary",
            view="webhook_health",
            request_context={},
        )
        with pytest.raises(ValueError, match="boom"):
            await skill._read_stripe_service_view(  # noqa: SLF001
                owner_id="owner-1",
                app_profile=_app_profile(),
                connector_id="stripe-primary",
                view="products",
                request_context={"limit": 5},
            )

    assert overview["account"]["id"] == "acct_1"
    assert products["products"][0]["id"] == "prod_1"
    assert prices["prices"][0]["id"] == "price_1"
    assert customers["customers"][0]["id"] == "cus_1"
    assert subscriptions["subscriptions"][0]["id"] == "sub_1"
    assert invoices["invoices"][0]["id"] == "inv_1"
    assert webhook_health["webhook_endpoints"][0]["id"] == "wh_1"
    assert stripe_client.close.await_count == 8


@pytest.mark.asyncio
async def test_read_service_view_dispatches_to_provider_handlers_and_records_unsupported_view_gaps(
) -> None:
    storage = MagicMock()
    storage.record_agent_audit_event = AsyncMock(return_value={"audit_id": "audit-1"})

    skill = _skill(storage)
    skill._read_github_service_view = AsyncMock(return_value={"service_kind": "github"})  # type: ignore[method-assign]
    skill._read_vercel_service_view = AsyncMock(return_value={"service_kind": "vercel"})  # type: ignore[method-assign]
    skill._read_clerk_service_view = AsyncMock(return_value={"service_kind": "clerk"})  # type: ignore[method-assign]
    skill._read_stripe_service_view = AsyncMock(return_value={"service_kind": "stripe"})  # type: ignore[method-assign]

    github = await skill._read_service_view(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        service_kind="github",
        view="overview",
        public_base_url="https://cgs.example.com",
        request_context={"session_id": "sess-1"},
    )
    vercel = await skill._read_service_view(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        service_kind="vercel",
        view="overview",
        public_base_url="https://cgs.example.com",
        request_context={"session_id": "sess-1"},
    )
    clerk = await skill._read_service_view(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        service_kind="clerk",
        view="overview",
        public_base_url="https://cgs.example.com",
        request_context={"session_id": "sess-1"},
    )
    stripe = await skill._read_service_view(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        service_kind="stripe",
        view="overview",
        public_base_url="https://cgs.example.com",
        request_context={"session_id": "sess-1"},
    )

    assert github["service_kind"] == "github"
    assert vercel["service_kind"] == "vercel"
    assert clerk["service_kind"] == "clerk"
    assert stripe["service_kind"] == "stripe"
    skill._read_github_service_view.assert_awaited_once()  # type: ignore[attr-defined]
    skill._read_vercel_service_view.assert_awaited_once()  # type: ignore[attr-defined]
    skill._read_clerk_service_view.assert_awaited_once()  # type: ignore[attr-defined]
    skill._read_stripe_service_view.assert_awaited_once()  # type: ignore[attr-defined]
    assert storage.record_agent_audit_event.await_count == 4

    with pytest.raises(ValueError, match="Unsupported `github` broker view `unknown`"):
        await skill._read_service_view(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            service_kind="github",
            view="unknown",
            public_base_url="https://cgs.example.com",
            request_context={"session_id": "sess-1"},
        )

    assert skill._record_gap.await_args.kwargs["required_capability"] == "github:unknown"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_read_service_view_helpers_cover_validation_errors() -> None:
    skill = _skill()
    missing_repo_profile = _app_profile()
    missing_repo_profile["profile"]["github_repos"] = []
    skill._require_github_connector = AsyncMock(return_value={"secret_value": "gh-secret"})  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="does not declare a GitHub repository"):
        await skill._read_github_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile=missing_repo_profile,
            connector_id="github-primary",
            view="overview",
            request_context={},
        )

    repository = SimpleNamespace(default_branch="main", to_dict=lambda: {"name": "cgs"})
    github_client = MagicMock()
    github_client.get_repository = AsyncMock(return_value=repository)
    github_client.close = AsyncMock()

    with patch("zetherion_ai.skills.agent_bootstrap.GitHubClient", return_value=github_client):
        with pytest.raises(ValueError, match="base and head are required for GitHub compare"):
            await skill._read_github_service_view(  # noqa: SLF001
                owner_id="owner-1",
                app_profile=_app_profile(),
                connector_id="github-primary",
                view="compare",
                request_context={},
            )
        with pytest.raises(ValueError, match="Unsupported GitHub broker view `unknown`"):
            await skill._read_github_service_view(  # noqa: SLF001
                owner_id="owner-1",
                app_profile=_app_profile(),
                connector_id="github-primary",
                view="unknown",
                request_context={},
            )

    assert github_client.close.await_count == 2

    skill._require_connector = AsyncMock(  # type: ignore[method-assign]
        return_value={"secret_value": "vercel-secret", "metadata": {}}
    )
    with pytest.raises(ValueError, match="project_ref is required for Vercel broker access"):
        await skill._read_vercel_service_view(  # noqa: SLF001
            owner_id="owner-1",
            app_profile={"app_id": "", "profile": {"service_connector_map": {}}},
            connector_id="vercel-primary",
            view="overview",
            request_context={},
        )


@pytest.mark.asyncio
async def test_read_service_view_execute_service_action_and_execute_stripe_actions_cover_dispatch(
) -> None:
    storage = MagicMock()
    storage.get_external_service_connector_with_secret = AsyncMock(
        return_value={
            "connector_id": "docker-primary",
            "service_kind": "docker",
            "active": True,
            "secret_value": None,
            "metadata": {"scope": "local"},
            "policy": {"read_only": True},
        }
    )
    storage.record_agent_audit_event = AsyncMock(return_value={"audit_id": "audit-1"})
    storage.create_agent_service_request = AsyncMock(return_value={"request_id": "req-1"})

    skill = _skill(storage)
    skill._read_generic_service_view = AsyncMock(  # type: ignore[method-assign]
        return_value={"service_kind": "docker", "view": "overview"}
    )
    skill._execute_stripe_service_action = AsyncMock(  # type: ignore[method-assign]
        return_value={"status": "executed", "product": {"id": "prod_1"}}
    )

    generic = await skill._read_service_view(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        service_kind="docker",
        view="overview",
        public_base_url="https://cgs.example.com",
        request_context={"session_id": "sess-1"},
    )
    execution = await skill._execute_service_action(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        service_kind="stripe",
        action_id="product.ensure",
        request_context={"session_id": "sess-1", "input": {"name": "Gold"}},
    )

    stripe_client = MagicMock()
    stripe_client.ensure_product = AsyncMock(return_value={"id": "prod_1"})
    stripe_client.ensure_price = AsyncMock(return_value={"id": "price_1"})
    stripe_client.link_customer = AsyncMock(return_value={"id": "cus_1"})
    stripe_client.link_subscription = AsyncMock(return_value={"id": "sub_1"})
    stripe_client.update_subscription_price = AsyncMock(return_value={"id": "sub_1"})
    stripe_client.ensure_meter = AsyncMock(return_value={"id": "meter_1"})
    stripe_client.close = AsyncMock()

    stripe_skill = _skill(MagicMock())
    stripe_skill._require_connector = AsyncMock(  # type: ignore[method-assign]
        return_value={"secret_value": "stripe-secret"}
    )

    with patch("zetherion_ai.skills.agent_bootstrap.StripeClient", return_value=stripe_client):
        product = await stripe_skill._execute_stripe_service_action(  # noqa: SLF001
            owner_id="owner-1",
            connector_id="stripe-primary",
            action_id="product.ensure",
            input_payload={"name": "Gold"},
        )
        price = await stripe_skill._execute_stripe_service_action(  # noqa: SLF001
            owner_id="owner-1",
            connector_id="stripe-primary",
            action_id="price.ensure",
            input_payload={"product_id": "prod_1", "currency": "usd", "unit_amount": 500},
        )
        customer = await stripe_skill._execute_stripe_service_action(  # noqa: SLF001
            owner_id="owner-1",
            connector_id="stripe-primary",
            action_id="customer.link",
            input_payload={"email": "test@example.com"},
        )
        subscription = await stripe_skill._execute_stripe_service_action(  # noqa: SLF001
            owner_id="owner-1",
            connector_id="stripe-primary",
            action_id="subscription.link",
            input_payload={"customer_id": "cus_1", "price_id": "price_1"},
        )
        updated = await stripe_skill._execute_stripe_service_action(  # noqa: SLF001
            owner_id="owner-1",
            connector_id="stripe-primary",
            action_id="subscription.update_price",
            input_payload={"subscription_id": "sub_1", "price_id": "price_2"},
        )
        meter = await stripe_skill._execute_stripe_service_action(  # noqa: SLF001
            owner_id="owner-1",
            connector_id="stripe-primary",
            action_id="meter.config.ensure",
            input_payload={"event_name": "api.calls"},
        )
        with pytest.raises(ValueError, match="Unsupported Stripe action"):
            await stripe_skill._execute_stripe_service_action(  # noqa: SLF001
                owner_id="owner-1",
                connector_id="stripe-primary",
                action_id="unknown",
                input_payload={},
            )

    assert generic["service_kind"] == "docker"
    assert execution["request"]["request_id"] == "req-1"
    assert product["product"]["id"] == "prod_1"
    assert price["price"]["id"] == "price_1"
    assert customer["customer"]["id"] == "cus_1"
    assert subscription["subscription"]["id"] == "sub_1"
    assert updated["subscription"]["id"] == "sub_1"
    assert meter["meter"]["id"] == "meter_1"
    skill._execute_stripe_service_action.assert_awaited_once()  # type: ignore[attr-defined]
