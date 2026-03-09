"""Deterministic policy engine for announcement routing decisions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from zetherion_ai.announcements.storage import (
    AnnouncementEventInput,
    AnnouncementRecipient,
    AnnouncementRepository,
    AnnouncementSeverity,
    resolve_announcement_recipient,
)
from zetherion_ai.config import get_dynamic

SettingResolver = Callable[[str, str, Any], Any]


@dataclass
class ResolvedAnnouncementPreferences:
    """User preference view after source-order resolution."""

    timezone: str
    digest_enabled: bool
    digest_window_local: str
    immediate_categories: list[str] = field(default_factory=list)
    muted_categories: list[str] = field(default_factory=list)
    max_immediate_per_hour: int = 6
    quiet_start_hour: int | None = None
    quiet_end_hour: int | None = None


@dataclass
class AnnouncementPolicyDecision:
    """Policy decision returned to producers/API callers."""

    status: str
    delivery_mode: str
    severity: AnnouncementSeverity
    scheduled_for: datetime | None
    reason_code: str
    suppression_id: int | None = None
    preferences: ResolvedAnnouncementPreferences | None = None


class AnnouncementPolicyEngine:
    """Apply deterministic routing policy for announcement events."""

    DEFAULT_CATEGORY_SEVERITY: dict[str, AnnouncementSeverity] = {
        "provider.billing": AnnouncementSeverity.HIGH,
        "provider.auth": AnnouncementSeverity.HIGH,
        "provider.rate_limit": AnnouncementSeverity.HIGH,
        "deploy.failed": AnnouncementSeverity.CRITICAL,
        "promotions.failed": AnnouncementSeverity.CRITICAL,
        "security.critical": AnnouncementSeverity.CRITICAL,
        "update.available": AnnouncementSeverity.NORMAL,
        "skill.reminder": AnnouncementSeverity.NORMAL,
        "insight.summary": AnnouncementSeverity.NORMAL,
    }

    def __init__(
        self,
        repository: AnnouncementRepository,
        *,
        setting_resolver: SettingResolver | None = None,
    ) -> None:
        self._repository = repository
        self._setting_resolver = setting_resolver or get_dynamic

    async def evaluate_event(
        self,
        event: AnnouncementEventInput,
        *,
        personal_profile: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> AnnouncementPolicyDecision:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)

        category = str(event.category).strip().lower()
        severity = self._resolve_severity(event, category=category)
        recipient = resolve_announcement_recipient(event)
        if self._recipient_uses_user_preferences(recipient):
            preferences = await self._resolve_preferences(
                user_id=int(recipient.target_user_id or 0),
                personal_profile=personal_profile,
            )
        else:
            preferences = self._default_preferences()

        if category in preferences.muted_categories:
            return AnnouncementPolicyDecision(
                status="deferred",
                delivery_mode="deferred",
                severity=severity,
                scheduled_for=None,
                reason_code="muted_category",
                preferences=preferences,
            )

        fingerprint = self._event_fingerprint(event)
        suppression = await self._repository.upsert_suppression_observation(
            source=str(event.source).strip(),
            category=category,
            target_user_id=int(recipient.target_user_id or 0),
            recipient_key=recipient.routing_key,
            fingerprint=fingerprint,
            seen_at=current,
        )

        if suppression.next_allowed_at is not None and suppression.next_allowed_at > current:
            return AnnouncementPolicyDecision(
                status="deferred",
                delivery_mode="deferred",
                severity=severity,
                scheduled_for=suppression.next_allowed_at,
                reason_code="suppression_cooldown_active",
                suppression_id=suppression.id,
                preferences=preferences,
            )

        if not self._recipient_uses_user_preferences(recipient):
            mode = "immediate"
            reason = "recipient_channel_immediate_default"
            if severity is AnnouncementSeverity.CRITICAL:
                reason = "critical_immediate"
        else:
            mode = "digest"
            reason = "digest_window"
            if severity is AnnouncementSeverity.CRITICAL:
                mode = "immediate"
                reason = "critical_immediate"
            elif category in preferences.immediate_categories:
                mode = "immediate"
                reason = "user_immediate_category"

        if (
            mode == "immediate"
            and severity is not AnnouncementSeverity.CRITICAL
            and self._recipient_uses_user_preferences(recipient)
        ):
            is_quiet = self._is_quiet_hours(current=current, preferences=preferences)
            if is_quiet:
                mode = "digest"
                reason = "quiet_hours_digest_fallback"
            else:
                recent = await self._repository.count_recent_events(
                    target_user_id=int(recipient.target_user_id or 0),
                    recipient_key=recipient.routing_key,
                    since=current - timedelta(hours=1),
                    categories=preferences.immediate_categories or [category],
                )
                if recent >= max(1, preferences.max_immediate_per_hour):
                    mode = "digest"
                    reason = "rate_limited_to_digest"

        if mode == "immediate":
            cooldown_seconds = max(
                1,
                int(
                    self._setting_resolver(
                        "notifications",
                        "announcement_suppression_cooldown_seconds",
                        3600,
                    )
                ),
            )
            await self._repository.mark_suppression_notified(
                suppression_id=suppression.id,
                notified_at=current,
                cooldown_seconds=cooldown_seconds,
            )
            return AnnouncementPolicyDecision(
                status="scheduled",
                delivery_mode="immediate",
                severity=severity,
                scheduled_for=current,
                reason_code=reason,
                suppression_id=suppression.id,
                preferences=preferences,
            )

        if not preferences.digest_enabled:
            return AnnouncementPolicyDecision(
                status="deferred",
                delivery_mode="deferred",
                severity=severity,
                scheduled_for=None,
                reason_code="digest_disabled",
                suppression_id=suppression.id,
                preferences=preferences,
            )

        scheduled_for = self._next_digest_time(current=current, preferences=preferences)
        return AnnouncementPolicyDecision(
            status="scheduled",
            delivery_mode="digest",
            severity=severity,
            scheduled_for=scheduled_for,
            reason_code=reason,
            suppression_id=suppression.id,
            preferences=preferences,
        )

    def _resolve_severity(
        self,
        event: AnnouncementEventInput,
        *,
        category: str,
    ) -> AnnouncementSeverity:
        severity = AnnouncementSeverity.coerce(event.severity)
        mapped = self.DEFAULT_CATEGORY_SEVERITY.get(category)
        if severity is AnnouncementSeverity.NORMAL and mapped is not None:
            return mapped
        return severity

    async def _resolve_preferences(
        self,
        *,
        user_id: int,
        personal_profile: dict[str, Any] | None,
    ) -> ResolvedAnnouncementPreferences:
        global_defaults = self._global_defaults()
        stored = await self._repository.get_user_preferences(user_id, with_defaults=False)

        if personal_profile is None:
            personal_profile = await self._repository.get_personal_profile_preferences(user_id)
        personal_values = self._personal_profile_values(personal_profile or {})

        return ResolvedAnnouncementPreferences(
            timezone=str(
                personal_values.get("timezone")
                or (stored.timezone if stored is not None else "")
                or global_defaults["timezone"]
            ),
            digest_enabled=self._as_bool(
                personal_values.get("digest_enabled"),
                (
                    stored.digest_enabled
                    if stored is not None
                    else self._as_bool(global_defaults["digest_enabled"], True)
                ),
            ),
            digest_window_local=str(
                personal_values.get("digest_window_local")
                or (stored.digest_window_local if stored is not None else "")
                or global_defaults["digest_window_local"]
            ),
            immediate_categories=self._coerce_list(
                personal_values.get("immediate_categories"),
                (
                    stored.immediate_categories
                    if stored is not None
                    else self._coerce_list(global_defaults["immediate_categories"], [])
                ),
            ),
            muted_categories=self._coerce_list(
                personal_values.get("muted_categories"),
                (
                    stored.muted_categories
                    if stored is not None
                    else self._coerce_list(global_defaults["muted_categories"], [])
                ),
            ),
            max_immediate_per_hour=max(
                1,
                self._as_int(
                    personal_values.get("max_immediate_per_hour"),
                    (
                        stored.max_immediate_per_hour
                        if stored is not None
                        else self._as_int(global_defaults["max_immediate_per_hour"], 6)
                    ),
                ),
            ),
            quiet_start_hour=self._as_optional_hour(personal_values.get("quiet_start_hour")),
            quiet_end_hour=self._as_optional_hour(personal_values.get("quiet_end_hour")),
        )

    def _global_defaults(self) -> dict[str, Any]:
        return {
            "timezone": str(
                self._setting_resolver("notifications", "announcement_timezone_default", "UTC")
            ),
            "digest_enabled": self._setting_resolver(
                "notifications",
                "announcement_digest_enabled_default",
                True,
            ),
            "digest_window_local": str(
                self._setting_resolver("scheduler", "announcement_digest_window_local", "09:00")
            ),
            "immediate_categories": self._setting_resolver(
                "notifications",
                "announcement_immediate_categories_default",
                [],
            ),
            "muted_categories": self._setting_resolver(
                "notifications",
                "announcement_muted_categories_default",
                [],
            ),
            "max_immediate_per_hour": self._setting_resolver(
                "notifications",
                "announcement_max_immediate_per_hour_default",
                6,
            ),
        }

    def _default_preferences(self) -> ResolvedAnnouncementPreferences:
        global_defaults = self._global_defaults()
        return ResolvedAnnouncementPreferences(
            timezone=str(global_defaults["timezone"]),
            digest_enabled=self._as_bool(global_defaults["digest_enabled"], True),
            digest_window_local=str(global_defaults["digest_window_local"]),
            immediate_categories=self._coerce_list(global_defaults["immediate_categories"], []),
            muted_categories=self._coerce_list(global_defaults["muted_categories"], []),
            max_immediate_per_hour=max(
                1,
                self._as_int(global_defaults["max_immediate_per_hour"], 6),
            ),
        )

    @staticmethod
    def _recipient_uses_user_preferences(recipient: AnnouncementRecipient) -> bool:
        return recipient.channel == "discord_dm" and int(recipient.target_user_id or 0) > 0

    @staticmethod
    def _personal_profile_values(profile: dict[str, Any]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        timezone = profile.get("timezone")
        if isinstance(timezone, str) and timezone.strip():
            values["timezone"] = timezone.strip()

        raw_prefs = profile.get("preferences")
        if isinstance(raw_prefs, str):
            try:
                raw_prefs = json.loads(raw_prefs)
            except json.JSONDecodeError:
                raw_prefs = {}
        prefs = raw_prefs if isinstance(raw_prefs, dict) else {}
        announcements = prefs.get("announcements")
        announcement_prefs = announcements if isinstance(announcements, dict) else {}

        for key in (
            "digest_enabled",
            "digest_window_local",
            "immediate_categories",
            "muted_categories",
            "max_immediate_per_hour",
        ):
            if key in announcement_prefs:
                values[key] = announcement_prefs.get(key)

        quiet = prefs.get("quiet_hours")
        if isinstance(quiet, dict):
            if "start_hour" in quiet:
                values["quiet_start_hour"] = quiet.get("start_hour")
            if "end_hour" in quiet:
                values["quiet_end_hour"] = quiet.get("end_hour")
        return values

    @staticmethod
    def _event_fingerprint(event: AnnouncementEventInput) -> str:
        explicit = str(event.fingerprint or "").strip()
        if explicit:
            return explicit
        recipient = resolve_announcement_recipient(event)
        base = "|".join(
            [
                str(event.source).strip(),
                str(event.category).strip().lower(),
                recipient.routing_key,
                str(event.title).strip(),
                str(event.body).strip(),
            ]
        )
        return hashlib.sha256(base.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_quiet_hours(
        *,
        current: datetime,
        preferences: ResolvedAnnouncementPreferences,
    ) -> bool:
        if preferences.quiet_start_hour is None or preferences.quiet_end_hour is None:
            return False
        try:
            local_now = current.astimezone(ZoneInfo(preferences.timezone))
        except Exception:
            local_now = current
        hour = local_now.hour
        start = preferences.quiet_start_hour
        end = preferences.quiet_end_hour
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    @staticmethod
    def _next_digest_time(
        *,
        current: datetime,
        preferences: ResolvedAnnouncementPreferences,
    ) -> datetime:
        try:
            zone = ZoneInfo(preferences.timezone)
        except Exception:
            zone = ZoneInfo("UTC")
        local_now = current.astimezone(zone)
        hour, minute = AnnouncementPolicyEngine._parse_digest_window(
            preferences.digest_window_local
        )
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local_now:
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)

    @staticmethod
    def _parse_digest_window(value: str) -> tuple[int, int]:
        parts = str(value or "").strip().split(":")
        if len(parts) != 2:
            return 9, 0
        try:
            hour = max(0, min(23, int(parts[0])))
            minute = max(0, min(59, int(parts[1])))
        except ValueError:
            return 9, 0
        return hour, minute

    @staticmethod
    def _coerce_list(value: Any, default: list[str] | None = None) -> list[str]:
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed.startswith("["):
                try:
                    value = json.loads(trimmed)
                except json.JSONDecodeError:
                    value = [piece.strip() for piece in trimmed.split(",")]
            else:
                value = [piece.strip() for piece in trimmed.split(",")]
        if isinstance(value, list):
            out = [str(item).strip().lower() for item in value if str(item).strip()]
            return out
        return list(default or [])

    @staticmethod
    def _as_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return default

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _as_optional_hour(value: Any) -> int | None:
        if value is None:
            return None
        try:
            hour = int(value)
        except (TypeError, ValueError):
            return None
        if 0 <= hour <= 23:
            return hour
        return None
