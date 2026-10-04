"""Offline behavioral tests for the IDSE Stage 3 migration tool."""
from copy import deepcopy
from decimal import Decimal

import pytest


class Collection:
    def __init__(self, documents=()):
        self.documents = {doc["_id"]: deepcopy(doc) for doc in documents}
        self.write_count = 0

    def find(self, query=None):
        query = query or {}
        return [deepcopy(doc) for doc in self.documents.values()
                if all(doc.get(key) == value for key, value in query.items())]

    def count_documents(self, query=None):
        return len(self.find(query))

    def update_one(self, selector, update, upsert=False):
        self.write_count += 1
        key = selector["_id"]
        if key not in self.documents and upsert:
            self.documents[key] = deepcopy(update.get("$setOnInsert", {}))
        return None


class Database:
    def __init__(self, collections=()):
        self.collections = {name: Collection(docs) for name, docs in collections}

    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())


def tool(source, target, *, dry_run=True):
    from idse_stage3_migration import IDSEStage3Migration
    return IDSEStage3Migration(source, target, dry_run=dry_run, environment="development")


def test_dry_run_returns_plan_without_target_writes():
    source = Database([("products", [{"_id": "p1", "name": "Plan", "custom_business_field": "kept"}])])
    target = Database()

    report = tool(source, target).run()

    assert report["dry_run"] is True
    assert report["collections"]["products"]["planned"] == 1
    assert target["products"].write_count == 0
    assert target["migration_progress"].write_count == 0


def test_execute_is_idempotent_and_does_not_overwrite_existing_target_document():
    source = Database([("products", [{"_id": "p1", "name": "Source"}])])
    target = Database([("products", [{"_id": "p1", "name": "Existing", "tenant_id": "idse"}])])

    first = tool(source, target, dry_run=False).run()
    second = tool(source, target, dry_run=False).run()

    assert first["collections"]["products"] == {"migrated": 0, "skipped": 1, "planned": 1}
    assert second["collections"]["products"]["skipped"] == 1
    assert target["products"].documents["p1"]["name"] == "Existing"


def test_secret_collection_is_refused_and_never_planned():
    source = Database([("tg_accounts", [{"_id": "a1", "session_encrypted": "never-copy"}])])
    target = Database()

    report = tool(source, target).run()

    assert "tg_accounts" not in report["collections"]
    assert "tg_accounts" in report["refused_collections"]
    assert target["tg_accounts"].write_count == 0


def test_migrated_business_document_is_tenant_tagged_and_secret_fields_are_stripped():
    source = Database([("inventory_items", [{"_id": "i1", "product_id": "p1", "secret": "credential", "note": "business"}])])
    target = Database()

    tool(source, target, dry_run=False).run()

    result = target["inventory_items"].documents["i1"]
    assert result["tenant_id"] == "idse"
    assert result["note"] == "business"
    assert "secret" not in result


def test_reconciliation_mismatch_fails_explicit_verification():
    from idse_stage3_migration import ReconciliationMismatch, verify_reconciliation

    source = Database([("purchases", [{"_id": "p1", "total": "12.50"}]), ("deposits", [{"_id": "d1", "amount": "2"}])])
    target = Database([("purchases", [{"_id": "p1", "tenant_id": "idse", "total": "11.50"}]), ("deposits", [{"_id": "d1", "tenant_id": "idse", "amount": "2"}])])

    report = tool(source, target).financial_reconciliation()

    assert report["source"]["purchases"]["total"] == Decimal("12.50")
    assert report["target"]["purchases"]["total"] == Decimal("11.50")
    assert report["source"]["deposits"]["credited_amount"] == Decimal("0")
    with pytest.raises(ReconciliationMismatch):
        verify_reconciliation(report)


def test_counter_seed_uses_highest_invoice_sequence_or_source_counter_never_lower():
    source = Database([
        ("purchases", [{"_id": "p1", "invoice_id": "INV-20261004-0007"}, {"_id": "p2", "invoice_id": "idse-12"}]),
        ("counters", [{"_id": "invoice:20261004", "seq": 19}]),
    ])
    target = Database()

    result = tool(source, target, dry_run=False).seed_invoice_counter()

    assert result["value"] == 19
    assert target["counters"].documents["idse:invoice"] == {
        "_id": "idse:invoice", "tenant_id": "idse", "counter_name": "invoice", "value": 19
    }


def test_rejects_production_and_port_27017():
    from idse_stage3_migration import MigrationSafetyError
    from idse_stage3_migration import IDSEStage3Migration

    with pytest.raises(MigrationSafetyError):
        IDSEStage3Migration(Database(), Database(), environment="production")
    with pytest.raises(MigrationSafetyError):
        IDSEStage3Migration(Database(), Database(), target_uri="mongodb://localhost:27017/forbidden")
