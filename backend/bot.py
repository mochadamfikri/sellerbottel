import uuid
import logging
import math
import re
import asyncio
import secrets
from html import escape
from datetime import datetime, timezone, timedelta
from db import db, get_settings
from rates import get_rate
from chain import verify_tx, looks_like_tx_hash
from tgapi import (
    send_message as tg_send_message,
    edit_message as tg_edit_message,
    answer_callback,
    send_document,
    delete_message,
    send_photo_bytes,
)
from services import credit_deposit, reject_deposit, cancel_deposit, notify_admin, fmt_amount, now_iso, user_lang
from storage import get_object
from i18n import t, LANG_NAMES
from checkout import execute_checkout, stock_for
from inventory import decrypt_items
from join_gate import check_user_membership, build_gate_keyboard, clear_cache_for_user
from gopay_provider import create_gopay_payment
from pricing import price_for_product

logger = logging.getLogger("bot")

_EDIT_TARGETS = {}


async def send_message(chat_id, text, kb=None):
    message_id = _EDIT_TARGETS.pop(chat_id, None)
    if message_id is not None:
        try:
            result = await tg_edit_message(chat_id, message_id, text, kb=kb)
            if result.get("ok"):
                return result
        except Exception:
            logger.exception("Failed to edit callback message; falling back to sendMessage")
    return await tg_send_message(chat_id, text, kb=kb)


_CHECKOUT_LOCKS = {}


def _checkout_lock(tid):
    lock = _CHECKOUT_LOCKS.get(tid)
    if lock is None:
        lock = asyncio.Lock()
        _CHECKOUT_LOCKS[tid] = lock
    return lock


NET_LABELS = {"SOL": "Solana", "POL": "Polygon", "BNB": "BNB (BEP-20)", "AVAX": "Avalanche"}
CUR_FIELD = {"USD": "balance_usd", "IDR": "balance_idr"}


def main_menu_kb(lang):
    return {"inline_keyboard": [
        [{"text": t(lang, "btn_products"), "callback_data": "menu:products"}, {"text": t(lang, "btn_cart"), "callback_data": "menu:cart"}],
        [{"text": t(lang, "btn_deposit"), "callback_data": "menu:deposit"}, {"text": t(lang, "btn_balance"), "callback_data": "menu:balance"}],
        [{"text": t(lang, "btn_stock"), "callback_data": "menu:stock"}, {"text": t(lang, "btn_history"), "callback_data": "menu:history"}],
        [{"text": t(lang, "btn_settings"), "callback_data": "menu:settings"}, {"text": t(lang, "btn_help"), "callback_data": "menu:help"}],
    ]}


def back_kb(lang):
    return {"inline_keyboard": [[{"text": t(lang, "btn_main"), "callback_data": "menu:main"}]]}


def cancel_kb(lang):
    return {"inline_keyboard": [[{"text": t(lang, "btn_cancel"), "callback_data": "cancel"}]]}


async def get_user(tg_from: dict) -> dict:
    tid = tg_from["id"]
    user = await db.bot_users.find_one({"telegram_id": tid})
    if not user:
        user = {
            "_id": str(uuid.uuid4()), "telegram_id": tid,
            "username": tg_from.get("username", ""), "first_name": tg_from.get("first_name", ""),
            "currency": None, "lang": "id", "balance_usd": 0.0, "balance_idr": 0.0,
            "frozen": False, "frozen_reason": "", "cart": [],
            "state": None, "state_data": {}, "created_at": now_iso(),
        }
        await db.bot_users.insert_one(user)
    else:
        await db.bot_users.update_one({"telegram_id": tid}, {"$set": {
            "username": tg_from.get("username", ""), "first_name": tg_from.get("first_name", "")}})
        if not user.get("lang"):
            user["lang"] = "id"
    return user


async def set_state(tid, state, data=None):
    await db.bot_users.update_one({"telegram_id": tid}, {"$set": {"state": state, "state_data": data or {}}})


async def product_price(prod: dict, currency: str, quantity: int = 1) -> float:
    pricing = await price_for_product(prod, currency, quantity)
    return pricing["unit_price"]


async def stock_label(prod, lang):
    stock = await stock_for(prod)
    return "∞" if stock is None else str(int(stock))


async def has_stock(prod, qty=1):
    stock = await stock_for(prod)
    return True if stock is None else stock >= qty


def norm_cart(cart):
    out = []
    for it in cart or []:
        if isinstance(it, str):
            out.append({"pid": it, "qty": 1})
        elif isinstance(it, dict) and it.get("pid"):
            out.append({"pid": it["pid"], "qty": max(1, int(it.get("qty", 1)))})
    return out


def user_label(user):
    uname = f"@{user.get('username')}" if user.get("username") else "-"
    return f"{user.get('first_name','')} ({uname}, ID: <code>{user['telegram_id']}</code>)"


async def show_main_menu(chat_id, user):
    lang = user.get("lang", "id")
    bal = fmt_amount(user.get(CUR_FIELD[user["currency"]], 0), user["currency"])
    await send_message(chat_id, t(lang, "main_title", name=user.get("first_name", ""), balance=bal), kb=main_menu_kb(lang))


async def show_currency_selection(chat_id, lang="id"):
    kb = {"inline_keyboard": [
        [{"text": "💵 USD (Dolar AS)", "callback_data": "cur:USD"}],
        [{"text": "🇮🇩 IDR (Rupiah)", "callback_data": "cur:IDR"}],
    ]}
    await send_message(chat_id, t(lang, "choose_currency"), kb=kb)


# ============ PRODUCTS & STOCK ============

async def show_products(chat_id, user, page=1):
    lang = user.get("lang", "id")
    page = max(1, int(page))
    page_size = 8
    query = {"active": True}
    total_count = await db.products.count_documents(query)
    products = await db.products.find(query).sort("created_at", -1).skip((page - 1) * page_size).limit(page_size).to_list(page_size)

    if not products:
        await send_message(chat_id, t(lang, "no_products"), kb=back_kb(lang))
        return

    rows = []
    for p in products:
        price = await product_price(p, user["currency"])
        sl = await stock_label(p, lang)
        prefix = "❌ " if not await has_stock(p) else ""
        rows.append([
            {
                "text": f"{prefix}{p['name']} — {fmt_amount(price, user['currency'])} ({t(lang,'stock_word')} {sl})",
                "callback_data": f"prod:{p['_id']}",
            }
        ])

    nav = []
    if page > 1:
        nav.append({"text": "⬅️ Sebelumnya", "callback_data": f"products:{page - 1}"})
    if page * page_size < total_count:
        nav.append({"text": "➡️ Berikutnya", "callback_data": f"products:{page + 1}"})
    if nav:
        rows.append(nav)
    rows.append([{"text": t(lang, "btn_main"), "callback_data": "menu:main"}])

    await send_message(
        chat_id,
        t(lang, "products_title") + f"\n\nHalaman {page}/{max(1, (total_count + page_size - 1) // page_size)}",
        kb={"inline_keyboard": rows},
    )

async def show_stock(chat_id, user):
    lang = user.get("lang", "id")
    products = await db.products.find({"active": True}).to_list(200)
    if not products:
        await send_message(chat_id, t(lang, "stock_title") + "\n" + t(lang, "stock_empty"), kb=back_kb(lang))
        return
    lines = [t(lang, "stock_title")]
    for p in products:
        price = await product_price(p, user["currency"])
        sl = await stock_label(p, lang)
        mark = "❌" if not await has_stock(p) else "✅"
        lines.append(f"{mark} {p['name']} — {fmt_amount(price, user['currency'])} → {t(lang,'stock_word')} <b>{sl}</b>")
    await send_message(chat_id, "\n".join(lines), kb=back_kb(lang))


async def show_product_detail(chat_id, user, pid):
    lang = user.get("lang", "id")
    p = await db.products.find_one({"_id": pid, "active": True})
    if not p:
        await send_message(chat_id, t(lang, "product_not_found"), kb=back_kb(lang))
        return
    price = await product_price(p, user["currency"])
    type_label = t(lang, {"file": "type_file", "link": "type_link", "license": "type_license", "inventory": "type_inventory"}.get(p["delivery_type"], "type_file"))
    text = t(lang, "prod_detail", name=p["name"], desc=p.get("description", ""), type=type_label,
             price=fmt_amount(price, user["currency"]), stock=await stock_label(p, lang))
    rows = []
    if await has_stock(p):
        rows.append([{"text": t(lang, "btn_buy", price=fmt_amount(price, user["currency"])), "callback_data": f"buy:{pid}"}])
        rows.append([{"text": t(lang, "btn_add_cart"), "callback_data": f"cartadd:{pid}"}])
    else:
        text += "\n\n" + t(lang, "out_of_stock")
    rows.append([{"text": t(lang, "btn_back"), "callback_data": "menu:products"}, {"text": t(lang, "btn_menu_short"), "callback_data": "menu:main"}])
    await send_message(chat_id, text, kb={"inline_keyboard": rows})


# ============ CART ============

async def save_cart(tid, cart):
    await db.bot_users.update_one({"telegram_id": tid}, {"$set": {"cart": cart}})


