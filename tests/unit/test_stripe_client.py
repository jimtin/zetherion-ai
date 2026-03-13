from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from zetherion_ai.skills.stripe.client import StripeAPIError, StripeClient


def _response(
    *,
    status_code: int = 200,
    json_data: object | None = None,
    text: str = "",
) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.text = text
    if isinstance(json_data, Exception):
        response.json.side_effect = json_data
    else:
        response.json.return_value = json_data
    return response


@pytest.fixture
def stripe_http_client() -> MagicMock:
    with patch("zetherion_ai.skills.stripe.client.httpx.AsyncClient") as mock_cls:
        client = MagicMock()
        client.is_closed = False
        client.request = AsyncMock()
        client.aclose = AsyncMock()
        mock_cls.return_value = client
        yield client


@pytest.mark.asyncio
async def test_request_encodes_form_data_and_reuses_client(stripe_http_client: MagicMock) -> None:
    stripe_http_client.request.return_value = _response(json_data={"id": "prod_1"})
    client = StripeClient("token-123")

    first = await client.create_product(
        name="Zetherion",
        description="Platform",
        metadata={"lookup_key": "zetherion"},
    )
    second_http_client = await client._get_client()

    assert first["id"] == "prod_1"
    assert second_http_client is stripe_http_client
    _, kwargs = stripe_http_client.request.await_args
    assert kwargs["method"] == "POST"
    assert kwargs["url"] == "/v1/products"
    assert kwargs["data"] == {
        "name": "Zetherion",
        "description": "Platform",
        "metadata[lookup_key]": "zetherion",
    }
    assert kwargs["headers"] == {"Content-Type": "application/x-www-form-urlencoded"}

    await client.close()
    stripe_http_client.aclose.assert_awaited_once()
    assert client._client is None


@pytest.mark.asyncio
async def test_request_raises_api_error_on_transport_failure(
    stripe_http_client: MagicMock,
) -> None:
    stripe_http_client.request.side_effect = httpx.RequestError(
        "boom",
        request=httpx.Request("GET", "https://stripe.example/v1/account"),
    )
    client = StripeClient("token-123")

    with pytest.raises(StripeAPIError, match="Request failed: boom"):
        await client.get_account()


@pytest.mark.asyncio
async def test_request_raises_api_error_for_http_error_payload(
    stripe_http_client: MagicMock,
) -> None:
    stripe_http_client.request.return_value = _response(
        status_code=402,
        json_data={"error": {"message": "Card declined"}},
    )
    client = StripeClient("token-123")

    with pytest.raises(StripeAPIError, match="Card declined") as exc_info:
        await client.get_account()

    assert exc_info.value.status_code == 402
    assert exc_info.value.response == {"error": {"message": "Card declined"}}


@pytest.mark.asyncio
async def test_request_rejects_non_object_payload(stripe_http_client: MagicMock) -> None:
    stripe_http_client.request.return_value = _response(json_data=["not", "a", "dict"])
    client = StripeClient("token-123")

    with pytest.raises(StripeAPIError, match="Unexpected Stripe response format"):
        await client.get_account()


@pytest.mark.asyncio
async def test_list_methods_clamp_limits_and_filter_non_dict_rows(
    stripe_http_client: MagicMock,
) -> None:
    client = StripeClient("token-123")
    client._request = AsyncMock(
        side_effect=[
            {"data": [{"id": "prod_1"}, "skip"]},
            {"data": [{"id": "price_1"}, "skip"]},
            {"data": [{"id": "cus_1"}, "skip"]},
            {"data": [{"id": "sub_1"}, "skip"]},
            {"data": [{"id": "inv_1"}, "skip"]},
            {"data": [{"id": "wh_1"}, "skip"]},
            {"data": [{"id": "evt_1"}, "skip"]},
        ]
    )

    assert await client.list_products(limit=0) == [{"id": "prod_1"}]
    assert await client.list_prices(limit=999, product_id="prod_1") == [{"id": "price_1"}]
    assert await client.list_customers(limit=999, email="user@example.com") == [{"id": "cus_1"}]
    assert await client.list_subscriptions(limit=999, customer_id="cus_1") == [{"id": "sub_1"}]
    assert await client.list_invoices(limit=999, customer_id="cus_1") == [{"id": "inv_1"}]
    assert await client.list_webhook_endpoints(limit=999) == [{"id": "wh_1"}]
    assert await client.list_events(limit=999, event_type="invoice.paid") == [{"id": "evt_1"}]

    request_calls = client._request.await_args_list
    assert request_calls[0].kwargs["params"] == {"limit": 1}
    assert request_calls[1].kwargs["params"] == {"limit": 100, "product": "prod_1"}
    assert request_calls[2].kwargs["params"] == {"limit": 100, "email": "user@example.com"}
    assert request_calls[3].kwargs["params"] == {"limit": 100, "customer": "cus_1"}
    assert request_calls[4].kwargs["params"] == {"limit": 100, "customer": "cus_1"}
    assert request_calls[5].kwargs["params"] == {"limit": 100}
    assert request_calls[6].kwargs["params"] == {"limit": 100, "type": "invoice.paid"}


