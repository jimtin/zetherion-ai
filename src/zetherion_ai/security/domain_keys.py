"""Owner/tenant encryption-domain helpers."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from zetherion_ai.logging import get_logger
from zetherion_ai.security.encryption import KEY_SIZE, NONCE_SIZE, FieldEncryptor
from zetherion_ai.security.keys import KeyManager

log = get_logger("zetherion_ai.security.domain_keys")


class EncryptionDomain(StrEnum):
    """Canonical encryption domains for runtime storage."""

    OWNER_PERSONAL = "owner_personal"
    TENANT_DATA = "tenant_data"


class TenantKeyEnvelopeService:
    """Wrap and unwrap per-tenant data keys under the tenant master key."""

    def __init__(self, wrapping_key: bytes) -> None:
        if len(wrapping_key) != KEY_SIZE:
            raise ValueError(f"Wrapping key must be exactly {KEY_SIZE} bytes")
        self._aesgcm = AESGCM(wrapping_key)

    def generate_tenant_key(self) -> bytes:
        """Generate a fresh per-tenant data key."""

        return os.urandom(KEY_SIZE)

    def wrap_key(self, tenant_key: bytes) -> str:
        """Encrypt a tenant key into a base64 envelope."""

        if len(tenant_key) != KEY_SIZE:
            raise ValueError(f"Tenant key must be exactly {KEY_SIZE} bytes")
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = self._aesgcm.encrypt(nonce, tenant_key, None)
        return base64.b64encode(nonce + ciphertext).decode("ascii")

    def unwrap_key(self, envelope: str) -> bytes:
        """Decrypt a base64-wrapped tenant key."""

        decoded = base64.b64decode(envelope.encode("ascii"))
        nonce = decoded[:NONCE_SIZE]
        ciphertext = decoded[NONCE_SIZE:]
        tenant_key = self._aesgcm.decrypt(nonce, ciphertext, None)
        if len(tenant_key) != KEY_SIZE:
            raise ValueError("Wrapped tenant key decoded to an unexpected length")
        return tenant_key


@dataclass(frozen=True)
class DomainKeyMaterial:
    """Derived key material for one encryption domain."""

    domain: EncryptionDomain
    salt_path: str
    key_manager: KeyManager
    encryptor: FieldEncryptor


@dataclass(frozen=True)
class RuntimeEncryptors:
    """Runtime encryptor bundle for owner and tenant data."""

    owner_personal: FieldEncryptor
    tenant_data: FieldEncryptor
    owner_personal_salt_path: str
    tenant_data_salt_path: str
    tenant_key_envelope: TenantKeyEnvelopeService


class DomainKeyProvider:
    """Build domain-specific encryptors with backward-compatible fallbacks."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    def _secret_value(self, candidate: Any) -> str:
        if candidate is None:
            return ""
        getter = getattr(candidate, "get_secret_value", None)
        if callable(getter):
            return str(getter()).strip()
        return str(candidate).strip()

    def _fallback_secret(self, *names: str) -> str:
        for name in names:
            value = self._secret_value(getattr(self._settings, name, None))
            if value:
                return value
        return ""

    def passphrase_for_domain(self, domain: EncryptionDomain) -> str:
        """Return the configured passphrase for one encryption domain."""

        if domain == EncryptionDomain.OWNER_PERSONAL:
            return self._fallback_secret("encryption_owner_passphrase", "encryption_passphrase")
        return self._fallback_secret("encryption_tenant_passphrase", "encryption_passphrase")

    def salt_path_for_domain(self, domain: EncryptionDomain) -> str:
        """Return the salt path for one encryption domain."""

        if domain == EncryptionDomain.OWNER_PERSONAL:
            configured = str(
                getattr(self._settings, "encryption_owner_salt_path", "") or ""
            ).strip()
        else:
            configured = str(
                getattr(self._settings, "encryption_tenant_salt_path", "") or ""
            ).strip()
        if configured:
            return configured
        return str(getattr(self._settings, "encryption_salt_path", "data/salt.bin"))

    def build_material(
        self,
        domain: EncryptionDomain,
        *,
        strict: bool = False,
        sensitive_fields: set[str] | None = None,
    ) -> DomainKeyMaterial:
        """Build a key manager and encryptor for one domain."""

        passphrase = self.passphrase_for_domain(domain)
        if not passphrase:
            raise ValueError(f"Missing passphrase for encryption domain: {domain.value}")
        salt_path = self.salt_path_for_domain(domain)
        key_manager = KeyManager(passphrase=passphrase, salt_path=salt_path)
        encryptor = FieldEncryptor(
            key=key_manager.key,
            sensitive_fields=sensitive_fields,
            strict=strict,
        )
        log.info(
            "domain_key_material_built",
            domain=domain.value,
            salt_path=salt_path,
            strict=strict,
        )
        return DomainKeyMaterial(
            domain=domain,
            salt_path=salt_path,
            key_manager=key_manager,
            encryptor=encryptor,
        )


def build_runtime_encryptors(settings: Any) -> RuntimeEncryptors:
    """Build the owner-personal and tenant-data runtime encryptors."""

    provider = DomainKeyProvider(settings)
    strict = bool(getattr(settings, "encryption_strict", False))
    owner_material = provider.build_material(EncryptionDomain.OWNER_PERSONAL, strict=strict)
    tenant_material = provider.build_material(EncryptionDomain.TENANT_DATA, strict=strict)
    return RuntimeEncryptors(
        owner_personal=owner_material.encryptor,
        tenant_data=tenant_material.encryptor,
        owner_personal_salt_path=owner_material.salt_path,
        tenant_data_salt_path=tenant_material.salt_path,
        tenant_key_envelope=TenantKeyEnvelopeService(tenant_material.key_manager.key),
    )
