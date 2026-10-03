"""Platform and tenant authorization dependencies.

Tenant membership enforcement is intentionally deferred to Phase 2. Until then,
``require_tenant_membership`` preserves the authenticated principal so routes can
adopt a stable dependency interface without changing tokens or authorization data.
"""
from collections.abc import Callable

from fastapi import Depends, HTTPException

from auth import get_current_admin

PLATFORM_ADMIN_ROLE = "platform_admin"
LEGACY_ADMIN_ROLE = "admin"


def is_platform_admin(admin: dict) -> bool:
    """Return whether an authenticated principal has platform-wide access.

    ``role='admin'`` without a ``platform_role`` is accepted temporarily for
    records created before platform roles were introduced. An explicit non-platform
    role always takes precedence and is denied.
    """
    platform_role = admin.get("platform_role")
    if platform_role is not None:
        return platform_role == PLATFORM_ADMIN_ROLE
    return admin.get("role") == LEGACY_ADMIN_ROLE


async def require_platform_admin(
    admin: dict = Depends(get_current_admin),
) -> dict:
    """Require platform-wide administrator privileges."""
    if not is_platform_admin(admin):
        raise HTTPException(status_code=403, detail="Platform admin access required")
    return admin


def require_tenant_membership(tenant_id: str) -> Callable:
    """Return a Phase 2-ready tenant-membership dependency.

    The tenant ID is captured now to provide a stable route-facing API. Membership
    lookup and tenant role enforcement will be implemented in Phase 2.
    """
    async def tenant_membership_stub(
        admin: dict = Depends(get_current_admin),
    ) -> dict:
        _ = tenant_id
        return admin

    return tenant_membership_stub