@pytest.mark.asyncio
async def test_simple_getters_delegate_to_request(stripe_http_client: MagicMock) -> None:
    client = StripeClient("token-123")
    client._request = AsyncMock(side_effect=[{"id": "evt_1"}, {"id": "prod_1"}, {"id": "price_1"}])

    assert await client.get_event("evt_1") == {"id": "evt_1"}
    assert await client.get_product("prod_1") == {"id": "prod_1"}
    assert await client.get_price("price_1") == {"id": "price_1"}
    assert client._request.await_args_list[0].args == ("GET", "/v1/events/evt_1")
    assert client._request.await_args_list[1].args == ("GET", "/v1/products/prod_1")
    assert client._request.await_args_list[2].args == ("GET", "/v1/prices/price_1")


@pytest.mark.asyncio
async def test_ensure_product_uses_product_id_lookup_key_name_or_create() -> None:
    client = StripeClient("token-123")
    client.get_product = AsyncMock(return_value={"id": "prod_existing"})
    client.list_products = AsyncMock(
        side_effect=[
            [{"id": "prod_lookup", "metadata": {"lookup_key": "zetherion"}}],
            [{"id": "prod_named", "name": "Zetherion", "metadata": {}}],
            [],
            [],
        ]
    )
    client.create_product = AsyncMock(return_value={"id": "prod_created"})

    assert await client.ensure_product(
        name="ignored",
        product_id="prod_existing",
        lookup_key=None,
        metadata={},
        description=None,
    ) == {"id": "prod_existing"}
    assert await client.ensure_product(
        name="Zetherion",
        product_id=None,
        lookup_key="zetherion",
        metadata={},
        description=None,
    ) == {"id": "prod_lookup", "metadata": {"lookup_key": "zetherion"}}
    assert await client.ensure_product(
        name="Zetherion",
        product_id=None,
        lookup_key=None,
        metadata={},
        description=None,
    ) == {"id": "prod_named", "name": "Zetherion", "metadata": {}}
    created = await client.ensure_product(
        name="Zetherion",
        product_id=None,
        lookup_key="zetherion",
        metadata={"tier": "pro"},
        description="Platform",
    )

    assert created == {"id": "prod_created"}
    client.create_product.assert_awaited_once_with(
        name="Zetherion",
        description="Platform",
        metadata={"tier": "pro", "lookup_key": "zetherion"},
    )

    with pytest.raises(StripeAPIError, match="name is required"):
        await client.ensure_product(
            name="",
            product_id=None,
            lookup_key=None,
            metadata={},
            description=None,
        )


