"""QRIS orders for the main bot. Payments go directly to an order, not a deposit."""
import asyncio
import base64
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from pymongo.errors import DuplicateKeyError

from checkout import next_invoice_id, stock_for, _cart_after_purchase
from db import db, get_settings
from gopay_provider import _run_node
from inventory import commit_items, decrypt_items, release_items, reserve_items
from pricing import price_for_product
from promo_service import coupon_discount, release_coupon, reserve_coupon, validate_coupon
from services import notify_transaction_admin, notify_transaction_channel, now_iso

logger = logging.getLogger(__name__)
PAYMENT_SCOPE = "bot1"


async def qris_ready():
    settings = await get_settings()
    return bool(settings.get("qris_enabled")) and os.environ.get("GOPAY_ENABLED", "").lower() in {"1", "true", "yes"}


async def quote_items(user, cart_items, coupon_code=None):
    items = []
    for raw in cart_items:
        product = await db.products.find_one({"_id": raw.get("pid"), "active": True})
        if not product:
            return {"error": "Produk sudah tidak tersedia."}
        qty = max(1, int(raw.get("qty") or 1))
        stock = await stock_for(product)
        if stock is not None and stock < qty:
            return {"error": f"Stok {product['name']} berubah. Tersedia {stock}."}
        pricing = await price_for_product(product, "IDR", qty)
        items.append({"product": product, "qty": qty, **pricing,
                      "subtotal": pricing["unit_price"] * qty})
    if not items:
        return {"error": "Keranjang kosong."}
    total = sum(item["subtotal"] for item in items)
    coupon = None
    coupon_amount = 0
    if coupon_code:
        base = sum(float(item["base_unit_price"]) * item["qty"] for item in items)
        coupon, error = await validate_coupon(
            coupon_code, user["telegram_id"], "IDR", base,
            [item["product"]["_id"] for item in items],
        )
        if error:
            return {"error": error}
        eligible_ids = set(coupon.get("product_ids") or [])
        eligible = [item for item in items if not eligible_ids or item["product"]["_id"] in eligible_ids]
        eligible_base = sum(float(item["base_unit_price"]) * item["qty"] for item in eligible)
        product_discount = sum(float(item["discount_total"]) for item in eligible)
        if coupon_discount(coupon, eligible_base) > product_discount:
            coupon_amount = round(coupon_discount(coupon, eligible_base) - product_discount, 2)
            total = round(total - coupon_amount, 2)
        else:
            coupon = None
    total = int(round(total))
    if total < 1:
        return {"error": "Total QRIS harus minimal Rp1."}
    return {"items": items, "total": total, "coupon": coupon,
            "coupon_amount": coupon_amount}


async def _release_order_stock(order):
    await release_items(f"qris:{order['_id']}")
    for item in order.get("items", []):
        if item.get("reserved_stock_qty"):
            await db.products.update_one({"_id": item["product_id"]},
                                         {"$inc": {"stock": int(item["reserved_stock_qty"])}})
    await release_coupon(order["_id"])


