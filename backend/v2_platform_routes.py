"""V2 platform-control-plane tenant registry routes."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from audit_events import write_audit_event
from db import db
from platform_rbac import (
    add_tenant_member,
    list_tenant_members,
    remove_tenant_member,
    require_platform_admin,
)
from tenant_registry import TenantRegistry

router = APIRouter(prefix="/api/v2/platform", tags=["v2-platform"])
tenant_registry = TenantRegistry(environment=os.environ)


class CreateTenantBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    slug: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)


class UpdateTenantStatusBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["active", "suspended"]


class AddTenantMemberBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_id: str = Field(min_length=1, max_length=200)
    role: Literal["tenant_viewer", "tenant_operator", "tenant_admin", "tenant_owner"]


class UpdateTenantPlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: Literal["demo", "monthly", "yearly", "lifetime"]
    quotas: dict[str, int] = Field(default_factory=dict)


class ProvisionTenantResponse(BaseModel):
    provisioned: bool
    database_name: str


class TenantMemberResponse(BaseModel):
    tenant_id: str
    user_id: str
    role: str


class TenantPlanResponse(BaseModel):
    plan: Literal["demo", "monthly", "yearly", "lifetime"]
    quotas: dict[str, int]


class TenantResponse(BaseModel):
    id: str
    slug: str
    name: str
    status: Literal["active", "suspended"]
    database_name: str
    created_at: datetime


def _tenant_response(tenant: dict[str, object]) -> TenantResponse:
    """Expose tenant metadata only; never return database credentials."""
    return TenantResponse(
        id=str(tenant["_id"]),
        slug=str(tenant["slug"]),
        name=str(tenant["name"]),
        status=str(tenant["status"]),
        database_name=str(tenant["database_name"]),
        created_at=tenant["created_at"],  # type: ignore[arg-type]
    )


def _find_tenant(tenant_id: str) -> dict[str, object] | None:
    return next(
        (
            tenant
            for tenant in tenant_registry.list_tenants()
            if str(tenant["_id"]) == tenant_id
        ),
        None,
    )


def _actor_id(admin: dict) -> str:
    return str(admin.get("_id") or admin.get("user_id") or admin.get("email"))


async def _audit(admin: dict, action: str, tenant_id: str, metadata: dict[str, Any]) -> None:
    await write_audit_event(
        actor=_actor_id(admin),
        action=action,
        tenant_id=tenant_id,
        platform="control-plane",
        metadata=metadata,
    )


def _require_tenant(tenant_id: str) -> dict[str, object]:
    tenant = _find_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


def _member_response(member: dict[str, Any]) -> TenantMemberResponse:
    return TenantMemberResponse(
        tenant_id=str(member["tenant_id"]), user_id=str(member["user_id"]), role=str(member["role"])
    )


async def _provision_database(database_name: str) -> None:
    """Create the tenant database boundary by touching its database handle."""
    await db.client[database_name].command("ping")


@router.post(
    "/tenants",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant(
    body: CreateTenantBody,
    admin: dict = Depends(require_platform_admin),
) -> TenantResponse:
    try:
        tenant = tenant_registry.create_tenant(slug=body.slug, name=body.name)
    except ValueError as error:
        detail = str(error)
        if "already exists" in detail:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from error
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail) from error
    
    await _audit(admin, "tenant.created", str(tenant["_id"]), {"slug": body.slug, "name": body.name})
    
    return _tenant_response(tenant)


@router.get("/tenants", response_model=list[TenantResponse])
async def list_tenants(
    _: dict = Depends(require_platform_admin),
) -> list[TenantResponse]:
    return [_tenant_response(tenant) for tenant in tenant_registry.list_tenants()]


@router.get("/tenants/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: str,
    _: dict = Depends(require_platform_admin),
) -> TenantResponse:
    tenant = _find_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return _tenant_response(tenant)


@router.patch("/tenants/{tenant_id}/status", response_model=TenantResponse)
async def update_tenant_status(
    tenant_id: str,
    body: UpdateTenantStatusBody,
    admin: dict = Depends(require_platform_admin),
) -> TenantResponse:
    tenant = _find_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    updated = tenant_registry.update_status(str(tenant["slug"]), body.status)
    await _audit(admin, "tenant.status_updated", tenant_id, {"status": body.status})
    return _tenant_response(updated)


@router.post(
    "/tenants/{tenant_id}/provision",
    response_model=ProvisionTenantResponse,
)
async def provision_tenant(
    tenant_id: str,
    admin: dict = Depends(require_platform_admin),
) -> ProvisionTenantResponse:
    tenant = _require_tenant(tenant_id)
    database_name = str(tenant["database_name"])
    
    await _provision_database(database_name)
    tenant_registry.mark_provisioned(str(tenant["slug"]))
    
    await _audit(admin, "tenant.provisioned", tenant_id, {"database_name": database_name})
    
    return ProvisionTenantResponse(provisioned=True, database_name=database_name)


@router.post(
    "/tenants/{tenant_id}/members",
    response_model=TenantMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    tenant_id: str,
    body: AddTenantMemberBody,
    admin: dict = Depends(require_platform_admin),
) -> TenantMemberResponse:
    _require_tenant(tenant_id)
    
    try:
        member = await add_tenant_member(db, tenant_id, body.user_id, body.role)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    
    await _audit(admin, "tenant.member_added", tenant_id, {"user_id": body.user_id, "role": body.role})
    
    return _member_response(member)


@router.get("/tenants/{tenant_id}/members", response_model=list[TenantMemberResponse])
async def list_members(
    tenant_id: str,
    _: dict = Depends(require_platform_admin),
) -> list[TenantMemberResponse]:
    _require_tenant(tenant_id)
    members = await list_tenant_members(db, tenant_id)
    return [_member_response(member) for member in members]


@router.delete("/tenants/{tenant_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    tenant_id: str,
    user_id: str,
    admin: dict = Depends(require_platform_admin),
) -> Response:
    _require_tenant(tenant_id)
    
    removed = await remove_tenant_member(db, tenant_id, user_id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    
    await _audit(admin, "tenant.member_removed", tenant_id, {"user_id": user_id})
    
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/tenants/{tenant_id}/plan", response_model=TenantPlanResponse)
async def update_plan(
    tenant_id: str,
    body: UpdateTenantPlanBody,
    admin: dict = Depends(require_platform_admin),
) -> TenantPlanResponse:
    tenant = _require_tenant(tenant_id)
    
    try:
        tenant_registry.update_plan(str(tenant["slug"]), body.plan, body.quotas)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    
    await _audit(
        admin, "tenant.plan_updated", tenant_id, {"plan": body.plan, "quotas": body.quotas}
    )
    
    return TenantPlanResponse(plan=body.plan, quotas=body.quotas)


@router.get("/health")
async def health() -> dict[str, str]:
    """Report that the V2 platform API is mounted."""
    return {"status": "ok", "scope": "platform"}
