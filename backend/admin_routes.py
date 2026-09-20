import io
import csv
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Response
from pydantic import BaseModel
from i18n import t
from db import db, get_settings
from auth import get_current_admin
from rates import get_rate
from services import credit_deposit, reject_deposit, cancel_deposit, fmt_amount, now_iso
from storage import put_object
from tgapi import download_telegram_file, send_message
from inventory import validate_items, add_items, available_count

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
    products = await db.products.find().sort("created_at", -1).to_list(500)
    for product in products:
        if product.get("delivery_type") == "inventory" or product.get("inventory_enabled"):
            product["inventory_stock"] = await available_count(product["_id"])
            product["stock"] = product["inventory_stock"]
    return products


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
    content: str = Form(""), active: bool = Form(True), stock: Optional[int] = Form(None),
    file: Optional[UploadFile] = File(None),
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
        "active": active, "stock": stock, "created_at": now_iso(),
    }
    await db.products.insert_one(prod)
    return prod


@router.put("/products/{pid}")
async def update_product(
    pid: str, name: str = Form(...), description: str = Form(""), price_usd: float = Form(...),
    price_idr: Optional[float] = Form(None), delivery_type: str = Form(...),
    content: str = Form(""), active: bool = Form(True), stock: Optional[int] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    prod = await db.products.find_one({"_id": pid})
    if not prod:
        raise HTTPException(404, "Produk tidak ditemukan")
    updates = {"name": name, "description": description, "price_usd": price_usd,
               "price_idr": price_idr, "delivery_type": delivery_type, "content": content,
               "active": active, "stock": stock}
    if file:
        updates["storage_path"], updates["original_filename"] = await _save_file(file)
    await db.products.update_one({"_id": pid}, {"$set": updates})
    return await db.products.find_one({"_id": pid})


def _parse_num(v, idr: bool):
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("Rp", "").replace("$", "").strip()
    if idr:
        s = s.replace(".", "").replace(",", "")
    else:
        s = s.replace(",", "")
    return float(s)


@router.post("/products/import")
async def import_products(currency: str = Form("IDR"), file: UploadFile = File(...)):
    data = await file.read()
    fn = (file.filename or "").lower()
    rows = []
    if fn.endswith(".csv") or fn.endswith(".txt"):
        text_data = data.decode("utf-8-sig", errors="ignore")
        first_line = text_data.splitlines()[0] if text_data.splitlines() else ""
        delim = "|" if "|" in first_line else ("," if "," in first_line else ";")
        rows = list(csv.reader(io.StringIO(text_data), delimiter=delim))
    else:
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
        except Exception:
            raise HTTPException(400, "File tidak valid. Gunakan .xlsx atau .csv")
        rows = [list(r) for r in wb.active.iter_rows(values_only=True)]
    rate = await get_rate()
    idr = currency == "IDR"
    docs, skipped = [], 0
    for row in rows:
        if not row or row[0] is None or not str(row[0]).strip():
            skipped += 1
            continue
        try:
            stock = int(_parse_num(row[1], False))
            price = _parse_num(row[2], idr)
        except (ValueError, TypeError, IndexError):
            skipped += 1
            continue
        desc = str(row[3]).strip() if len(row) > 3 and row[3] is not None else ""
        docs.append({
            "_id": str(uuid.uuid4()), "name": str(row[0]).strip(), "description": desc,
            "price_usd": round(price / rate, 2) if idr else price,
            "price_idr": price if idr else None,
            "delivery_type": "license", "content": "",
            "storage_path": None, "original_filename": None,
            "active": True, "stock": stock, "created_at": now_iso(),
        })
    if docs:
        await db.products.insert_many(docs)
    return {"imported": len(docs), "skipped": skipped}


class InventoryBody(BaseModel):
    content: str = ""


async def _inventory_lines(file: Optional[UploadFile], content: str):
    if file:
        data = await file.read()
        fn = (file.filename or "").lower()
        if fn.endswith(".xlsx"):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
                lines = []
                for row in wb.active.iter_rows(values_only=True):
                    value = row[0] if row else None
                    if value:
                        lines.append(str(value))
                return lines
            except Exception as exc:
                raise HTTPException(400, f"File XLSX tidak valid: {exc}")
        if fn.endswith(".txt") or fn.endswith(".csv"):
            return data.decode("utf-8-sig", errors="ignore").splitlines()
        raise HTTPException(400, "Inventory hanya menerima .txt, .csv atau .xlsx")
    return content.splitlines()


@router.post("/products/{pid}/inventory/validate")
async def validate_inventory(pid: str, body: InventoryBody):
    product = await db.products.find_one({"_id": pid})
    if not product:
        raise HTTPException(404, "Produk tidak ditemukan")
    check = await validate_items(body.content.splitlines())
    return {
        "valid_count": check["valid_count"],
        "duplicate_count": check["duplicate_count"],
        "preview": check["valid"][:20],
        "duplicates": check["duplicates"][:20],
    }


@router.post("/products/{pid}/inventory/import")
async def import_inventory(
    pid: str,
    content: str = Form(""),
    file: Optional[UploadFile] = File(None),
):
    product = await db.products.find_one({"_id": pid})
    if not product:
        raise HTTPException(404, "Produk tidak ditemukan")

    lines = await _inventory_lines(file, content)
    result = await add_items(pid, lines)
    result.pop("valid", None)
    result.pop("duplicates", None)
    result["stock"] = await available_count(pid)
    return result


@router.get("/products/{pid}/inventory")
async def inventory_list(pid: str, status: str = "available"):
    product = await db.products.find_one({"_id": pid})
    if not product:
        raise HTTPException(404, "Produk tidak ditemukan")
    q = {"product_id": pid}
    if status != "all":
        q["status"] = status
    cursor = db.inventory_items.find(q).sort("created_at", -1).limit(1000)
    from inventory import decrypt_items
    rows = []
    for item in await cursor.to_list(1000):
        row = {
            "_id": item["_id"],
            "status": item["status"],
            "order_id": item.get("order_id"),
            "user_tid": item.get("user_tid"),
            "created_at": item.get("created_at"),
            "sold_at": item.get("sold_at"),
        }
        if status != "sold":
            row["item"] = decrypt_items([item])[0]
        rows.append(row)
    return rows


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
    lang = user.get("lang") or "id"
    sign = "+" if body.amount >= 0 else "-"
    amt_str = f"{sign}{fmt_amount(abs(body.amount), body.currency)}"
    reason = t(lang, "reason_label", r=body.reason) if body.reason else ""
    await send_message(tid, t(lang, "adj_notice", amount=amt_str, reason=reason))
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
    lang = user.get("lang") or "id"
    reason = t(lang, "reason_label", r=body.reason) if body.reason else ""
    await send_message(tid, t(lang, "frozen_notice", reason=reason))
    return {"ok": True}


@router.post("/users/{tid}/unfreeze")
async def unfreeze_user(tid: int):
    user = await db.bot_users.find_one({"telegram_id": tid})
    if not user:
        raise HTTPException(404, "Pengguna tidak ditemukan")
    await db.bot_users.update_one({"telegram_id": tid}, {"$set": {"frozen": False, "frozen_reason": ""}})
    await db.freeze_log.insert_one({"_id": str(uuid.uuid4()), "user_tid": tid, "action": "unfreeze", "reason": "", "created_at": now_iso()})
    await send_message(tid, t(user.get("lang") or "id", "unfrozen_notice"))
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

@router.post("/products/import-test")
async def import_test():
    return {"ok": True, "message": "POST browser berhasil"}


@router.post("/products/upload-test")
async def upload_test(file: UploadFile = File(...)):
    data = await file.read()
    return {
        "ok": True,
        "filename": file.filename,
        "content_type": file.content_type,
        "size": len(data),
    }
