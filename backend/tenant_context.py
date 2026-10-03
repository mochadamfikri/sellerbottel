"""Development-only tenant resolution from the ``X-Tenant-ID`` header."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import Header, HTTPException

from tenant_db import tenant_database_name, validate_tenant_id


@dataclass(frozen=True)
class TenantContext:
    """A resolved tenant identity and its isolated database handle."""

    tenant_id: str
    database: Any
    status: str


# These explicit development hooks keep resolution deterministic and make the
# future platform-registry integration a request-boundary change only.
_development_tenants: Mapping[str, Mapping[str, str]] = {}
_development_database_client: Any = None


def configure_development_tenant_resolver(
    tenants: Mapping[str, Mapping[str, str]], database_client: Any,
) -> None:
    """Configure the development-only source used by the FastAPI dependency."""
    global _development_tenants, _development_database_client
    _development_tenants = tenants
    _development_database_client = database_client


def resolve_tenant_context(
    tenant_id: str,
    tenants: Mapping[str, Mapping[str, str]],
    database_client: Any,
) -> TenantContext:
    """Resolve a tenant record and construct its canonical database handle."""
    try:
        validated_tenant_id = validate_tenant_id(tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid X-Tenant-ID header") from exc

    tenant = tenants.get(validated_tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Unknown tenant")

    status = tenant.get("status", "")
    if status != "active":
        raise HTTPException(status_code=403, detail="Tenant is not active")

    database_name = tenant.get("database_name") or tenant_database_name(validated_tenant_id)
    return TenantContext(
        tenant_id=validated_tenant_id,
        database=database_client[database_name],
        status=status,
    )


def get_tenant_context(
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> TenantContext:
    """FastAPI dependency resolving ``X-Tenant-ID`` in development only."""
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header is required")

    if _development_database_client is None:
        from db import client

        database_client = client
    else:
        database_client = _development_database_client

    return resolve_tenant_context(x_tenant_id, _development_tenants, database_client)
