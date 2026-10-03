"""V2 platform-control-plane tenant registry routes."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from platform_rbac import require_platform_admin
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


@router.post(
    "/tenants",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant(
    body: CreateTenantBody,
    _: dict = Depends(require_platform_admin),
) -> TenantResponse:
    try:
        tenant = tenant_registry.create_tenant(slug=body.slug, name=body.name)
    except ValueError as error:
        detail = str(error)
        if "already exists" in detail:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from error
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail) from error
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
    _: dict = Depends(require_platform_admin),
) -> TenantResponse:
    tenant = _find_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    updated = tenant_registry.update_status(str(tenant["slug"]), body.status)
    return _tenant_response(updated)


@router.get("/health")
async def health() -> dict[str, str]:
    """Report that the V2 platform API is mounted."""
    return {"status": "ok", "scope": "platform"}
