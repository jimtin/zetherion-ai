# Licensing: Polar.sh Integration

**Status**: Planning (Required for Phase 6+)
**Dependencies**: Phase 5D (Skills Framework)
**Created**: 2026-02-06

---

## Overview

Phase 6+ skills are paid features using [Polar.sh](https://polar.sh/) for license management and payments. This document outlines the integration architecture, pricing model, and implementation plan.

---

## Why Polar.sh

- Officially supported GitHub funding platform
- License key generation + automatic delivery
- Subscription management with automatic renewals
- 4% + 40¢ per transaction (no monthly fees)
- Integrates with GitHub repos for access control
- Webhook support for real-time license updates

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Polar.sh                                 │
│                                                                 │
│  Products:                                                      │
│  - zetherion-premium ($X/mo)                                    │
│                                                                 │
│  On purchase → generates license key → sends to customer        │
│  On renewal/cancel → sends webhook                              │
└──────────────────────────┬──────────────────────────────────────┘
                           │ Webhooks
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Skills Service (5D)                           │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                   LicenseManager                         │   │
│  │                                                          │   │
│  │  - Validates license keys via Polar API                  │   │
│  │  - Caches valid licenses (5 min TTL)                     │   │
│  │  - Handles webhooks for subscription changes             │   │
│  │  - Maps Discord user ID → license status                 │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                   │
│                              ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    Skill Base Class                      │   │
│  │                                                          │   │
│  │  requires_license: bool = False                          │   │
│  │  license_product_id: str | None = None                   │   │
│  │                                                          │   │
│  │  async def check_license(user_id) -> bool                │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Pricing Model: Freemium + Premium

### Tier Structure

| Tier | What's Included | Price |
|------|-----------------|-------|
| **Free** | Core Zetherion AI (Phases 1-5): Memory, routing, profiles, basic agent | $0 |
| **Premium** | All paid skills (Phase 6+): Discord Management, GitHub Management, Gmail Correlator, future skills | $X/month or $Y/year |

### Freemium Boundary

**Free Features:**
- Personal assistant conversation
- Long-term memory (Qdrant)
- Multi-provider LLM routing
- User profile system
- Employment profile / relationship tracking
- Basic Discord bot interaction

**Premium Features:**
- Discord server management (Phase 6)
- GitHub repository management (Phase 7)
- Gmail email correlation + AI replies (Phase 8)
- All future paid skills
- Priority support

### Why Subscription Only

- Predictable revenue for ongoing development
- Skills require maintenance (API changes, security updates)
- Encourages continuous improvement
- Simpler licensing logic (active/inactive)

### Pricing Examples

**Monthly:**
- $9/month for Premium

**Yearly (20% discount):**
- $86/year (~$7.17/month)

### Trial Period

- 7-day free trial of Premium (no card required)
- Full access to all Premium skills
- Converts to Free tier if not subscribed

---

## Implementation

### 1. Polar Setup

1. Create Polar account linked to GitHub
2. Create product: `zetherion-premium`
3. Configure pricing tiers (monthly + yearly)
4. Set up webhook endpoint
5. Generate API keys for validation

### 2. License Validation in Skills Framework

Add to base Skill class in Phase 5D:

```python
from secureclaw.licensing import LicenseManager

class Skill:
    requires_license: bool = False
    license_product_id: str | None = None

    def __init__(self, license_manager: LicenseManager):
        self.license_manager = license_manager

    async def check_license(self, user_id: str) -> bool:
        """Validate user has active license for this skill."""
        if not self.requires_license:
            return True
        return await self.license_manager.validate(
            user_id=user_id,
            product_id=self.license_product_id
        )

    async def handle(self, user_id: str, request: SkillRequest) -> SkillResponse:
        """Handle skill request with license check."""
        if not await self.check_license(user_id):
            return SkillResponse(
                success=False,
                message=f"This skill requires a Premium subscription.\n"
                        f"Get it here: https://polar.sh/jameshinton/zetherion-premium\n"
                        f"Already subscribed? Run: /activate <license-key>"
            )
        return await self._handle(user_id, request)

    async def _handle(self, user_id: str, request: SkillRequest) -> SkillResponse:
        """Override in subclass."""
        raise NotImplementedError
```

### 3. License Manager Component

New module: `src/secureclaw/licensing/`

**manager.py:**
```python
from cachetools import TTLCache
from secureclaw.licensing.polar import PolarClient
from secureclaw.licensing.cache import LicenseCache

class LicenseManager:
    def __init__(self, polar_client: PolarClient, cache: LicenseCache):
        self.polar = polar_client
        self.cache = cache

    async def validate(self, user_id: str, product_id: str) -> bool:
        """Check if user has valid license for product."""
        # Check cache first (5 min TTL)
        cached = self.cache.get(user_id, product_id)
        if cached is not None:
            return cached

        # Query Polar API
        is_valid = await self.polar.check_license(user_id, product_id)

        # Cache result
        self.cache.set(user_id, product_id, is_valid)

        return is_valid

    async def get_user_entitlements(self, user_id: str) -> list[str]:
        """Return list of product_ids user has access to."""
        return await self.polar.get_entitlements(user_id)

    async def activate_license(self, user_id: str, license_key: str) -> bool:
        """Activate a license key for a user."""
        result = await self.polar.activate(license_key, user_id)
        if result.success:
            # Invalidate cache to pick up new license
            self.cache.invalidate(user_id)
        return result.success

    async def handle_webhook(self, event: dict) -> None:
        """Handle Polar webhooks for subscription changes."""
        event_type = event.get("type")
        user_id = event.get("user_id")

        if event_type in ("subscription.canceled", "subscription.expired", "refund.created"):
            # Invalidate cache immediately
            self.cache.invalidate(user_id)
        elif event_type in ("subscription.created", "subscription.renewed"):
            # Invalidate to pick up new status
            self.cache.invalidate(user_id)
```

**polar.py:**
```python
import httpx
from dataclasses import dataclass

@dataclass
class ActivationResult:
    success: bool
    message: str
    product_id: str | None = None

class PolarClient:
    def __init__(self, api_key: str, base_url: str = "https://api.polar.sh"):
        self.api_key = api_key
        self.base_url = base_url
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"}
        )

    async def check_license(self, user_id: str, product_id: str) -> bool:
        """Check if user has active subscription for product."""
        response = await self.client.get(
            f"/v1/subscriptions",
            params={"user_id": user_id, "product_id": product_id, "status": "active"}
        )
        data = response.json()
        return len(data.get("items", [])) > 0

    async def get_entitlements(self, user_id: str) -> list[str]:
        """Get all active product IDs for user."""
        response = await self.client.get(
            f"/v1/subscriptions",
            params={"user_id": user_id, "status": "active"}
        )
        data = response.json()
        return [item["product_id"] for item in data.get("items", [])]

    async def activate(self, license_key: str, user_id: str) -> ActivationResult:
        """Activate a license key and link to user."""
        response = await self.client.post(
            f"/v1/licenses/activate",
            json={"key": license_key, "user_id": user_id}
        )
        if response.status_code == 200:
            data = response.json()
            return ActivationResult(
                success=True,
                message="License activated successfully",
                product_id=data.get("product_id")
            )
        return ActivationResult(
            success=False,
            message=response.json().get("message", "Activation failed")
        )
```

**cache.py:**
```python
from cachetools import TTLCache
from typing import Optional

class LicenseCache:
    def __init__(self, ttl: int = 300, maxsize: int = 1000):
        self._cache = TTLCache(maxsize=maxsize, ttl=ttl)

    def _key(self, user_id: str, product_id: str) -> str:
        return f"{user_id}:{product_id}"

    def get(self, user_id: str, product_id: str) -> Optional[bool]:
        return self._cache.get(self._key(user_id, product_id))

    def set(self, user_id: str, product_id: str, valid: bool) -> None:
        self._cache[self._key(user_id, product_id)] = valid

    def invalidate(self, user_id: str) -> None:
        """Invalidate all cached entries for a user."""
        keys_to_delete = [k for k in self._cache.keys() if k.startswith(f"{user_id}:")]
        for key in keys_to_delete:
            del self._cache[key]
```

### 4. Webhook Integration

- Polar sends webhooks on: purchase, renewal, cancellation, refund
- Skills service handles webhooks to update license cache
- Immediate revocation on cancellation/refund

**Webhook endpoint:**
```python
from fastapi import APIRouter, Request, HTTPException
import hmac
import hashlib

router = APIRouter()

@router.post("/webhooks/polar")
async def handle_polar_webhook(request: Request, license_manager: LicenseManager):
    # Verify webhook signature
    signature = request.headers.get("X-Polar-Signature")
    body = await request.body()

    expected = hmac.new(
        settings.polar_webhook_secret.encode(),
        body,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = await request.json()
    await license_manager.handle_webhook(event)

    return {"status": "ok"}
```

### 5. User Flow

```
1. User tries to use paid skill (e.g., "Set up my Discord server")
2. Bot checks license via LicenseManager
3. If no license:
   Bot: "Discord Management requires a Premium subscription.
         Get it here: https://polar.sh/jameshinton/zetherion-premium
         Already subscribed? Run: /activate <license-key>"
4. If license valid:
   Bot proceeds with skill execution
```

### 6. Activation Command

```
/activate <license-key>
- Validates key with Polar API
- Links license to Discord user ID
- Stores in `user_licenses` collection
- Confirms activation
```

**Implementation:**
```python
@bot.command(name="activate")
async def activate_license(ctx, license_key: str):
    user_id = str(ctx.author.id)

    result = await license_manager.activate_license(user_id, license_key)

    if result.success:
        await ctx.reply(
            f"✅ License activated successfully!\n"
            f"You now have access to all Premium skills."
        )
    else:
        await ctx.reply(
            f"❌ Activation failed: {result.message}\n"
            f"Please check your license key and try again."
        )
```

---

## Storage

- `user_licenses` collection — Maps Discord user_id → Polar subscription data
- `license_cache` — In-memory TTL cache for validation results (5 min TTL)

---

## Security Considerations

- **License keys are secrets** — Never log full keys, only last 4 chars
- **Webhook signature validation** — Polar signs webhooks, always verify
- **Rate limit license checks** — Prevent enumeration attacks
- **Grace period** — 3-day grace period on payment failures before revocation
- **Encrypted storage** — OAuth tokens encrypted at rest (Phase 5A)

---

## Configuration

Add to `src/secureclaw/config.py`:

```python
class Settings(BaseSettings):
    # Polar.sh integration
    polar_api_key: SecretStr = Field(default="")
    polar_webhook_secret: SecretStr = Field(default="")
    polar_product_id: str = "zetherion-premium"

    # License settings
    license_cache_ttl: int = 300  # 5 minutes
    license_grace_period_days: int = 3
```

Add to `.env.example`:

```bash
# ======================================
# POLAR.SH LICENSING (Phase 6+)
# ======================================

# API key from Polar dashboard
POLAR_API_KEY=

# Webhook signing secret
POLAR_WEBHOOK_SECRET=

# Product ID for premium tier
POLAR_PRODUCT_ID=zetherion-premium
```

---

## Files to Create

- `src/secureclaw/licensing/__init__.py`
- `src/secureclaw/licensing/manager.py`
- `src/secureclaw/licensing/polar.py`
- `src/secureclaw/licensing/cache.py`
- `tests/unit/test_licensing.py`

---

## Testing

**Unit tests should cover:**
- License validation (valid, invalid, expired)
- Cache behavior (hit, miss, invalidation)
- Webhook handling (all event types)
- Activation flow (success, failure)
- Grace period logic

**Integration tests:**
- End-to-end flow with mock Polar API
- Webhook signature verification
- License → skill access gating
