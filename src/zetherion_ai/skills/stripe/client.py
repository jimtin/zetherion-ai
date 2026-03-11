"""Minimal async Stripe API client for brokered reads and named actions."""

from __future__ import annotations

from typing import Any

import httpx

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.skills.stripe.client")

STRIPE_API_BASE = "https://api.stripe.com"
DEFAULT_TIMEOUT = 30.0


class StripeAPIError(Exception):
    """Base exception for Stripe API failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = response or {}


class StripeClient:
    """Async client for a narrow subset of the Stripe REST API."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = STRIPE_API_BASE,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = await self._get_client()
        encoded: dict[str, str] | None = None
        if data is not None:
            encoded = {}
            for key, value in data.items():
                if value is None:
                    continue
                encoded[key] = str(value)
        try:
            response = await client.request(
                method=method,
                url=path,
                params=params,
                data=encoded,
                headers=(
                    {"Content-Type": "application/x-www-form-urlencoded"}
                    if encoded is not None
                    else None
                ),
            )
        except httpx.RequestError as exc:
            log.error("stripe_request_failed", path=path, error=str(exc))
            raise StripeAPIError(f"Request failed: {exc}") from exc
        if response.status_code >= 400:
            try:
                payload = response.json()
            except Exception:
                payload = {"message": response.text}
            error_payload = payload.get("error") if isinstance(payload, dict) else {}
            message = (
                str((error_payload or {}).get("message") or "")
                or str((payload or {}).get("message") or "")
                or response.text
            )
            raise StripeAPIError(
                message or f"Stripe request failed with HTTP {response.status_code}",
                status_code=response.status_code,
                response=payload if isinstance(payload, dict) else {"message": response.text},
            )
        try:
            payload = response.json()
        except Exception as exc:  # pragma: no cover - defensive
            raise StripeAPIError("Failed to decode Stripe response") from exc
        if not isinstance(payload, dict):
            raise StripeAPIError("Unexpected Stripe response format")
        return payload

    async def get_account(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/account")

    async def list_products(self, *, limit: int = 10) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            "/v1/products",
            params={"limit": max(1, min(limit, 100))},
        )
        data = payload.get("data", [])
        return [dict(item) for item in data if isinstance(item, dict)]

    async def list_prices(
        self,
        *,
        limit: int = 10,
        product_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 100))}
        if product_id:
            params["product"] = product_id
        payload = await self._request("GET", "/v1/prices", params=params)
        data = payload.get("data", [])
        return [dict(item) for item in data if isinstance(item, dict)]

    async def list_customers(
        self,
        *,
        limit: int = 10,
        email: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 100))}
        if email:
            params["email"] = email
        payload = await self._request("GET", "/v1/customers", params=params)
        data = payload.get("data", [])
        return [dict(item) for item in data if isinstance(item, dict)]

    async def list_subscriptions(
        self,
        *,
        limit: int = 10,
        customer_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 100))}
        if customer_id:
            params["customer"] = customer_id
        payload = await self._request("GET", "/v1/subscriptions", params=params)
        data = payload.get("data", [])
        return [dict(item) for item in data if isinstance(item, dict)]

    async def list_invoices(
        self,
        *,
        limit: int = 10,
        customer_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 100))}
        if customer_id:
            params["customer"] = customer_id
        payload = await self._request("GET", "/v1/invoices", params=params)
        data = payload.get("data", [])
        return [dict(item) for item in data if isinstance(item, dict)]

    async def list_webhook_endpoints(self, *, limit: int = 10) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            "/v1/webhook_endpoints",
            params={"limit": max(1, min(limit, 100))},
        )
        data = payload.get("data", [])
        return [dict(item) for item in data if isinstance(item, dict)]

    async def get_product(self, product_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/products/{product_id}")

    async def create_product(
        self,
        *,
        name: str,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name}
        if description:
            payload["description"] = description
        for key, value in (metadata or {}).items():
            payload[f"metadata[{key}]"] = value
        return await self._request("POST", "/v1/products", data=payload)

    async def get_price(self, price_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/prices/{price_id}")

    async def create_price(
        self,
        *,
        product_id: str,
        currency: str,
        unit_amount: int,
        recurring_interval: str | None = None,
        lookup_key: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "product": product_id,
            "currency": currency,
            "unit_amount": unit_amount,
        }
        if recurring_interval:
            payload["recurring[interval]"] = recurring_interval
        if lookup_key:
            payload["lookup_key"] = lookup_key
        return await self._request("POST", "/v1/prices", data=payload)

    async def get_customer(self, customer_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/customers/{customer_id}")

    async def create_customer(
        self,
        *,
        email: str | None,
        name: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if email:
            payload["email"] = email
        if name:
            payload["name"] = name
        for key, value in (metadata or {}).items():
            payload[f"metadata[{key}]"] = value
        return await self._request("POST", "/v1/customers", data=payload)

    async def get_subscription(self, subscription_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/subscriptions/{subscription_id}")

    async def update_subscription(
        self,
        subscription_id: str,
        *,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._request("POST", f"/v1/subscriptions/{subscription_id}", data=payload)

    async def list_meters(self, *, limit: int = 20) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            "/v1/billing/meters",
            params={"limit": max(1, min(limit, 100))},
        )
        data = payload.get("data", [])
        return [dict(item) for item in data if isinstance(item, dict)]

    async def create_meter(
        self,
        *,
        event_name: str,
        display_name: str | None = None,
        customer_mapping_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event_name": event_name,
            "default_aggregation[formula]": "sum",
        }
        if display_name:
            payload["display_name"] = display_name
        if customer_mapping_key:
            payload["customer_mapping[event_payload_key]"] = customer_mapping_key
            payload["customer_mapping[type]"] = "by_id"
        for key, value in (metadata or {}).items():
            payload[f"metadata[{key}]"] = value
        return await self._request("POST", "/v1/billing/meters", data=payload)

    async def ensure_product(
        self,
        *,
        name: str,
        product_id: str | None,
        lookup_key: str | None,
        metadata: dict[str, Any],
        description: str | None,
    ) -> dict[str, Any]:
        if product_id:
            return await self.get_product(product_id)
        for product in await self.list_products(limit=100):
            product_metadata = dict(product.get("metadata") or {})
            if lookup_key and product_metadata.get("lookup_key") == lookup_key:
                return product
            if name and str(product.get("name") or "") == name:
                return product
        if not name:
            raise StripeAPIError("name is required to create a Stripe product")
        merged_metadata = dict(metadata)
        if lookup_key:
            merged_metadata.setdefault("lookup_key", lookup_key)
        return await self.create_product(
            name=name,
            description=description,
            metadata=merged_metadata,
        )

    async def ensure_price(
        self,
        *,
        product_id: str,
        currency: str,
        unit_amount: int,
        recurring_interval: str | None,
        lookup_key: str | None,
    ) -> dict[str, Any]:
        if not product_id:
            raise StripeAPIError("product_id is required")
        if unit_amount <= 0:
            raise StripeAPIError("unit_amount must be greater than zero")
        for price in await self.list_prices(limit=100, product_id=product_id):
            if lookup_key and str(price.get("lookup_key") or "") == lookup_key:
                return price
            if (
                int(price.get("unit_amount") or 0) == unit_amount
                and str(price.get("currency") or "").lower() == currency.lower()
                and (
                    not recurring_interval
                    or str((price.get("recurring") or {}).get("interval") or "").lower()
                    == recurring_interval.lower()
                )
            ):
                return price
        return await self.create_price(
            product_id=product_id,
            currency=currency,
            unit_amount=unit_amount,
            recurring_interval=recurring_interval,
            lookup_key=lookup_key,
        )

    async def link_customer(
        self,
        *,
        customer_id: str | None,
        email: str | None,
        name: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        if customer_id:
            return await self.get_customer(customer_id)
        if email:
            customers = await self.list_customers(limit=100, email=email)
            if customers:
                return customers[0]
        return await self.create_customer(email=email, name=name, metadata=metadata)

    async def link_subscription(
        self,
        *,
        subscription_id: str | None,
        customer_id: str | None,
        price_id: str | None,
    ) -> dict[str, Any]:
        if subscription_id:
            return await self.get_subscription(subscription_id)
        if not customer_id:
            raise StripeAPIError("customer_id or subscription_id is required")
        subscriptions = await self.list_subscriptions(limit=100, customer_id=customer_id)
        if price_id:
            for subscription in subscriptions:
                items = (subscription.get("items") or {}).get("data") or []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    price = dict(item.get("price") or {})
                    if str(price.get("id") or "") == price_id:
                        return subscription
        if subscriptions:
            return subscriptions[0]
        raise StripeAPIError("No matching subscription found")

    async def update_subscription_price(
        self,
        *,
        subscription_id: str,
        price_id: str,
    ) -> dict[str, Any]:
        if not subscription_id or not price_id:
            raise StripeAPIError("subscription_id and price_id are required")
        subscription = await self.get_subscription(subscription_id)
        items = (subscription.get("items") or {}).get("data") or []
        first_item = next((item for item in items if isinstance(item, dict)), None)
        if first_item is None:
            raise StripeAPIError("Subscription has no items to update")
        item_id = str(first_item.get("id") or "").strip()
        if not item_id:
            raise StripeAPIError("Subscription item id is missing")
        return await self.update_subscription(
            subscription_id,
            payload={
                "items[0][id]": item_id,
                "items[0][price]": price_id,
                "proration_behavior": "create_prorations",
            },
        )

    async def ensure_meter(
        self,
        *,
        event_name: str,
        display_name: str | None,
        customer_mapping_key: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        if not event_name:
            raise StripeAPIError("event_name is required")
        for meter in await self.list_meters(limit=100):
            if str(meter.get("event_name") or "") == event_name:
                return meter
        return await self.create_meter(
            event_name=event_name,
            display_name=display_name,
            customer_mapping_key=customer_mapping_key,
            metadata=metadata,
        )
