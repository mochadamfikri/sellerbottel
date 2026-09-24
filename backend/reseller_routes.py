"""Admin controls and webhook entry point for reseller bots."""
import asyncio
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import get_current_admin
from db import db, get_settings
from reseller_service import activation_fees, configure_webhook
from services import now_iso

admin_router = APIRouter(prefix="/api/admin/resellers", dependencies=[Depends(get_current_admin)])
webhook_router = APIRouter(prefix="/api/telegram/reseller")


class ResellerSettings(BaseModel):
    enabled: bool = False
    bot_price_idr: int = Field(ge=0, le=100_000_000)
    admin_fee_idr: int = Field(ge=0, le=100_000_000)
    platform_fee_idr: int = Field(ge=0, le=100_000_000)
    wholesale_reduction_idr: int = Field(ge=0, le=100_000_000)


class PayoutDecision(BaseModel):
    transfer_reference: str = Field(default="", max_length=120)


@admin_router.get("/settings")
async def reseller_settings():
    s = await get_settings()
    return {"enabled": s.get("reseller_enabled", False),
            "bot_price_idr": s.get("reseller_bot_price_idr", 0),
            "admin_fee_idr": s.get("reseller_admin_fee_idr", 0),
            "platform_fee_idr": s.get("reseller_platform_fee_idr", 0),
            "wholesale_reduction_idr": s.get("reseller_wholesale_reduction_idr", 2000)}


@admin_router.put("/settings")
async def save_reseller_settings(body: ResellerSettings):
    if body.enabled and body.bot_price_idr + body.admin_fee_idr + body.platform_fee_idr <= 0:
        raise HTTPException(400, "Isi biaya langganan bulanan sebelum membuka pendaftaran.")
    data = {"reseller_enabled": body.enabled,
            "reseller_bot_price_idr": body.bot_price_idr,
            "reseller_admin_fee_idr": body.admin_fee_idr,
            "reseller_platform_fee_idr": body.platform_fee_idr,
            "reseller_wholesale_reduction_idr": body.wholesale_reduction_idr}
    await db.settings.update_one({"_id": "main"}, {"$set": data})
    return await reseller_settings()


async def bot_summary(bot: dict) -> dict:
    count = await db.reseller_bot_users.count_documents({"bot_id": bot["_id"]})
    orders = await db.purchases.count_documents({"reseller_bot_id": bot["_id"], "status": "delivered"})
    pipeline = [{"$match": {"reseller_bot_id": bot["_id"], "status": "delivered"}},
                {"$group": {"_id": None, "sales": {"$sum": "$total"}, "profit": {"$sum": "$reseller_margin"}}}]
    amounts = await db.purchases.aggregate(pipeline).to_list(1)
    latest_sale = await db.purchases.find_one(
        {"reseller_bot_id": bot["_id"],
         "status": {"$in": ["paid", "processing", "service_waiting", "delivered", "delivery_failed"]}},
        {"created_at": 1}, sort=[("created_at", -1)])
    return {"_id": bot["_id"], "username": bot.get("username"), "name": bot.get("name"),
            "owner_tid": bot.get("owner_tid"), "admin_tid": bot.get("admin_tid"),
            "status": bot.get("status"), "created_at": bot.get("created_at"),
            "activated_at": bot.get("activated_at"), "expires_at": bot.get("expires_at"),
            "last_cycle_paid_at": bot.get("last_cycle_paid_at"),
            "last_sale_at": (latest_sale or {}).get("created_at"),
            "inactivated_at": bot.get("inactivated_at"),
            "inactivation_reason": bot.get("inactivation_reason"),
            "renewal_pending": bool(bot.get("renewal_pending")), "fees": bot.get("fees"),
            "user_count": count, "completed_orders": orders,
            "sales_idr": (amounts[0]["sales"] if amounts else 0),
            "profit_idr": (amounts[0]["profit"] if amounts else 0),
            "price_count": len(bot.get("markups") or {}),
            "default_markup_idr": bot.get("default_markup_idr", 0)}


@admin_router.get("")
async def list_resellers():
    bots = await db.reseller_bots.find({}, {"token_encrypted": 0, "webhook_secret": 0}).sort("created_at", -1).to_list(500)
    return [await bot_summary(bot) for bot in bots]


@admin_router.get("/payouts")
async def list_payouts():
    return await db.reseller_payouts.find().sort("created_at", -1).limit(200).to_list(200)


@admin_router.post("/payouts/{payout_id}/paid")
async def mark_payout_paid(payout_id: str, body: PayoutDecision):
    payout = await db.reseller_payouts.find_one_and_update(
        {"_id": payout_id, "status": "pending_transfer"},
        {"$set": {"status": "paid", "paid_at": now_iso(),
                  "transfer_reference": body.transfer_reference.strip()}},
    )
    if not payout:
        raise HTTPException(400, "Pencairan tidak ditemukan atau sudah diproses.")
    await db.reseller_commissions.update_many({"payout_id": payout_id, "status": "reserved"},
                                              {"$set": {"status": "paid", "paid_at": now_iso()}})
    bot = await db.reseller_bots.find_one({"_id": payout["bot_id"]})
    if bot:
        try:
            from reseller_bot import send
            await send(bot, bot["admin_tid"],
                       f"✅ Komisi {payout.get('net_amount') or payout['amount']:,.0f} IDR telah ditransfer oleh admin pusat.\n"
                       f"Referensi: <code>{body.transfer_reference.strip() or '-'}</code>")
        except Exception:
            pass
    return {"ok": True}


