import uuid
import logging
from db import db, get_settings
from rates import get_rate
from chain import verify_tx, looks_like_tx_hash
from tgapi import send_message, edit_message, answer_callback, send_document
from services import credit_deposit, reject_deposit, cancel_deposit, notify_admin, fmt_amount, now_iso
from storage import get_object

logger = logging.getLogger("bot")

NET_LABELS = {"SOL": "Solana", "POL": "Polygon", "BNB": "BNB (BEP-20)", "AVAX": "Avalanche"}
CUR_FIELD = {"USD": "balance_usd", "IDR": "balance_idr"}

MAIN_MENU_KB = {"inline_keyboard": [
    [{"text": "🛍 Lihat Produk", "callback_data": "menu:products"}, {"text": "🛒 Keranjang", "callback_data": "menu:cart"}],
    [{"text": "💰 Deposit", "callback_data": "menu:deposit"}, {"text": "💳 Saldo Saya", "callback_data": "menu:balance"}],
    [{"text": "📜 Riwayat", "callback_data": "menu:history"}, {"text": "⚙️ Pengaturan", "callback_data": "menu:settings"}],
    [{"text": "❓ Bantuan", "callback_data": "menu:help"}],
]}

BACK_KB = {"inline_keyboard": [[{"text": "🏠 Menu Utama", "callback_data": "menu:main"}]]}


def cancel_kb():
    return {"inline_keyboard": [[{"text": "❌ Batal", "callback_data": "cancel"}]]}


async def get_user(tg_from: dict) -> dict:
    tid = tg_from["id"]
    user = await db.bot_users.find_one({"telegram_id": tid})
    if not user:
        user = {
            "_id": str(uuid.uuid4()), "telegram_id": tid,
            "username": tg_from.get("username", ""), "first_name": tg_from.get("first_name", ""),
            "currency": None, "balance_usd": 0.0, "balance_idr": 0.0,
            "frozen": False, "frozen_reason": "", "cart": [],
            "state": None, "state_data": {}, "created_at": now_iso(),
        }
        await db.bot_users.insert_one(user)
    else:
        await db.bot_users.update_one({"telegram_id": tid}, {"$set": {
            "username": tg_from.get("username", ""), "first_name": tg_from.get("first_name", "")}})
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


def user_label(user):
    uname = f"@{user.get('username')}" if user.get("username") else "-"
    return f"{user.get('first_name','')} ({uname}, ID: <code>{user['telegram_id']}</code>)"


async def show_main_menu(chat_id, user):
    bal = fmt_amount(user.get(CUR_FIELD[user["currency"]], 0), user["currency"])
    await send_message(chat_id,
        f"🏪 <b>Toko Produk Digital</b>\n\nHalo, {user.get('first_name','')}! 👋\nSaldo Anda: <b>{bal}</b>\n\nSilakan pilih menu:",
        kb=MAIN_MENU_KB)


async def show_currency_selection(chat_id):
    kb = {"inline_keyboard": [
        [{"text": "💵 USD (Dolar AS)", "callback_data": "cur:USD"}],
        [{"text": "🇮🇩 IDR (Rupiah)", "callback_data": "cur:IDR"}],
    ]}
    await send_message(chat_id,
        "🏪 <b>Selamat datang di Toko Produk Digital!</b>\n\nSilakan pilih mata uang yang ingin Anda gunakan:\n\n"
        "💵 <b>USD</b> — deposit via crypto (USDT/USDC)\n🇮🇩 <b>IDR</b> — deposit via transfer bank\n\n"
        "Pilihan ini bisa diubah kapan saja lewat menu Pengaturan.", kb=kb)


# ============ PRODUCTS ============

