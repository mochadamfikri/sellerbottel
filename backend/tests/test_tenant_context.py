from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tenant_context import get_tenant_context, resolve_tenant_context


class DatabaseClient:
    def __getitem__(self, database_name):
        return {"database_name": database_name}


def test_resolve_tenant_context_validates_header_before_lookup():
    with pytest.raises(HTTPException) as exc_info:
        resolve_tenant_context("tenant/db", {}, DatabaseClient())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid X-Tenant-ID header"


def test_get_tenant_context_rejects_missing_header():
    with pytest.raises(HTTPException) as exc_info:
        get_tenant_context(None)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "X-Tenant-ID header is required"


def test_resolve_tenant_context_rejects_unknown_tenant():
    with pytest.raises(HTTPException) as exc_info:
        resolve_tenant_context("unknown", {}, DatabaseClient())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Unknown tenant"


def test_resolve_tenant_context_creates_database_handle_for_active_tenant():
    tenants = {
        "acme-shop": {
            "slug": "acme-shop",
            "database_name": "sellerbottel_tenant_acme_shop",
            "status": "active",
        }
    }

    context = resolve_tenant_context("acme-shop", tenants, DatabaseClient())

    assert context.tenant_id == "acme-shop"
    assert context.database == {"database_name": "sellerbottel_tenant_acme_shop"}
    assert context.status == "active"
