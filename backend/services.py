from datetime import datetime, timezone
from db import db, get_settings
from tgapi import send_message
from i18n import t

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
