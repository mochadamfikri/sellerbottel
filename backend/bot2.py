import asyncio
import base64
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from html import escape

import httpx

from db import db, get_settings
from inventory import commit_items, decrypt_items, release_items, reserve_items
from pricing import price_for_product
from services import fmt_amount
from checkout import next_invoice_id, stock_for
from bot import deliver_inventory, deliver_product, build_invoice_text
from gopay_provider import _run_node

logger = logging.getLogger("bot2")

BOT2_TOKEN = ""
PAYMENT_SCOPE = "bot2"


def _token():
    token = os.environ.get("BOT2_TELEGRAM_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT2_TELEGRAM_TOKEN belum dikonfigurasi.")
    return token


async def tg2(method: str, **payload):
    token = _token()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"https://api.telegram.org/bot{token}/{method}", json=payload)
        return response.json()



_BOT2_LAST_MESSAGES = {}

async def _remember_last_message(chat_id, message_id):
    if not message_id:
        return
    _BOT2_LAST_MESSAGES[int(chat_id)] = int(message_id)
    try:
        await db.bot_users.update_one(
            {"telegram_id": int(chat_id)},
            {"$set": {"bot2_last_message_id": int(message_id)}},
        )
    except Exception:
        logger.exception("Failed to persist Bot2 last message id")


async def _get_last_message(chat_id):
    chat_id = int(chat_id)
    message_id = _BOT2_LAST_MESSAGES.get(chat_id)
    if message_id:
        return message_id
    try:
        user = await db.bot_users.find_one(
            {"telegram_id": chat_id},
            {"bot2_last_message_id": 1},
        )
        message_id = (user or {}).get("bot2_last_message_id")
        if message_id:
            _BOT2_LAST_MESSAGES[chat_id] = int(message_id)
            return int(message_id)
    except Exception:
        logger.exception("Failed to load Bot2 last message id")
    return None


async def send2(chat_id, text, kb=None, force_new=False):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if kb:
        payload["reply_markup"] = kb

    if not force_new:
        previous_id = await _get_last_message(chat_id)
        if previous_id:
            edited = await edit2(chat_id, previous_id, text, kb=kb)
            if edited.get("ok"):
                return edited

    result = await tg2("sendMessage", **payload)
    if result.get("ok"):
        await _remember_last_message(chat_id, (result.get("result") or {}).get("message_id"))
    return result


async def edit2(chat_id, message_id, text, kb=None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if kb:
        payload["reply_markup"] = kb
    else:
        payload["reply_markup"] = {"inline_keyboard": []}
    result = await tg2("editMessageText", **payload)
    if result.get("ok"):
        await _remember_last_message(chat_id, message_id)
    return result


async def answer2(callback_id, text=None):
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    return await tg2("answerCallbackQuery", **payload)


async def delete2(chat_id, message_id):
    result = await tg2("deleteMessage", chat_id=chat_id, message_id=message_id)
    if result.get("ok") and _BOT2_LAST_MESSAGES.get(int(chat_id)) == int(message_id):
        _BOT2_LAST_MESSAGES.pop(int(chat_id), None)
    return result


async def send_photo2(chat_id, data: bytes, filename="qris.jpg", caption=None, kb=None):
    payload = {"chat_id": str(chat_id)}
    if caption:
        payload["caption"] = caption
        payload["parse_mode"] = "HTML"
    if kb:
        import json
        payload["reply_markup"] = json.dumps(kb, ensure_ascii=False, separators=(",", ":"))
    token = _token()
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data=payload,
            files={"photo": (filename, data, "image/jpeg")},
        )
        return response.json()


async def send_document2(chat_id, data: bytes, filename, caption=None):
    payload = {"chat_id": str(chat_id)}
    if caption:
        payload["caption"] = caption
        payload["parse_mode"] = "HTML"
    token = _token()
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data=payload,
            files={"document": (filename, data)},
        )
        return response.json()


