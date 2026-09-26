from fastapi import APIRouter, Depends
import re

from auth import get_current_admin
from db import db, get_settings
from promo_telegram import decrypt_session, _api
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.errors import UserAlreadyParticipantError

router = APIRouter(prefix="/api/admin", dependencies=[Depends(get_current_admin)])


@router.get("/store-customers")
async def list_store_customers(search: str = ""):
    query = {}
    term = search.strip()[:120]
    if term:
        username_term = term[1:] if term.startswith("@") else term
        bot_clauses = [
            {"username": {"$regex": re.escape(username_term), "$options": "i"}},
            {"first_name": {"$regex": re.escape(term), "$options": "i"}},
            {"last_name": {"$regex": re.escape(term), "$options": "i"}},
        ]
        if term.isdigit():
            bot_clauses.append({"telegram_id": int(term)})
        matching_tids = [
            row["telegram_id"]
            for row in await db.bot_users.find({"$or": bot_clauses}, {"telegram_id": 1}).limit(500).to_list(500)
        ]
        customer_clauses = [{"email": {"$regex": re.escape(term), "$options": "i"}}]
        if matching_tids:
            customer_clauses.append({"telegram_id": {"$in": matching_tids}})
        if term.isdigit():
            customer_clauses.append({"telegram_id": int(term)})
        query["$or"] = customer_clauses
    customers = await db.store_customers.find(query, {"password_hash": 0}).sort("created_at", -1).limit(500).to_list(500)
    result = []
    for customer in customers:
        tid = customer.get("telegram_id")
        bot_user = await db.bot_users.find_one({"telegram_id": tid}) if tid else None
        result.append({
            "_id": "store:" + customer["_id"],
            "user_type": "store_customer",
            "email": customer.get("email"),
            "telegram_id": tid,
            "first_name": (bot_user or {}).get("first_name"),
            "username": (bot_user or {}).get("username"),
            "balance_usd": (bot_user or {}).get("balance_usd", customer.get("balance_usd", 0)),
            "balance_idr": (bot_user or {}).get("balance_idr", customer.get("balance_idr", 0)),
            "currency": (bot_user or {}).get("currency", "IDR"),
            "frozen": (bot_user or {}).get("frozen", customer.get("account_disabled", False)),
            "purchase_count": (await db.purchases.count_documents({"customer_id": customer["_id"]}) +
                               (await db.purchases.count_documents({"user_tid": tid}) if tid else 0)),
            "telegram_linked": bool(tid),
            "email_verified": bool(customer.get("verified_at")),
            "created_at": customer.get("created_at"),
        })
    return result


@router.get("/users/all")
async def list_all_users():
    purchase_counts = {
        row["_id"]: row["n"]
        for row in await db.purchases.aggregate([
            {"$group": {"_id": "$user_tid", "n": {"$sum": 1}}}
        ]).to_list(10000)
    }

    users = []
    async for user in db.bot_users.find({}).sort("created_at", -1):
        user.pop("state", None)
        user.pop("state_data", None)
        user["purchase_count"] = purchase_counts.get(user.get("telegram_id"), 0)
        user["telegram_account_connected"] = bool(await db.tg_accounts.find_one({
            "tg_user_id": user.get("telegram_id"),
            "status": "active",
            "session_encrypted": {"$type": "string"},
        }))
        users.append(user)

    return users


@router.post("/users/{tid}/join-group")
async def join_group_for_user(tid: int):
    account = await db.tg_accounts.find_one({
        "tg_user_id": tid,
        "status": "active",
        "session_encrypted": {"$type": "string"},
    })
    if not account:
        return {"ok": False, "message": "Tidak ada akun Telegram terhubung yang aktif untuk pengguna ini."}

    settings = await get_settings()
    target = str(settings.get("join_group_target") or "").strip()
    if not target:
        return {"ok": False, "message": "Target group belum dikonfigurasi di Pengaturan."}

    api_id, api_hash = _api()
    client = TelegramClient(StringSession(decrypt_session(account["session_encrypted"])), api_id, api_hash)
    await client.connect()
    try:
        if target.startswith("https://t.me/+") or target.startswith("https://t.me/joinchat/"):
            invite_hash = target.split("/")[-1].replace("+", "")
            try:
                await client(ImportChatInviteRequest(invite_hash))
            except UserAlreadyParticipantError:
                pass
        else:
            entity = await client.get_entity(target)
            try:
                await client(JoinChannelRequest(entity))
            except UserAlreadyParticipantError:
                pass
        return {"ok": True, "message": "Akun Telegram berhasil diproses untuk join group.", "target": target}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    finally:
        await client.disconnect()
