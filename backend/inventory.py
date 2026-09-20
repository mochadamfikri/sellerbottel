import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Iterable

from cryptography.fernet import Fernet
from db import db


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _fernet():
    key = os.environ.get("INVENTORY_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError("INVENTORY_ENCRYPTION_KEY wajib di-set untuk inventory.")
    return Fernet(key.encode())


def normalize_schema(schema: Iterable[str]) -> list[str]:
    result = []
    seen = set()
    for raw in schema or []:
        field = str(raw or "").strip()
        if not field or field in seen:
            continue
        seen.add(field)
        result.append(field)
    return result


def normalize_record(record: dict, schema: list[str] | None = None):
    schema = normalize_schema(schema or list(record.keys()))
    clean = {}
    for field in schema:
        value = record.get(field, "")
        if value is None:
            value = ""
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        clean[field] = str(value).strip()
    if not clean or not any(value for value in clean.values()):
        return None, None, schema
    canonical = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return clean, fingerprint, schema


async def validate_records(product_id: str, records: list[dict], schema: list[str]):
    schema = normalize_schema(schema)
    normalized = []
    seen = set()
    for record in records:
        clean, fingerprint, _ = normalize_record(record, schema)
        if not clean or fingerprint in seen:
            continue
        seen.add(fingerprint)
        normalized.append((clean, fingerprint))

    fingerprints = [fingerprint for _, fingerprint in normalized]
    existing = set()
    if fingerprints:
        cursor = db.inventory_items.find(
            {
                "product_id": product_id,
                "fingerprint": {"$in": fingerprints},
            },
            {"fingerprint": 1},
        )
        existing = {doc["fingerprint"] async for doc in cursor}

    valid = [record for record, fingerprint in normalized if fingerprint not in existing]
    duplicates = [record for record, fingerprint in normalized if fingerprint in existing]
    return {
        "valid": valid,
        "duplicates": duplicates,
        "valid_count": len(valid),
        "duplicate_count": len(duplicates),
        "schema": schema,
    }


async def add_records(product_id: str, records: list[dict], schema: list[str]):
    check = await validate_records(product_id, records, schema)
    created = 0
    cipher = _fernet()

    for record in check["valid"]:
        canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        try:
            await db.inventory_items.insert_one({
                "_id": hashlib.sha256(
                    f"{product_id}:{fingerprint}".encode("utf-8")
                ).hexdigest(),
                "product_id": product_id,
                "fingerprint": fingerprint,
                "secret": cipher.encrypt(canonical.encode("utf-8")).decode("utf-8"),
                "status": "available",
                "reservation_id": None,
                "order_id": None,
                "user_tid": None,
                "created_at": now_iso(),
                "reserved_at": None,
                "sold_at": None,
            })
            created += 1
        except Exception:
            continue

    await db.products.update_one(
        {"_id": product_id},
        {
            "$set": {
                "delivery_type": "inventory",
                "inventory_enabled": True,
                "inventory_schema": schema,
                "updated_at": now_iso(),
            }
        },
    )
    return {
        "created": created,
        "skipped": check["duplicate_count"] + (len(check["valid"]) - created),
        "valid": check["valid"],
        "duplicates": check["duplicates"],
        "schema": schema,
    }


async def available_count(product_id: str):
    return await db.inventory_items.count_documents(
        {"product_id": product_id, "status": "available"}
    )


def _decrypt_secret(item: dict):
    cipher = _fernet()
    decoded = cipher.decrypt(item["secret"].encode("utf-8")).decode("utf-8")
    try:
        value = json.loads(decoded)
        if isinstance(value, dict):
            return value
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    # Backward compatibility with the old email:password inventory format.
    return {"value": decoded}


def decrypt_items(items):
    return [_decrypt_secret(item) for item in items]


async def reserve_items(product_id: str, quantity: int, reservation_id: str):
    if quantity < 1:
        return []

    reserved = []
    for _ in range(quantity):
        item = await db.inventory_items.find_one_and_update(
            {
                "product_id": product_id,
                "status": "available",
            },
            {
                "$set": {
                    "status": "reserved",
                    "reservation_id": reservation_id,
                    "reserved_at": now_iso(),
                }
            },
            sort=[("_id", 1)],
        )
        if not item:
            await release_items(reservation_id)
            return []
        reserved.append(item)

    return reserved


async def release_items(reservation_id: str):
    await db.inventory_items.update_many(
        {"reservation_id": reservation_id, "status": "reserved"},
        {
            "$set": {
                "status": "available",
                "reservation_id": None,
                "reserved_at": None,
            }
        },
    )


async def commit_items(
    reservation_id: str,
    order_id: str,
    user_tid: int,
):
    await db.inventory_items.update_many(
        {"reservation_id": reservation_id, "status": "reserved"},
        {
            "$set": {
                "status": "sold",
                "reservation_id": None,
                "order_id": order_id,
                "user_tid": user_tid,
                "sold_at": now_iso(),
            }
        },
    )
