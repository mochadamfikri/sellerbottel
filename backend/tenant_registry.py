"""In-memory registry for platform tenant metadata."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping
from uuid import uuid4

from tenant_db import resolve_platform_database_name, tenant_database_name, validate_tenant_id


VALID_STATUSES = frozenset({"active", "suspended"})


class TenantRegistry:
    """Store tenant records in memory for the configured platform database."""

    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self.platform_database_name = resolve_platform_database_name(environment or {})
        self._tenants: dict[str, dict[str, object]] = {}

    def create_tenant(self, slug: str, name: str) -> dict[str, object]:
        if slug in self._tenants:
            raise ValueError(f"tenant slug '{slug}' already exists")

        tenant = {
            "_id": str(uuid4()),
            "slug": slug,
            "name": name,
            "status": "active",
            "database_name": tenant_database_name(slug, self.platform_database_name),
            "created_at": datetime.now(timezone.utc),
        }
        self._tenants[slug] = tenant
        return tenant.copy()

    def get_tenant(self, slug: str) -> dict[str, object] | None:
        tenant = self._tenants.get(slug)
        return tenant.copy() if tenant is not None else None

    def clear(self) -> None:
        """Clear registered tenants; intended for isolated tests."""
        self._tenants.clear()

    def list_tenants(self) -> list[dict[str, object]]:
        return [tenant.copy() for tenant in self._tenants.values()]

    def update_status(self, slug: str, status: str) -> dict[str, object]:
        if status not in VALID_STATUSES:
            raise ValueError(f"unsupported tenant status: {status}")
        tenant = self._tenants.get(slug)
        if tenant is None:
            raise KeyError(f"tenant slug '{slug}' was not found")
        tenant["status"] = status
        return tenant.copy()

    def ensure_indexes(self) -> dict[str, dict[str, bool]]:
        """Return the unique index contract used by this in-memory registry."""
        return {"slug": {"unique": True}}