async def show_products(chat_id, user):
    products = await db.products.find({"active": True}).to_list(100)
    if not products:
        await send_message(chat_id, "🛍 <b>Produk</b>\n\nBelum ada produk tersedia saat ini.", kb=BACK_KB)
        return
    rows = []
    for p in products:
        price = await product_price(p, user["currency"])
        rows.append([{"text": f"{p['name']} — {fmt_amount(price, user['currency'])}", "callback_data": f"prod:{p['_id']}"}])
    rows.append([{"text": "🏠 Menu Utama", "callback_data": "menu:main"}])
    await send_message(chat_id, "🛍 <b>Daftar Produk</b>\n\nKlik produk untuk melihat detail:", kb={"inline_keyboard": rows})


async def show_product_detail(chat_id, user, pid):
    p = await db.products.find_one({"_id": pid, "active": True})
    if not p:
        await send_message(chat_id, "Produk tidak ditemukan.", kb=BACK_KB)
        return
    price = await product_price(p, user["currency"])
    type_label = {"file": "📁 File", "link": "🔗 Link", "license": "🔑 Kode Lisensi"}.get(p["delivery_type"], p["delivery_type"])
    kb = {"inline_keyboard": [
        [{"text": f"🛒 Beli Sekarang ({fmt_amount(price, user['currency'])})", "callback_data": f"buy:{pid}"}],
        [{"text": "➕ Tambah ke Keranjang", "callback_data": f"cartadd:{pid}"}],
        [{"text": "◀️ Kembali", "callback_data": "menu:products"}, {"text": "🏠 Menu", "callback_data": "menu:main"}],
    ]}
    await send_message(chat_id,
        f"📦 <b>{p['name']}</b>\n\n{p.get('description','')}\n\n"
        f"Jenis: {type_label}\nHarga: <b>{fmt_amount(price, user['currency'])}</b>", kb=kb)


# ============ CART ============

async def show_cart(chat_id, user):
    cart = user.get("cart", [])
    if not cart:
        await send_message(chat_id, "🛒 <b>Keranjang</b>\n\nKeranjang Anda kosong.", kb={"inline_keyboard": [
            [{"text": "🛍 Lihat Produk", "callback_data": "menu:products"}],
            [{"text": "🏠 Menu Utama", "callback_data": "menu:main"}]]})
        return
    lines, total, rows = [], 0.0, []
    for pid in cart:
        p = await db.products.find_one({"_id": pid, "active": True})
        if not p:
            continue
        price = await product_price(p, user["currency"])
        total += price
        lines.append(f"• {p['name']} — {fmt_amount(price, user['currency'])}")
        rows.append([{"text": f"🗑 Hapus {p['name']}", "callback_data": f"cartrm:{pid}"}])
    rows.append([{"text": f"✅ Checkout ({fmt_amount(total, user['currency'])})", "callback_data": "checkout"}])
    rows.append([{"text": "🧹 Kosongkan", "callback_data": "cartclear"}, {"text": "🏠 Menu", "callback_data": "menu:main"}])
    await send_message(chat_id,
        "🛒 <b>Keranjang Anda</b>\n\n" + "\n".join(lines) + f"\n\nTotal: <b>{fmt_amount(total, user['currency'])}</b>",
        kb={"inline_keyboard": rows})


async def deliver_product(chat_id, p):
    if p["delivery_type"] == "file" and p.get("storage_path"):
        try:
            data, _ = await get_object(p["storage_path"])
            await send_document(chat_id, data, p.get("original_filename", "produk.bin"), caption=f"📦 {p['name']}")
        except Exception:
            logger.exception("file delivery failed")
            await send_message(chat_id, f"⚠️ Gagal mengirim file <b>{p['name']}</b>. Hubungi admin.")
    elif p["delivery_type"] == "link":
        await send_message(chat_id, f"📦 <b>{p['name']}</b>\n\n🔗 Link produk Anda:\n{p.get('content','')}")
    else:
        await send_message(chat_id, f"📦 <b>{p['name']}</b>\n\n🔑 Kode lisensi Anda:\n<code>{p.get('content','')}</code>")