async def show_cart(chat_id, user):
    lang = user.get("lang", "id")
    cart = norm_cart(user.get("cart"))
    if not cart:
        await send_message(chat_id, t(lang, "cart_empty"), kb={"inline_keyboard": [
            [{"text": t(lang, "btn_products"), "callback_data": "menu:products"}],
            [{"text": t(lang, "btn_main"), "callback_data": "menu:main"}]]})
        return
    lines, total, rows = [], 0.0, []
    valid_cart = []
    for item in cart:
        p = await db.products.find_one({"_id": item["pid"], "active": True})
        if not p:
            continue
        valid_cart.append(item)
        price = await product_price(p, user["currency"], item["qty"])
        subtotal = price * item["qty"]
        total += subtotal
        lines.append(f"• {p['name']} ×{item['qty']} — {fmt_amount(subtotal, user['currency'])}")
        rows.append([
            {"text": "➖", "callback_data": f"qtydec:{item['pid']}"},
            {"text": f"{p['name'][:20]} ×{item['qty']}", "callback_data": f"prod:{item['pid']}"},
            {"text": "➕", "callback_data": f"qtyinc:{item['pid']}"},
            {"text": "🗑", "callback_data": f"cartrm:{item['pid']}"},
        ])
        rows.append([
            {"text": "🔢 Masukkan jumlah sendiri", "callback_data": f"qtycustom:{item['pid']}"},
        ])
    if len(valid_cart) != len(cart):
        await save_cart(user["telegram_id"], valid_cart)
    rows.append([{"text": t(lang, "btn_checkout", total=fmt_amount(total, user["currency"])), "callback_data": "checkout"}])
    rows.append([{"text": t(lang, "btn_clear"), "callback_data": "cartclear"}, {"text": t(lang, "btn_menu_short"), "callback_data": "menu:main"}])
    text = t(lang, "cart_title") + "\n\n" + "\n".join(lines) + "\n\n" + t(lang, "cart_total", total=fmt_amount(total, user["currency"]))
    await send_message(chat_id, text, kb={"inline_keyboard": rows})


async def change_qty(chat_id, user, pid, delta):
    lang = user.get("lang", "id")
    cart = norm_cart(user.get("cart"))
    p = await db.products.find_one({"_id": pid})
    for item in cart:
        if item["pid"] == pid:
            new_qty = item["qty"] + delta
            if new_qty < 1:
                cart = [i for i in cart if i["pid"] != pid]
            elif p and not await has_stock(p, new_qty):
                await send_message(chat_id, t(lang, "qty_max", stock=await stock_label(p, lang)))
                return
            else:
                item["qty"] = new_qty
            break
    await save_cart(user["telegram_id"], cart)
    user["cart"] = cart
    await show_cart(chat_id, user)


async def start_custom_quantity(chat_id, user, pid):
    lang = user.get("lang", "id")
    product = await db.products.find_one({"_id": pid, "active": True})
    if not product:
        await send_message(chat_id, "❌ Product tidak ditemukan atau sudah tidak aktif.", kb=back_kb(lang))
        return

    cart = norm_cart(user.get("cart"))
    if not any(item["pid"] == pid for item in cart):
        await send_message(chat_id, "⚠️ Product tersebut tidak ada di keranjang.", kb={"inline_keyboard": [
            [{"text": "🛒 Kembali ke Keranjang", "callback_data": "menu:cart"}],
            [{"text": "🏠 Menu", "callback_data": "menu:main"}],
        ]})
        return

    current_item = next((item for item in cart if item["pid"] == pid), None)
    current_qty = int(current_item.get("qty", 1)) if current_item else 1
    price = await product_price(product, user["currency"])
    stock = await stock_for(product)
    stock_text = "♾️ Unlimited" if stock is None else f"📦 Tersedia: {int(stock)}"

    await set_state(
        user["telegram_id"],
        "cart_custom_qty",
        {"pid": pid},
    )
    await send_message(
        chat_id,
        f"🔢 <b>Masukkan Jumlah Pembelian</b>\n\n"
        f"📦 Product: <b>{escape(product['name'])}</b>\n"
        f"💰 Harga satuan: <b>{fmt_amount(price, user['currency'])}</b>\n"
        f"🔢 Jumlah saat ini: <b>{current_qty}</b>\n"
        f"{stock_text}\n\n"
        "Silakan kirim <b>angka total pembelian</b> yang diinginkan.\n"
        "Contoh: <code>15</code>\n\n"
        "💡 Jumlah akan langsung diperbarui di keranjang setelah Anda memasukkan angka.",
        kb={"inline_keyboard": [
            [{"text": "❌ Batal", "callback_data": "menu:cart"}],
        ]},
    )


async def handle_custom_quantity(chat_id, user, text):
    lang = user.get("lang", "id")
    data = user.get("state_data") or {}
    pid = str(data.get("pid") or "").strip()

    if not pid:
        await set_state(user["telegram_id"], None)
        await show_cart(chat_id, user)
        return

    if not text.isdigit():
        await send_message(
            chat_id,
            "❗ <b>Input tidak valid.</b>\n\nSilakan masukkan angka bulat saja.\nContoh: <code>15</code>",
            kb={"inline_keyboard": [
                [{"text": "❌ Batal", "callback_data": "menu:cart"}],
            ]},
        )
        return

    qty = int(text)
    if qty < 1:
        await send_message(chat_id, "❗ Jumlah minimal adalah <b>1</b>.", kb={"inline_keyboard": [
            [{"text": "❌ Batal", "callback_data": "menu:cart"}],
        ]})
        return

    if qty > 10000:
        await send_message(
            chat_id,
            "⚠️ Jumlah terlalu besar. Maksimal <b>10.000</b> item per input.",
            kb={"inline_keyboard": [
                [{"text": "❌ Batal", "callback_data": "menu:cart"}],
            ]},
        )
        return

    product = await db.products.find_one({"_id": pid, "active": True})
    if not product:
        await set_state(user["telegram_id"], None)
        await send_message(chat_id, "❌ Product sudah tidak tersedia.", kb={"inline_keyboard": [
            [{"text": "🛒 Keranjang", "callback_data": "menu:cart"}],
        ]})
        return

    stock = await stock_for(product)
    if stock is not None and qty > stock:
        await send_message(
            chat_id,
            f"⚠️ <b>Jumlah melebihi stok.</b>\n\n"
            f"Product: <b>{escape(product['name'])}</b>\n"
            f"📦 Stok tersedia: <b>{int(stock)}</b>\n"
            f"🔢 Anda memasukkan: <b>{qty}</b>\n\n"
            "Silakan masukkan jumlah yang tidak melebihi stok.",
            kb={"inline_keyboard": [
                [{"text": "❌ Batal", "callback_data": "menu:cart"}],
            ]},
        )
        return

    pricing = await price_for_product(product, user["currency"], qty)
    unit_price = pricing["unit_price"]
    total = unit_price * qty

    cart = norm_cart(user.get("cart"))
    updated = False
    for item in cart:
        if item["pid"] == pid:
            item["qty"] = qty
            updated = True
            break
    if not updated:
        cart.append({"pid": pid, "qty": qty})
    await save_cart(user["telegram_id"], cart)
    user["cart"] = cart

    await set_state(
        user["telegram_id"],
        "cart_custom_confirm",
        {"pid": pid, "qty": qty},
    )

    await send_message(
        chat_id,
        "🛒 <b>Konfirmasi Pembelian</b>\n\n"
        f"📦 Anda akan membeli product: <b>{escape(product['name'])}</b>\n"
        f"💰 Harga satuan: <b>{fmt_amount(unit_price, user['currency'])}</b>\n"
        f"🔢 Total pesanan: <b>{qty}</b>\n"
        f"🧮 Perhitungan: <b>{qty} × {fmt_amount(unit_price, user['currency'])}</b>\n"
        f"💵 <b>Total harga: {fmt_amount(total, user['currency'])}</b>\n\n"
        "✅ Silakan klik <b>Bayar</b> untuk melanjutkan proses pembayaran dan pengiriman.",
        kb={"inline_keyboard": [
            [{"text": f"💳 Bayar {fmt_amount(total, user['currency'])}", "callback_data": f"custompay:{pid}:{qty}"}],
            [{"text": "✏️ Ubah Jumlah", "callback_data": f"qtycustom:{pid}"}, {"text": "🛒 Keranjang", "callback_data": "menu:cart"}],
        ]},
    )


async def add_to_cart(chat_id, user, pid):
    lang = user.get("lang", "id")
    p = await db.products.find_one({"_id": pid, "active": True})
    if not p:
        await send_message(chat_id, t(lang, "product_not_found"), kb=back_kb(lang))
        return
    cart = norm_cart(user.get("cart"))
    existing = next((i for i in cart if i["pid"] == pid), None)
    new_qty = (existing["qty"] + 1) if existing else 1
    if not await has_stock(p, new_qty):
        await send_message(chat_id, t(lang, "qty_max", stock=await stock_label(p, lang)), kb=back_kb(lang))
        return
    if existing:
        existing["qty"] = new_qty
    else:
        cart.append({"pid": pid, "qty": 1})
    await save_cart(user["telegram_id"], cart)
    await send_message(chat_id, t(lang, "added_cart"), kb={"inline_keyboard": [
        [{"text": t(lang, "btn_view_cart"), "callback_data": "menu:cart"}],
        [{"text": t(lang, "btn_continue"), "callback_data": "menu:products"}]]})


# ============ CHECKOUT & DELIVERY ============

async def deliver_product(chat_id, p, lang):
    try:
        if p["delivery_type"] == "file" and p.get("storage_path"):
            data, _ = await get_object(p["storage_path"])
            result = await send_document(
                chat_id,
                data,
                p.get("original_filename", "produk.bin"),
                caption=f"📦 {p['name']}",
            )
            return bool(result.get("ok"))
        if p["delivery_type"] == "link":
            result = await send_message(
                chat_id,
                t(lang, "deliver_link", name=p["name"], content=p.get("content", "")),
            )
            return bool(result.get("ok"))
        result = await send_message(
            chat_id,
            t(lang, "deliver_license", name=p["name"], content=p.get("content", "")),
        )
        return bool(result.get("ok"))
    except Exception:
        logger.exception("product delivery failed")
        await send_message(chat_id, t(lang, "deliver_fail", name=p["name"]))
        return False