async def create_qris_order(user, cart_items, coupon_code=None, preserve_cart=False):
    if not await qris_ready():
        return {"error": "Pembayaran QRIS sedang tidak tersedia."}
    existing = await db.purchases.find_one({"user_tid": user["telegram_id"],
        "payment_scope": PAYMENT_SCOPE, "payment_method": "qris",
        "status": "pending_payment"})
    if existing:
        if existing.get("expires_at", "") <= now_iso():
            return {"error": "QRIS sebelumnya sedang diverifikasi. Coba lagi sebentar."}
        payment = await db.gopay_payments.find_one({"_id": existing.get("payment_id"), "status": "pending"})
        if payment:
            data = await asyncio.to_thread(_run_node, "create_qris.mjs", [str(payment["payment_amount"])])
            return {"order": existing, "payment": payment,
                    "image": base64.b64decode(data["image_base64"], validate=True)}
        return {"error": "Pembayaran QRIS sebelumnya sedang diproses. Coba lagi sebentar."}
    quote = await quote_items(user, cart_items, coupon_code)
    if quote.get("error"):
        return quote

    order_id = str(uuid.uuid4())
    reservation_id = f"qris:{order_id}"
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    order_items = []
    order = {"_id": order_id, "items": order_items}
    payment_id = str(uuid.uuid4())
    try:
        for item in quote["items"]:
            product = item["product"]
            qty = item["qty"]
            stock_qty = 0
            if product.get("product_kind") == "digital" or product.get("delivery_type") == "inventory" or product.get("inventory_enabled"):
                reserved = await reserve_items(product["_id"], qty, reservation_id)
                if len(reserved) != qty:
                    raise ValueError(f"Stok {product['name']} baru saja habis.")
            elif product.get("stock") is not None:
                changed = await db.products.update_one(
                    {"_id": product["_id"], "active": True, "stock": {"$gte": qty}},
                    {"$inc": {"stock": -qty}},
                )
                if changed.modified_count != 1:
                    raise ValueError(f"Stok {product['name']} baru saja habis.")
                stock_qty = qty
            order_items.append({
                "product_id": product["_id"], "name": product["name"], "qty": qty,
                "unit_price": item["unit_price"], "base_unit_price": item["base_unit_price"],
                "discount_per_unit": item["discount_per_unit"], "discount_total": item["discount_total"],
                "discount_id": item["discount_id"], "discount_name": item["discount_name"],
                "subtotal": item["subtotal"], "delivery_type": product.get("delivery_type"),
                "reserved_stock_qty": stock_qty,
            })

        if quote["coupon"] and not await reserve_coupon(
            quote["coupon"], user["telegram_id"], order_id, quote["coupon_amount"]
        ):
            raise ValueError("Kupon baru saja mencapai batas penggunaan.")

        admin_fee = max(1, int(round(quote["total"] * 0.007)))
        invoice_id = await next_invoice_id()
        order.update({
            "invoice_id": invoice_id, "user_tid": user["telegram_id"],
            "username": user.get("username", ""), "total": quote["total"],
            "currency": "IDR", "payment_method": "qris", "payment_id": payment_id,
            "payment_scope": PAYMENT_SCOPE, "status": "pending_payment",
            "created_at": now_iso(), "expires_at": expires.isoformat(),
            "paid_at": None, "delivered_at": None, "delivery_error": None,
            "discount_total": sum(item["discount_total"] for item in quote["items"]) + quote["coupon_amount"],
            "coupon_code": quote["coupon"]["code"] if quote["coupon"] else None,
            "coupon_discount": quote["coupon_amount"], "preserve_cart": bool(preserve_cart),
            "source_code": user.get("traffic_source_code"),
            "source_kind": user.get("traffic_source_kind"),
        })
        await db.purchases.insert_one(order)
        payment = None
        for _ in range(200):
            code = secrets.randbelow(900) + 100
            amount = quote["total"] + admin_fee + code
            try:
                payment = {"_id": payment_id, "payment_scope": PAYMENT_SCOPE,
                           "payment_type": "checkout", "order_id": order_id,
                           "user_tid": user["telegram_id"], "base_amount": quote["total"],
                           "admin_fee": admin_fee, "platform_code": code,
                           "payment_amount": amount, "active_payment_amount": amount,
                           "status": "pending", "tx_id": None,
                           "created_at": order["created_at"], "expires_at": expires.isoformat(),
                           "confirmed_at": None}
                await db.gopay_payments.insert_one(payment)
                break
            except DuplicateKeyError:
                payment = None
        if payment is None:
            raise RuntimeError("Nominal QRIS unik tidak tersedia.")
        data = await asyncio.to_thread(_run_node, "create_qris.mjs", [str(payment["payment_amount"])])
        image = base64.b64decode(data["image_base64"], validate=True)
        from stock_monitor import schedule_stock_scan
        for item in order_items:
            schedule_stock_scan(item["product_id"])
        return {"order": order, "payment": payment, "image": image}
    except Exception:
        await _release_order_stock(order)
        await db.gopay_payments.delete_one({"_id": payment_id})
        await db.purchases.update_one({"_id": order_id}, {"$set": {"status": "failed"}})
        raise


async def expire_qris_orders():
    now = now_iso()
    async for order in db.purchases.find({"payment_scope": PAYMENT_SCOPE,
                                           "payment_method": "qris", "status": "pending_payment",
                                           "expires_at": {"$lte": now}}):
        changed = await db.purchases.update_one(
            {"_id": order["_id"], "status": "pending_payment"},
            {"$set": {"status": "expired"}},
        )
        if changed.modified_count:
            await _release_order_stock(order)
            await db.gopay_payments.update_one({"_id": order["payment_id"], "status": "pending"},
                {"$set": {"status": "expired", "expired_at": now}, "$unset": {"active_payment_amount": ""}})
            try:
                from tgapi import send_message
                await send_message(order["user_tid"],
                    f"⌛ QRIS invoice <code>{order['invoice_id']}</code> kedaluwarsa. Stok dilepas; silakan checkout lagi.")
            except Exception:
                logger.exception("Failed to notify expired QRIS order")