async def do_checkout(chat_id, user, product_ids):
    currency = user["currency"]
    items, total = [], 0.0
    for pid in product_ids:
        p = await db.products.find_one({"_id": pid, "active": True})
        if not p:
            continue
        price = await product_price(p, currency)
        items.append((p, price))
        total += price
    if not items:
        await send_message(chat_id, "Tidak ada produk valid untuk dibeli.", kb=BACK_KB)
        return
    balance = float(user.get(CUR_FIELD[currency], 0))
    if balance < total:
        short = total - balance
        await send_message(chat_id,
            f"⚠️ <b>Saldo Tidak Cukup</b>\n\nTotal belanja: <b>{fmt_amount(total, currency)}</b>\n"
            f"Saldo Anda: {fmt_amount(balance, currency)}\nKekurangan: <b>{fmt_amount(short, currency)}</b>\n\n"
            "Silakan deposit terlebih dahulu.", kb={"inline_keyboard": [
                [{"text": "💰 Deposit Sekarang", "callback_data": "menu:deposit"}],
                [{"text": "🏠 Menu Utama", "callback_data": "menu:main"}]]})
        return
    await db.bot_users.update_one({"telegram_id": user["telegram_id"]}, {"$inc": {CUR_FIELD[currency]: -total}, "$set": {"cart": []}})
    purchase = {
        "_id": str(uuid.uuid4()), "user_tid": user["telegram_id"], "username": user.get("username", ""),
        "items": [{"product_id": p["_id"], "name": p["name"], "price": price} for p, price in items],
        "total": total, "currency": currency, "created_at": now_iso(),
    }
    await db.purchases.insert_one(purchase)
    await send_message(chat_id, f"✅ <b>Pembayaran Berhasil!</b>\n\nTotal: <b>{fmt_amount(total, currency)}</b>\nProduk sedang dikirim...")
    for p, _ in items:
        await deliver_product(chat_id, p)
    new_bal = balance - total
    await send_message(chat_id, f"🎉 Semua produk telah dikirim!\nSisa saldo: <b>{fmt_amount(new_bal, currency)}</b>", kb=BACK_KB)
    names = ", ".join(p["name"] for p, _ in items)
    await notify_admin(f"🛒 <b>Penjualan Baru!</b>\n\nPembeli: {user_label(user)}\nProduk: {names}\nTotal: <b>{fmt_amount(total, currency)}</b>")


# ============ DEPOSIT ============

async def show_deposit_menu(chat_id, user):
    s = await get_settings()
    if user["currency"] == "USD":
        kb = {"inline_keyboard": [
            [{"text": "💎 USDT", "callback_data": "depcoin:USDT"}, {"text": "🔵 USDC", "callback_data": "depcoin:USDC"}],
            [{"text": "🏠 Menu Utama", "callback_data": "menu:main"}],
        ]}
        await send_message(chat_id,
            f"💰 <b>Deposit USD</b>\n\nMinimum deposit: <b>${s.get('min_deposit_usd', 15):,.2f}</b>\n\nPilih koin:", kb=kb)
    else:
        if not s.get("bank_account_number"):
            await send_message(chat_id, "💰 <b>Deposit IDR</b>\n\n⚠️ Rekening bank belum dikonfigurasi. Hubungi admin.", kb=BACK_KB)
            return
        min_idr = s.get("min_deposit_idr", 50000)
        await set_state(user["telegram_id"], "dep_idr_amount")
        await send_message(chat_id,
            f"💰 <b>Deposit IDR — Transfer Bank</b>\n\n"
            f"🏦 Bank: <b>{s.get('bank_name','')}</b>\n"
            f"💳 No. Rekening: <code>{s.get('bank_account_number','')}</code>\n"
            f"👤 Atas Nama: <b>{s.get('bank_account_holder','')}</b>\n\n"
            f"Minimum deposit: <b>{fmt_amount(min_idr, 'IDR')}</b>\n\n"
            "Ketik <b>jumlah</b> yang akan Anda transfer (contoh: 100000):", kb=cancel_kb())


