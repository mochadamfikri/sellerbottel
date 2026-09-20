import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from db import db
from inventory import commit_items, release_items, reserve_items
from rates import get_rate


CUR_FIELD = {"USD": "balance_usd", "IDR": "balance_idr"}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def jakarta_date():
    return datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y%m%d")


async def next_invoice_id():
    counter = await db.counters.find_one_and_update(
        {"_id": f"invoice:{jakarta_date()}"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=__import__("pymongo").ReturnDocument.AFTER,
    )
    return f"INV-{jakarta_date()}-{counter['seq']:04d}"


async def product_price(product, currency):
    if currency == "USD":
        return round(float(product.get("price_usd") or 0), 2)
    if product.get("price_idr") is not None:
        return round(float(product["price_idr"]))
    rate = await get_rate()
    return round(float(product.get("price_usd") or 0) * rate / 100) * 100


async def stock_for(product):
    if product.get("delivery_type") == "inventory" or product.get("inventory_enabled"):
        return await db.inventory_items.count_documents({
            "product_id": product["_id"],
            "status": "available",
        })
    stock = product.get("stock")
    return None if stock is None else int(stock)


async def execute_checkout(user, cart_items):
    currency = user["currency"]
    field = CUR_FIELD[currency]
    order_id = str(uuid.uuid4())
    reservation_id = f"order:{order_id}"

    items = []
    total = 0.0

    for raw in cart_items:
        product = await db.products.find_one({
            "_id": raw["pid"],
            "active": True,
        })
        if not product:
            continue

        qty = max(1, int(raw.get("qty", 1)))
        stock = await stock_for(product)
        if stock is not None and stock < qty:
            return {
                "ok": False,
                "error": "stock",
                "product": product,
                "stock": stock,
            }

        price = await product_price(product, currency)
        items.append({
            "product": product,
            "qty": qty,
            "unit_price": price,
            "subtotal": price * qty,
        })
        total += price * qty

    if not items:
        return {"ok": False, "error": "empty"}

    invoice_id = await next_invoice_id()
    order = {
        "_id": order_id,
        "invoice_id": invoice_id,
        "user_tid": user["telegram_id"],
        "username": user.get("username", ""),
        "items": [
            {
                "product_id": item["product"]["_id"],
                "name": item["product"]["name"],
                "qty": item["qty"],
                "unit_price": item["unit_price"],
                "subtotal": item["subtotal"],
                "delivery_type": item["product"].get("delivery_type"),
            }
            for item in items
        ],
        "total": total,
        "currency": currency,
        "payment_method": "balance",
        "status": "pending",
        "created_at": now_iso(),
        "paid_at": None,
        "delivered_at": None,
        "delivery_error": None,
        "discount_total": 0.0,
        "coupon_code": None,
    }
    await db.purchases.insert_one(order)

    allocations = []
    try:
        for item in items:
            product = item["product"]
            qty = item["qty"]

            if product.get("delivery_type") == "inventory" or product.get("inventory_enabled"):
                reserved = await reserve_items(product["_id"], qty, reservation_id)
                if len(reserved) != qty:
                    raise ValueError(f"Stok {product['name']} tidak cukup.")
                allocations.append({
                    "kind": "inventory",
                    "product_id": product["_id"],
                    "items": reserved,
                })
            elif product.get("stock") is not None:
                result = await db.products.update_one(
                    {
                        "_id": product["_id"],
                        "active": True,
                        "stock": {"$gte": qty},
                    },
                    {"$inc": {"stock": -qty}},
                )
                if result.modified_count != 1:
                    raise ValueError(f"Stok {product['name']} tidak cukup.")
                allocations.append({
                    "kind": "stock",
                    "product_id": product["_id"],
                    "qty": qty,
                })

        balance_result = await db.bot_users.update_one(
            {
                "telegram_id": user["telegram_id"],
                field: {"$gte": total},
                "frozen": {"$ne": True},
            },
            {
                "$inc": {field: -total},
                "$set": {"cart": []},
            },
        )
        if balance_result.modified_count != 1:
            raise ValueError("Saldo tidak cukup atau akun dibekukan.")

        await db.purchases.update_one(
            {"_id": order_id, "status": "pending"},
            {"$set": {"status": "paid", "paid_at": now_iso()}},
        )

        for allocation in allocations:
            if allocation["kind"] == "inventory":
                await commit_items(reservation_id, order_id, user["telegram_id"])

        return {
            "ok": True,
            "order": order,
            "items": items,
            "allocations": allocations,
            "remaining_balance": float(user.get(field, 0)) - total,
        }

    except Exception as exc:
        for allocation in allocations:
            if allocation["kind"] == "stock":
                await db.products.update_one(
                    {"_id": allocation["product_id"]},
                    {"$inc": {"stock": allocation["qty"]}},
                )

        await release_items(reservation_id)
        await db.purchases.update_one(
            {"_id": order_id},
            {"$set": {
                "status": "failed",
                "delivery_error": str(exc),
            }},
        )
        return {
            "ok": False,
            "error": "checkout",
            "message": str(exc),
        }