def menu_keyboard():
    return {
        "keyboard": [
            [{"text": "📦 STOCK"}, {"text": "💰 DEPOSIT"}],
            [{"text": "🛒 PESANAN"}, {"text": "🎟 VOUCHER"}],
            [{"text": "👤 AKUN / INFORMATION"}, {"text": "❓ CARA ORDER"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": "Pilih menu atau ketik /start",
    }

def cancel_keyboard():
    return {"inline_keyboard": [[{"text": "❌ Batal", "callback_data": "b2:cancel"}]]}


def back_keyboard():
    return {"inline_keyboard": [[{"text": "◀️ Kembali", "callback_data": "b2:home"}]]}


async def get_user2(tg_from):
    tid = tg_from["id"]
    user = await db.bot_users.find_one({"telegram_id": tid})
    if not user:
        user = {
            "_id": str(uuid.uuid4()),
            "telegram_id": tid,
            "username": tg_from.get("username", ""),
            "first_name": tg_from.get("first_name", ""),
            "lang": "id",
            "currency": "IDR",
            "balance_usd": 0.0,
            "balance_idr": 0.0,
            "frozen": False,
            "cart": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.bot_users.insert_one(user)
    else:
        await db.bot_users.update_one(
            {"telegram_id": tid},
            {"$set": {
                "username": tg_from.get("username", ""),
                "first_name": tg_from.get("first_name", ""),
            }},
        )
    return user


async def set_b2_state(tid, state=None, data=None):
    await db.bot_users.update_one(
        {"telegram_id": tid},
        {"$set": {"bot2_state": state, "bot2_state_data": data or {}}},
    )


async def _progress_bar(percent):
    filled = max(0, min(10, int(percent / 10)))
    return "▰" * filled + "▱" * (10 - filled)


def _active_date_for_bot2(doc):
    now = datetime.now(timezone.utc)
    try:
        starts = doc.get("starts_at")
        ends = doc.get("ends_at")
        if starts and now < datetime.fromisoformat(starts):
            return False
        if ends and now > datetime.fromisoformat(ends):
            return False
    except (TypeError, ValueError):
        return False
    return doc.get("active", True)


async def show_home(chat_id, user, loaded=True):
    balance = fmt_amount(float(user.get("balance_idr") or 0), "IDR")
    await send2(
        chat_id,
        f"💰 Saldo IDR: <b>{balance}</b>\n\nPilih menu di bawah.",
        kb=menu_keyboard(),
    )


async def show_products(chat_id, page=1, message_id=None):
    """Shared stock list: only ready products, dynamic promo/voucher data, 2 buttons per row."""
    products = await db.products.find({"active": True}).sort("created_at", 1).to_list(500)
    ready = []
    for product in products:
        stock = await stock_for(product)
        if stock is None or stock > 0:
            ready.append((product, stock))

    lines = ["<b>📦 STOCK PRODUCT</b>", ""]
    for idx, (product, stock) in enumerate(ready, start=1):
        pricing = await price_for_product(product, "IDR", 1)
        stock_text = "∞" if stock is None else str(int(stock))
        lines.append(
            f"<b>{idx}.</b> {escape(str(product.get('name') or 'Product'))} "
            f"— <b>{fmt_amount(pricing['unit_price'], 'IDR')}</b> · Stok: <b>{stock_text}</b>"
        )
    if not ready:
        lines.append("Belum ada produk yang ready.")

    promo_lines = []
    cursor = db.discounts.find({"active": True}).sort([("priority", -1), ("created_at", -1)]).limit(10)
    async for discount in cursor:
        if _active_date_for_bot2(discount):
            name = escape(str(discount.get("name") or "Promo"))
            mode = str(discount.get("mode") or "")
            value = discount.get("value")
            value_text = f"{value:g}%" if mode == "percent" and isinstance(value, (int, float)) else fmt_amount(value or 0, "IDR")
            promo_lines.append(f"• <b>{name}</b> — {value_text}")

    coupon_lines = []
    cursor = db.coupons.find({"active": True}).limit(10)
    async for coupon in cursor:
        code = escape(str(coupon.get("code") or "-"))
        condition = coupon.get("min_purchase") or coupon.get("min_amount") or coupon.get("minimum_purchase")
        condition_text = f" · Min. {fmt_amount(condition, 'IDR')}" if condition else ""
        coupon_lines.append(f"• <code>{code}</code>{condition_text}")

    if promo_lines:
        lines += ["", "<b>🔥 PROMO AKTIF</b>"] + promo_lines
    if coupon_lines:
        lines += ["", "<b>🎟 VOUCHER AKTIF</b>"] + coupon_lines

    # Product selector buttons: NUMBERS ONLY. Each number maps to the
    # corresponding product position in this ready-stock list.
    buttons = []
    for idx, (_product, _stock) in enumerate(ready, start=1):
        buttons.append({
            "text": str(idx),
            "callback_data": f"b2:productnum:{idx}",
        })
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    markup = {"inline_keyboard": rows}

    if message_id is not None:
        return await edit2(chat_id, message_id, "\n".join(lines), kb=markup)
    return await send2(chat_id, "\n".join(lines), kb=markup)


async def show_product(chat_id, pid=None, message_id=None, product_index=None):
    if product_index is not None:
        products = await db.products.find({"active": True}).sort("created_at", 1).to_list(500)
        ready = []
        for product in products:
            stock = await stock_for(product)
            if stock is None or stock > 0:
                ready.append(product)
        pos = int(product_index) - 1
        product = ready[pos] if 0 <= pos < len(ready) else None
    else:
        product = await db.products.find_one({"_id": pid, "active": True})

    if not product:
        text = "❌ Produk tidak ditemukan atau stok sudah habis."
        return await edit2(chat_id, message_id, text, kb=back_keyboard()) if message_id else await send2(chat_id, text, kb=back_keyboard())

    stock = await stock_for(product)
    price = await price_for_product(product, "IDR", 1)
    stock_text = "∞" if stock is None else str(int(stock))
    variation = product.get("variation") or product.get("variant") or product.get("sku") or product.get("name")
    code = product.get("code") or str(product.get("_id", ""))[-6:]
    desc = product.get("description") or "Tidak ada deskripsi."

    text = (
        "╭───────────────\n"
        f"• <b>Produk</b> : {escape(str(product.get('name') or 'Product'))}\n"
        f"• <b>Variasi</b> : {escape(str(variation))}\n"
        f"• <b>Kode</b> : <code>{escape(str(code))}</code>\n"
        f"• <b>Sisa Produk</b> : {stock_text}\n"
        f"• <b>Desk</b> : {escape(str(desc))}\n"
        "╰───────────────\n\n"
        f"• <b>Harga</b> : {fmt_amount(price['unit_price'], 'IDR')}\n"
        f"• <b>Total Harga</b> : {fmt_amount(price['unit_price'], 'IDR')}"
    )
    if stock is not None and stock <= 0:
        text += "\n\n❌ Stok habis."
        kb = back_keyboard()
    else:
        kb = {"inline_keyboard": [
            [
                {"text": "📝 Buy ( Saldo )", "callback_data": f"b2:buy_balance:{product['_id']}"},
                {"text": "🔄 Buy ( Now )", "callback_data": f"b2:buy_now:{product['_id']}"},
            ],
            [{"text": "◀️ Kembali ke Stock", "callback_data": "b2:products:1"}],
            [{"text": "🔔 Notif Restok", "callback_data": f"b2:restock:{product['_id']}"}],
        ]}
    return await edit2(chat_id, message_id, text, kb=kb) if message_id else await send2(chat_id, text, kb=kb)


async def show_stock(chat_id):
    products = await db.products.find({"active": True}).to_list(200)
    lines = ["──── 「 LAPORAN STOK 」 ────"]
    for product in products:
        stock = await stock_for(product)
        stock_text = "∞" if stock is None else f"{int(stock)}x"
        lines.append(f"• {escape(product['name'])}: {stock_text}")
    if len(lines) == 1:
        lines.append("Belum ada data stok.")
    lines.append("────")
    await send2(chat_id, "\n".join(lines), kb=menu_keyboard())


async def show_voucher(chat_id):
    active = await db.coupons.count_documents({"active": True})
    if not active:
        await send2(chat_id, "🎟️ Tidak ada voucher tersedia saat ini.", kb=menu_keyboard())
        return
    rows = []
    cursor = db.coupons.find({"active": True}).limit(20)
    async for coupon in cursor:
        rows.append(f"• <b>{escape(coupon.get('code','-'))}</b>")
    await send2(chat_id, "🎟️ <b>VOUCHER</b>\n\n" + "\n".join(rows), kb=menu_keyboard())


async def show_information(chat_id, user):
    username = user.get("username") or "-"
    phone = user.get("phone") or user.get("phone_number") or "-"
    text = (
        "👤 <b>ACCOUNT / INFORMATION</b>\n\n"
        f"Telegram ID : <code>{user['telegram_id']}</code>\n"
        f"Username : @{escape(username).lstrip('@')}\n"
        f"Phone : <code>{escape(str(phone))}</code>\n\n"
        "🤖 <b>BOT BUILD BY</b>\n"
        "@pardoxbuilder\n"
        "t.me/pardoxbuilder\n\n"
        "🛠️ Menerima jasa <b>build bot Telegram</b> dan <b>website</b> "
        "sesuai kebutuhan."
    )
    await send2(chat_id, text, kb=menu_keyboard())


async def show_history(chat_id, user):
    deps = await db.deposits.find({"user_tid": user["telegram_id"]}).sort("created_at", -1).limit(10).to_list(10)
    orders = await db.purchases.find({"user_tid": user["telegram_id"]}).sort("created_at", -1).limit(10).to_list(10)
    events = [(d.get("created_at", ""), "deposit", d) for d in deps]
    events += [(o.get("created_at", ""), "order", o) for o in orders]
    events.sort(reverse=True)
    if not events:
        text = "──── 「 RIWAYAT MUTASI 」 ────\n\nBelum ada riwayat transaksi.\n\n📄 Halaman 1 / 1"
        await send2(chat_id, text, kb=menu_keyboard())
        return

    lines = ["──── 「 RIWAYAT MUTASI 」 ────"]
    for _, kind, item in events[:10]:
        if kind == "deposit":
            lines.append(
                f"💰 Deposit {fmt_amount(item.get('amount') or 0, 'IDR')} — "
                f"{item.get('status','pending')}"
            )
        else:
            lines.append(
                f"🧾 {escape(str(item.get('invoice_id','-')))} — "
                f"{fmt_amount(item.get('total') or 0, 'IDR')} — {item.get('status','pending')}"
            )
    lines.append("📄 Halaman 1 / 1")
    await send2(chat_id, "\n".join(lines), kb=menu_keyboard())


async def show_how_to_order(chat_id):
    me = await tg2("getMe")
    username = (me.get("result") or {}).get("username") or "bot"
    text = (
        f"Cara Order di bot @{username}\n\n"
        "1. Ketik /start untuk memulai\n"
        "2. Klik titik biru untuk melihat menu\n"
        "3. Pilih 📦 Stock untuk melihat stok yang ada\n"
        "4. Pilih produk yang ingin kamu beli\n"
        "5. Masukkan jumlah item yang ingin kamu beli\n"
        "6. Pilih metode pembayaran\n"
        "7. Selesai!\n\n"
        "Punya pertanyaan? Hubungi admin."
    )
    await send2(chat_id, text, kb=menu_keyboard())


async def ask_quantity(chat_id, pid):
    product = await db.products.find_one({"_id": pid, "active": True})
    if not product:
        await send2(chat_id, "❌ Produk tidak ditemukan.", kb=menu_keyboard())
        return
    stock = await stock_for(product)
    if stock is not None and stock <= 0:
        await send2(chat_id, "❌ Stok produk sedang kosong.", kb=menu_keyboard())
        return
    price = await price_for_product(product, "IDR", 1)
    await send2(        chat_id,
        f"🛒 <b>{escape(product['name'])}</b>\n"
        f"Harga/unit: <b>{fmt_amount(price['unit_price'], 'IDR')}</b>\n"
        f"Stok: <b>{'∞' if stock is None else int(stock)}</b>\n\n"
        "Masukkan jumlah item yang ingin kamu beli.\nContoh: <code>1</code>",
        kb=cancel_keyboard(),
    )


async def reserve_order_items(order_id, items):
    reservation_id = f"bot2:{order_id}"
    allocations = []
    for item in items:
        product = item["product"]
        qty = item["qty"]
        if product.get("product_kind") == "digital" or product.get("delivery_type") == "inventory" or product.get("inventory_enabled"):
            reserved = await reserve_items(product["_id"], qty, reservation_id)
            if len(reserved) != qty:
                await release_items(reservation_id)
                return None, reservation_id
            allocations.append({"kind": "inventory", "product_id": product["_id"], "items": reserved})
        elif product.get("stock") is not None:
            result = await db.products.update_one(
                {"_id": product["_id"], "active": True, "stock": {"$gte": qty}},
                {"$inc": {"stock": -qty}},
            )
            if result.modified_count != 1:
                await release_items(reservation_id)
                for alloc in allocations:
                    if alloc["kind"] == "stock":
                        await db.products.update_one({"_id": alloc["product_id"]}, {"$inc": {"stock": alloc["qty"]}})
                return None, reservation_id
            allocations.append({"kind": "stock", "product_id": product["_id"], "qty": qty})
    return allocations, reservation_id


async def begin_bot2_note(chat_id, user, pid, mode):
    product = await db.products.find_one({"_id": pid, "active": True})
    if not product:
        await send2(chat_id, "❌ Produk tidak ditemukan.", kb=menu_keyboard())
        return
    stock = await stock_for(product)
    if stock is not None and stock < 1:
        await send2(chat_id, "❌ Stok produk sedang kosong.", kb=back_keyboard())
        return
    await set_b2_state(user["telegram_id"], "note_input", {"pid": pid, "mode": mode, "qty": 1})
    await send2(chat_id, "📝 <b>Catatan untuk penjual</b>", kb={"inline_keyboard": [
        [{"text": "📝 Isi Catatan", "callback_data": "b2:note_input"}],
        [{"text": "⏭️ Skip", "callback_data": "b2:note_skip"}],
        [{"text": "❌ Batal", "callback_data": "b2:cancel"}],
    ]})


async def confirm_bot2_order(chat_id, user, pid, qty, mode, note=""):
    product = await db.products.find_one({"_id": pid, "active": True})
    if not product:
        await send2(chat_id, "❌ Produk sudah tidak tersedia.", kb=menu_keyboard())
        return
    pricing = await price_for_product(product, "IDR", qty)
    total = round(pricing["unit_price"] * qty)
    await set_b2_state(user["telegram_id"], "confirm", {"pid": pid, "qty": qty, "mode": mode, "note": note})
    await send2(chat_id, "🧾 <b>Konfirmasi Pesanan</b>\n\n"
        f"Produk: <b>{escape(product['name'])}</b>\n"
        f"Qty: <b>{qty}</b>\n"
        f"Total: <b>{fmt_amount(total, 'IDR')}</b>\n"
        f"Catatan: <b>{escape(note or 'tidak ada')}</b>",
        kb={"inline_keyboard": [
            [{"text": "✅ Konfirmasi & Proses", "callback_data": "b2:confirm_order"}],
            [{"text": "❌ Batal", "callback_data": "b2:cancel"}],
        ]})


async def process_confirmed_bot2_order(chat_id, user, pid, qty, mode, note):
    if mode == "balance":
        from checkout import execute_checkout
        bot2_user = dict(user)
        bot2_user["currency"] = "IDR"
        result = await execute_checkout(bot2_user, [{"pid": pid, "qty": qty}], preserve_cart=True, coupon_code=user.get("pending_coupon"))
        if not result.get("ok"):
            await send2(chat_id, "❌ Saldo IDR tidak mencukupi." if result.get("error") == "balance" else "❌ Checkout gagal.", kb=menu_keyboard())
            return
        order = result["order"]
        order["note"] = note
        await db.purchases.update_one(
            {"_id": order["_id"]},
            {"$set": {
                "bot2": True,
                "payment_scope": PAYMENT_SCOPE,
                "note": note,
            }},
        )
        await send2(chat_id, build_invoice_text(order))
        allocation_map = {x["product_id"]: x for x in result.get("allocations", [])}
        all_ok = True
        for item in result["items"]:
            product = item["product"]
            if product.get("product_kind") == "digital" or product.get("delivery_type") == "inventory" or product.get("inventory_enabled"):
                allocation = allocation_map.get(product["_id"], {})
                ok = await deliver_inventory(
                    chat_id, product, decrypt_items(allocation.get("items", [])),
                    send_message_fn=send2, send_document_fn=send_document2
                )
            else:
                ok = True
                for _ in range(item["qty"]):
                    ok = (await deliver_product(
                        chat_id, product, "id",
                        send_message_fn=send2, send_document_fn=send_document2
                    )) and ok
            all_ok = all_ok and ok
        await db.purchases.update_one({"_id": order["_id"]}, {"$set": {
            "note": note,
            "status": "delivered" if all_ok else "delivery_failed",
            "delivered_at": datetime.now(timezone.utc).isoformat() if all_ok else None,
        }})
        await send2(chat_id, "✅ <b>Pesanan berhasil diproses.</b>" if all_ok else "⚠️ Pembayaran berhasil, tetapi pengiriman membutuhkan perhatian admin.", kb=menu_keyboard())
        return
    await create_bot2_checkout(chat_id, user, pid, qty, note=note)

async def create_bot2_checkout(chat_id, user, pid, qty, note=''):
    settings = await get_settings()
    if not settings.get("qris_enabled", False) or os.environ.get("GOPAY_ENABLED", "").lower() not in {"1", "true", "yes"}:
        await send2(chat_id, "⚠️ Pembayaran QRIS sedang tidak tersedia.", kb=menu_keyboard())
        return
    product = await db.products.find_one({"_id": pid, "active": True})
    if not product:
        await send2(chat_id, "❌ Produk sudah tidak tersedia.", kb=menu_keyboard())
        return
    qty = max(1, int(qty))
    stock = await stock_for(product)
    if stock is not None and stock < qty:
        await send2(chat_id, f"⚠️ Stok berubah. Tersedia <b>{int(stock)}</b>.", kb=menu_keyboard())
        return

    pricing = await price_for_product(product, "IDR", qty)
    subtotal = round(pricing["unit_price"] * qty)
    invoice_id = await next_invoice_id()
    order_id = str(uuid.uuid4())
    reservation_id = f"bot2:{order_id}"
    items = [{
        "product": product,
        "qty": qty,
        "unit_price": pricing["unit_price"],
        "base_unit_price": pricing["base_unit_price"],
        "discount_per_unit": pricing["discount_per_unit"],
        "discount_total": pricing["discount_total"],
        "discount_id": pricing["discount_id"],
        "discount_name": pricing["discount_name"],
        "subtotal": subtotal,
    }]

    allocations, reservation_id = await reserve_order_items(order_id, items)
    if allocations is None:
        await send2(chat_id, "⚠️ Stok tidak cukup atau baru saja dibeli pengguna lain.", kb=menu_keyboard())
        return

    admin_fee = max(1, int(round(subtotal * 0.007)))
    platform_code = secrets.randbelow(900) + 100
    payment_amount = subtotal + admin_fee + platform_code
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    payment_id = str(uuid.uuid4())

    order = {
        "_id": order_id,
        "invoice_id": invoice_id,
        "user_tid": user["telegram_id"],
        "username": user.get("username", ""),
        "items": [{
            "product_id": product["_id"],
            "name": product["name"],
            "qty": qty,
            "unit_price": pricing["unit_price"],
            "base_unit_price": pricing["base_unit_price"],
            "discount_per_unit": pricing["discount_per_unit"],
            "discount_total": pricing["discount_total"],
            "discount_id": pricing["discount_id"],
            "discount_name": pricing["discount_name"],
            "subtotal": subtotal,
            "delivery_type": product.get("delivery_type"),
        }],
        "total": subtotal,
        "currency": "IDR",
        "payment_method": "qris",
        "payment_id": payment_id,
        "status": "pending_payment",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires.isoformat(),
        "paid_at": None,
        "delivered_at": None,
        "delivery_error": None,
        "discount_total": pricing["discount_total"],
        "source_code": user.get("traffic_source_code"),
        "source_kind": user.get("traffic_source_kind"),
        "note": note,
        "bot2": True,
        "payment_scope": PAYMENT_SCOPE,
    }

    await db.purchases.insert_one(order)
    try:
        await db.gopay_payments.insert_one({
            "_id": payment_id,
            "payment_scope": PAYMENT_SCOPE,
            "payment_type": "checkout",
            "order_id": order_id,
            "user_tid": user["telegram_id"],
            "base_amount": subtotal,
            "admin_fee": admin_fee,
            "platform_code": platform_code,
            "payment_amount": payment_amount,
            "active_payment_amount": payment_amount,
            "status": "pending",
            "tx_id": None,
            "created_at": order["created_at"],
            "expires_at": expires.isoformat(),
            "confirmed_at": None,
        })
        data = await asyncio.to_thread(_run_node, "create_qris.mjs", [str(payment_amount)])
        image = base64.b64decode(data["image_base64"])
    except Exception as exc:
        await release_items(reservation_id)
        await db.gopay_payments.delete_one({"_id": payment_id})
        await db.purchases.update_one({"_id": order_id}, {"$set": {"status": "failed", "delivery_error": str(exc)}})
        logger.exception("Bot2 QR checkout creation failed")
        await send2(chat_id, "⚠️ Payment gateway sedang bermasalah. Pesanan tidak dibuat.", kb=menu_keyboard())
        return

    await send2(
        chat_id,
        (
            "🧾 <b>CHECKOUT</b>\n\n"
            f"Invoice: <code>{invoice_id}</code>\n"
            f"Produk: <b>{escape(product['name'])}</b>\n"
            f"Jumlah: <b>{qty}</b>\n"
            f"Harga/unit: <b>{fmt_amount(pricing['unit_price'], 'IDR')}</b>\n"
            f"Harga total: <b>{fmt_amount(subtotal, 'IDR')}</b>\n\n"
            "Pilih metode pembayaran:"
        ),
        kb={"inline_keyboard": [[            {"text": f"📱 QRIS — {fmt_amount(payment_amount, 'IDR')}", "callback_data": f"b2:pay:{order_id}"}
        ], [{"text": "❌ Batal", "callback_data": f"b2:cancel_order:{order_id}"}]]},
    )

    # Keep QR bytes temporarily in DB-backed local state so the callback can send them.
    # The image is small enough for this short-lived dev flow; production can move it to object storage.
    await db.bot_users.update_one(
        {"telegram_id": user["telegram_id"]},
        {"$set": {
            "bot2_state": "checkout_payment",
            "bot2_state_data": {"order_id": order_id, "payment_id": payment_id, "qr_base64": base64.b64encode(image).decode("ascii")},
        }},
    )


async def show_checkout_qr(chat_id, user, order_id):
    order = await db.purchases.find_one({"_id": order_id, "user_tid": user["telegram_id"], "bot2": True, "payment_scope": PAYMENT_SCOPE, "status": "pending_payment"})
    if not order:
        await send2(chat_id, "❌ Transaksi tidak ditemukan atau sudah selesai.", kb=menu_keyboard())
        return
    payment = await db.gopay_payments.find_one({"_id": order.get("payment_id"), "payment_scope": PAYMENT_SCOPE})
    if not payment:
        await send2(chat_id, "❌ Data pembayaran tidak ditemukan.", kb=menu_keyboard())
        return
    expires = payment.get("expires_at", "")
    invoice = order.get("invoice_id", "-")
    text = (
        "【 TRANSAKSI PENDING 】\n"
        f"• ID payment: <code>{payment['_id']}</code>\n"
        "• Service Payment: QRIS\n"
        f"• Harga/Unit: {int(order['items'][0]['unit_price'])}\n"
        f"• Harga Total: {int(order['total']):,}\n"
        f"• Jumlah: {int(order['items'][0]['qty'])}\n"
        f"• Fee: {int(payment['admin_fee'] + payment['platform_code']):,}\n"
        f"• Note: Pembelian stok via QRIS (auto)\n"
        f"• Total Dibayar: {int(payment['payment_amount']):,}\n"
        f"• Invoice: <code>{invoice}</code>\n"
        f"• Bayar sebelum: {escape(expires)}"
    )
    state = await db.bot_users.find_one({"telegram_id": user["telegram_id"]}, {"bot2_state_data": 1})
    encoded = ((state or {}).get("bot2_state_data") or {}).get("qr_base64")
    if encoded:
        image = base64.b64decode(encoded)
    else:
        data = await asyncio.to_thread(_run_node, "create_qris.mjs", [str(payment["payment_amount"])])
        image = base64.b64decode(data["image_base64"])
    await send_photo2(chat_id, image, "bot2-qris.jpg", caption=text, kb=menu_keyboard())


async def deliver_order(chat_id, order):
    all_ok = True
    for item in order.get("items", []):
        product = await db.products.find_one({"_id": item["product_id"]})
        if not product:
            all_ok = False
            continue
        qty = int(item.get("qty") or 1)

        if (
            product.get("product_kind") == "digital"
            or product.get("delivery_type") == "inventory"
            or product.get("inventory_enabled")
        ):
            reservation_id = f"bot2:{order['_id']}"
            inventory = await db.inventory_items.find(
                {"reservation_id": reservation_id, "status": "reserved"}
            ).to_list(qty)
            if len(inventory) != qty:
                all_ok = False
                continue

            try:
                records = decrypt_items(inventory)
                ok = await deliver_inventory(
                    chat_id,
                    product,
                    records,
                    send_message_fn=send2,
                    send_document_fn=send_document2,
                )
                if ok:
                    await commit_items(reservation_id, order["_id"], order["user_tid"])
                else:
                    await release_items(reservation_id)
                all_ok = all_ok and ok
            except Exception:
                logger.exception("Bot2 inventory delivery failed")
                await release_items(reservation_id)
                all_ok = False
        else:
            ok = True
            for _ in range(qty):
                ok = await deliver_product(
                    chat_id,
                    product,
                    "id",
                    send_message_fn=send2,
                    send_document_fn=send_document2,
                ) and ok
            all_ok = all_ok and ok

    return all_ok


async def finalize_bot2_checkout(order_id, tx_id=None):
    order = await db.purchases.find_one({"_id": order_id})
    if not order or order.get("status") != "pending_payment":
        return False
    user_tid = order["user_tid"]
    await db.purchases.update_one(
        {"_id": order_id, "status": "pending_payment"},
        {"$set": {"status": "paid", "paid_at": datetime.now(timezone.utc).isoformat(), "payment_tx_id": tx_id}},
    )
    order["status"] = "paid"
    ok = await deliver_order(user_tid, order)
    final_status = "delivered" if ok else "delivery_failed"
    await db.purchases.update_one(
        {"_id": order_id},
        {"$set": {
            "status": final_status,            "delivered_at": datetime.now(timezone.utc).isoformat() if ok else None,
            "delivery_error": None if ok else "Satu atau lebih produk gagal dikirim.",
        }},
    )
    if ok:
        user = await db.bot_users.find_one({"telegram_id": user_tid})
        await send2(
            user_tid,
            f"✅ <b>Pembayaran berhasil!</b>\n\nInvoice: <code>{order['invoice_id']}</code>\n"
            f"Total: <b>{fmt_amount(order['total'], 'IDR')}</b>\n\n🎉 Produk berhasil dikirim.",
            kb=menu_keyboard(),
        )
    else:
        await send2(
            user_tid,
            f"⚠️ <b>Pembayaran berhasil</b>, tetapi pengiriman membutuhkan perhatian admin.\nInvoice: <code>{order['invoice_id']}</code>",
            kb=menu_keyboard(),
        )
    return ok


async def create_bot2_deposit_qr(chat_id, user, amount):
    settings = await get_settings()
    if not settings.get("qris_enabled", False) or os.environ.get("GOPAY_ENABLED", "").lower() not in {"1", "true", "yes"}:
        await send2(chat_id, "⚠️ Payment gateway QRIS sedang tidak tersedia.", kb=menu_keyboard())
        return
    admin_fee = max(1, int(round(amount * 0.007)))
    platform_code = secrets.randbelow(900) + 100
    payment_amount = int(amount + admin_fee + platform_code)
    payment_id = str(uuid.uuid4())
    deposit_id = str(uuid.uuid4())
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    now = datetime.now(timezone.utc).isoformat()
    await db.deposits.insert_one({
        "_id": deposit_id,
        "user_tid": user["telegram_id"],
        "username": user.get("username", ""),
        "first_name": user.get("first_name", ""),
        "method": "gopay",
        "bot2": True,
        "currency": "IDR",
        "amount": amount,
        "admin_fee": admin_fee,
        "platform_code": platform_code,
        "payment_amount": payment_amount,
        "status": "pending",
        "auto_verified": True,
        "note": "Bot2 QRIS deposit",
        "created_at": now,
        "decided_at": None,
        "expires_at": expires.isoformat(),
    })
    try:
        await db.gopay_payments.insert_one({
            "_id": payment_id,
            "payment_scope": PAYMENT_SCOPE,
            "payment_type": "deposit",
            "deposit_id": deposit_id,
            "user_tid": user["telegram_id"],
            "base_amount": amount,
            "admin_fee": admin_fee,
            "platform_code": platform_code,
            "payment_amount": payment_amount,
            "active_payment_amount": payment_amount,
            "status": "pending",
            "tx_id": None,
            "created_at": now,
            "expires_at": expires.isoformat(),
            "confirmed_at": None,
        })
        await db.deposits.update_one({"_id": deposit_id}, {"$set": {"payment_id": payment_id}})
        data = await asyncio.to_thread(_run_node, "create_qris.mjs", [str(payment_amount)])
        image = base64.b64decode(data["image_base64"])
    except Exception:
        await db.gopay_payments.delete_one({"_id": payment_id})
        await db.deposits.delete_one({"_id": deposit_id})
        logger.exception("Bot2 deposit QR creation failed")
        await send2(chat_id, "⚠️ Payment gateway sedang bermasalah.", kb=menu_keyboard())
        return

    caption = (
        "【 DEPOSIT PENDING 】\n"
        f"• Nominal saldo: {fmt_amount(amount, 'IDR')}\n"
        f"• Fee: {admin_fee + platform_code:,}\n"
        f"• Total Dibayar: {payment_amount:,}\n"
        "• Service Payment: QRIS\n"
        f"• Bayar sebelum: {expires.isoformat()}"
    )
    await send_photo2(chat_id, image, "bot2-deposit-qris.jpg", caption=caption, kb=menu_keyboard())


async def create_manual_deposit(user, amount, proof_file_id):
    deposit = {
        "_id": str(uuid.uuid4()),
        "user_tid": user["telegram_id"],
        "username": user.get("username", ""),
        "first_name": user.get("first_name", ""),
        "method": "bank",
        "bot2": True,
        "currency": "IDR",
        "amount": amount,
        "credited_amount": None,
        "proof_file_id": proof_file_id,
        "status": "pending",
        "auto_verified": False,
        "note": "Bot2 bank transfer manual",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "decided_at": None,
    }
    await db.deposits.insert_one(deposit)
    admin_id = (await get_settings()).get("admin_telegram_id")
    if admin_id:
        await send2(
            admin_id,
            f"💰 <b>Deposit IDR Bot 2</b>\nUser: <code>{user['telegram_id']}</code>\nNominal: <b>{fmt_amount(amount,'IDR')}</b>",
            kb={"inline_keyboard": [[
                {"text": "✅ Setujui", "callback_data": f"b2:adm:approve:{deposit['_id']}"},
                {"text": "❌ Tolak", "callback_data": f"b2:adm:reject:{deposit['_id']}"},
            ]]},
        )
        if proof_file_id:
            try:
                await tg2("sendPhoto", chat_id=admin_id, photo=proof_file_id, caption="Bukti transfer deposit Bot 2")
            except Exception:
                logger.exception("Bot2 admin proof send failed")
    await send2(user["telegram_id"], "⏳ Deposit menunggu verifikasi admin.", kb=menu_keyboard())


async def handle_admin_deposit(cb, action, deposit_id):
    settings = await get_settings()
    if str(cb.get("from", {}).get("id")) != str(settings.get("admin_telegram_id")):
        await answer2(cb["id"], "Bukan admin.")
        return
    dep = await db.deposits.find_one({"_id": deposit_id, "method": "bank"})
    if not dep:
        await answer2(cb["id"], "Deposit tidak ditemukan.")
        return
    if dep.get("status") != "pending":
        await answer2(cb["id"], f"Sudah diproses: {dep.get('status')}")
        return
    if action == "approve":
        result = await db.bot_users.update_one(
            {"telegram_id": dep["user_tid"], "deposit_credit_ids": {"$ne": deposit_id}},
            {"$inc": {"balance_idr": dep["amount"]}, "$addToSet": {"deposit_credit_ids": deposit_id}},
        )
        if result.modified_count:
            await db.deposits.update_one({"_id": deposit_id}, {"$set": {"status": "approved", "credited_amount": dep["amount"], "decided_at": datetime.now(timezone.utc).isoformat()}})
            await send2(dep["user_tid"], f"✅ Deposit disetujui. Saldo bertambah <b>{fmt_amount(dep['amount'],'IDR')}</b>.", kb=menu_keyboard())
        await answer2(cb["id"], "Deposit disetujui.")
    else:
        await db.deposits.update_one({"_id": deposit_id}, {"$set": {"status": "rejected", "decided_at": datetime.now(timezone.utc).isoformat()}})
        await send2(dep["user_tid"], "❌ Deposit ditolak.", kb=menu_keyboard())
        await answer2(cb["id"], "Deposit ditolak.")


async def handle_callback2(cb):
    data = cb.get("data", "")
    chat_id = cb["message"]["chat"]["id"]
    await _remember_last_message(chat_id, cb["message"].get("message_id"))
    user = await get_user2(cb["from"])
    await answer2(cb["id"])

    if data.startswith("b2:adm:"):
        _, _, action, deposit_id = data.split(":", 3)
        await handle_admin_deposit(cb, action, deposit_id)
        return

    if data == "b2:home":
        await set_b2_state(user["telegram_id"])
        await show_home(chat_id, user, loaded=False)
        return
    if data == "b2:cancel":
        await set_b2_state(user["telegram_id"])
        await show_home(chat_id, user, loaded=False)
        return
    if data == "b2:note_input":
        await set_b2_state(user["telegram_id"], "note_input", user.get("bot2_state_data") or {})
        await send2(chat_id, "✍️ Silakan kirim catatan untuk penjual.", kb=cancel_keyboard())
        return
    if data == "b2:note_skip":
        state_data = user.get("bot2_state_data") or {}
        await set_b2_state(user["telegram_id"])
        await confirm_bot2_order(chat_id, user, state_data.get("pid"), int(state_data.get("qty") or 1), state_data.get("mode", "now"), "")
        return
    if data == "b2:confirm_order":
        state_data = user.get("bot2_state_data") or {}
        await set_b2_state(user["telegram_id"])
        await process_confirmed_bot2_order(
            chat_id, user, state_data.get("pid"), int(state_data.get("qty") or 1),
            state_data.get("mode", "now"), state_data.get("note", "")
        )
        return
    if data.startswith("b2:products:"):
        await show_products(chat_id, int(data.split(":")[-1]), message_id=cb["message"]["message_id"])
        return
    if data.startswith("b2:productnum:"):
        await show_product(chat_id, message_id=cb["message"]["message_id"], product_index=int(data.split(":", 2)[2]))
        return
    if data.startswith("b2:product:"):
        await show_product(chat_id, pid=data.split(":", 2)[2], message_id=cb["message"]["message_id"])
        return
    if data.startswith("b2:buy_balance:"):
        pid = data.split(":", 2)[2]
        await begin_bot2_note(chat_id, user, pid, "balance")
        return
    if data.startswith("b2:buy_now:"):
        pid = data.split(":", 2)[2]
        await begin_bot2_note(chat_id, user, pid, "now")
        return
    if data.startswith("b2:restock:"):
        pid = data.split(":", 2)[2]
        await db.bot2_restock_requests.update_one(
            {"user_tid": user["telegram_id"], "product_id": pid},
            {"$set": {"active": True, "updated_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        await send2(chat_id, "🔔 Notifikasi restok diaktifkan untuk produk ini.", kb=back_keyboard())
        return
    if data.startswith("b2:pay:"):
        order_id = data.split(":", 2)[2]
        await show_checkout_qr(chat_id, user, order_id)
        return
    if data.startswith("b2:cancel_order:"):
        order_id = data.split(":", 2)[2]
        order = await db.purchases.find_one({"_id": order_id, "user_tid": user["telegram_id"], "status": "pending_payment"})
        if order:
            await release_items(f"bot2:{order_id}")
            await db.gopay_payments.update_one({"_id": order.get("payment_id")}, {"$set": {"status": "cancelled"}, "$unset": {"active_payment_amount": ""}})
            await db.purchases.update_one({"_id": order_id}, {"$set": {"status": "cancelled"}})
        await set_b2_state(user["telegram_id"])
        await show_home(chat_id, user, loaded=False)
        return
    if data == "b2:popular":
        await show_products(chat_id, 1)
        return
    if data == "b2:flash":
        discounts = await db.discounts.find({"active": True}).sort("priority", -1).limit(10).to_list(10)
        if not discounts:
            await send2(chat_id, "⚡ Belum ada Flash Sale aktif.", kb=menu_keyboard())
            return
        await send2(chat_id, "⚡ <b>FLASH SALE</b>\n\n" + "\n".join(f"• {escape(str(d.get('name','Promo')))}" for d in discounts), kb=menu_keyboard())


async def handle_message2(message):
    if "from" not in message or message["from"].get("is_bot"):
        return
    chat_id = message["chat"]["id"]
    user = await get_user2(message["from"])
    text = (message.get("text") or "").strip()

    if text.startswith("/start"):
        await set_b2_state(user["telegram_id"])
        loading = await send2(chat_id, "⏳ <b>MEMUAT DATA</b>\n\n▱▱▱▱▱▱▱▱▱▱ <b>0%</b>")
        loading_id = (loading.get("result") or {}).get("message_id")
        import random
        step_count = random.randint(7, 15)
        steps = sorted(random.sample(range(5, 96), step_count))
        for percent in steps + [100]:
            if loading_id:
                await edit2(
                    chat_id,
                    loading_id,
                    f"⏳ <b>MEMUAT DATA</b>\n\n{_progress_bar(percent)} <b>{percent}%</b>",
                )
                # Telegram clients can coalesce very-fast edits; keep each frame visible.
                await asyncio.sleep(0.25)
        if loading_id:
            await show_products(chat_id, 1, message_id=loading_id)
        else:
            await show_products(chat_id, 1)
        return

    if text in ("/menu", "/batal", "/cancel"):
        await set_b2_state(user["telegram_id"])
        await show_home(chat_id, user, loaded=False)
        return

    if text in ("/stock", "/stok", "📦 Laporan Stok"):
        await set_b2_state(user["telegram_id"])
        await show_stock(chat_id)
        return

    if text in ("/help", "❓ Cara Order"):
        await set_b2_state(user["telegram_id"])
        await show_how_to_order(chat_id)
        return

    if text in ("📦 STOCK", "🏷️ List Produk", "🛒 List Produk", "/products"):
        await set_b2_state(user["telegram_id"])
        await show_products(chat_id, 1)
        return

    if text in ("🎟 VOUCHER", "📚 Voucher", "/voucher"):
        await set_b2_state(user["telegram_id"])
        await show_voucher(chat_id)
        return

    if text in ("👤 AKUN / INFORMATION", "⚠️ Information", "/info"):
        await set_b2_state(user["telegram_id"])
        await show_information(chat_id, user)
        return

    if text in ("🛒 PESANAN", "📜 Riwayat", "/history", "/riwayat"):
        await set_b2_state(user["telegram_id"])
        await show_history(chat_id, user)
        return

    if text in ("💰 Deposit", "/deposit"):
        await set_b2_state(user["telegram_id"], "deposit_amount", {})
        await send2(chat_id, "Jumlah Deposit: <b>masukkan nominal IDR</b>\nContoh: <code>50000</code>", kb=cancel_keyboard())
        return

    state = user.get("bot2_state")
    data = user.get("bot2_state_data") or {}

    if state == "note_input":
        await set_b2_state(user["telegram_id"])
        await confirm_bot2_order(chat_id, user, data.get("pid"), 1, data.get("mode", "now"), text)
        return

    if state == "deposit_amount":
        try:
            amount = int(text.replace("Rp", "").replace(".", "").replace(",", "").replace(" ", ""))
        except ValueError:
            await send2(chat_id, "⚠️ Nominal tidak valid. Contoh: <code>50000</code>", kb=cancel_keyboard())
            return
        settings = await get_settings()
        minimum = int(settings.get("min_deposit_idr", 50000))
        maximum = int(settings.get("max_deposit_idr", 100000000))
        if amount < minimum or amount > maximum:
            await send2(chat_id, f"⚠️ Deposit harus antara <b>{fmt_amount(minimum,'IDR')}</b> dan <b>{fmt_amount(maximum,'IDR')}</b>.", kb=cancel_keyboard())
            return
        await set_b2_state(user["telegram_id"], "deposit_method", {"amount": amount})
        await send2(chat_id, f"Jumlah Deposit: <b>{fmt_amount(amount,'IDR')}</b>", kb={"inline_keyboard": [
            [{"text": "💳 Deposit (Now)", "callback_data": "b2:deposit_now"}, {"text": "🏦 Deposit (Manual)", "callback_data": "b2:deposit_manual"}],
            [{"text": "❌ Batal", "callback_data": "b2:cancel"}],
        ]})
        return

    if state == "deposit_bank_proof":
        if not message.get("photo"):
            await send2(chat_id, "⚠️ Kirim foto bukti transfer.", kb=cancel_keyboard())
            return
        amount = int(data.get("amount") or 0)
        file_id = message["photo"][-1]["file_id"]
        await set_b2_state(user["telegram_id"])
        await create_manual_deposit(user, amount, file_id)
        return

    if state == "checkout_payment":
        await send2(chat_id, "⏳ Pembayaran ini menggunakan QRIS. Tekan tombol QRIS pada pesan checkout.", kb=menu_keyboard())
        return

    await show_home(chat_id, user, loaded=False)


async def handle_deposit_callback2(cb):
    user = await get_user2(cb["from"])
    chat_id = cb["message"]["chat"]["id"]
    data = user.get("bot2_state_data") or {}
    amount = int(data.get("amount") or 0)
    if cb.get("data") == "b2:deposit_now":
        await answer2(cb["id"])
        await set_b2_state(user["telegram_id"])
        await create_bot2_deposit_qr(chat_id, user, amount)
    elif cb.get("data") == "b2:deposit_manual":
        await answer2(cb["id"])
        settings = await get_settings()
        if not settings.get("bank_enabled") or not settings.get("bank_account_number"):
            await send2(chat_id, "⚠️ Bank transfer manual belum dikonfigurasi.", kb=menu_keyboard())
            await set_b2_state(user["telegram_id"])
            return
        await set_b2_state(user["telegram_id"], "deposit_bank_proof", {"amount": amount})
        await send2(
            chat_id,
            f"🏦 <b>Deposit Manual</b>\n\n"
            f"Bank: <b>{escape(str(settings.get('bank_name','')))}</b>\n"
            f"Nomor: <code>{escape(str(settings.get('bank_account_number','')))}</code>\n"
            f"Atas Nama: <b>{escape(str(settings.get('bank_account_holder','')))}</b>\n"
            f"Nominal: <b>{fmt_amount(amount,'IDR')}</b>\n\n"            "Silakan transfer sesuai nominal, lalu kirim foto bukti transfer.",
            kb=cancel_keyboard(),
        )


async def process_update2(update):
    try:
        if "callback_query" in update:
            data = update["callback_query"].get("data", "")
            if data in {"b2:deposit_now", "b2:deposit_manual"}:
                await handle_deposit_callback2(update["callback_query"])
            else:
                await handle_callback2(update["callback_query"])
        elif "message" in update:
            await handle_message2(update["message"])
    except Exception:
        logger.exception("Bot2 update failed")


async def _expire_bot2_orders():
    now = datetime.now(timezone.utc).isoformat()
    cursor = db.purchases.find({"bot2": True, "payment_scope": PAYMENT_SCOPE, "status": "pending_payment", "expires_at": {"$lte": now}})
    async for order in cursor:
        await release_items(f"bot2:{order['_id']}")
        await db.gopay_payments.update_one(
            {"_id": order.get("payment_id"), "payment_scope": PAYMENT_SCOPE, "status": "pending"},
            {"$set": {"status": "expired"}, "$unset": {"active_payment_amount": ""}},
        )
        await db.purchases.update_one(
            {"_id": order["_id"], "status": "pending_payment"},
            {"$set": {"status": "expired"}},
        )


async def _expire_bot2_deposits():
    now = datetime.now(timezone.utc).isoformat()
    cursor = db.deposits.find({"bot2": True, "method": "gopay", "payment_id": {"$exists": True}, "status": "pending", "expires_at": {"$lte": now}})
    async for dep in cursor:
        payment = await db.gopay_payments.find_one({"_id": dep.get("payment_id"), "payment_scope": PAYMENT_SCOPE})
        if payment:
            await db.gopay_payments.update_one({"_id": payment["_id"], "status": "pending"}, {"$set": {"status": "expired"}, "$unset": {"active_payment_amount": ""}})
        await db.deposits.update_one({"_id": dep["_id"], "status": "pending"}, {"$set": {"status": "expired", "decided_at": datetime.now(timezone.utc).isoformat()}})


async def run_bot2_payment_monitor(stop_event: asyncio.Event):
    if os.environ.get("GOPAY_ENABLED", "").lower() not in {"1", "true", "yes"}:
        return
    interval = max(10, int(os.environ.get("BOT2_PAYMENT_POLL_INTERVAL", "15")))
    while not stop_event.is_set():
        try:
            await _expire_bot2_orders()
            await _expire_bot2_deposits()
            settings = await get_settings()
            if settings.get("qris_enabled", False):
                history = await asyncio.to_thread(_run_node, "history.mjs", [], 90)
                now = datetime.now(timezone.utc).isoformat()
                for tx in history:
                    tx_id = tx.get("tx_id")
                    tx_amount = int(round(float(tx.get("amount") or 0)))
                    status = str(tx.get("status") or "").lower()
                    tx_type = str(tx.get("type") or "").lower()
                    if not tx_id or tx_amount <= 0:
                        continue
                    if tx_type and tx_type != "payin":
                        continue
                    if status and status not in {"settlement", "capture", "paid", "success", "successful"}:
                        continue
                    payment = await db.gopay_payments.find_one_and_update(
                        {
                            "payment_scope": PAYMENT_SCOPE,
                            "status": "pending",
                            "active_payment_amount": tx_amount,
                            "expires_at": {"$gt": now},
                        },
                        {"$set": {"status": "confirmed", "tx_id": tx_id, "confirmed_at": datetime.now(timezone.utc).isoformat()}, "$unset": {"active_payment_amount": ""}},
                    )
                    if not payment:
                        continue
                    if payment.get("payment_type") == "checkout":
                        await finalize_bot2_checkout(payment["order_id"], tx_id=tx_id)
                    elif payment.get("payment_type") == "deposit":
                        dep = await db.deposits.find_one({"_id": payment.get("deposit_id"), "status": "pending"})
                        if not dep:
                            continue
                        result = await db.bot_users.update_one(
                            {"telegram_id": dep["user_tid"], "deposit_credit_ids": {"$ne": dep["_id"]}},
                            {"$inc": {"balance_idr": dep["amount"]}, "$addToSet": {"deposit_credit_ids": dep["_id"]}},
                        )
                        if result.modified_count:
                            await db.deposits.update_one(
                                {"_id": dep["_id"], "status": "pending"},
                                {"$set": {"status": "approved", "credited_amount": dep["amount"], "gopay_tx_id": tx_id, "decided_at": datetime.now(timezone.utc).isoformat(), "note": f"QRIS Bot2 terverifikasi: {tx_id}"}},
                            )
                            await send2(dep["user_tid"], f"✅ <b>Deposit berhasil!</b>\n\nSaldo bertambah <b>{fmt_amount(dep['amount'],'IDR')}</b>.", kb=menu_keyboard())
        except Exception:
            logger.exception("Bot2 payment monitor error")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass