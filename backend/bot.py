import uuid
import logging
from db import db, get_settings
from rates import get_rate
from chain import verify_tx, looks_like_tx_hash
from tgapi import send_message, answer_callback, send_document
from services import credit_deposit, reject_deposit, cancel_deposit, notify_admin, fmt_amount, now_iso
from storage import get_object
from i18n import t, LANG_NAMES

logger = logging.getLogger("bot")

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


async def product_price(prod: dict, currency: str) -> float:
    if currency == "USD":
        return float(prod["price_usd"])
    if prod.get("price_idr"):
        return float(prod["price_idr"])
    rate = await get_rate()
    return round(float(prod["price_usd"]) * rate / 100) * 100


def stock_label(prod, lang):
    s = prod.get("stock")
    return "∞" if s is None else str(int(s))


def has_stock(prod, qty=1):
    s = prod.get("stock")
    return True if s is None else s >= qty


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

async def show_products(chat_id, user):
    lang = user.get("lang", "id")
    products = await db.products.find({"active": True}).to_list(100)
    if not products:
        await send_message(chat_id, t(lang, "no_products"), kb=back_kb(lang))
        return
    rows = []
    for p in products:
        price = await product_price(p, user["currency"])
        sl = stock_label(p, lang)
        prefix = "❌ " if not has_stock(p) else ""
        rows.append([{"text": f"{prefix}{p['name']} — {fmt_amount(price, user['currency'])} ({t(lang,'stock_word')} {sl})", "callback_data": f"prod:{p['_id']}"}])
    rows.append([{"text": t(lang, "btn_main"), "callback_data": "menu:main"}])
    await send_message(chat_id, t(lang, "products_title"), kb={"inline_keyboard": rows})


async def show_stock(chat_id, user):
    lang = user.get("lang", "id")
    products = await db.products.find({"active": True}).to_list(200)
    if not products:
        await send_message(chat_id, t(lang, "stock_title") + "\n" + t(lang, "stock_empty"), kb=back_kb(lang))
        return
    lines = [t(lang, "stock_title")]
    for p in products:
        price = await product_price(p, user["currency"])
        sl = stock_label(p, lang)
        mark = "❌" if not has_stock(p) else "✅"
        lines.append(f"{mark} {p['name']} — {fmt_amount(price, user['currency'])} → {t(lang,'stock_word')} <b>{sl}</b>")
    await send_message(chat_id, "\n".join(lines), kb=back_kb(lang))