@pytest.mark.asyncio
async def test_ensure_price_validates_and_reuses_matching_rows() -> None:
    client = StripeClient("token-123")
    client.list_prices = AsyncMock(
        side_effect=[
            [{"id": "price_lookup", "lookup_key": "team"}],
            [
                {
                    "id": "price_match",
                    "unit_amount": 5000,
                    "currency": "usd",
                    "recurring": {"interval": "month"},
                }
            ],
            [],
        ]
    )
    client.create_price = AsyncMock(return_value={"id": "price_created"})

    assert await client.ensure_price(
        product_id="prod_1",
        currency="USD",
        unit_amount=5000,
        recurring_interval="month",
        lookup_key="team",
    ) == {"id": "price_lookup", "lookup_key": "team"}
    assert await client.ensure_price(
        product_id="prod_1",
        currency="USD",
        unit_amount=5000,
        recurring_interval="month",
        lookup_key=None,
    ) == {
        "id": "price_match",
        "unit_amount": 5000,
        "currency": "usd",
        "recurring": {"interval": "month"},
    }
    assert await client.ensure_price(
        product_id="prod_1",
        currency="USD",
        unit_amount=6000,
        recurring_interval=None,
        lookup_key="business",
    ) == {"id": "price_created"}
    client.create_price.assert_awaited_once_with(
        product_id="prod_1",
        currency="USD",
        unit_amount=6000,
        recurring_interval=None,
        lookup_key="business",
    )

    with pytest.raises(StripeAPIError, match="product_id is required"):
        await client.ensure_price(
            product_id="",
            currency="usd",
            unit_amount=5000,
            recurring_interval=None,
            lookup_key=None,
        )
    with pytest.raises(StripeAPIError, match="unit_amount must be greater than zero"):
        await client.ensure_price(
            product_id="prod_1",
            currency="usd",
            unit_amount=0,
            recurring_interval=None,
            lookup_key=None,
        )


@pytest.mark.asyncio
async def test_link_customer_prefers_existing_customer_then_falls_back_to_create() -> None:
    client = StripeClient("token-123")
    client.get_customer = AsyncMock(return_value={"id": "cus_direct"})
    client.list_customers = AsyncMock(side_effect=[[{"id": "cus_email"}], []])
    client.create_customer = AsyncMock(return_value={"id": "cus_created"})

    assert await client.link_customer(
        customer_id="cus_direct",
        email=None,
        name=None,
        metadata={},
    ) == {"id": "cus_direct"}
    assert await client.link_customer(
        customer_id=None,
        email="user@example.com",
        name="User",
        metadata={},
    ) == {"id": "cus_email"}
    assert await client.link_customer(
        customer_id=None,
        email="new@example.com",
        name="User",
        metadata={"tenant": "abc"},
    ) == {"id": "cus_created"}
    client.create_customer.assert_awaited_once_with(
        email="new@example.com",
        name="User",
        metadata={"tenant": "abc"},
    )


@pytest.mark.asyncio
async def test_link_subscription_prefers_direct_lookup_price_match_first_row_or_error() -> None:
    client = StripeClient("token-123")
    client.get_subscription = AsyncMock(return_value={"id": "sub_direct"})
    client.list_subscriptions = AsyncMock(
        side_effect=[
            [{"id": "sub_price", "items": {"data": [{"price": {"id": "price_1"}}]}}],
            [{"id": "sub_first"}],
            [],
        ]
    )

    assert await client.link_subscription(
        subscription_id="sub_direct",
        customer_id=None,
        price_id=None,
    ) == {"id": "sub_direct"}
    assert await client.link_subscription(
        subscription_id=None,
        customer_id="cus_1",
        price_id="price_1",
    ) == {"id": "sub_price", "items": {"data": [{"price": {"id": "price_1"}}]}}
    assert await client.link_subscription(
        subscription_id=None,
        customer_id="cus_1",
        price_id=None,
    ) == {"id": "sub_first"}

    with pytest.raises(StripeAPIError, match="customer_id or subscription_id is required"):
        await client.link_subscription(
            subscription_id=None,
            customer_id=None,
            price_id=None,
        )
    with pytest.raises(StripeAPIError, match="No matching subscription found"):
        await client.link_subscription(
            subscription_id=None,
            customer_id="cus_1",
            price_id="price_missing",
        )


@pytest.mark.asyncio
async def test_update_subscription_price_validates_items_and_updates_subscription() -> None:
    client = StripeClient("token-123")
    client.get_subscription = AsyncMock(
        side_effect=[
            {"items": {"data": []}},
            {"items": {"data": [{}]}},
            {"items": {"data": [{"id": "si_1"}]}},
        ]
    )
    client.update_subscription = AsyncMock(return_value={"id": "sub_updated"})

    with pytest.raises(StripeAPIError, match="subscription_id and price_id are required"):
        await client.update_subscription_price(subscription_id="", price_id="")
    with pytest.raises(StripeAPIError, match="Subscription has no items to update"):
        await client.update_subscription_price(subscription_id="sub_1", price_id="price_1")
    with pytest.raises(StripeAPIError, match="Subscription item id is missing"):
        await client.update_subscription_price(subscription_id="sub_1", price_id="price_1")

    updated = await client.update_subscription_price(subscription_id="sub_1", price_id="price_1")

    assert updated == {"id": "sub_updated"}
    client.update_subscription.assert_awaited_once_with(
        "sub_1",
        payload={
            "items[0][id]": "si_1",
            "items[0][price]": "price_1",
            "proration_behavior": "create_prorations",
        },
    )


