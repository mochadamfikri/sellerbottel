import hashlib
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


def normalize_lines(lines: Iterable[str]):
    values = []
    seen = set()
    for raw in lines:
        line = str(raw or "").strip()
        if not line:
            continue
        if ":" not in line:
            continue
        email, password = line.split(":", 1)
        email = email.strip()
        password = password.strip()
        if not email or not password:
            continue
        normalized = f"{email}:{password}"
        fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        values.append((normalized, fingerprint))
    return values


async def validate_items(lines: Iterable[str]):
    normalized = normalize_lines(lines)
    fingerprints = [fp for _, fp in normalized]
    existing = set()
    if fingerprints:
        cursor = db.inventory_items.find(
            {"fingerprint": {"$in": fingerprints}},
            {"fingerprint": 1},
        )
        existing = {doc["fingerprint"] async for doc in cursor}

    valid = [line for line, fp in normalized if fp not in existing]
    duplicates = [line for line, fp in normalized if fp in existing]
    return {
        "valid": valid,
        "duplicates": duplicates,
        "valid_count": len(valid),
        "duplicate_count": len(duplicates),
    }


async def add_items(product_id: str, lines: Iterable[str]):
    check = await validate_items(lines)
    created = []
    cipher = _fernet()

    for line in check["valid"]:
        fingerprint = hashlib.sha256(line.encode("utf-8")).hexdigest()
        try:
            await db.inventory_items.insert_one({
                "_id": hashlib.sha256(
                    f"{product_id}:{fingerprint}".encode("utf-8")
                ).hexdigest(),
                "product_id": product_id,
                "fingerprint": fingerprint,
                "secret": cipher.encrypt(line.encode("utf-8")).decode("utf-8"),
                "status": "available",
                "reservation_id": None,
                "order_id": None,
                "user_tid": None,
                "created_at": now_iso(),
                "reserved_at": None,
                "sold_at": None,
            })
            created.append(line)
        except Exception:
            continue

    await db.products.update_one(
        {"_id": product_id},
        {
            "$set": {
                "delivery_type": "inventory",
                "inventory_enabled": True,
                "updated_at": now_iso(),
            }
        },
    )
    return {
        "created": len(created),
        "skipped": check["duplicate_count"] + (len(check["valid"]) - len(created)),
        "valid": check["valid"],
        "duplicates": check["duplicates"],
    }


async def available_count(product_id: str):
    return await db.inventory_items.count_documents(
        {"product_id": product_id, "status": "available"}
    )


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


def decrypt_items(items):
    cipher = _fernet()
    return [cipher.decrypt(i["secret"].encode("utf-8")).decode("utf-8") for i in items]
