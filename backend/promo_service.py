import re
import uuid
from datetime import datetime, timezone

from db import db


COUPON_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,31}$")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize_coupon_code(code: str) -> str:
    return re.sub(r"\s+", "", str(code or "")).upper()


async def create_coupon(data: dict):
    code = normalize_coupon_code(data.get("code"))
    if not COUPON_CODE_RE.fullmatch(code):
        raise ValueError("Kode kupon harus 3-32 karakter: A-Z, 0-9, _ atau -.")

    kind = data.get("type")
    if kind not in {"percent", "fixed"}:
        raise ValueError("Jenis kupon tidak valid.")

    value = float(data.get("value") or 0)
    if value <= 0 or (kind == "percent" and value > 100):
        raise ValueError("Nilai kupon tidak valid.")

    quota = data.get("quota_total")
    quota = None if quota in (None, "", 0) else int(quota)
    per_user = max(1, int(data.get("per_user_limit") or 1))
    min_purchase = max(0.0, float(data.get("min_purchase") or 0))
    currency = data.get("currency") or "IDR"
    if currency not in {"IDR", "USD"}:
        raise ValueError("Currency kupon tidak valid.")

    existing = await db.promo_coupons.find_one({"code": code})
    if existing:
        raise ValueError("Kode kupon sudah digunakan.")

    doc = {
        "_id": str(uuid.uuid4()),
        "code": code,
        "type": kind,
        "value": value,
        "currency": currency,
        "quota_total": quota,
        "used_count": 0,
        "per_user_limit": per_user,
        "min_purchase": min_purchase,
        "product_ids": [str(x) for x in (data.get("product_ids") or [])],
        "starts_at": data.get("starts_at"),
        "ends_at": data.get("ends_at"),
        "active": bool(data.get("active", True)),
        "created_at": now_iso(),
    }
    await db.promo_coupons.insert_one(doc)
    return doc


def coupon_is_time_valid(coupon: dict, at: datetime | None = None) -> bool:
    at = at or datetime.now(timezone.utc)
    for field, is_start in (("starts_at", True), ("ends_at", False)):
        raw = coupon.get(field)
        if not raw:
            continue
        try:
            value = datetime.fromisoformat(raw)
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return False
        if is_start and at < value:
            return False
        if not is_start and at > value:
            return False
    return True


async def validate_coupon(code: str, user_tid: int, currency: str, subtotal: float, product_ids: list[str]):
    code = normalize_coupon_code(code)
    coupon = await db.promo_coupons.find_one({"code": code, "active": True})
    if not coupon:
        return None, "Kupon tidak ditemukan atau sudah tidak aktif."

    if not coupon_is_time_valid(coupon):
        return None, "Kupon belum aktif atau sudah kedaluwarsa."

    if coupon.get("currency") != currency:
        return None, "Kupon hanya berlaku untuk currency yang sesuai."

    quota = coupon.get("quota_total")
    if quota is not None and int(coupon.get("used_count") or 0) >= int(quota):
        return None, "Kuota kupon sudah habis."

    if subtotal < float(coupon.get("min_purchase") or 0):
        return None, "Total belanja belum memenuhi minimum kupon."

    eligible = coupon.get("product_ids") or []
    if eligible and not set(product_ids).intersection(eligible):
        return None, "Kupon tidak berlaku untuk produk di keranjang."

    used = await db.promo_coupon_redemptions.count_documents({
        "coupon_id": coupon["_id"],
        "user_tid": user_tid,
    })
    if used >= int(coupon.get("per_user_limit") or 1):
        return None, "Batas penggunaan kupon untuk akun ini sudah tercapai."

    return coupon, None


def coupon_discount(coupon: dict, amount: float) -> float:
    if not coupon:
        return 0.0
    if coupon["type"] == "percent":
        return round(amount * float(coupon["value"]) / 100, 2)
    return min(round(float(coupon["value"]), 2), round(amount, 2))


async def reserve_coupon(coupon: dict, user_tid: int, order_id: str, discount_amount: float):
    query = {"_id": coupon["_id"], "active": True}
    quota = coupon.get("quota_total")
    if quota is not None:
        query["used_count"] = {"$lt": int(quota)}

    result = await db.promo_coupons.update_one(query, {"$inc": {"used_count": 1}})
    if result.modified_count != 1:
        return False

    try:
        await db.promo_coupon_redemptions.insert_one({
            "_id": str(uuid.uuid4()),
            "coupon_id": coupon["_id"],
            "coupon_code": coupon["code"],
            "user_tid": user_tid,
            "order_id": order_id,
            "discount_amount": discount_amount,
            "created_at": now_iso(),
        })
    except Exception:
        await db.promo_coupons.update_one({"_id": coupon["_id"], "used_count": {"$gt": 0}}, {"$inc": {"used_count": -1}})
        return False

    return True


async def release_coupon(order_id: str):
    redemption = await db.promo_coupon_redemptions.find_one_and_delete({"order_id": order_id})
    if not redemption:
        return False
    await db.promo_coupons.update_one(
        {"_id": redemption["coupon_id"], "used_count": {"$gt": 0}},
        {"$inc": {"used_count": -1}},
    )
    return True
