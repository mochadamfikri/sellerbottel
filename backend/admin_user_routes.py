from fastapi import APIRouter, Depends

from auth import get_current_admin
from db import db, get_settings
from promo_telegram import decrypt_session, _api
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.errors import UserAlreadyParticipantError

router = APIRouter(prefix="/api/admin", dependencies=[Depends(get_current_admin)])


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