async def show_network_selection(chat_id, coin):
    kb = {"inline_keyboard": [
        [{"text": "◎ Solana", "callback_data": f"depnet:{coin}:SOL"}, {"text": "🟣 Polygon", "callback_data": f"depnet:{coin}:POL"}],
        [{"text": "🟡 BNB (BEP-20)", "callback_data": f"depnet:{coin}:BNB"}, {"text": "🔺 Avalanche", "callback_data": f"depnet:{coin}:AVAX"}],
        [{"text": "◀️ Kembali", "callback_data": "menu:deposit"}],
    ]}
    await send_message(chat_id, f"💰 <b>Deposit {coin}</b>\n\nPilih jaringan:", kb=kb)


async def show_deposit_address(chat_id, user, coin, network):
    s = await get_settings()
    address = (s.get("crypto_addresses") or {}).get(f"{coin}_{network}", "")
    if not address:
        await send_message(chat_id,
            f"⚠️ Alamat deposit {coin} di jaringan {NET_LABELS[network]} belum tersedia.\nSilakan pilih jaringan lain atau hubungi admin.",
            kb={"inline_keyboard": [[{"text": "◀️ Pilih Jaringan Lain", "callback_data": f"depcoin:{coin}"}],
                                     [{"text": "🏠 Menu Utama", "callback_data": "menu:main"}]]})
        return
    min_usd = s.get("min_deposit_usd", 15)
    await set_state(user["telegram_id"], "dep_usd_amount", {"coin": coin, "network": network})
    await send_message(chat_id,
        f"💰 <b>Deposit {coin} — {NET_LABELS[network]}</b>\n\n"
        f"Kirim {coin} Anda ke alamat berikut:\n\n<code>{address}</code>\n\n"
        f"⚠️ <b>PENTING:</b>\n• Hanya kirim <b>{coin}</b> di jaringan <b>{NET_LABELS[network]}</b>\n"
        f"• Minimum deposit: <b>${min_usd:,.2f}</b>\n\n"
        "Setelah transfer, ketik <b>jumlah deposit</b> Anda (contoh: 20):", kb=cancel_kb())


