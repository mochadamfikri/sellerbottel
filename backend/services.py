from datetime import datetime, timezone
from db import db, get_settings
from tgapi import send_message, send_photo_bytes
from html import escape
from i18n import t
from pricing import price_for_product
from inventory import available_count

CUR_FIELD = {"USD": "balance_usd", "IDR": "balance_idr"}


def fmt_amount(amount: float, currency: str) -> str:
    if currency == "USD":
        return f"${amount:,.2f}"
    return f"Rp {amount:,.0f}".replace(",", ".")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


async def user_lang(tid) -> str:
    u = await db.bot_users.find_one({"telegram_id": tid}, {"lang": 1})
    return (u or {}).get("lang") or "id"


async def credit_deposit(deposit: dict, note: str = ""):
    amount = deposit.get("credited_amount") or deposit["amount"]
    field = CUR_FIELD[deposit["currency"]]
    deposit_id = deposit["_id"]

    user_result = await db.bot_users.update_one(
        {"telegram_id": deposit["user_tid"], "deposit_credit_ids": {"$ne": deposit_id}},
        {"$inc": {field: amount}, "$addToSet": {"deposit_credit_ids": deposit_id}},
    )

    if user_result.modified_count == 0:
        user = await db.bot_users.find_one({"telegram_id": deposit["user_tid"]}, {"deposit_credit_ids": 1})
        if not user or deposit_id not in user.get("deposit_credit_ids", []):
            return False

    await db.deposits.update_one(
        {"_id": deposit_id, "status": "pending"},
        {"$set": {
            "status": "approved",
            "credited_amount": amount,
            "decided_at": now_iso(),
            "note": note,
        }},
    )

    lang = await user_lang(deposit["user_tid"])
    await send_message(
        deposit["user_tid"],
        t(lang, "dep_approved", amount=fmt_amount(amount, deposit["currency"])),
    )
    return True


async def reject_deposit(deposit: dict, note: str = ""):
    await db.deposits.update_one({"_id": deposit["_id"]}, {"$set": {
        "status": "rejected", "decided_at": now_iso(), "note": note,
    }})
    lang = await user_lang(deposit["user_tid"])
    reason = t(lang, "reason_label", r=note) if note else ""
    await send_message(deposit["user_tid"], t(lang, "dep_rejected", amount=fmt_amount(deposit["amount"], deposit["currency"]), reason=reason))


async def cancel_deposit(deposit: dict):
    amount = deposit.get("credited_amount") or deposit["amount"]
    field = CUR_FIELD[deposit["currency"]]
    deposit_id = deposit["_id"]

    result = await db.bot_users.update_one(
        {
            "telegram_id": deposit["user_tid"],
            field: {"$gte": amount},
            "deposit_debit_ids": {"$ne": deposit_id},
        },
        {"$inc": {field: -amount}, "$addToSet": {"deposit_debit_ids": deposit_id}},
    )

    if result.modified_count == 0:
        user = await db.bot_users.find_one({"telegram_id": deposit["user_tid"]}, {"deposit_debit_ids": 1})
        if not user or deposit_id not in user.get("deposit_debit_ids", []):
            raise ValueError("Saldo pengguna tidak cukup atau deposit sudah dibatalkan.")

    await db.deposits.update_one(
        {"_id": deposit_id, "status": "approved"},
        {"$set": {"status": "cancelled", "decided_at": now_iso()}},
    )

    lang = await user_lang(deposit["user_tid"])
    await send_message(
        deposit["user_tid"],
        t(lang, "dep_cancelled", amount=fmt_amount(amount, deposit["currency"])),
    )
    return True


async def notify_admin(text: str, kb=None, photo_file_id=None):
    s = await get_settings()
    admin_id = s.get("admin_telegram_id")
    if not admin_id:
        return
    if photo_file_id:
        from tgapi import send_photo_by_id
        await send_photo_by_id(admin_id, photo_file_id, caption=text, kb=kb)
    else:
        await send_message(admin_id, text, kb=kb)


async def _broadcast_channel_id():
    import os
    configured = str(os.environ.get("BROADCAST_CHANNEL_ID", "")).strip()
    if configured:
        return configured
    settings = await get_settings()
    configured = str(settings.get("broadcast_channel_id") or "").strip()
    if configured:
        return configured
    for channel in settings.get("required_channels") or []:
        if channel.get("enabled", True) and channel.get("channel_id"):
            return str(channel["channel_id"])
    return ""


async def notify_transaction_channel(order: dict):
    settings = await get_settings()
    if not settings.get("transaction_success_channel_enabled", False):
        return {"ok": False, "disabled": True}

    channel_id = await _broadcast_channel_id()
    if not channel_id:
        return {"ok": False, "error": "channel_not_configured"}

    names = []
    total_qty = 0
    for item in order.get("items") or []:
        qty = max(1, int(item.get("qty") or 1))
        total_qty += qty
        names.append(f"{item.get('name') or 'Product'} ×{qty}")

    body = (
        "🛒 <b>Transaction Succes!!</b>\n\n"
        f"Invoice: {escape(str(order.get('invoice_id') or ''))}\n"
        f"Produk: {escape(', '.join(names))}\n"
        f"Total: {escape(fmt_amount(order.get('total') or 0, order.get('currency') or 'IDR'))}\n"
        "Status: <b>completed</b>"
    )

    image = None
    if settings.get("broadcast_auto_image_enabled", False):
        try:
            from broadcast_image import render_transaction_image
            image = render_transaction_image(total_qty, order.get("total") or 0, order.get("currency") or "IDR")
        except Exception:
            image = None

    if image:
        result = await send_photo_bytes(channel_id, image, "transaction-success.jpg", caption=body)
    else:
        result = await send_message(channel_id, body)
    return result


async def _product_channel_notification(product: dict, title: str, heading: str, stock: int | None = None):
    settings = await get_settings()
    channel_id = await _broadcast_channel_id()
    if not channel_id:
        return {"ok": False, "error": "channel_not_configured"}

    name = escape(str(product.get("name") or "Product"))
    description = escape(str(product.get("description") or "").strip())
    pricing = await price_for_product(product, "IDR", 1)
    price = fmt_amount(pricing["unit_price"], "IDR")
    body = (
        f"{heading} <b>{escape(title)}</b>\n\n"
        f"Produk: <b>{name}</b>\n"
        f"Harga: <b>{price}</b>"
        + (f"\nStock: <b>{int(stock)}</b>" if stock is not None else "")
        + (f"\n\n{description}" if description else "")
        + "\n\n🛒 Order: @Idse_MarketBot"
    )

    image = None
    if settings.get("broadcast_auto_image_enabled", False):
        try:
            from broadcast_image import render_product_image
            image = render_product_image(
                product.get("name") or "Product",
                price,
                stock,
                product.get("description") or "",
                title=title.upper(),
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Generate product notification image gagal")

    if image:
        return await send_photo_bytes(channel_id, image, "product-update.jpg", caption=body)
    return await send_message(channel_id, body)


async def notify_product_created(product: dict):
    settings = await get_settings()
    if not settings.get("auto_broadcast_new_product", False):
        return {"ok": False, "disabled": True}
    return await _product_channel_notification(product, "PRODUCT BARU", "🆕")


async def notify_product_stock_updated(product: dict, stock: int | None = None):
    settings = await get_settings()
    if not settings.get("auto_broadcast_new_product", False):
        return {"ok": False, "disabled": True}
    if stock is None:
        stock = await available_count(product["_id"])
    return await _product_channel_notification(product, "STOCK UPDATE", "📦", stock)
