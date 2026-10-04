"""Platform and tenant authorization dependencies."""
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Depends, HTTPException

from auth import get_current_admin
from db import db

PLATFORM_ADMIN_ROLE = "platform_admin"
LEGACY_ADMIN_ROLE = "admin"
TENANT_ROLES = (
    "tenant_viewer",
    "tenant_operator",
    "tenant_admin",
    "tenant_owner",
)
TENANT_ROLE_LEVELS = {role: level for level, role in enumerate(TENANT_ROLES)}


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


def _validate_tenant_role(role: str) -> None:
    if role not in TENANT_ROLE_LEVELS:
        raise ValueError(f"Unknown tenant role: {role}")


async def add_tenant_member(db, tenant_id: str, user_id: str, role: str) -> dict:
    """Create or update a user's role in a tenant membership."""
    _validate_tenant_role(role)
    collection = db.tenant_memberships
    now = datetime.now(timezone.utc)
    membership = await collection.find_one({"tenant_id": tenant_id, "user_id": user_id})

    if membership:
        await collection.update_one(
            {"_id": membership["_id"]},
            {"$set": {"role": role, "updated_at": now}},
        )
        return await collection.find_one({"_id": membership["_id"]})

    membership = {
        "_id": str(uuid4()),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "role": role,
        "created_at": now,
        "updated_at": now,
    }
    await collection.insert_one(membership)
    return await collection.find_one({"_id": membership["_id"]}) or membership


async def get_tenant_membership(db, tenant_id: str, user_id: str) -> dict | None:
    """Return one user's membership in a tenant, if it exists."""
    return await db.tenant_memberships.find_one(
        {"tenant_id": tenant_id, "user_id": user_id}
    )


async def list_tenant_members(db, tenant_id: str) -> list[dict]:
    """Return all memberships for a tenant in creation order."""
    return await db.tenant_memberships.find({"tenant_id": tenant_id}).to_list(None)


async def remove_tenant_member(db, tenant_id: str, user_id: str) -> bool:
    """Remove a user's membership from a tenant and report whether one existed."""
    result = await db.tenant_memberships.delete_one(
        {"tenant_id": tenant_id, "user_id": user_id}
    )
    return result.deleted_count == 1


def _principal_user_id(admin: dict) -> str | None:
    """Resolve the stable authenticated identifier used by membership records."""
    return admin.get("_id") or admin.get("user_id") or admin.get("email")


def require_tenant_role(tenant_id: str, min_role: str) -> Callable:
    """Return a dependency requiring a tenant membership at ``min_role`` or above."""
    _validate_tenant_role(min_role)

    async def tenant_role_required(
        admin: dict = Depends(get_current_admin),
    ) -> dict:
        user_id = _principal_user_id(admin)
        membership = (
            await get_tenant_membership(db, tenant_id, user_id) if user_id is not None else None
        )
        if (
            membership is None
            or TENANT_ROLE_LEVELS.get(membership.get("role"), -1)
            < TENANT_ROLE_LEVELS[min_role]
        ):
            raise HTTPException(status_code=403, detail="Tenant role access required")
        return admin

    return tenant_role_required


def require_tenant_membership(tenant_id: str) -> Callable:
    """Return a dependency requiring at least viewer membership in a tenant."""
    return require_tenant_role(tenant_id, "tenant_viewer")