@admin_router.post("/payouts/{payout_id}/cancel")
async def cancel_payout(payout_id: str):
    payout = await db.reseller_payouts.find_one_and_update(
        {"_id": payout_id, "status": "pending_transfer"},
        {"$set": {"status": "cancelled", "cancelled_at": now_iso()}},
    )
    if not payout:
        raise HTTPException(400, "Pencairan tidak ditemukan atau sudah diproses.")
    await db.reseller_commissions.update_many({"payout_id": payout_id, "status": "reserved"},
                                              {"$set": {"status": "pending_payout"},
                                               "$unset": {"payout_id": ""}})
    return {"ok": True}


@admin_router.get("/{bot_id}")
async def reseller_detail(bot_id: str):
    bot = await db.reseller_bots.find_one({"_id": bot_id}, {"token_encrypted": 0, "webhook_secret": 0})
    if not bot:
        raise HTTPException(404, "Bot reseller tidak ditemukan.")
    summary = await bot_summary(bot)
    summary["markups"] = bot.get("markups") or {}
    summary["recent_users"] = await db.reseller_bot_users.find({"bot_id": bot_id}).sort("created_at", -1).limit(20).to_list(20)
    summary["recent_orders"] = await db.purchases.find({"reseller_bot_id": bot_id},
                                                       {"_id": 1, "invoice_id": 1, "user_tid": 1,
                                                        "total": 1, "status": 1, "created_at": 1,
                                                        "reseller_margin": 1}).sort("created_at", -1).limit(20).to_list(20)
    summary["payments"] = await db.reseller_payments.find({"bot_id": bot_id}).sort("created_at", -1).limit(12).to_list(12)
    summary["payouts"] = await db.reseller_payouts.find({"bot_id": bot_id}).sort("created_at", -1).limit(20).to_list(20)
    summary["commission_balance"] = await __import__("reseller_payout").commission_balance(bot_id)
    return summary


@admin_router.post("/{bot_id}/orders/{order_id}/complete")
async def complete_reseller_service(bot_id: str, order_id: str):
    order = await db.purchases.find_one_and_update(
        {"_id": order_id, "reseller_bot_id": bot_id, "status": "service_waiting"},
        {"$set": {"status": "delivered", "delivered_at": now_iso()}},
    )
    if not order:
        raise HTTPException(400, "Pesanan jasa tidak ditemukan atau sudah selesai.")
    bot = await db.reseller_bots.find_one({"_id": bot_id})
    if bot:
        from reseller_bot import record_commission, send
        await record_commission(bot, order)
        await send(bot, order["user_tid"], f"✅ Pesanan jasa <code>{order['invoice_id']}</code> selesai.")
    return {"ok": True}


@admin_router.post("/{bot_id}/pause")
async def pause_reseller(bot_id: str):
    result = await db.reseller_bots.update_one({"_id": bot_id, "status": "active"},
                                               {"$set": {"status": "paused", "updated_at": now_iso()}})
    if not result.modified_count:
        raise HTTPException(400, "Hanya bot aktif yang bisa dijeda.")
    return {"ok": True}


@admin_router.post("/{bot_id}/resume")
async def resume_reseller(bot_id: str):
    bot = await db.reseller_bots.find_one({"_id": bot_id, "status": "paused"})
    if not bot or not bot.get("expires_at") or bot["expires_at"] <= now_iso():
        raise HTTPException(400, "Langganan bot tidak aktif atau sudah kedaluwarsa.")
    try:
        await configure_webhook(bot)
    except Exception as exc:
        raise HTTPException(502, f"Webhook Telegram gagal: {exc}") from exc
    await db.reseller_bots.update_one({"_id": bot_id, "status": "paused"},
                                      {"$set": {"status": "active", "updated_at": now_iso()}})
    return {"ok": True}


@webhook_router.post("/{bot_id}/webhook")
async def reseller_webhook(bot_id: str, request: Request):
    bot = await db.reseller_bots.find_one({"_id": bot_id})
    received = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not bot or not received or not hmac.compare_digest(received, bot.get("webhook_secret") or ""):
        raise HTTPException(403, "Invalid webhook token")
    update = await request.json()
    update_id = update.get("update_id")
    if update_id is not None:
        try:
            await db.reseller_updates.insert_one({"bot_id": bot_id, "update_id": update_id,
                                                   "created_at": now_iso()})
        except Exception:
            return {"ok": True, "duplicate": True}
    from reseller_bot import process_reseller_update
    asyncio.create_task(process_reseller_update(bot, update))
    return {"ok": True}
