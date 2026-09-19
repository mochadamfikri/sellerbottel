import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Response
from pydantic import BaseModel
from db import db, get_settings
from auth import get_current_admin
from rates import get_rate
from services import credit_deposit, reject_deposit, cancel_deposit, fmt_amount, now_iso
from storage import put_object
from tgapi import download_telegram_file, send_message

router = APIRouter(prefix="/api/admin", dependencies=[Depends(get_current_admin)])


# ============ STATS ============

@router.get("/stats")
async def stats():
    async def sum_by(coll, match, field, currency):
        pipeline = [{"$match": {**match, "currency": currency}}, {"$group": {"_id": None, "t": {"$sum": f"${field}"}}}]
        res = await coll.aggregate(pipeline).to_list(1)
        return res[0]["t"] if res else 0
    dep_usd = await sum_by(db.deposits, {"status": "approved"}, "credited_amount", "USD")
    dep_idr = await sum_by(db.deposits, {"status": "approved"}, "credited_amount", "IDR")
    sales_usd = await sum_by(db.purchases, {}, "total", "USD")
    sales_idr = await sum_by(db.purchases, {}, "total", "IDR")
    circ = await db.bot_users.aggregate([{"$group": {"_id": None, "usd": {"$sum": "$balance_usd"}, "idr": {"$sum": "$balance_idr"}}}]).to_list(1)
    pending = await db.deposits.count_documents({"status": "pending"})
    recent = await db.deposits.find().sort("created_at", -1).to_list(8)
    rate = await get_rate()
    s = await get_settings()
    return {
        "total_deposit_usd": dep_usd, "total_deposit_idr": dep_idr,
        "total_sales_usd": sales_usd, "total_sales_idr": sales_idr,
        "circulating_usd": circ[0]["usd"] if circ else 0, "circulating_idr": circ[0]["idr"] if circ else 0,
        "pending_deposits": pending, "recent_deposits": recent,
        "rate": rate, "rate_mode": s.get("rate_mode"),
        "users_count": await db.bot_users.count_documents({}),
        "products_count": await db.products.count_documents({}),
    }


# ============ PRODUCTS ============

@router.get("/products")
async def list_products():
    return await db.products.find().sort("created_at", -1).to_list(500)


async def _save_file(file: UploadFile):
    ext = file.filename.split(".")[-1] if "." in file.filename else "bin"
    path = f"tokobot/products/{uuid.uuid4()}.{ext}"
    data = await file.read()
    result = await put_object(path, data, file.content_type or "application/octet-stream")
    return result["path"], file.filename


@router.post("/products")
async def create_product(
    name: str = Form(...), description: str = Form(""), price_usd: float = Form(...),
    price_idr: Optional[float] = Form(None), delivery_type: str = Form(...),
    content: str = Form(""), active: bool = Form(True), file: Optional[UploadFile] = File(None),
):
    storage_path, original_filename = None, None
    if delivery_type == "file":
        if not file:
            raise HTTPException(400, "File wajib diupload untuk produk tipe file")
        storage_path, original_filename = await _save_file(file)
    prod = {
        "_id": str(uuid.uuid4()), "name": name, "description": description,
        "price_usd": price_usd, "price_idr": price_idr, "delivery_type": delivery_type,
        "content": content, "storage_path": storage_path, "original_filename": original_filename,
        "active": active, "created_at": now_iso(),
    }
    await db.products.insert_one(prod)
    return prod


@router.put("/products/{pid}")
async def update_product(
    pid: str, name: str = Form(...), description: str = Form(""), price_usd: float = Form(...),
    price_idr: Optional[float] = Form(None), delivery_type: str = Form(...),
    content: str = Form(""), active: bool = Form(True), file: Optional[UploadFile] = File(None),
):
    prod = await db.products.find_one({"_id": pid})
    if not prod:
        raise HTTPException(404, "Produk tidak ditemukan")
    updates = {"name": name, "description": description, "price_usd": price_usd,
               "price_idr": price_idr, "delivery_type": delivery_type, "content": content, "active": active}
    if file:
        updates["storage_path"], updates["original_filename"] = await _save_file(file)
    await db.products.update_one({"_id": pid}, {"$set": updates})
    return await db.products.find_one({"_id": pid})


@router.patch("/products/{pid}/toggle")
async def toggle_product(pid: str):
    prod = await db.products.find_one({"_id": pid})
    if not prod:
        raise HTTPException(404, "Produk tidak ditemukan")
    await db.products.update_one({"_id": pid}, {"$set": {"active": not prod.get("active", True)}})
    return {"active": not prod.get("active", True)}


@router.delete("/products/{pid}")
async def delete_product(pid: str):
    await db.products.delete_one({"_id": pid})
    return {"ok": True}


# ============ DEPOSITS ============

@router.get("/deposits")
async def list_deposits(status: Optional[str] = None):
    q = {"status": status} if status and status != "all" else {}
    return await db.deposits.find(q).sort("created_at", -1).to_list(500)


class DecisionBody(BaseModel):
    note: str = ""


@router.post("/deposits/{dep_id}/approve")
async def approve_deposit_api(dep_id: str, body: DecisionBody):
    dep = await db.deposits.find_one({"_id": dep_id})
    if not dep:
        raise HTTPException(404, "Deposit tidak ditemukan")
    if dep["status"] != "pending":
        raise HTTPException(400, f"Deposit sudah diproses ({dep['status']})")
    await credit_deposit(dep, note=body.note or "Disetujui via dashboard")
    return {"ok": True}