async def finalize_qris_order(order_id, tx_id):
    order = await db.purchases.find_one({"_id": order_id, "payment_scope": PAYMENT_SCOPE})
    if not order:
        return False
    claimed = await db.purchases.update_one(
        {"_id": order_id, "status": "pending_payment"},
        {"$set": {"status": "paid", "paid_at": now_iso(), "payment_tx_id": tx_id}},
    )
    if claimed.modified_count != 1:
        return False
    order["status"] = "paid"

    from bot import (deliver_inventory, deliver_product, queue_service_delivery,
                     send_message, user_label, build_invoice_text)
    from services import user_lang
    user = await db.bot_users.find_one({"telegram_id": order["user_tid"]}) or {"telegram_id": order["user_tid"]}
    lang = await user_lang(order["user_tid"])
    all_ok = True
    service_count = 0
    try:
        await commit_items(f"qris:{order_id}", order_id, order["user_tid"])
        if order.get("source_code"):
            await db.prospects.update_many({"tg_user_id": order["user_tid"]},
                {"$set": {"status": "customer", "customer_order_id": order_id, "converted_at": now_iso()}})
        if order.get("preserve_cart"):
            fresh = await db.bot_users.find_one({"telegram_id": order["user_tid"]}, {"cart": 1})
            new_cart = _cart_after_purchase((fresh or {}).get("cart", []),
                [{"pid": item["product_id"], "qty": item["qty"]} for item in order["items"]])
        else:
            new_cart = []
        user_update = {"$set": {"cart": new_cart}}
        if order.get("coupon_code"):
            user_update["$unset"] = {"pending_coupon": ""}
        await db.bot_users.update_one({"telegram_id": order["user_tid"]}, user_update)
        await send_message(order["user_tid"], build_invoice_text(order))
        for item in order["items"]:
            product = await db.products.find_one({"_id": item["product_id"]})
            if not product:
                all_ok = False
                continue
            if product.get("product_kind") == "digital" or product.get("delivery_type") == "inventory" or product.get("inventory_enabled"):
                stock_items = await db.inventory_items.find({"order_id": order_id, "product_id": product["_id"], "status": "sold"}).to_list(item["qty"])
                if len(stock_items) != item["qty"]:
                    all_ok = False
                    continue
                all_ok = await deliver_inventory(order["user_tid"], product, decrypt_items(stock_items)) and all_ok
            elif product.get("product_kind") == "service" or product.get("delivery_type") == "service":
                service_count += 1
                await queue_service_delivery(order["user_tid"], user, product, order, lang)
            else:
                for _ in range(item["qty"]):
                    all_ok = await deliver_product(order["user_tid"], product, lang) and all_ok
        final_status = "delivery_failed" if not all_ok else ("service_waiting" if service_count else "delivered")
        await db.purchases.update_one({"_id": order_id}, {"$set": {
            "status": final_status, "delivered_at": now_iso() if all_ok and not service_count else None,
            "delivery_error": None if all_ok else "Satu atau lebih produk gagal dikirim.",
        }})
        await send_message(order["user_tid"],
            f"✅ Pembayaran QRIS terverifikasi. Invoice <code>{order['invoice_id']}</code>. "
            + ("Produk sedang dikirim." if all_ok else "Pengiriman membutuhkan bantuan admin."))
        sale = {**order, "status": final_status}
        for notify, args in ((notify_transaction_admin, (sale, user_label(user))),
                             (notify_transaction_channel, (sale,))):
            try:
                await notify(*args)
            except Exception:
                logger.exception("QRIS sale notification failed")
        return all_ok
    except Exception as exc:
        logger.exception("QRIS order delivery failed: %s", order_id)
        await db.purchases.update_one({"_id": order_id},
            {"$set": {"status": "delivery_failed", "delivery_error": str(exc)}})
        try:
            await send_message(order["user_tid"],
                f"⚠️ Pembayaran invoice <code>{order['invoice_id']}</code> berhasil, tetapi pengiriman membutuhkan bantuan admin.")
        except Exception:
            pass
        return False