def parse_amount(text: str):
    t = text.strip().replace("$", "").replace("Rp", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        try:
            return float(text.strip().replace("$", "").replace(",", ""))
        except ValueError:
            return None


async def handle_dep_usd_amount(chat_id, user, text):
    s = await get_settings()
    min_usd = float(s.get("min_deposit_usd", 15))
    t = text.strip().replace("$", "").replace(",", "")
    try:
        amount = float(t)
    except ValueError:
        await send_message(chat_id, "⚠️ Format jumlah tidak valid. Ketik angka saja (contoh: 20):", kb=cancel_kb())
        return
    if amount < min_usd:
        await send_message(chat_id, f"⚠️ Jumlah minimum deposit adalah <b>${min_usd:,.2f}</b>. Ketik ulang jumlah:", kb=cancel_kb())
        return
    data = user.get("state_data", {})
    data["amount"] = amount
    await set_state(user["telegram_id"], "dep_usd_proof", data)
    await send_message(chat_id,
        f"👍 Jumlah deposit: <b>${amount:,.2f}</b>\n\n"
        "Sekarang kirim <b>bukti transfer</b> Anda:\n\n"
        "🔗 <b>TX Hash</b> (disarankan) — saldo terverifikasi & masuk <b>otomatis</b>\n"
        "📷 <b>Screenshot</b> — diverifikasi manual oleh admin", kb=cancel_kb())


async def handle_dep_idr_amount(chat_id, user, text):
    s = await get_settings()
    min_idr = float(s.get("min_deposit_idr", 50000))
    t = text.strip().replace("Rp", "").replace(".", "").replace(",", "").replace(" ", "")
    try:
        amount = float(t)
    except ValueError:
        await send_message(chat_id, "⚠️ Format jumlah tidak valid. Ketik angka saja (contoh: 100000):", kb=cancel_kb())
        return
    if amount < min_idr:
        await send_message(chat_id, f"⚠️ Jumlah minimum deposit adalah <b>{fmt_amount(min_idr, 'IDR')}</b>. Ketik ulang jumlah:", kb=cancel_kb())
        return
    await set_state(user["telegram_id"], "dep_idr_proof", {"amount": amount})
    await send_message(chat_id,
        f"👍 Jumlah deposit: <b>{fmt_amount(amount, 'IDR')}</b>\n\n"
        "Sekarang kirim <b>foto bukti transfer</b> Anda di chat ini:", kb=cancel_kb())


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
        await send_message(chat_id,
            "⏳ <b>Deposit Menunggu Verifikasi</b>\n\nBukti screenshot Anda telah diterima. "
            "Admin akan memverifikasi secepatnya. Anda akan diberi tahu setelah disetujui.", kb=BACK_KB)
        await notify_admin(
            f"💰 <b>Deposit Baru — Perlu Verifikasi</b>\n\nDari: {user_label(user)}\n"
            f"Metode: {coin} / {NET_LABELS[network]}\nJumlah klaim: <b>${amount:,.2f}</b>\nBukti: screenshot 👆",
            kb=admin_decision_kb(dep["_id"]), photo_file_id=file_id)
        return

    tx_hash = text.strip()
    if not looks_like_tx_hash(tx_hash, network):
        await send_message(chat_id,
            "⚠️ Format TX hash tidak valid.\n\nKirim <b>TX hash</b> transaksi Anda, atau kirim <b>screenshot</b> bukti transfer:",
            kb=cancel_kb())
        return

    existing = await db.deposits.find_one({"tx_hash": tx_hash, "status": {"$in": ["pending", "approved"]}})
    if existing:
        await send_message(chat_id, "⚠️ TX hash ini sudah pernah digunakan. Setiap transaksi hanya bisa diklaim satu kali.", kb=BACK_KB)
        await set_state(user["telegram_id"], None)
        return

    await send_message(chat_id, "🔍 Memeriksa transaksi di blockchain, mohon tunggu...")
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
        await send_message(chat_id,
            f"✅ <b>Deposit Terverifikasi Otomatis!</b>\n\n"
            f"Transaksi ditemukan di blockchain:\n• Koin: {coin} ({NET_LABELS[network]})\n"
            f"• Jumlah on-chain: <b>${onchain_amount:,.2f}</b>\n\n"
            f"Saldo Anda sekarang: <b>${new_bal:,.2f}</b> 🎉", kb=BACK_KB)
        await notify_admin(
            f"✅ <b>Deposit Otomatis Terverifikasi</b>\n\nDari: {user_label(user)}\n"
            f"Koin: {coin} / {NET_LABELS[network]}\nJumlah on-chain: <b>${onchain_amount:,.2f}</b>\n"
            f"TX: <code>{tx_hash}</code>",
            kb={"inline_keyboard": [[{"text": "🚫 Batalkan Deposit Ini", "callback_data": f"adm:cxl:{dep['_id']}"}]]})
    else:
        dep = await create_pending_deposit(user, data, tx_hash=tx_hash)
        await set_state(user["telegram_id"], None)
        why = reason or f"Jumlah on-chain (${onchain_amount:,.2f}) di bawah minimum" if verified else (reason or "Tidak dapat diverifikasi")
        await send_message(chat_id,
            f"⏳ <b>Deposit Menunggu Verifikasi Manual</b>\n\n"
            f"Verifikasi otomatis tidak berhasil: {why}.\n"
            "Admin akan memeriksa deposit Anda secepatnya.", kb=BACK_KB)
        await notify_admin(
            f"💰 <b>Deposit Baru — Perlu Verifikasi Manual</b>\n\nDari: {user_label(user)}\n"
            f"Koin: {coin} / {NET_LABELS[network]}\nJumlah klaim: <b>${amount:,.2f}</b>\n"
            f"TX: <code>{tx_hash}</code>\n⚠️ Auto-verify gagal: {why}",
            kb=admin_decision_kb(dep["_id"]))


async def handle_idr_proof(chat_id, user, message):
    data = user.get("state_data", {})
    amount = data.get("amount")
    photo = message.get("photo")
    if not photo:
        await send_message(chat_id, "⚠️ Silakan kirim <b>foto</b> bukti transfer bank Anda:", kb=cancel_kb())
        return
    file_id = photo[-1]["file_id"]
    dep = await create_pending_deposit(user, {"amount": amount}, proof_file_id=file_id)
    await set_state(user["telegram_id"], None)
    await send_message(chat_id,
        "⏳ <b>Deposit Menunggu Verifikasi</b>\n\nBukti transfer Anda telah diterima. "
        "Admin akan memverifikasi secepatnya dan saldo akan masuk otomatis setelah disetujui.", kb=BACK_KB)
    await notify_admin(
        f"💰 <b>Deposit IDR Baru — Perlu Verifikasi</b>\n\nDari: {user_label(user)}\n"
        f"Metode: Transfer Bank\nJumlah: <b>{fmt_amount(amount, 'IDR')}</b>\nBukti: 👆",
        kb=admin_decision_kb(dep["_id"]), photo_file_id=file_id)


# ============ OTHER MENUS ============

async def show_balance(chat_id, user):
    rate = await get_rate()
    await send_message(chat_id,
        f"💳 <b>Saldo Anda</b>\n\n"
        f"💵 USD: <b>${user.get('balance_usd', 0):,.2f}</b>\n"
        f"🇮🇩 IDR: <b>{fmt_amount(user.get('balance_idr', 0), 'IDR')}</b>\n\n"
        f"Mata uang aktif: <b>{user['currency']}</b>\nKurs saat ini: $1 = {fmt_amount(rate, 'IDR')}", kb=BACK_KB)


async def show_history(chat_id, user):
    deps = await db.deposits.find({"user_tid": user["telegram_id"]}).sort("created_at", -1).to_list(5)
    purs = await db.purchases.find({"user_tid": user["telegram_id"]}).sort("created_at", -1).to_list(5)
    status_label = {"pending": "⏳ Menunggu", "approved": "✅ Disetujui", "rejected": "❌ Ditolak", "cancelled": "🚫 Dibatalkan"}
    lines = ["📜 <b>Riwayat Anda</b>\n", "<b>Deposit terakhir:</b>"]
    if deps:
        for d in deps:
            amt = d.get("credited_amount") or d["amount"]
            lines.append(f"• {fmt_amount(amt, d['currency'])} — {status_label.get(d['status'], d['status'])} — {d['created_at'][:10]}")
    else:
        lines.append("Belum ada deposit.")
    lines.append("\n<b>Pembelian terakhir:</b>")
    if purs:
        for p in purs:
            names = ", ".join(i["name"] for i in p["items"])
            lines.append(f"• {names} — {fmt_amount(p['total'], p['currency'])} — {p['created_at'][:10]}")
    else:
        lines.append("Belum ada pembelian.")
    await send_message(chat_id, "\n".join(lines), kb=BACK_KB)


async def show_settings(chat_id, user):
    other = "IDR" if user["currency"] == "USD" else "USD"
    kb = {"inline_keyboard": [
        [{"text": f"🔄 Ganti ke {other}", "callback_data": f"setcur:{other}"}],
        [{"text": "🏠 Menu Utama", "callback_data": "menu:main"}],
    ]}
    await send_message(chat_id,
        f"⚙️ <b>Pengaturan</b>\n\nMata uang aktif: <b>{user['currency']}</b>", kb=kb)


async def handle_set_currency(chat_id, user, new_cur):
    old_cur = user["currency"]
    old_field = CUR_FIELD[old_cur]
    balance = float(user.get(old_field, 0))
    if balance > 0:
        rate = await get_rate()
        if old_cur == "USD":
            converted = balance * rate
        else:
            converted = balance / rate
        kb = {"inline_keyboard": [
            [{"text": "✅ Ya, konversi saldo", "callback_data": f"conv:yes:{new_cur}"}],
            [{"text": "❌ Tidak, saldo tetap terpisah", "callback_data": f"conv:no:{new_cur}"}],
        ]}
        await send_message(chat_id,
            f"🔄 <b>Ganti Mata Uang ke {new_cur}</b>\n\n"
            f"Anda punya saldo <b>{fmt_amount(balance, old_cur)}</b>.\n"
            f"Konversi ke <b>{fmt_amount(converted, new_cur)}</b> (kurs $1 = {fmt_amount(rate, 'IDR')})?", kb=kb)
    else:
        await db.bot_users.update_one({"telegram_id": user["telegram_id"]}, {"$set": {"currency": new_cur}})
        await send_message(chat_id, f"✅ Mata uang diubah ke <b>{new_cur}</b>.", kb=BACK_KB)


async def handle_conversion(chat_id, user, convert, new_cur):
    old_cur = "USD" if new_cur == "IDR" else "IDR"
    updates = {"currency": new_cur}
    if convert:
        rate = await get_rate()
        balance = float(user.get(CUR_FIELD[old_cur], 0))
        converted = balance * rate if old_cur == "USD" else balance / rate
        await db.bot_users.update_one({"telegram_id": user["telegram_id"]}, {
            "$set": {**updates, CUR_FIELD[old_cur]: 0.0},
            "$inc": {CUR_FIELD[new_cur]: converted},
        })
        await send_message(chat_id,
            f"✅ Mata uang diubah ke <b>{new_cur}</b> dan saldo dikonversi menjadi <b>{fmt_amount(converted, new_cur)}</b>.", kb=BACK_KB)
    else:
        await db.bot_users.update_one({"telegram_id": user["telegram_id"]}, {"$set": updates})
        await send_message(chat_id, f"✅ Mata uang diubah ke <b>{new_cur}</b>. Saldo lama tetap tersimpan terpisah.", kb=BACK_KB)


HELP_TEXT = (
    "❓ <b>Bantuan</b>\n\n"
    "<b>Cara belanja:</b>\n"
    "1️⃣ Isi saldo lewat menu <b>Deposit</b>\n"
    "2️⃣ Pilih produk di <b>Lihat Produk</b>\n"
    "3️⃣ Beli langsung atau lewat <b>Keranjang</b>\n"
    "4️⃣ Produk terkirim otomatis ✨\n\n"
    "<b>Perintah:</b>\n"
    "/start — mulai bot\n/menu — menu utama\n/saldo — cek saldo\n/riwayat — riwayat transaksi\n/batal — batalkan proses"
)


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

FROZEN_MSG = "🚫 <b>Akun Anda Dibekukan</b>\n\n{reason}Anda tidak dapat melakukan deposit atau pembelian. Hubungi admin untuk informasi lebih lanjut."


def frozen_text(user):
    r = user.get("frozen_reason", "")
    return FROZEN_MSG.format(reason=f"Alasan: {r}\n\n" if r else "")


async def handle_callback(cb):
    data = cb.get("data", "")
    chat_id = cb["message"]["chat"]["id"]
    if data.startswith("adm:"):
        _, action, dep_id = data.split(":", 2)
        await handle_admin_callback(cb, action, dep_id)
        return
    user = await get_user(cb["from"])
    await answer_callback(cb["id"])

    if data.startswith("cur:"):
        new_cur = data.split(":")[1]
        await db.bot_users.update_one({"telegram_id": user["telegram_id"]}, {"$set": {"currency": new_cur}})
        user["currency"] = new_cur
        await show_main_menu(chat_id, user)
        return

    if not user.get("currency"):
        await show_currency_selection(chat_id)
        return

    if user.get("frozen") and data not in ("menu:help",):
        await send_message(chat_id, frozen_text(user))
        return

    if data == "cancel":
        await set_state(user["telegram_id"], None)
        await show_main_menu(chat_id, user)
    elif data == "menu:main":
        await set_state(user["telegram_id"], None)
        await show_main_menu(chat_id, user)
    elif data == "menu:products":
        await show_products(chat_id, user)
    elif data.startswith("prod:"):
        await show_product_detail(chat_id, user, data.split(":", 1)[1])
    elif data.startswith("buy:"):
        await do_checkout(chat_id, user, [data.split(":", 1)[1]])
    elif data.startswith("cartadd:"):
        pid = data.split(":", 1)[1]
        if pid not in user.get("cart", []):
            await db.bot_users.update_one({"telegram_id": user["telegram_id"]}, {"$push": {"cart": pid}})
            await send_message(chat_id, "✅ Produk ditambahkan ke keranjang!", kb={"inline_keyboard": [
                [{"text": "🛒 Lihat Keranjang", "callback_data": "menu:cart"}],
                [{"text": "🛍 Lanjut Belanja", "callback_data": "menu:products"}]]})
        else:
            await send_message(chat_id, "Produk sudah ada di keranjang.", kb=BACK_KB)
    elif data == "menu:cart":
        await show_cart(chat_id, user)
    elif data.startswith("cartrm:"):
        await db.bot_users.update_one({"telegram_id": user["telegram_id"]}, {"$pull": {"cart": data.split(":", 1)[1]}})
        user = await db.bot_users.find_one({"telegram_id": user["telegram_id"]})
        await show_cart(chat_id, user)
    elif data == "cartclear":
        await db.bot_users.update_one({"telegram_id": user["telegram_id"]}, {"$set": {"cart": []}})
        await send_message(chat_id, "🧹 Keranjang dikosongkan.", kb=BACK_KB)
    elif data == "checkout":
        await do_checkout(chat_id, user, user.get("cart", []))
    elif data == "menu:deposit":
        await show_deposit_menu(chat_id, user)
    elif data.startswith("depcoin:"):
        await show_network_selection(chat_id, data.split(":")[1])
    elif data.startswith("depnet:"):
        _, coin, network = data.split(":")
        await show_deposit_address(chat_id, user, coin, network)
    elif data == "menu:balance":
        await show_balance(chat_id, user)
    elif data == "menu:history":
        await show_history(chat_id, user)
    elif data == "menu:settings":
        await show_settings(chat_id, user)
    elif data.startswith("setcur:"):
        await handle_set_currency(chat_id, user, data.split(":")[1])
    elif data.startswith("conv:"):
        _, yn, new_cur = data.split(":")
        await handle_conversion(chat_id, user, yn == "yes", new_cur)
    elif data == "menu:help":
        await send_message(chat_id, HELP_TEXT, kb=BACK_KB)


async def handle_message(message):
    if "from" not in message or message["from"].get("is_bot"):
        return
    chat_id = message["chat"]["id"]
    user = await get_user(message["from"])
    text = (message.get("text") or "").strip()

    if text == "/start":
        await set_state(user["telegram_id"], None)
        if not user.get("currency"):
            await show_currency_selection(chat_id)
        elif user.get("frozen"):
            await send_message(chat_id, frozen_text(user))
        else:
            await show_main_menu(chat_id, user)
        return

    if not user.get("currency"):
        await show_currency_selection(chat_id)
        return

    if user.get("frozen"):
        await send_message(chat_id, frozen_text(user))
        return

    if text == "/batal":
        await set_state(user["telegram_id"], None)
        await show_main_menu(chat_id, user)
        return
    if text == "/menu":
        await set_state(user["telegram_id"], None)
        await show_main_menu(chat_id, user)
        return
    if text == "/saldo":
        await show_balance(chat_id, user)
        return
    if text == "/riwayat":
        await show_history(chat_id, user)
        return
    if text == "/help":
        await send_message(chat_id, HELP_TEXT, kb=BACK_KB)
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