def _normalize_inventory_field_name(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _inventory_field_value(record: dict, aliases: list[str], default="none"):
    if not isinstance(record, dict):
        return default

    normalized = {
        _normalize_inventory_field_name(key): value
        for key, value in record.items()
    }
    for alias in aliases:
        key = _normalize_inventory_field_name(alias)
        if key in normalized:
            value = normalized[key]
            if value is None or str(value).strip() == "":
                return default
            return str(value).strip()
    return default


def _account_values(record: dict):
    return {
        "email": _inventory_field_value(
            record,
            ["email", "email_address", "mail", "emailaddress"],
        ),
        "password": _inventory_field_value(
            record,
            ["password", "pass", "passwd", "pwd"],
        ),
        "recovery": _inventory_field_value(
            record,
            ["recovery", "recovery_email", "recoveryemail", "recovery_mail"],
        ),
        "2fa": _inventory_field_value(
            record,
            ["2fa", "2fa_key", "2fa_secret", "authenticator", "otp_secret", "totp", "totp_secret"],
        ),
    }


def _looks_like_account_record(record: dict):
    if not isinstance(record, dict):
        return False
    keys = {_normalize_inventory_field_name(key) for key in record.keys()}
    aliases = {
        "email", "email_address", "mail", "emailaddress",
        "password", "pass", "passwd", "pwd",
        "recovery", "recovery_email", "recoveryemail", "recovery_mail",
        "2fa", "2fa_key", "2fa_secret", "authenticator", "otp_secret", "totp", "totp_secret",
    }
    return bool(keys & aliases)


def _inventory_record_lines(record: dict, schema: list[str]):
    if not isinstance(record, dict):
        return [str(record)]

    if _looks_like_account_record(record):
        values = _account_values(record)
        return [
            f"email: {values['email']}",
            f"password: {values['password']}",
            f"recovery: {values['recovery']}",
            f"2fa: {values['2fa']}",
        ]

    fields = schema or list(record.keys())
    return [
        f"{field}: {record.get(field, '')}"
        for field in fields
        if str(record.get(field, "")).strip() != ""
    ]


def _account_txt_line(record: dict):
    values = _account_values(record)
    return ":".join([
        values["email"].replace("\n", " ").replace("\r", " "),
        values["password"].replace("\n", " ").replace("\r", " "),
        values["recovery"].replace("\n", " ").replace("\r", " "),
        values["2fa"].replace("\n", " ").replace("\r", " "),
    ])


def _safe_filename_part(value):
    value = re.sub(r"[^\w.-]+", "_", str(value or "").strip(), flags=re.UNICODE)
    return value.strip("._") or "product"


async def deliver_inventory(chat_id, product, records):
    if not records:
        return False

    schema = product.get("inventory_schema") or ["value"]
    account_mode = all(_looks_like_account_record(record) for record in records)
    plain_records = [
        _inventory_record_lines(record, schema)
        for record in records
    ]

    if len(records) > 20:
        if account_mode:
            detail_lines = [
                f"{index}. {_account_txt_line(record)}"
                for index, record in enumerate(records, 1)
            ]
            payload_text = (
                "format akun= email:password:recovery:2fa_key\n"
                "note: recovery jika none berarti tidak ada opsi pemulihan yang tertanam di akun\n\n"
                + "\n".join(detail_lines)
            )
        else:
            detail_lines = [
                f"{index}.\n" + "\n".join(lines)
                for index, lines in enumerate(plain_records, 1)
            ]
            payload_text = "\n\n".join(detail_lines)

        filename = (
            f"invoice_{_safe_filename_part(product.get('name'))}_{len(records)}.txt"
        )
        result = await send_document(
            chat_id,
            payload_text.encode("utf-8"),
            filename,
            caption=f"📦 {product['name']} — {len(records)} item",
        )
        return bool(result.get("ok"))

    blocks = []
    for index, record in enumerate(records, 1):
        if _looks_like_account_record(record):
            values = _account_values(record)
            body = "\n".join([
                f"email: <code>{escape(values['email'])}</code>",
                f"password: <code>{escape(values['password'])}</code>",
                f"recovery: <code>{escape(values['recovery'])}</code>",
                f"2fa: <code>{escape(values['2fa'])}</code>",
            ])
        else:
            record_lines = plain_records[index - 1]
            body = "\n".join(
                f"<b>{escape(line.split(':', 1)[0])}:</b> <code>{escape(line.split(':', 1)[1].strip())}</code>"
                if ":" in line else f"<code>{escape(line)}</code>"
                for line in record_lines
            )
        blocks.append(f"<b>#{index}</b>\n{body}")

    result = await send_message(
        chat_id,
        "<b>📦 " + escape(product["name"]) + "</b>\n\n" + "\n\n".join(blocks),
    )
    return bool(result.get("ok"))


def build_invoice_text(order):
    currency = order.get("currency", "IDR")
    lines = [
        f"Invoice {order.get('invoice_id', '-')}",
        f"tanggal transaksi: {order.get('created_at', '-')}",
        f"status: {order.get('status', 'pending')}",
        f"metode pembayaran: {order.get('payment_method', 'balance')}",
        "",
        "Detail pembelian:",
    ]
    for item in order.get("items", []):
        name = str(item.get("name", "Produk"))
        qty = int(item.get("qty") or 0)
        unit = fmt_amount(item.get("unit_price", 0), currency)
        subtotal = fmt_amount(item.get("subtotal", 0), currency)
        lines.append(f"• {name}")
        lines.append(f"  quantity: {qty} akun/item")
        lines.append(f"  harga/unit: {unit}")
        lines.append(f"  subtotal: {subtotal}")
        if float(item.get("discount_total") or 0) > 0:
            lines.append(f"  diskon: {fmt_amount(item.get('discount_total'), currency)}")

    lines.extend([
        "",
        f"total diskon: {fmt_amount(order.get('discount_total', 0), currency)}",
        f"total transaksi: {fmt_amount(order.get('total', 0), currency)}",
        "",
        "terimakasih telah membeli.",
    ])
    return "<pre>" + escape("\n".join(lines)) + "</pre>"


_SERVICE_TASKS = {}

def _service_remaining_text(seconds: int) -> str:
    minutes = max(0, int((seconds + 59) // 60))
    return f"{minutes} menit" if minutes != 1 else "1 menit"


def _service_template(product: dict) -> str:
    return (
        product.get("service_message_template")
        or "Jasa {product_name} sedang dalam antrean, harap tunggu {wait_minutes} untuk dapat menghubungi admin."
    )


async def _run_service_wait(order_id: str, user_tid: int, chat_id: int, product: dict, lang: str, ready_at: str, message_id: int | None = None):
    task_key = f"{order_id}:{product['_id']}"
    try:
        ready = datetime.fromisoformat(ready_at)
        while True:
            remaining = (ready - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                break
            await asyncio.sleep(min(60, max(1, remaining)))
            if message_id:
                left = (ready - datetime.now(timezone.utc)).total_seconds()
                if left > 0:
                    try:
                        await tg_edit_message(
                            chat_id,
                            message_id,
                            f"⏳ <b>{escape(product['name'])}</b> masih dalam antrean.\nWaktu tersisa: <b>{_service_remaining_text(int(left))}</b>.",
                            kb={"inline_keyboard": [[{"text": "🔒 Hubungi Admin", "callback_data": f"service:wait:{order_id}"}]]},
                        )
                    except Exception:
                        logger.exception("Gagal memperbarui countdown jasa %s", order_id)

        order = await db.purchases.find_one({"_id": order_id, "user_tid": user_tid})
        if not order or order.get("status") not in {"service_waiting", "paid"}:
            return

        admin_settings = await get_settings()
        admin_id = str(admin_settings.get("admin_telegram_id") or "").strip()
        admin_kb = None
        if admin_id:
            admin_kb = {"inline_keyboard": [[{"text": "💬 Hubungi Customer", "url": f"tg://user?id={user_tid}"}]]}

        try:
            await notify_admin(
                f"🛎️ <b>Jasa siap dihubungi</b>\n"
                f"Produk: <b>{escape(product['name'])}</b>\n"
                f"Order: <code>{escape(str(order.get('invoice_id', order_id)))}</code>\n"
                f"Customer ID: <code>{user_tid}</code>",
                kb=admin_kb,
            )
        except Exception:
            logger.exception("Notifikasi admin jasa gagal untuk order %s", order_id)

        if message_id:
            try:
                await tg_edit_message(
                    chat_id,
                    message_id,
                    f"✅ <b>{escape(product['name'])}</b> sudah selesai antre.\nSilakan hubungi admin untuk melanjutkan.",
                    kb={"inline_keyboard": [[{"text": "💬 Chat Admin", "url": f"tg://user?id={admin_id}"}]]} if admin_id else None,
                )
            except Exception:
                logger.exception("Gagal membuka tombol admin untuk order %s", order_id)

        await db.purchases.update_one(
            {"_id": order_id, "status": {"$in": ["paid", "service_waiting"]}},
            {"$inc": {"service_pending_count": -1}},
        )
        fresh = await db.purchases.find_one({"_id": order_id})
        if fresh and int(fresh.get("service_pending_count") or 0) <= 0:
            await db.purchases.update_one(
                {"_id": order_id, "status": "service_waiting"},
                {"$set": {"status": "delivered", "delivered_at": now_iso(), "delivery_error": None}},
            )
            latest = await db.purchases.find_one({"_id": order_id})
            if latest and latest.get("status") == "delivered":
                u = await db.bot_users.find_one({"telegram_id": user_tid})
                if u:
                    await send_message(
                        chat_id,
                        t(
                            u.get("lang") or lang,
                            "delivered_all",
                            balance=fmt_amount(
                                u.get(CUR_FIELD[latest.get("currency", "IDR")], 0),
                                latest.get("currency", "IDR"),
                            ),
                        ),
                        kb=back_kb(u.get("lang") or lang),
                    )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Service wait failed for order %s product %s", order_id, product.get("_id"))
    finally:
        _SERVICE_TASKS.pop(task_key, None)


async def queue_service_delivery(chat_id: int, user: dict, product: dict, order: dict, lang: str):
    wait_minutes = int(product.get("service_wait_minutes") or 5)
    ready_at = (datetime.now(timezone.utc) + timedelta(minutes=wait_minutes)).isoformat()
    template = _service_template(product)
    try:
        text = template.format(
            product_name=escape(str(product.get("name") or "Produk Jasa")),
            wait_minutes=wait_minutes,
        )
    except Exception:
        text = (
            f"Jasa <b>{escape(str(product.get('name') or 'Produk Jasa'))}</b> sedang dalam antrean, "
            f"harap tunggu <b>{wait_minutes} menit</b> untuk dapat menghubungi admin."
        )
    text = f"⏳ {text}\n\nWaktu tersisa: <b>{wait_minutes} menit</b>."
    result = await send_message(
        chat_id,
        text,
        kb={"inline_keyboard": [[{"text": "🔒 Hubungi Admin", "callback_data": f"service:wait:{order['_id']}"}]]},
    )
    message_id = ((result or {}).get("result") or {}).get("message_id")
    await db.purchases.update_one(
        {"_id": order["_id"]},
        {
            "$set": {
                "status": "service_waiting",
                "service_waiting": True,
                "service_ready_at": ready_at,
                "service_message_id": message_id,
            },
            "$inc": {"service_pending_count": 1},
        },
    )
    task_key = f"{order['_id']}:{product['_id']}"
    _SERVICE_TASKS[task_key] = asyncio.create_task(
        _run_service_wait(
            order["_id"],
            user["telegram_id"],
            chat_id,
            product,
            lang,
            ready_at,
            message_id,
        )
    )


async def resume_service_waiters():
    orders = await db.purchases.find({"status": "service_waiting", "service_ready_at": {"$exists": True}}).to_list(200)
    for order in orders:
        for item in order.get("items", []):
            if item.get("delivery_type") != "service":
                continue
            product = await db.products.find_one({"_id": item.get("product_id")})
            if not product:
                continue
            task_key = f"{order['_id']}:{product['_id']}"
            if task_key in _SERVICE_TASKS:
                continue
            _SERVICE_TASKS[task_key] = asyncio.create_task(
                _run_service_wait(
                    order["_id"],
                    order["user_tid"],
                    order["user_tid"],
                    product,
                    (await user_lang(order["user_tid"])),
                    order.get("service_ready_at") or now_iso(),
                    order.get("service_message_id"),
                )
            )


async def _do_checkout(chat_id, user, cart_items, preserve_cart=False):
    lang = user.get("lang", "id")

    result = await execute_checkout(user, cart_items, preserve_cart=preserve_cart)
    if not result["ok"]:
        if result["error"] == "stock":
            p = result["product"]
            await send_message(
                chat_id,
                t(
                    lang,
                    "stock_insufficient",
                    name=p["name"],
                    stock=result["stock"],
                ),
                kb=back_kb(lang),
            )
            return

        if result["error"] == "empty":
            await send_message(chat_id, t(lang, "no_valid_products"), kb=back_kb(lang))
            return

        if result["error"] == "checkout" and "Saldo" in result.get("message", ""):
            ptotal = 0.0
            for item in cart_items:
                p = await db.products.find_one({"_id": item["pid"], "active": True})
                if p:
                    ptotal += await product_price(p, user["currency"]) * max(1, int(item.get("qty", 1)))
            balance = float(user.get(CUR_FIELD[user["currency"]], 0))
            await send_message(
                chat_id,
                t(
                    lang,
                    "insufficient",
                    total=fmt_amount(ptotal, user["currency"]),
                    balance=fmt_amount(balance, user["currency"]),
                    short=fmt_amount(max(0, ptotal - balance), user["currency"]),
                ),
                kb={
                    "inline_keyboard": [
                        [{"text": t(lang, "btn_deposit_now"), "callback_data": "menu:deposit"}],
                        [{"text": t(lang, "btn_main"), "callback_data": "menu:main"}],
                    ]
                },
            )
            return

        await send_message(chat_id, t(lang, "checkout_failed"), kb=back_kb(lang))
        return

    order = result["order"]
    await send_message(chat_id, build_invoice_text(order))
    await send_message(
        chat_id,
        t(lang, "pay_success", total=fmt_amount(order["total"], order["currency"])),
    )

    all_delivered = True
    service_count = 0
    allocation_by_product = {
        item["product_id"]: item
        for item in result.get("allocations", [])
    }

    for item in result["items"]:
        product = item["product"]
        qty = item["qty"]

        if product.get("product_kind") == "digital" or product.get("delivery_type") == "inventory" or product.get("inventory_enabled"):
            allocation = allocation_by_product.get(product["_id"])
            inventory_items = allocation.get("items", []) if allocation else []
            ok = await deliver_inventory(
                chat_id,
                product,
                decrypt_items(inventory_items),
            )
            all_delivered = all_delivered and ok
        elif product.get("product_kind") == "service" or product.get("delivery_type") == "service":
            service_count += 1
            await queue_service_delivery(chat_id, user, product, order, lang)
        else:
            ok = True
            for _ in range(qty):
                one = await deliver_product(chat_id, product, lang)
                ok = ok and one
            all_delivered = all_delivered and ok

    if service_count:
        if not all_delivered:
            await db.purchases.update_one(
                {"_id": order["_id"]},
                {"$set": {"status": "delivery_failed", "delivery_error": "Satu atau lebih produk gagal dikirim."}},
            )
            await send_message(
                chat_id,
                t(lang, "delivery_attention", invoice=order["invoice_id"]),
                kb=back_kb(lang),
            )
        return

    final_status = "delivered" if all_delivered else "delivery_failed"
    await db.purchases.update_one(
        {"_id": order["_id"]},
        {
            "$set": {
                "status": final_status,
                "delivered_at": now_iso() if all_delivered else None,
                "delivery_error": None if all_delivered else "Satu atau lebih produk gagal dikirim.",
            }
        },
    )

    if all_delivered:
        await send_message(
            chat_id,
            t(
                lang,
                "delivered_all",
                balance=fmt_amount(result["remaining_balance"], order["currency"]),
            ),
            kb=back_kb(lang),
        )
    else:
        await send_message(
            chat_id,
            t(lang, "delivery_attention", invoice=order["invoice_id"]),
            kb=back_kb(lang),
        )

    names = ", ".join(f"{item['product']['name']} ×{item['qty']}" for item in result["items"])
    await notify_admin(
        f"🛒 <b>Penjualan Baru!</b>\n\n"
        f"Invoice: <code>{order['invoice_id']}</code>\n"
        f"Pembeli: {user_label(user)}\n"
        f"Produk: {names}\n"
        f"Total: <b>{fmt_amount(order['total'], order['currency'])}</b>\n"
        f"Status: <b>{final_status}</b>"
    )


async def do_checkout(chat_id, user, cart_items, preserve_cart=False):
    lock = _checkout_lock(user["telegram_id"])
    if lock.locked():
        await send_message(chat_id, t(user.get("lang", "id"), "checkout_in_progress"), kb=back_kb(user.get("lang", "id")))
        return

    async with lock:
        return await _do_checkout(chat_id, user, cart_items, preserve_cart=preserve_cart)


# ============ DEPOSIT ============

async def ensure_join_gate(chat_id, user, force_refresh=False):
    joined, missing = await check_user_membership(user["telegram_id"], force_refresh=force_refresh)
    if joined:
        return True
    lang = user.get("lang", "id")
    await send_message(
        chat_id,
        "📢 <b>Akses bot membutuhkan join channel terlebih dahulu.</b>\n\n"
        "Silakan join semua channel di bawah, lalu tekan tombol <b>Saya sudah join</b>.",
        kb=build_gate_keyboard(missing),
    )
    return False


async def show_deposit_menu(chat_id, user):
    lang = user.get("lang", "id")
    s = await get_settings()

    if user["currency"] == "USD":
        kb = {"inline_keyboard": [
            [{"text": "💎 USDT", "callback_data": "depcoin:USDT"}, {"text": "🔵 USDC", "callback_data": "depcoin:USDC"}],
            [{"text": t(lang, "btn_main"), "callback_data": "menu:main"}],
        ]}
        await send_message(
            chat_id,
            t(lang, "dep_usd_title", min=float(s.get("min_deposit_usd", 15))),
            kb=kb,
        )
        return

    import os
    qris_ready = bool(s.get("qris_enabled", False)) and os.environ.get("GOPAY_ENABLED", "").lower() in {"1", "true", "yes"}
    bank_ready = bool(s.get("bank_enabled", False)) and bool(s.get("bank_account_number"))

    if qris_ready and bank_ready:
        await send_message(chat_id, t(lang, "dep_idr_method_title"), kb={
            "inline_keyboard": [
                [{"text": t(lang, "btn_gopay_qris"), "callback_data": "depmethod:qris"}],
                [{"text": t(lang, "btn_bank_transfer"), "callback_data": "depmethod:bank"}],
                [{"text": t(lang, "btn_main"), "callback_data": "menu:main"}],
            ]
        })
        return

    if qris_ready:
        await start_idr_deposit(chat_id, user, "qris")
        return

    if bank_ready:
        await start_idr_deposit(chat_id, user, "bank")
        return

    await send_message(chat_id, t(lang, "dep_gateway_offline"), kb=back_kb(lang))


async def start_idr_deposit(chat_id, user, method):
    lang = user.get("lang", "id")
    s = await get_settings()
    min_idr = float(s.get("min_deposit_idr", 50000))
    import os

    if method == "qris":
        qris_ready = bool(s.get("qris_enabled", False)) and os.environ.get("GOPAY_ENABLED", "").lower() in {"1", "true", "yes"}
        if not qris_ready:
            await send_message(chat_id, t(lang, "dep_gateway_offline"), kb=back_kb(lang))
            return
        await set_state(user["telegram_id"], "dep_idr_amount", {"method": "qris"})
        prompt = await send_message(
            chat_id,
            t(lang, "dep_idr_gopay_title", min=fmt_amount(min_idr, "IDR")),
            kb=cancel_kb(lang),
        )
        if prompt.get("message_id"):
            await db.bot_users.update_one(
                {"telegram_id": user["telegram_id"]},
                {"$set": {"state_data.prompt_message_id": prompt["message_id"]}},
            )
        return

    if method == "bank":
        if not s.get("bank_enabled") or not s.get("bank_account_number"):
            await send_message(chat_id, t(lang, "dep_no_bank"), kb=back_kb(lang))
            return
        await set_state(user["telegram_id"], "dep_idr_amount", {"method": "bank"})
        await send_message(
            chat_id,
            t(
                lang,
                "dep_idr_title",
                bank=s.get("bank_name", ""),
                account=s.get("bank_account_number", ""),
                holder=s.get("bank_account_holder", ""),
                min=fmt_amount(min_idr, "IDR"),
            ),
            kb=cancel_kb(lang),
        )
        return

    await send_message(chat_id, t(lang, "dep_gateway_offline"), kb=back_kb(lang))

async def show_network_selection(chat_id, user, coin):
    lang = user.get("lang", "id")
    kb = {"inline_keyboard": [
        [{"text": "◎ Solana", "callback_data": f"depnet:{coin}:SOL"}, {"text": "🟣 Polygon", "callback_data": f"depnet:{coin}:POL"}],
        [{"text": "🟡 BNB (BEP-20)", "callback_data": f"depnet:{coin}:BNB"}, {"text": "🔺 Avalanche", "callback_data": f"depnet:{coin}:AVAX"}],
        [{"text": t(lang, "btn_back"), "callback_data": "menu:deposit"}],
    ]}
    await send_message(chat_id, t(lang, "choose_network", coin=coin), kb=kb)


async def show_deposit_address(chat_id, user, coin, network):
    lang = user.get("lang", "id")
    s = await get_settings()
    address = (s.get("crypto_addresses") or {}).get(f"{coin}_{network}", "")
    if not address:
        await send_message(chat_id, t(lang, "dep_no_address", coin=coin, network=NET_LABELS[network]),
            kb={"inline_keyboard": [[{"text": t(lang, "btn_other_network"), "callback_data": f"depcoin:{coin}"}],
                                     [{"text": t(lang, "btn_main"), "callback_data": "menu:main"}]]})
        return
    await set_state(user["telegram_id"], "dep_usd_amount", {"coin": coin, "network": network})
    await send_message(chat_id,
        t(lang, "dep_address", coin=coin, network=NET_LABELS[network], address=address, min=float(s.get("min_deposit_usd", 15))),
        kb=cancel_kb(lang))


async def handle_dep_usd_amount(chat_id, user, text):
    lang = user.get("lang", "id")
    s = await get_settings()
    min_usd = float(s.get("min_deposit_usd", 15))
    try:
        amount = float(text.strip().replace("$", "").replace(",", ""))
    except ValueError:
        await send_message(chat_id, t(lang, "invalid_amount"), kb=cancel_kb(lang))
        return
    if amount < min_usd:
        await send_message(chat_id, t(lang, "min_deposit", min=f"${min_usd:,.2f}"), kb=cancel_kb(lang))
        return
    if not math.isfinite(amount) or amount > float(s.get("max_deposit_usd", 100000)):
        await send_message(chat_id, t(lang, "invalid_amount"), kb=cancel_kb(lang))
        return
    data = user.get("state_data", {})
    data["amount"] = amount
    await set_state(user["telegram_id"], "dep_usd_wallet", data)
    await send_message(chat_id, t(lang, "wallet_prompt", network=NET_LABELS.get(data.get("network"), data.get("network", ""))), kb=cancel_kb(lang))


def valid_sender_wallet(network, wallet):
    wallet = wallet.strip()
    if network in {"POL", "BNB", "AVAX"}:
        return bool(re.fullmatch(r"0x[a-fA-F0-9]{40}", wallet))
    return 32 <= len(wallet) <= 44 and bool(re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]+", wallet))


async def handle_dep_usd_wallet(chat_id, user, text):
    lang = user.get("lang", "id")
    data = user.get("state_data", {})
    network = data.get("network")
    wallet = text.strip()

    if not valid_sender_wallet(network, wallet):
        await send_message(chat_id, t(lang, "wallet_invalid"), kb=cancel_kb(lang))
        return

    data["sender_wallet"] = wallet
    await set_state(user["telegram_id"], "dep_usd_proof", data)
    await send_message(chat_id, t(lang, "amount_set_usd", amount=data.get("amount", 0)), kb=cancel_kb(lang))


async def handle_dep_idr_amount(chat_id, user, text):
    lang = user.get("lang", "id")
    s = await get_settings()
    data = user.get("state_data", {})
    method = data.get("method") or "bank"
    min_idr = float(s.get("min_deposit_idr", 50000))
    try:
        amount = float(
            text.strip()
            .replace("Rp", "")
            .replace(".", "")
            .replace(",", "")
            .replace(" ", "")
        )
    except ValueError:
        await send_message(chat_id, t(lang, "invalid_amount"), kb=cancel_kb(lang))
        return

    if not math.isfinite(amount) or amount > float(s.get("max_deposit_idr", 100000000)):
        await send_message(chat_id, t(lang, "invalid_amount"), kb=cancel_kb(lang))
        return

    if amount < min_idr:
        await send_message(
            chat_id,
            t(lang, "min_deposit", min=fmt_amount(min_idr, "IDR")),
            kb=cancel_kb(lang),
        )
        return

    import os
    qris_ready = bool(s.get("qris_enabled", False)) and os.environ.get("GOPAY_ENABLED", "").lower() in {"1", "true", "yes"}
    bank_ready = bool(s.get("bank_enabled", False)) and bool(s.get("bank_account_number"))

    if method == "qris":
        if not qris_ready:
            await set_state(user["telegram_id"], None)
            await send_message(chat_id, t(lang, "dep_gateway_offline"), kb=back_kb(lang))
            return
        old_prompt_id = data.get("prompt_message_id")
        if old_prompt_id:
            try:
                await delete_message(chat_id, old_prompt_id)
            except Exception:
                pass
        admin_fee = max(1, int(round(amount * 0.007)))
        platform_code = secrets.randbelow(900) + 100
        total_payment = int(amount + admin_fee + platform_code)
        await set_state(
            user["telegram_id"],
            "dep_idr_confirm",
            {
                "method": "qris",
                "amount": int(amount),
                "admin_fee": admin_fee,
                "platform_code": platform_code,
                "total_payment": total_payment,
            },
        )
        await send_message(
            chat_id,
            t(
                lang,
                "gopay_deposit_confirm",
                amount=fmt_amount(amount, "IDR"),
                fee=fmt_amount(admin_fee, "IDR"),
                platform_code=platform_code,
                total=fmt_amount(total_payment, "IDR"),
            ),
            kb={
                "inline_keyboard": [
                    [{"text": t(lang, "btn_deposit_agree"), "callback_data": "gopay:yes"}],
                    [{"text": t(lang, "btn_deposit_cancel"), "callback_data": "gopay:no"}],
                ]
            },
        )
        return

    if not bank_ready:
        await set_state(user["telegram_id"], None)
        await send_message(chat_id, t(lang, "dep_gateway_offline"), kb=back_kb(lang))
        return

    await set_state(user["telegram_id"], "dep_idr_proof", {"method": "bank", "amount": amount})
    await send_message(
        chat_id,
        t(lang, "amount_set_idr", amount=fmt_amount(amount, "IDR")),
        kb=cancel_kb(lang),
    )

async def confirm_gopay_deposit(chat_id, user):
    lang = user.get("lang", "id")
    data = user.get("state_data", {})
    s = await get_settings()
    import os
    qris_ready = bool(s.get("qris_enabled", False)) and os.environ.get("GOPAY_ENABLED", "").lower() in {"1", "true", "yes"}
    if not qris_ready:
        await set_state(user["telegram_id"], None)
        await send_message(chat_id, t(lang, "dep_gateway_offline"), kb=back_kb(lang))
        return
    amount = int(data.get("amount") or 0)
    admin_fee = int(data.get("admin_fee") or 0)
    platform_code = int(data.get("platform_code") or 0)
    if amount < 1 or admin_fee < 1 or not 100 <= platform_code <= 999:
        await set_state(user["telegram_id"], None)
        await send_message(chat_id, t(lang, "gopay_unavailable"), kb=back_kb(lang))
        return

    try:
        payment = await create_gopay_payment(user, amount, platform_code=platform_code)
        await set_state(user["telegram_id"], None)
        caption = t(
            lang,
            "gopay_qr_created",
            amount=fmt_amount(amount, "IDR"),
            fee=fmt_amount(payment["admin_fee"], "IDR"),
            platform_code=payment["platform_code"],
            payment_amount=fmt_amount(payment["payment_amount"], "IDR"),
        )
        await send_photo_bytes(
            chat_id,
            payment["image"],
            "gopay-qris.jpg",
            caption=caption,
            kb=back_kb(lang),
        )
    except Exception:
        logger.exception("GoPay QR creation failed")
        await set_state(user["telegram_id"], None)
        await send_message(chat_id, t(lang, "gopay_unavailable"), kb=back_kb(lang))

async def create_pending_deposit(user, data, tx_hash=None, proof_file_id=None, credited_amount=None, auto_verified=False):
    dep = {
        "_id": str(uuid.uuid4()), "user_tid": user["telegram_id"], "username": user.get("username", ""),
        "first_name": user.get("first_name", ""),
        "method": "crypto" if data.get("coin") else "bank",
        "coin": data.get("coin"), "network": data.get("network"),
        "currency": "USD" if data.get("coin") else "IDR",
        "amount": data["amount"], "credited_amount": credited_amount,
        "sender_wallet": data.get("sender_wallet"),
        "tx_hash": tx_hash, "proof_file_id": proof_file_id,
        "status": "pending", "auto_verified": auto_verified, "note": "",
        "created_at": now_iso(), "decided_at": None,
    }
    await db.deposits.insert_one(dep)
    return dep


def admin_decision_kb(dep_id):
    return {"inline_keyboard": [[
        {"text": "✅ Setujui", "callback_data": f"adm:app:{dep_id}"},
        {"text": "❌ Tolak", "callback_data": f"adm:rej:{dep_id}"},
    ]]}


async def handle_usd_proof(chat_id, user, message):
    lang = user.get("lang", "id")
    data = user.get("state_data", {})
    coin, network, amount = data.get("coin"), data.get("network"), data.get("amount")
    sender_wallet = data.get("sender_wallet")
    s = await get_settings()
    address = (s.get("crypto_addresses") or {}).get(f"{coin}_{network}", "")
    text = message.get("text", "")
    photo = message.get("photo")

    if photo:
        file_id = photo[-1]["file_id"]
        dep = await create_pending_deposit(user, data, proof_file_id=file_id)
        await set_state(user["telegram_id"], None)
        await send_message(chat_id, t(lang, "proof_received"), kb=back_kb(lang))
        await notify_admin(
            f"💰 <b>Deposit Baru — Perlu Verifikasi</b>\n\nDari: {user_label(user)}\n"
            f"Metode: {coin} / {NET_LABELS[network]}\nJumlah klaim: {amount:,.2f}\nBukti: screenshot 👆",
            kb=admin_decision_kb(dep["_id"]), photo_file_id=file_id)
        return

    tx_hash = text.strip()
    if not sender_wallet or not valid_sender_wallet(network, sender_wallet):
        await send_message(chat_id, t(lang, "wallet_invalid"), kb=cancel_kb(lang))
        return
    if not looks_like_tx_hash(tx_hash, network):
        await send_message(chat_id, t(lang, "invalid_txhash"), kb=cancel_kb(lang))
        return

    existing = await db.deposits.find_one({"tx_hash": tx_hash})
    if existing:
        await send_message(chat_id, t(lang, "tx_used"), kb=back_kb(lang))
        await set_state(user["telegram_id"], None)
        return

    await send_message(chat_id, t(lang, "checking"))
    verified, onchain_amount, reason = await verify_tx(
        network,
        coin,
        address,
        tx_hash,
        expected_sender=sender_wallet,
    )
    min_usd = float(s.get("min_deposit_usd", 15))

    if verified and onchain_amount >= min_usd:
        dep = await create_pending_deposit(
            user,
            data,
            tx_hash=tx_hash,
            credited_amount=onchain_amount,
            auto_verified=True,
        )
        await credit_deposit(dep, note="Verifikasi on-chain otomatis")
        fresh = await db.bot_users.find_one({"telegram_id": user["telegram_id"]})
        new_bal = float((fresh or {}).get("balance_usd", 0))
        await set_state(user["telegram_id"], None)
        await send_message(
            chat_id,
            t(
                lang,
                "auto_ok",
                coin=coin,
                network=NET_LABELS[network],
                amount=onchain_amount,
                balance=new_bal,
            ),
            kb=back_kb(lang),
        )
        await notify_admin(
            f"✅ <b>Deposit Otomatis Terverifikasi</b>\n\nDari: {user_label(user)}\n"
            f"Koin: {coin} / {NET_LABELS[network]}\nWallet: <code>{sender_wallet}</code>\n"
            f"Jumlah on-chain: {onchain_amount:,.2f}\nTX: <code>{tx_hash}</code>",
            kb={"inline_keyboard": [[{"text": "🚫 Batalkan Deposit Ini", "callback_data": f"adm:cxl:{dep['_id']}"}]]})
    else:
        dep = await create_pending_deposit(user, data, tx_hash=tx_hash)
        await set_state(user["telegram_id"], None)
        if verified:
            why = f"Jumlah on-chain ({onchain_amount:,.2f}) di bawah minimum"
        else:
            why = reason or "Tidak dapat diverifikasi"
        await send_message(chat_id, t(lang, "pending_manual", reason=why), kb=back_kb(lang))
        await notify_admin(
            f"💰 <b>Deposit Baru — Perlu Verifikasi Manual</b>\n\nDari: {user_label(user)}\n"
            f"Koin: {coin} / {NET_LABELS[network]}\nWallet: <code>{sender_wallet}</code>\n"
            f"Jumlah klaim: {amount:,.2f}\nTX: <code>{tx_hash}</code>\n⚠️ Auto-verify gagal: {why}",
            kb=admin_decision_kb(dep["_id"]))



async def handle_idr_proof(chat_id, user, message):
    lang = user.get("lang", "id")
    data = user.get("state_data", {})
    s = await get_settings()
    if not s.get("bank_enabled") or not s.get("bank_account_number"):
        await set_state(user["telegram_id"], None)
        await send_message(chat_id, t(lang, "dep_gateway_offline"), kb=back_kb(lang))
        return
    amount = data.get("amount")
    photo = message.get("photo")
    if not photo:
        await send_message(chat_id, t(lang, "send_photo_please"), kb=cancel_kb(lang))
        return
    file_id = photo[-1]["file_id"]
    dep = await create_pending_deposit(user, {"amount": amount}, proof_file_id=file_id)
    await set_state(user["telegram_id"], None)
    await send_message(chat_id, t(lang, "proof_received"), kb=back_kb(lang))
    await notify_admin(
        f"💰 <b>Deposit IDR Baru — Perlu Verifikasi</b>\n\nDari: {user_label(user)}\n"
        f"Metode: Transfer Bank\nJumlah: <b>{fmt_amount(amount, 'IDR')}</b>\nBukti: 👆",
        kb=admin_decision_kb(dep["_id"]), photo_file_id=file_id)


# ============ OTHER MENUS ============

async def show_balance(chat_id, user):
    lang = user.get("lang", "id")
    rate = await get_rate()
    await send_message(chat_id,
        t(lang, "balance_view", usd=float(user.get("balance_usd", 0)), idr=fmt_amount(user.get("balance_idr", 0), "IDR"),
          cur=user["currency"], rate=fmt_amount(rate, "IDR")), kb=back_kb(lang))


async def show_history(chat_id, user):
    lang = user.get("lang", "id")
    tid = user["telegram_id"]
    deps = await db.deposits.find({"user_tid": tid}).sort("created_at", -1).limit(20).to_list(20)
    purs = await db.purchases.find({"user_tid": tid}).sort("created_at", -1).limit(20).to_list(20)

    events = []
    for d in deps:
        events.append(("deposit", d.get("created_at") or "", d))
    for o in purs:
        events.append(("order", o.get("created_at") or "", o))
    events.sort(key=lambda x: x[1], reverse=True)
    events = events[:20]

    if not events:
        await send_message(chat_id, t(lang, "hist_header") + "\n\nBelum ada transaksi.", kb=back_kb(lang))
        return

    status_labels = {
        "pending": "⏳ Pending", "service_waiting": "⏳ Antrean Jasa", "approved": "✅ Disetujui", "rejected": "❌ Ditolak",
        "cancelled": "🚫 Dibatalkan", "expired": "⌛ Kedaluwarsa", "paid": "💳 Dibayar",
        "processing": "⚙️ Diproses", "delivered": "✅ Selesai", "delivery_failed": "⚠️ Gagal Kirim",
        "failed": "❌ Gagal", "refunded": "↩️ Refund",
    }
    lines = [t(lang, "hist_header"), ""]
    rows = []
    for kind, _, item in events:
        if kind == "order":
            invoice = item.get("invoice_id", "-")
            names = ", ".join(f"{escape(str(i.get('name','Produk')))} ×{i.get('qty',1)}" for i in item.get("items", []))
            label = f"🧾 <code>{escape(invoice)}</code> — {fmt_amount(item.get('total', 0), item.get('currency','IDR'))}"
            lines.append(f"{label}\n   {names}\n   {status_labels.get(item.get('status'), item.get('status','-'))}")
            rows.append([{"text": f"🧾 {invoice} — Detail", "callback_data": f"hist:ord:{item['_id']}"}])
        else:
            dep_id = item.get("_id", "")
            amount = item.get("payment_amount") or item.get("amount") or 0
            method = "GoPay QR" if item.get("method") == "gopay" else (item.get("method") or "Deposit")
            lines.append(f"💰 {method} — {fmt_amount(amount, item.get('currency','IDR'))}\n   {status_labels.get(item.get('status'), item.get('status','-'))}")
            rows.append([{"text": f"💰 Deposit — Detail", "callback_data": f"hist:dep:{dep_id}"}])

    rows.append([{"text": t(lang, "btn_main"), "callback_data": "menu:main"}])
    await send_message(chat_id, "\n".join(lines), kb={"inline_keyboard": rows})


async def show_order_history_detail(chat_id, user, order_id):
    lang = user.get("lang", "id")
    order = await db.purchases.find_one({"_id": order_id, "user_tid": user["telegram_id"]})
    if not order:
        await send_message(chat_id, "Transaksi tidak ditemukan.", kb=back_kb(lang))
        return

    status_labels = {
        "pending": "⏳ Pending", "paid": "💳 Dibayar", "processing": "⚙️ Diproses",
        "delivered": "✅ Selesai", "delivery_failed": "⚠️ Gagal Kirim",
        "failed": "❌ Gagal", "refunded": "↩️ Refund",
    }
    lines = [
        "🧾 <b>Detail Invoice</b>",
        f"Invoice: <code>{escape(str(order.get('invoice_id','-')))}</code>",
        f"Order ID: <code>{escape(str(order.get('_id','-')))}</code>",
        f"Tanggal transaksi: <b>{escape(str(order.get('created_at','-')))}</b>",
        f"Status: <b>{status_labels.get(order.get('status'), order.get('status','-'))}</b>",
        f"Metode pembayaran: <b>{escape(str(order.get('payment_method','balance')))}</b>",
        "",
        "<b>Produk yang dibeli:</b>",
    ]
    for item in order.get("items", []):
        name = escape(str(item.get("name", "Produk")))
        qty = int(item.get("qty") or 0)
        unit = fmt_amount(item.get("unit_price", 0), order.get("currency", "IDR"))
        subtotal = fmt_amount(item.get("subtotal", 0), order.get("currency", "IDR"))
        discount = fmt_amount(item.get("discount_total", 0), order.get("currency", "IDR"))
        lines.append(f"• <b>{name}</b> ×{qty}")
        lines.append(f"  Harga/unit: {unit} | Subtotal: {subtotal}")
        if float(item.get("discount_total") or 0) > 0:
            lines.append(f"  Diskon: {discount}" + (f" ({escape(str(item.get('discount_name')) )})" if item.get("discount_name") else ""))
    lines.extend([
        "",
        f"Total diskon: <b>{fmt_amount(order.get('discount_total',0), order.get('currency','IDR'))}</b>",
        f"Coupon: <b>{escape(str(order.get('coupon_code') or '-'))}</b>",
        f"Total transaksi: <b>{fmt_amount(order.get('total',0), order.get('currency','IDR'))}</b>",
    ])
    if order.get("paid_at"):
        lines.append(f"Dibayar: {escape(str(order['paid_at']))}")
    if order.get("delivered_at"):
        lines.append(f"Dikirim: {escape(str(order['delivered_at']))}")
    if order.get("delivery_error"):
        lines.append(f"Catatan: <b>{escape(str(order['delivery_error']))}</b>")
    await send_message(chat_id, "\n".join(lines), kb={"inline_keyboard": [
        [{"text": t(lang, "hist_back"), "callback_data": "menu:history"}],
        [{"text": t(lang, "btn_main"), "callback_data": "menu:main"}],
    ]})


async def show_deposit_history_detail(chat_id, user, dep_id):
    lang = user.get("lang", "id")
    dep = await db.deposits.find_one({"_id": dep_id, "user_tid": user["telegram_id"]})
    if not dep:
        await send_message(chat_id, "Deposit tidak ditemukan.", kb=back_kb(lang))
        return

    status_labels = {
        "pending": "⏳ Pending", "approved": "✅ Disetujui", "rejected": "❌ Ditolak",
        "cancelled": "🚫 Dibatalkan", "expired": "⌛ Kedaluwarsa",
    }
    lines = [
        "💰 <b>Detail Deposit</b>",
        f"Deposit ID: <code>{escape(str(dep.get('_id','-')))}</code>",
        f"Tanggal: <b>{escape(str(dep.get('created_at','-')))}</b>",
        f"Metode: <b>{escape(str(dep.get('method','-')))}</b>",
        f"Status: <b>{status_labels.get(dep.get('status'), dep.get('status','-'))}</b>",
        f"Deposit: <b>{fmt_amount(dep.get('amount',0), dep.get('currency','IDR'))}</b>",
    ]
    if dep.get("admin_fee") is not None:
        lines.append(f"Admin fee 0.7%: <b>{fmt_amount(dep.get('admin_fee'), dep.get('currency','IDR'))}</b>")
    if dep.get("platform_code") is not None:
        lines.append(f"Admin platform: <b>{dep.get('platform_code')}</b>")
    if dep.get("payment_amount") is not None:
        lines.append(f"Total dibayarkan: <b>{fmt_amount(dep.get('payment_amount'), dep.get('currency','IDR'))}</b>")
    if dep.get("credited_amount") is not None:
        lines.append(f"Saldo dikreditkan: <b>{fmt_amount(dep.get('credited_amount'), dep.get('currency','IDR'))}</b>")
    if dep.get("coin"):
        lines.append(f"Koin/Jaringan: <b>{escape(str(dep.get('coin')))} / {escape(str(dep.get('network')))}</b>")
    if dep.get("tx_hash"):
        lines.append(f"TX: <code>{escape(str(dep.get('tx_hash')))}</code>")
    if dep.get("gopay_tx_id"):
        lines.append(f"GoPay TX: <code>{escape(str(dep.get('gopay_tx_id')))}</code>")
    if dep.get("decided_at"):
        lines.append(f"Diproses: {escape(str(dep.get('decided_at')))}")
    await send_message(chat_id, "\n".join(lines), kb={"inline_keyboard": [
        [{"text": t(lang, "hist_back"), "callback_data": "menu:history"}],
        [{"text": t(lang, "btn_main"), "callback_data": "menu:main"}],
    ]})


async def show_settings(chat_id, user):
    lang = user.get("lang", "id")
    other = "IDR" if user["currency"] == "USD" else "USD"
    kb = {"inline_keyboard": [
        [{"text": t(lang, "btn_change_currency", cur=other), "callback_data": f"setcur:{other}"}],
        [{"text": t(lang, "btn_language"), "callback_data": "langmenu"}],
        [{"text": t(lang, "btn_main"), "callback_data": "menu:main"}],
    ]}
    await send_message(chat_id, t(lang, "settings_title", cur=user["currency"], lang=LANG_NAMES.get(lang, lang)), kb=kb)


async def show_language_menu(chat_id, user):
    lang = user.get("lang", "id")
    kb = {"inline_keyboard": [
        [{"text": "🇮🇩 Bahasa Indonesia", "callback_data": "setlang:id"}],
        [{"text": "🇬🇧 English", "callback_data": "setlang:en"}],
        [{"text": t(lang, "btn_back"), "callback_data": "menu:settings"}],
    ]}
    await send_message(chat_id, t(lang, "choose_language"), kb=kb)


async def handle_set_currency(chat_id, user, new_cur):
    lang = user.get("lang", "id")
    old_cur = user["currency"]
    balance = float(user.get(CUR_FIELD[old_cur], 0))
    if balance > 0:
        rate = await get_rate()
        converted = balance * rate if old_cur == "USD" else balance / rate
        kb = {"inline_keyboard": [
            [{"text": t(lang, "btn_convert_yes"), "callback_data": f"conv:yes:{new_cur}"}],
            [{"text": t(lang, "btn_convert_no"), "callback_data": f"conv:no:{new_cur}"}],
        ]}
        await send_message(chat_id,
            t(lang, "convert_ask", cur=new_cur, balance=fmt_amount(balance, old_cur),
              converted=fmt_amount(converted, new_cur), rate=fmt_amount(rate, "IDR")), kb=kb)
    else:
        await db.bot_users.update_one({"telegram_id": user["telegram_id"]}, {"$set": {"currency": new_cur}})
        await send_message(chat_id, t(lang, "currency_changed", cur=new_cur), kb=back_kb(lang))


async def handle_conversion(chat_id, user, convert, new_cur):
    lang = user.get("lang", "id")
    old_cur = "USD" if new_cur == "IDR" else "IDR"
    if convert:
        rate = await get_rate()
        balance = float(user.get(CUR_FIELD[old_cur], 0))
        converted = balance * rate if old_cur == "USD" else balance / rate
        await db.bot_users.update_one({"telegram_id": user["telegram_id"]}, {
            "$set": {"currency": new_cur, CUR_FIELD[old_cur]: 0.0},
            "$inc": {CUR_FIELD[new_cur]: converted},
        })
        await send_message(chat_id, t(lang, "converted_done", cur=new_cur, amount=fmt_amount(converted, new_cur)), kb=back_kb(lang))
    else:
        await db.bot_users.update_one({"telegram_id": user["telegram_id"]}, {"$set": {"currency": new_cur}})
        await send_message(chat_id, t(lang, "changed_kept", cur=new_cur), kb=back_kb(lang))


# ============ ADMIN CALLBACKS ============

async def handle_admin_callback(cb, action, dep_id):
    from_id = cb["from"]["id"]
    s = await get_settings()
    if str(from_id) != str(s.get("admin_telegram_id")):
        await answer_callback(cb["id"], "Bukan admin.")
        return
    dep = await db.deposits.find_one({"_id": dep_id})
    if not dep:
        await answer_callback(cb["id"], "Deposit tidak ditemukan.")
        return
    if action == "app":
        if dep["status"] != "pending":
            await answer_callback(cb["id"], f"Sudah diproses ({dep['status']}).")
            return
        await credit_deposit(dep, note="Disetujui via bot Telegram")
        await answer_callback(cb["id"], "✅ Deposit disetujui!")
        await send_message(from_id, f"✅ Deposit {fmt_amount(dep['amount'], dep['currency'])} dari <code>{dep['user_tid']}</code> disetujui.")
    elif action == "rej":
        if dep["status"] != "pending":
            await answer_callback(cb["id"], f"Sudah diproses ({dep['status']}).")
            return
        await reject_deposit(dep, note="Ditolak via bot Telegram")
        await answer_callback(cb["id"], "❌ Deposit ditolak.")
        await send_message(from_id, f"❌ Deposit {fmt_amount(dep['amount'], dep['currency'])} dari <code>{dep['user_tid']}</code> ditolak.")
    elif action == "cxl":
        if dep["status"] != "approved":
            await answer_callback(cb["id"], f"Tidak bisa dibatalkan ({dep['status']}).")
            return
        await cancel_deposit(dep)
        await answer_callback(cb["id"], "🚫 Deposit dibatalkan.")
        await send_message(from_id, f"🚫 Deposit dari <code>{dep['user_tid']}</code> dibatalkan dan saldo dikurangi.")


# ============ DISPATCH ============

def frozen_text(user):
    lang = user.get("lang", "id")
    r = user.get("frozen_reason", "")
    return t(lang, "frozen", reason=t(lang, "frozen_reason", r=r) if r else "")


async def handle_callback(cb):
    data = cb.get("data", "")
    chat_id = cb["message"]["chat"]["id"]

    if data.startswith("adm:"):
        _, action, dep_id = data.split(":", 2)
        await handle_admin_callback(cb, action, dep_id)
        return

    user = await get_user(cb["from"])
    lang = user.get("lang", "id")
    message_id = cb.get("message", {}).get("message_id")
    if message_id is not None:
        _EDIT_TARGETS[chat_id] = message_id
    await answer_callback(cb["id"])

    if data == "gate:check":
        clear_cache_for_user(user["telegram_id"])
        if not await ensure_join_gate(chat_id, user, force_refresh=True):
            return
        if not user.get("currency"):
            await show_currency_selection(chat_id, lang)
        else:
            await show_main_menu(chat_id, user)
        return

    if not await ensure_join_gate(chat_id, user):
        return

    if data.startswith("service:wait:"):
        return

    if data.startswith("setlang:"):
        new_lang = data.split(":")[1]
        await db.bot_users.update_one(
            {"telegram_id": user["telegram_id"]},
            {"$set": {"lang": new_lang}},
        )
        user["lang"] = new_lang
        await send_message(chat_id, t(new_lang, "lang_set"))
        if user.get("currency"):
            await show_main_menu(chat_id, user)
        else:
            await show_currency_selection(chat_id, new_lang)
        return

    if data.startswith("cur:"):
        new_cur = data.split(":")[1]
        if new_cur not in CUR_FIELD:
            return
        await db.bot_users.update_one(
            {"telegram_id": user["telegram_id"]},
            {"$set": {"currency": new_cur}},
        )
        user["currency"] = new_cur
        await show_main_menu(chat_id, user)
        return

    if not user.get("currency"):
        await show_currency_selection(chat_id, lang)
        return

    if user.get("frozen") and data not in ("menu:help",):
        await send_message(chat_id, frozen_text(user))
        return

    if data in ("cancel", "menu:main"):
        await set_state(user["telegram_id"], None)
        await show_main_menu(chat_id, user)
    elif data == "menu:products":
        await show_products(chat_id, user, 1)
    elif data.startswith("products:"):
        await show_products(chat_id, user, int(data.split(":", 1)[1]))
    elif data == "menu:stock":
        await show_stock(chat_id, user)
    elif data.startswith("prod:"):
        await show_product_detail(chat_id, user, data.split(":", 1)[1])
    elif data.startswith("buy:"):
        await do_checkout(chat_id, user, [{"pid": data.split(":", 1)[1], "qty": 1}])
    elif data.startswith("cartadd:"):
        await add_to_cart(chat_id, user, data.split(":", 1)[1])
    elif data.startswith("qtycustom:"):
        await start_custom_quantity(chat_id, user, data.split(":", 1)[1])
    elif data.startswith("custompay:"):
        _, pid, qty_raw = data.split(":", 2)
        pending = user.get("state_data") or {}
        try:
            qty = int(qty_raw)
        except ValueError:
            await send_message(chat_id, "❌ Jumlah pembelian tidak valid.", kb={"inline_keyboard": [
                [{"text": "🛒 Keranjang", "callback_data": "menu:cart"}],
            ]})
            return
        if user.get("state") != "cart_custom_confirm" or str(pending.get("pid") or "") != pid or int(pending.get("qty") or 0) != qty:
            await set_state(user["telegram_id"], None)
            await send_message(
                chat_id,
                "⚠️ Konfirmasi pembelian ini sudah tidak aktif. Silakan buka kembali keranjang dan pilih jumlah yang diinginkan.",
                kb={"inline_keyboard": [
                    [{"text": "🛒 Buka Keranjang", "callback_data": "menu:cart"}],
                ]},
            )
            return
        product = await db.products.find_one({"_id": pid, "active": True})
        if not product:
            await set_state(user["telegram_id"], None)
            await send_message(chat_id, "❌ Product sudah tidak tersedia.", kb={"inline_keyboard": [
                [{"text": "🛒 Buka Keranjang", "callback_data": "menu:cart"}],
            ]})
            return
        stock = await stock_for(product)
        if stock is not None and qty > stock:
            await set_state(user["telegram_id"], None)
            await send_message(
                chat_id,
                f"⚠️ Stok berubah. Saat ini hanya tersedia <b>{int(stock)}</b> item. Silakan pilih jumlah kembali.",
                kb={"inline_keyboard": [
                    [{"text": "🔢 Ubah Jumlah", "callback_data": f"qtycustom:{pid}"},
                     {"text": "🛒 Keranjang", "callback_data": "menu:cart"}],
                ]},
            )
            return
        await set_state(user["telegram_id"], None)
        await do_checkout(chat_id, user, [{"pid": pid, "qty": qty}], preserve_cart=True)
    elif data == "menu:cart":
        await set_state(user["telegram_id"], None)
        user["state"] = None
        user["state_data"] = {}
        await show_cart(chat_id, user)
    elif data.startswith("qtyinc:"):
        await change_qty(chat_id, user, data.split(":", 1)[1], 1)
    elif data.startswith("qtydec:"):
        await change_qty(chat_id, user, data.split(":", 1)[1], -1)
    elif data.startswith("cartrm:"):
        pid = data.split(":", 1)[1]
        cart = [i for i in norm_cart(user.get("cart")) if i["pid"] != pid]
        await save_cart(user["telegram_id"], cart)
        user["cart"] = cart
        await show_cart(chat_id, user)
    elif data == "cartclear":
        await save_cart(user["telegram_id"], [])
        await send_message(chat_id, t(lang, "cart_cleared"), kb=back_kb(lang))
    elif data == "checkout":
        await do_checkout(chat_id, user, norm_cart(user.get("cart")))
    elif data == "menu:deposit":
        await show_deposit_menu(chat_id, user)
    elif data == "depmethod:qris":
        await start_idr_deposit(chat_id, user, "qris")
    elif data == "depmethod:bank":
        await start_idr_deposit(chat_id, user, "bank")
    elif data == "gopay:yes":
        await confirm_gopay_deposit(chat_id, user)
    elif data == "gopay:no":
        await set_state(user["telegram_id"], None)
        _EDIT_TARGETS.pop(chat_id, None)
        if message_id is not None:
            try:
                await delete_message(chat_id, message_id)
            except Exception:
                pass
        await show_main_menu(chat_id, user)
    elif data.startswith("depcoin:"):
        await show_network_selection(chat_id, user, data.split(":")[1])
    elif data.startswith("depnet:"):
        _, coin, network = data.split(":")
        await show_deposit_address(chat_id, user, coin, network)
    elif data == "menu:balance":
        await show_balance(chat_id, user)
    elif data == "menu:history":
        await show_history(chat_id, user)
    elif data.startswith("hist:ord:"):
        await show_order_history_detail(chat_id, user, data.split(":", 2)[2])
    elif data.startswith("hist:dep:"):
        await show_deposit_history_detail(chat_id, user, data.split(":", 2)[2])
    elif data == "menu:settings":
        await show_settings(chat_id, user)
    elif data == "langmenu":
        await show_language_menu(chat_id, user)
    elif data.startswith("setcur:"):
        await handle_set_currency(chat_id, user, data.split(":")[1])
    elif data.startswith("conv:"):
        _, yn, new_cur = data.split(":")
        await handle_conversion(chat_id, user, yn == "yes", new_cur)
    elif data == "menu:help":
        await send_message(chat_id, t(lang, "help"), kb=back_kb(lang))


async def handle_message(message):
    if "from" not in message or message["from"].get("is_bot"):
        return

    chat_id = message["chat"]["id"]
    message_id = message.get("message_id")
    user = await get_user(message["from"])
    lang = user.get("lang", "id")
    text = (message.get("text") or "").strip()

    if text == "/start":
        if not await ensure_join_gate(chat_id, user):
            return
        await set_state(user["telegram_id"], None)
        if not user.get("currency"):
            await show_currency_selection(chat_id, lang)
        elif user.get("frozen"):
            await send_message(chat_id, frozen_text(user))
        else:
            await show_main_menu(chat_id, user)
        return

    if not await ensure_join_gate(chat_id, user):
        return

    if not user.get("currency"):
        await show_currency_selection(chat_id, lang)
        return

    if user.get("frozen"):
        await send_message(chat_id, frozen_text(user))
        return

    if text in ("/batal", "/menu", "/cancel"):
        await set_state(user["telegram_id"], None)
        await show_main_menu(chat_id, user)
        return
    if text in ("/saldo", "/balance"):
        await show_balance(chat_id, user)
        return
    if text in ("/riwayat", "/history"):
        await show_history(chat_id, user)
        return
    if text in ("/stok", "/stock"):
        await show_stock(chat_id, user)
        return
    if text == "/help":
        await send_message(chat_id, t(lang, "help"), kb=back_kb(lang))
        return

    state = user.get("state")
    if state == "cart_custom_qty":
        await handle_custom_quantity(chat_id, user, text)
    elif state == "cart_custom_confirm":
        await send_message(
            chat_id,
            "🛒 Pesanan masih menunggu konfirmasi.\n\nSilakan gunakan tombol <b>Bayar</b> atau <b>Ubah Jumlah</b> pada pesan sebelumnya.",
            kb={"inline_keyboard": [
                [{"text": "🛒 Kembali ke Keranjang", "callback_data": "menu:cart"}],
            ]},
        )
    elif state == "dep_usd_amount":
        await handle_dep_usd_amount(chat_id, user, text)
    elif state == "dep_usd_wallet":
        await handle_dep_usd_wallet(chat_id, user, text)
    elif state == "dep_usd_proof":
        await handle_usd_proof(chat_id, user, message)
    elif state == "dep_idr_amount":
        await handle_dep_idr_amount(chat_id, user, text)
    elif state == "dep_idr_confirm":
        await send_message(chat_id, t(lang, "gopay_confirm_button"))
    elif state == "dep_idr_proof":
        await handle_idr_proof(chat_id, user, message)
    else:
        await show_main_menu(chat_id, user)

    if message_id and state in {"cart_custom_qty", "cart_custom_confirm", "dep_usd_amount", "dep_usd_wallet", "dep_usd_proof", "dep_idr_amount", "dep_idr_confirm", "dep_idr_proof"}:
        try:
            await delete_message(chat_id, message_id)
        except Exception:
            pass


async def process_update(update: dict):
    chat_id = None
    try:
        if "callback_query" in update:
            chat_id = update["callback_query"].get("message", {}).get("chat", {}).get("id")
            await handle_callback(update["callback_query"])
        elif "message" in update:
            chat_id = update["message"].get("chat", {}).get("id")
            await handle_message(update["message"])
    except Exception:
        logger.exception("Failed processing update")
    finally:
        if chat_id is not None:
            _EDIT_TARGETS.pop(chat_id, None)