@router.post("/deposits/{dep_id}/reject")
async def reject_deposit_api(dep_id: str, body: DecisionBody):
    dep = await db.deposits.find_one({"_id": dep_id})
    if not dep:
        raise HTTPException(404, "Deposit tidak ditemukan")
    if dep["status"] != "pending":
        raise HTTPException(400, f"Deposit sudah diproses ({dep['status']})")
    await reject_deposit(dep, note=body.note)
    return {"ok": True}


@router.post("/deposits/{dep_id}/cancel")
async def cancel_deposit_api(dep_id: str):
    dep = await db.deposits.find_one({"_id": dep_id})
    if not dep:
        raise HTTPException(404, "Deposit tidak ditemukan")
    if dep["status"] != "approved":
        raise HTTPException(400, f"Hanya deposit disetujui yang bisa dibatalkan ({dep['status']})")
    await cancel_deposit(dep)
    return {"ok": True}


@router.get("/deposits/{dep_id}/proof")
async def deposit_proof(dep_id: str):
    dep = await db.deposits.find_one({"_id": dep_id})
    if not dep or not dep.get("proof_file_id"):
        raise HTTPException(404, "Bukti tidak ditemukan")
    data = await download_telegram_file(dep["proof_file_id"])
    if not data:
        raise HTTPException(502, "Gagal mengambil file dari Telegram")
    return Response(content=data, media_type="image/jpeg")


# ============ USERS ============

@router.get("/users")
async def list_users():
    users = await db.bot_users.find().sort("created_at", -1).to_list(500)
    counts = {c["_id"]: c["n"] for c in await db.purchases.aggregate([{"$group": {"_id": "$user_tid", "n": {"$sum": 1}}}]).to_list(1000)}
    for u in users:
        u["_id"] = str(u["_id"])
        u["purchase_count"] = counts.get(u["telegram_id"], 0)
        u.pop("state", None)
        u.pop("state_data", None)
    return users


class AdjustBody(BaseModel):
    currency: str
    amount: float
    reason: str = ""


@router.post("/users/{tid}/adjust")
async def adjust_balance(tid: int, body: AdjustBody):
    user = await db.bot_users.find_one({"telegram_id": tid})
    if not user:
        raise HTTPException(404, "Pengguna tidak ditemukan")
    field = "balance_usd" if body.currency == "USD" else "balance_idr"
    await db.bot_users.update_one({"telegram_id": tid}, {"$inc": {field: body.amount}})
    await db.balance_adjustments.insert_one({
        "_id": str(uuid.uuid4()), "user_tid": tid, "currency": body.currency,
        "amount": body.amount, "reason": body.reason, "created_at": now_iso(),
    })
    sign = "+" if body.amount >= 0 else ""
    await send_message(tid, f"ℹ️ <b>Penyesuaian Saldo oleh Admin</b>\n\nSaldo Anda disesuaikan: <b>{sign}{fmt_amount(abs(body.amount), body.currency) if body.amount >= 0 else '-' + fmt_amount(abs(body.amount), body.currency)}</b>" + (f"\nAlasan: {body.reason}" if body.reason else ""))
    return {"ok": True}


class FreezeBody(BaseModel):
    reason: str = ""


@router.post("/users/{tid}/freeze")
async def freeze_user(tid: int, body: FreezeBody):
    user = await db.bot_users.find_one({"telegram_id": tid})
    if not user:
        raise HTTPException(404, "Pengguna tidak ditemukan")
    await db.bot_users.update_one({"telegram_id": tid}, {"$set": {"frozen": True, "frozen_reason": body.reason}})
    await db.freeze_log.insert_one({"_id": str(uuid.uuid4()), "user_tid": tid, "action": "freeze", "reason": body.reason, "created_at": now_iso()})
    reason = f"\nAlasan: {body.reason}" if body.reason else ""
    await send_message(tid, f"🚫 <b>Akun Anda Dibekukan</b>{reason}\n\nSaldo terkunci dan Anda tidak dapat bertransaksi. Hubungi admin untuk info lebih lanjut.")
    return {"ok": True}


@router.post("/users/{tid}/unfreeze")
async def unfreeze_user(tid: int):
    user = await db.bot_users.find_one({"telegram_id": tid})
    if not user:
        raise HTTPException(404, "Pengguna tidak ditemukan")
    await db.bot_users.update_one({"telegram_id": tid}, {"$set": {"frozen": False, "frozen_reason": ""}})
    await db.freeze_log.insert_one({"_id": str(uuid.uuid4()), "user_tid": tid, "action": "unfreeze", "reason": "", "created_at": now_iso()})
    await send_message(tid, "✅ <b>Akun Anda Telah Dibuka Kembali</b>\n\nAnda bisa bertransaksi seperti biasa. Ketik /menu untuk mulai.")
    return {"ok": True}


# ============ SETTINGS ============

@router.get("/settings")
async def get_settings_api():
    s = await get_settings()
    s["current_rate"] = await get_rate()
    return s


class SettingsBody(BaseModel):
    crypto_addresses: dict
    bank_name: str = ""
    bank_account_number: str = ""
    bank_account_holder: str = ""
    min_deposit_usd: float = 15.0
    min_deposit_idr: float = 50000.0
    admin_telegram_id: str = ""
    rate_mode: str = "auto"
    manual_rate: float = 16000.0


@router.put("/settings")
async def update_settings(body: SettingsBody):
    await db.settings.update_one({"_id": "main"}, {"$set": body.model_dump()}, upsert=True)
    s = await get_settings()
    s["current_rate"] = await get_rate()
    return s