@pytest.mark.asyncio
async def test_ensure_meter_reuses_existing_or_creates_new_meter() -> None:
    client = StripeClient("token-123")
    client.list_meters = AsyncMock(
        side_effect=[[{"id": "meter_existing", "event_name": "usage"}], []]
    )
    client.create_meter = AsyncMock(return_value={"id": "meter_created"})

    assert await client.ensure_meter(
        event_name="usage",
        display_name="Usage",
        customer_mapping_key="tenant_id",
        metadata={"unit": "tokens"},
    ) == {"id": "meter_existing", "event_name": "usage"}
    assert await client.ensure_meter(
        event_name="new_usage",
        display_name="Usage",
        customer_mapping_key="tenant_id",
        metadata={"unit": "tokens"},
    ) == {"id": "meter_created"}
    client.create_meter.assert_awaited_once_with(
        event_name="new_usage",
        display_name="Usage",
        customer_mapping_key="tenant_id",
        metadata={"unit": "tokens"},
    )

    with pytest.raises(StripeAPIError, match="event_name is required"):
        await client.ensure_meter(
            event_name="",
            display_name=None,
            customer_mapping_key=None,
            metadata={},
        )


@pytest.mark.asyncio
async def test_create_customer_price_meter_and_subscription_helpers_delegate_payloads() -> None:
    client = StripeClient("token-123")
    client._request = AsyncMock(
        side_effect=[
            {"id": "cus_1"},
            {"id": "price_1"},
            {"id": "sub_1"},
            {"id": "meter_1"},
        ]
    )

    customer = await client.create_customer(
        email="owner@example.com",
        name="Owner",
        metadata={"tenant_id": "tenant-1"},
    )
    price = await client.create_price(
        product_id="prod_1",
        currency="usd",
        unit_amount=5000,
        recurring_interval="month",
        lookup_key="team",
    )
    subscription = await client.update_subscription(
        "sub_1",
        payload={"items[0][price]": "price_1"},
    )
    meter = await client.create_meter(
        event_name="usage.event",
        display_name="Usage Event",
        customer_mapping_key="customer_id",
        metadata={"env": "prod"},
    )

    assert customer == {"id": "cus_1"}
    assert price == {"id": "price_1"}
    assert subscription == {"id": "sub_1"}
    assert meter == {"id": "meter_1"}

    request_calls = client._request.await_args_list
    assert request_calls[0].args == ("POST", "/v1/customers")
    assert request_calls[0].kwargs["data"] == {
        "email": "owner@example.com",
        "name": "Owner",
        "metadata[tenant_id]": "tenant-1",
    }
    assert request_calls[1].kwargs["data"] == {
        "product": "prod_1",
        "currency": "usd",
        "unit_amount": 5000,
        "recurring[interval]": "month",
        "lookup_key": "team",
    }
    assert request_calls[2].args == ("POST", "/v1/subscriptions/sub_1")
    assert request_calls[3].kwargs["data"] == {
        "event_name": "usage.event",
        "default_aggregation[formula]": "sum",
        "display_name": "Usage Event",
        "customer_mapping[event_payload_key]": "customer_id",
        "customer_mapping[type]": "by_id",
        "metadata[env]": "prod",
    }


@pytest.mark.asyncio
async def test_link_subscription_skips_non_dict_items_and_returns_first_subscription() -> None:
    client = StripeClient("token-123")
    client.list_subscriptions = AsyncMock(
        return_value=[
            {
                "id": "sub_fallback",
                "items": {"data": ["skip", {"price": {"id": "price_other"}}]},
            }
        ]
    )

    subscription = await client.link_subscription(
        subscription_id=None,
        customer_id="cus_1",
        price_id="price_missing",
    )

    assert subscription["id"] == "sub_fallback"