async def show_product_detail(chat_id, user, pid):
    lang = user.get("lang", "id")
    p = await db.products.find_one({"_id": pid, "active": True})
    if not p:
        await send_message(chat_id, t(lang, "product_not_found"), kb=back_kb(lang))
        return
    price = await product_price(p, user["currency"])
    type_label = t(lang, {"file": "type_file", "link": "type_link", "license": "type_license"}.get(p["delivery_type"], "type_file"))
    text = t(lang, "prod_detail", name=p["name"], desc=p.get("description", ""), type=type_label,
             price=fmt_amount(price, user["currency"]), stock=stock_label(p, lang))
    rows = []
    if has_stock(p):
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
        price = await product_price(p, user["currency"])
        subtotal = price * item["qty"]
        total += subtotal
        lines.append(f"• {p['name']} ×{item['qty']} — {fmt_amount(subtotal, user['currency'])}")
        rows.append([
            {"text": "➖", "callback_data": f"qtydec:{item['pid']}"},
            {"text": f"{p['name'][:20]} ×{item['qty']}", "callback_data": f"prod:{item['pid']}"},
            {"text": "➕", "callback_data": f"qtyinc:{item['pid']}"},
            {"text": "🗑", "callback_data": f"cartrm:{item['pid']}"},
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
            elif p and not has_stock(p, new_qty):
                await send_message(chat_id, t(lang, "qty_max", stock=stock_label(p, lang)))
                return
            else:
                item["qty"] = new_qty
            break
    await save_cart(user["telegram_id"], cart)
    user["cart"] = cart
    await show_cart(chat_id, user)


async def add_to_cart(chat_id, user, pid):
    lang = user.get("lang", "id")
    p = await db.products.find_one({"_id": pid, "active": True})
    if not p:
        await send_message(chat_id, t(lang, "product_not_found"), kb=back_kb(lang))
        return
    cart = norm_cart(user.get("cart"))
    existing = next((i for i in cart if i["pid"] == pid), None)
    new_qty = (existing["qty"] + 1) if existing else 1
    if not has_stock(p, new_qty):
        await send_message(chat_id, t(lang, "qty_max", stock=stock_label(p, lang)), kb=back_kb(lang))
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
    if p["delivery_type"] == "file" and p.get("storage_path"):
        try:
            data, _ = await get_object(p["storage_path"])
            await send_document(chat_id, data, p.get("original_filename", "produk.bin"), caption=f"📦 {p['name']}")
        except Exception:
            logger.exception("file delivery failed")
            await send_message(chat_id, t(lang, "deliver_fail", name=p["name"]))
    elif p["delivery_type"] == "link":
        await send_message(chat_id, t(lang, "deliver_link", name=p["name"], content=p.get("content", "")))
    else:
        await send_message(chat_id, t(lang, "deliver_license", name=p["name"], content=p.get("content", "")))


async def do_checkout(chat_id, user, cart_items):
    lang = user.get("lang", "id")
    currency = user["currency"]
    items, total = [], 0.0
    for item in cart_items:
        p = await db.products.find_one({"_id": item["pid"], "active": True})
        if not p:
            continue
        if not has_stock(p, item["qty"]):
            await send_message(chat_id, t(lang, "stock_insufficient", name=p["name"], stock=stock_label(p, lang)), kb=back_kb(lang))
            return
        price = await product_price(p, currency)
        items.append((p, item["qty"], price))
        total += price * item["qty"]
    if not items:
        await send_message(chat_id, t(lang, "no_valid_products"), kb=back_kb(lang))
        return
    balance = float(user.get(CUR_FIELD[currency], 0))
    if balance < total:
        await send_message(chat_id,
            t(lang, "insufficient", total=fmt_amount(total, currency), balance=fmt_amount(balance, currency), short=fmt_amount(total - balance, currency)),
            kb={"inline_keyboard": [
                [{"text": t(lang, "btn_deposit_now"), "callback_data": "menu:deposit"}],
                [{"text": t(lang, "btn_main"), "callback_data": "menu:main"}]]})
        return
    await db.bot_users.update_one({"telegram_id": user["telegram_id"]}, {"$inc": {CUR_FIELD[currency]: -total}, "$set": {"cart": []}})
    for p, qty, _ in items:
        if p.get("stock") is not None:
            await db.products.update_one({"_id": p["_id"]}, {"$inc": {"stock": -qty}})
    purchase = {
        "_id": str(uuid.uuid4()), "user_tid": user["telegram_id"], "username": user.get("username", ""),
        "items": [{"product_id": p["_id"], "name": p["name"], "qty": qty, "price": price} for p, qty, price in items],
        "total": total, "currency": currency, "created_at": now_iso(),
    }
    await db.purchases.insert_one(purchase)
    await send_message(chat_id, t(lang, "pay_success", total=fmt_amount(total, currency)))
    for p, qty, _ in items:
        await deliver_product(chat_id, p, lang)
    await send_message(chat_id, t(lang, "delivered_all", balance=fmt_amount(balance - total, currency)), kb=back_kb(lang))
    names = ", ".join(f"{p['name']} ×{qty}" for p, qty, _ in items)
    await notify_admin(f"🛒 <b>Penjualan Baru!</b>\n\nPembeli: {user_label(user)}\nProduk: {names}\nTotal: <b>{fmt_amount(total, currency)}</b>")


# ============ DEPOSIT ============

async def show_deposit_menu(chat_id, user):
    lang = user.get("lang", "id")
    s = await get_settings()
    if user["currency"] == "USD":
        kb = {"inline_keyboard": [
            [{"text": "💎 USDT", "callback_data": "depcoin:USDT"}, {"text": "🔵 USDC", "callback_data": "depcoin:USDC"}],
            [{"text": t(lang, "btn_main"), "callback_data": "menu:main"}],
        ]}
        await send_message(chat_id, t(lang, "dep_usd_title", min=float(s.get("min_deposit_usd", 15))), kb=kb)
    else:
        if not s.get("bank_account_number"):
            await send_message(chat_id, t(lang, "dep_no_bank"), kb=back_kb(lang))
            return
        min_idr = s.get("min_deposit_idr", 50000)
        await set_state(user["telegram_id"], "dep_idr_amount")
        await send_message(chat_id,
            t(lang, "dep_idr_title", bank=s.get("bank_name", ""), account=s.get("bank_account_number", ""),
              holder=s.get("bank_account_holder", ""), min=fmt_amount(min_idr, "IDR")), kb=cancel_kb(lang))


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
    data = user.get("state_data", {})
    data["amount"] = amount
    await set_state(user["telegram_id"], "dep_usd_proof", data)
    await send_message(chat_id, t(lang, "amount_set_usd", amount=amount), kb=cancel_kb(lang))


async def handle_dep_idr_amount(chat_id, user, text):
    lang = user.get("lang", "id")
    s = await get_settings()
    min_idr = float(s.get("min_deposit_idr", 50000))
    try:
        amount = float(text.strip().replace("Rp", "").replace(".", "").replace(",", "").replace(" ", ""))
    except ValueError:
        await send_message(chat_id, t(lang, "invalid_amount"), kb=cancel_kb(lang))
        return
    if amount < min_idr:
        await send_message(chat_id, t(lang, "min_deposit", min=fmt_amount(min_idr, "IDR")), kb=cancel_kb(lang))
        return
    await set_state(user["telegram_id"], "dep_idr_proof", {"amount": amount})
    await send_message(chat_id, t(lang, "amount_set_idr", amount=fmt_amount(amount, "IDR")), kb=cancel_kb(lang))


async def create_pending_deposit(user, data, tx_hash=None, proof_file_id=None):
    dep = {
        "_id": str(uuid.uuid4()), "user_tid": user["telegram_id"], "username": user.get("username", ""),
        "first_name": user.get("first_name", ""),
        "method": "crypto" if data.get("coin") else "bank",
        "coin": data.get("coin"), "network": data.get("network"),
        "currency": "USD" if data.get("coin") else "IDR",
        "amount": data["amount"], "credited_amount": None,
        "tx_hash": tx_hash, "proof_file_id": proof_file_id,
        "status": "pending", "auto_verified": False, "note": "",
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
            f"Metode: {coin} / {NET_LABELS[network]}\nJumlah klaim: <b>${amount:,.2f}</b>\nBukti: screenshot 👆",
            kb=admin_decision_kb(dep["_id"]), photo_file_id=file_id)
        return

    tx_hash = text.strip()
    if not looks_like_tx_hash(tx_hash, network):
        await send_message(chat_id, t(lang, "invalid_txhash"), kb=cancel_kb(lang))
        return

    existing = await db.deposits.find_one({"tx_hash": tx_hash, "status": {"$in": ["pending", "approved"]}})
    if existing:
        await send_message(chat_id, t(lang, "tx_used"), kb=back_kb(lang))
        await set_state(user["telegram_id"], None)
        return

    await send_message(chat_id, t(lang, "checking"))
    verified, onchain_amount, reason = await verify_tx(network, coin, address, tx_hash)
    min_usd = float(s.get("min_deposit_usd", 15))

    if verified and onchain_amount >= min_usd:
        dep = {
            "_id": str(uuid.uuid4()), "user_tid": user["telegram_id"], "username": user.get("username", ""),
            "first_name": user.get("first_name", ""),
            "method": "crypto", "coin": coin, "network": network, "currency": "USD",
            "amount": amount, "credited_amount": onchain_amount, "tx_hash": tx_hash, "proof_file_id": None,
            "status": "approved", "auto_verified": True, "note": "Verifikasi on-chain otomatis",
            "created_at": now_iso(), "decided_at": now_iso(),
        }
        await db.deposits.insert_one(dep)
        await db.bot_users.update_one({"telegram_id": user["telegram_id"]}, {"$inc": {"balance_usd": onchain_amount}})
        await set_state(user["telegram_id"], None)
        new_bal = float(user.get("balance_usd", 0)) + onchain_amount
        await send_message(chat_id, t(lang, "auto_ok", coin=coin, network=NET_LABELS[network], amount=onchain_amount, balance=new_bal), kb=back_kb(lang))
        await notify_admin(
            f"✅ <b>Deposit Otomatis Terverifikasi</b>\n\nDari: {user_label(user)}\n"
            f"Koin: {coin} / {NET_LABELS[network]}\nJumlah on-chain: <b>${onchain_amount:,.2f}</b>\n"
            f"TX: <code>{tx_hash}</code>",
            kb={"inline_keyboard": [[{"text": "🚫 Batalkan Deposit Ini", "callback_data": f"adm:cxl:{dep['_id']}"}]]})
    else:
        dep = await create_pending_deposit(user, data, tx_hash=tx_hash)
        await set_state(user["telegram_id"], None)
        if verified:
            why = f"Jumlah on-chain (${onchain_amount:,.2f}) di bawah minimum"
        else:
            why = reason or "Tidak dapat diverifikasi"
        await send_message(chat_id, t(lang, "pending_manual", reason=why), kb=back_kb(lang))
        await notify_admin(
            f"💰 <b>Deposit Baru — Perlu Verifikasi Manual</b>\n\nDari: {user_label(user)}\n"
            f"Koin: {coin} / {NET_LABELS[network]}\nJumlah klaim: <b>${amount:,.2f}</b>\n"
            f"TX: <code>{tx_hash}</code>\n⚠️ Auto-verify gagal: {why}",
            kb=admin_decision_kb(dep["_id"]))


async def handle_idr_proof(chat_id, user, message):
    lang = user.get("lang", "id")
    data = user.get("state_data", {})
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
    deps = await db.deposits.find({"user_tid": user["telegram_id"]}).sort("created_at", -1).to_list(5)
    purs = await db.purchases.find({"user_tid": user["telegram_id"]}).sort("created_at", -1).to_list(5)
    st = {"pending": "st_pending", "approved": "st_approved", "rejected": "st_rejected", "cancelled": "st_cancelled"}
    lines = [t(lang, "hist_header"), t(lang, "hist_deposits")]
    if deps:
        for d in deps:
            amt = d.get("credited_amount") or d["amount"]
            lines.append(f"• {fmt_amount(amt, d['currency'])} — {t(lang, st.get(d['status'], 'st_pending'))} — {d['created_at'][:10]}")
    else:
        lines.append(t(lang, "hist_none_dep"))
    lines.append(t(lang, "hist_purchases"))
    if purs:
        for p in purs:
            names = ", ".join(f"{i['name']}×{i.get('qty',1)}" for i in p["items"])
            lines.append(f"• {names} — {fmt_amount(p['total'], p['currency'])} — {p['created_at'][:10]}")
    else:
        lines.append(t(lang, "hist_none_pur"))
    await send_message(chat_id, "\n".join(lines), kb=back_kb(lang))


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
    await answer_callback(cb["id"])

    if data.startswith("setlang:"):
        new_lang = data.split(":")[1]
        await db.bot_users.update_one({"telegram_id": user["telegram_id"]}, {"$set": {"lang": new_lang}})
        user["lang"] = new_lang
        await send_message(chat_id, t(new_lang, "lang_set"))
        if user.get("currency"):
            await show_main_menu(chat_id, user)
        else:
            await show_currency_selection(chat_id, new_lang)
        return

    if data.startswith("cur:"):
        new_cur = data.split(":")[1]
        await db.bot_users.update_one({"telegram_id": user["telegram_id"]}, {"$set": {"currency": new_cur}})
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
        await show_products(chat_id, user)
    elif data == "menu:stock":
        await show_stock(chat_id, user)
    elif data.startswith("prod:"):
        await show_product_detail(chat_id, user, data.split(":", 1)[1])
    elif data.startswith("buy:"):
        await do_checkout(chat_id, user, [{"pid": data.split(":", 1)[1], "qty": 1}])
    elif data.startswith("cartadd:"):
        await add_to_cart(chat_id, user, data.split(":", 1)[1])
    elif data == "menu:cart":
        await show_cart(chat_id, user)
    elif data.startswith("qtyinc:"):
        await change_qty(chat_id, user, data.split(":", 1)[1], 1)
    elif data.startswith("qtydec:"):
        await change_qty(chat_id, user, data.split(":", 1)[1], -1)
    elif data.startswith("cartrm:"):
        cart = [i for i in norm_cart(user.get("cart")) if i["pid"] != data.split(":", 1)[1]]
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
    elif data.startswith("depcoin:"):
        await show_network_selection(chat_id, user, data.split(":")[1])
    elif data.startswith("depnet:"):
        _, coin, network = data.split(":")
        await show_deposit_address(chat_id, user, coin, network)
    elif data == "menu:balance":
        await show_balance(chat_id, user)
    elif data == "menu:history":
        await show_history(chat_id, user)
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
    user = await get_user(message["from"])
    lang = user.get("lang", "id")
    text = (message.get("text") or "").strip()

    if text == "/start":
        await set_state(user["telegram_id"], None)
        if not user.get("currency"):
            await show_currency_selection(chat_id, lang)
        elif user.get("frozen"):
            await send_message(chat_id, frozen_text(user))
        else:
            await show_main_menu(chat_id, user)
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
    if text == "/stok" or text == "/stock":
        await show_stock(chat_id, user)
        return
    if text == "/help":
        await send_message(chat_id, t(lang, "help"), kb=back_kb(lang))
        return

    state = user.get("state")
    if state == "dep_usd_amount":
        await handle_dep_usd_amount(chat_id, user, text)
    elif state == "dep_usd_proof":
        await handle_usd_proof(chat_id, user, message)
    elif state == "dep_idr_amount":
        await handle_dep_idr_amount(chat_id, user, text)
    elif state == "dep_idr_proof":
        await handle_idr_proof(chat_id, user, message)
    else:
        await show_main_menu(chat_id, user)


async def process_update(update: dict):
    try:
        if "callback_query" in update:
            await handle_callback(update["callback_query"])
        elif "message" in update:
            await handle_message(update["message"])
    except Exception:
        logger.exception("Failed processing update")
