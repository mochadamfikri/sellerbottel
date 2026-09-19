from datetime import datetime, timezone
from db import db, get_settings
from tgapi import send_message

CUR_FIELD = {"USD": "balance_usd", "IDR": "balance_idr"}


def fmt_amount(amount: float, currency: str) -> str:
    if currency == "USD":
        return f"${amount:,.2f}"
    return f"Rp {amount:,.0f}".replace(",", ".")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


async def credit_deposit(deposit: dict, note: str = ""):
    amount = deposit.get("credited_amount") or deposit["amount"]
    field = CUR_FIELD[deposit["currency"]]
    await db.bot_users.update_one({"telegram_id": deposit["user_tid"]}, {"$inc": {field: amount}})
    await db.deposits.update_one({"_id": deposit["_id"]}, {"$set": {
        "status": "approved", "credited_amount": amount, "decided_at": now_iso(), "note": note,
    }})
    await send_message(deposit["user_tid"],
        f"✅ <b>Deposit Disetujui!</b>\n\nSaldo Anda bertambah <b>{fmt_amount(amount, deposit['currency'])}</b>.\nKetik /menu untuk mulai belanja.")


async def reject_deposit(deposit: dict, note: str = ""):
    await db.deposits.update_one({"_id": deposit["_id"]}, {"$set": {
        "status": "rejected", "decided_at": now_iso(), "note": note,
    }})
    reason = f"\nAlasan: {note}" if note else ""
    await send_message(deposit["user_tid"],
        f"❌ <b>Deposit Ditolak</b>\n\nDeposit {fmt_amount(deposit['amount'], deposit['currency'])} Anda ditolak.{reason}\nHubungi admin jika ada pertanyaan.")


async def cancel_deposit(deposit: dict):
    amount = deposit.get("credited_amount") or deposit["amount"]
    field = CUR_FIELD[deposit["currency"]]
    await db.bot_users.update_one({"telegram_id": deposit["user_tid"]}, {"$inc": {field: -amount}})
    await db.deposits.update_one({"_id": deposit["_id"]}, {"$set": {"status": "cancelled", "decided_at": now_iso()}})
    await send_message(deposit["user_tid"],
        f"⚠️ <b>Deposit Dibatalkan Admin</b>\n\nDeposit {fmt_amount(amount, deposit['currency'])} dibatalkan dan saldo dikurangi kembali.")


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
