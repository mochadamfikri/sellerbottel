import io
import csv
import uuid
import asyncio
import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Response
from pydantic import BaseModel
from i18n import t, message_catalog, set_override, reset_override, STRINGS
from db import db, get_settings
from auth import get_current_admin
from rates import get_rate
from services import credit_deposit, reject_deposit, cancel_deposit, fmt_amount, now_iso
from storage import put_object
from tgapi import download_telegram_file, send_message, send_photo_bytes
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
        "content": "" if delivery_type == "inventory" else content,
        "storage_path": storage_path, "original_filename": original_filename,
        "active": active, "stock": None if delivery_type == "inventory" else stock,
        "inventory_enabled": delivery_type == "inventory",
        "created_at": now_iso(),
    }
    await db.products.insert_one(prod)
    if delivery_type == "inventory" and content.strip():
        await add_items(prod["_id"], content.splitlines())
        prod["stock"] = await available_count(prod["_id"])
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
    updates = {
        "name": name,
        "description": description,
        "price_usd": price_usd,
        "price_idr": price_idr,
        "delivery_type": delivery_type,
        "content": "" if delivery_type == "inventory" else content,
        "active": active,
        "stock": None if delivery_type == "inventory" else stock,
        "inventory_enabled": delivery_type == "inventory",
        "updated_at": now_iso(),
    }
    if file:
        updates["storage_path"], updates["original_filename"] = await _save_file(file)
    await db.products.update_one({"_id": pid}, {"$set": updates})
    if delivery_type == "inventory" and content.strip():
        await add_items(pid, content.splitlines())
    result = await db.products.find_one({"_id": pid})
    if result and delivery_type == "inventory":
        result["stock"] = await available_count(pid)
    return result


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
async def validate_inventory(
    pid: str,
    content: str = Form(""),
    file: Optional[UploadFile] = File(None),
):
    product = await db.products.find_one({"_id": pid})
    if not product:
        raise HTTPException(404, "Produk tidak ditemukan")

    lines = await _inventory_lines(file, content)
    check = await validate_items(lines)
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
    max_deposit_usd: float = 100000.0
    max_deposit_idr: float = 100000000.0
    join_gate_enabled: bool = True
    join_gate_fail_open: bool = True
    required_channels: list[dict] = []


@router.put("/settings")
async def update_settings(body: SettingsBody):
    await db.settings.update_one({"_id": "main"}, {"$set": body.model_dump()}, upsert=True)
    s = await get_settings()
    s["current_rate"] = await get_rate()
    return s


# ============ ORDERS ============

@router.get("/orders")
async def list_orders(status: str = "all", search: str = "", limit: int = 200):
    q = {}
    if status != "all":
        q["status"] = status
    if search.strip():
        pattern = re.escape(search.strip())
        q["$or"] = [
            {"invoice_id": {"$regex": pattern, "$options": "i"}},
            {"username": {"$regex": pattern, "$options": "i"}},
        ]
        if search.strip().isdigit():
            q["$or"].append({"user_tid": int(search.strip())})
    return await db.purchases.find(q).sort("created_at", -1).limit(max(1, min(limit, 500))).to_list(max(1, min(limit, 500)))


@router.get("/orders/{oid}")
async def get_order(oid: str):
    order = await db.purchases.find_one({"_id": oid})
    if not order:
        raise HTTPException(404, "Order tidak ditemukan")
    return order


@router.post("/orders/{oid}/refund")
async def refund_order(oid: str):
    order = await db.purchases.find_one({"_id": oid})
    if not order:
        raise HTTPException(404, "Order tidak ditemukan")
    if order.get("status") not in {"failed", "delivery_failed"}:
        raise HTTPException(400, "Hanya order gagal yang bisa direfund otomatis.")

    field = "balance_usd" if order["currency"] == "USD" else "balance_idr"
    refund_key = f"refund:{oid}"
    result = await db.bot_users.update_one(
        {"telegram_id": order["user_tid"], "refund_ids": {"$ne": refund_key}},
        {"$inc": {field: float(order["total"])}, "$addToSet": {"refund_ids": refund_key}},
    )
    if result.modified_count != 1:
        raise HTTPException(409, "Order sudah direfund.")

    await db.purchases.update_one(
        {"_id": oid},
        {"$set": {"status": "refunded", "refunded_at": now_iso(), "refund_reason": "Admin refund"}},
    )
    user = await db.bot_users.find_one({"telegram_id": order["user_tid"]}, {"lang": 1})
    if user:
        await send_message(
            order["user_tid"],
            t(user.get("lang") or "id", "order_refunded", amount=fmt_amount(order["total"], order["currency"]), invoice=order["invoice_id"]),
        )
    return {"ok": True}


# ============ BOT MESSAGES ============

ALLOWED_PLACEHOLDERS = {
    "user_name", "username", "product_name", "quantity", "price", "balance",
    "invoice_id", "date", "order_id", "total", "currency", "amount",
    "network", "coin", "reason", "name", "lang", "cur", "stock", "short",
    "type", "desc", "bank", "account", "holder", "min", "r",
}
ALLOWED_HTML_TAGS = {"b", "strong", "i", "em", "u", "s", "code", "pre", "br", "a", "blockquote"}


def validate_bot_message(text: str, lang: str, key: str):
    placeholders = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", text))
    default_text = STRINGS.get(lang, {}).get(key) or STRINGS["id"].get(key, "")
    allowed = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", default_text))
    unknown = sorted(placeholders - allowed)
    tags = re.findall(r"</?([a-zA-Z][a-zA-Z0-9]*)", text)
    invalid_tags = sorted(set(tag.lower() for tag in tags) - ALLOWED_HTML_TAGS)
    if unknown:
        raise HTTPException(400, f"Placeholder tidak diizinkan untuk {lang}/{key}: {', '.join(unknown)}")
    if invalid_tags:
        raise HTTPException(400, f"HTML tag tidak diizinkan: {', '.join(invalid_tags)}")


@router.get("/messages")
async def list_messages():
    docs = []
    for lang in ("id", "en"):
        for key in message_catalog():
            override = await db.bot_messages.find_one({"lang": lang, "key": key})
            docs.append({
                "lang": lang,
                "key": key,
                "default": STRINGS[lang].get(key, STRINGS["id"].get(key, key)),
                "text": override.get("text") if override else STRINGS[lang].get(key, STRINGS["id"].get(key, key)),
                "custom": bool(override),
            })
    return docs


class MessageBody(BaseModel):
    text: str


@router.put("/messages/{lang}/{key}")
async def update_message(lang: str, key: str, body: MessageBody):
    if lang not in ("id", "en") or key not in message_catalog():
        raise HTTPException(404, "Message key tidak ditemukan")
    validate_bot_message(body.text, lang, key)
    await db.bot_messages.update_one(
        {"lang": lang, "key": key},
        {"$set": {"text": body.text, "active": True, "updated_at": now_iso()}},
        upsert=True,
    )
    set_override(lang, key, body.text)
    return {"lang": lang, "key": key, "text": body.text, "custom": True}


class MessageTestBody(BaseModel):
    lang: str = "id"
    key: str
    text: str


@router.post("/messages/test")
async def test_message(body: MessageTestBody):
    if body.lang not in ("id", "en") or body.key not in message_catalog():
        raise HTTPException(404, "Message key tidak ditemukan")
    validate_bot_message(body.text, body.lang, body.key)
    settings = await get_settings()
    admin_id = str(settings.get("admin_telegram_id") or "")
    if not admin_id:
        raise HTTPException(400, "Telegram ID admin belum dikonfigurasi")

    sample = {
        "name": "Admin Preview",
        "user_name": "Admin Preview",
        "username": "@preview",
        "product_name": "Produk Contoh",
        "quantity": "2",
        "price": "Rp 20.000",
        "balance": "Rp 100.000",
        "invoice_id": "INV-20260921-0001",
        "order_id": "ORDER-PREVIEW",
        "total": "Rp 40.000",
        "currency": "IDR",
        "amount": "Rp 40.000",
        "payment_amount": "Rp 40.123",
        "network": "Polygon",
        "coin": "USDT",
        "reason": "Preview",
        "lang": "Indonesia",
        "cur": "IDR",
        "stock": "10",
        "short": "Rp 10.000",
        "type": "Inventory",
        "desc": "Preview",
        "bank": "BCA",
        "account": "123456",
        "holder": "Admin",
        "min": "Rp 50.000",
        "r": "Preview",
        "invoice": "INV-20260921-0001",
    }
    try:
        rendered = body.text.format(**sample)
    except KeyError as exc:
        raise HTTPException(400, f"Placeholder tidak bisa dirender: {exc}")
    result = await send_message(int(admin_id), rendered)
    if not result.get("ok"):
        raise HTTPException(502, result.get("description", "Telegram gagal mengirim test"))
    return {"ok": True}

@router.delete("/messages/{lang}/{key}")
async def reset_message(lang: str, key: str):
    await db.bot_messages.delete_one({"lang": lang, "key": key})
    reset_override(lang, key)
    return {"ok": True}


# ============ USERS / SEARCH ============

@router.get("/users/search")
async def search_users(
    search: str = "",
    status: str = "all",
    lang: str = "all",
    has_deposit: str = "all",
    has_order: str = "all",
    limit: int = 200,
):
    query = {}
    search = search.strip()
    if search:
        parts = []
        if search.isdigit():
            parts.append({"telegram_id": int(search)})
        parts.extend([
            {"username": {"$regex": re.escape(search), "$options": "i"}},
            {"first_name": {"$regex": re.escape(search), "$options": "i"}},
        ])
        query["$or"] = parts
    if status == "frozen":
        query["frozen"] = True
    elif status == "active":
        query["frozen"] = {"$ne": True}
    if lang in ("id", "en"):
        query["lang"] = lang

    users = await db.bot_users.find(query).sort("created_at", -1).limit(max(1, min(limit, 500))).to_list(max(1, min(limit, 500)))

    deposit_map = {}
    dep_cur = db.deposits.find({"status": "approved"}, {"user_tid": 1, "amount": 1, "credited_amount": 1})
    async for dep in dep_cur:
        deposit_map.setdefault(dep["user_tid"], 0.0)
        deposit_map[dep["user_tid"]] += float(dep.get("credited_amount") or dep.get("amount") or 0)

    order_map = {}
    order_cur = db.purchases.find({}, {"user_tid": 1, "total": 1})
    async for order in order_cur:
        row = order_map.setdefault(order["user_tid"], {"count": 0, "spending": 0.0})
        row["count"] += 1
        row["spending"] += float(order.get("total") or 0)

    result = []
    for user in users:
        tid = user["telegram_id"]
        dep_total = deposit_map.get(tid, 0.0)
        stats = order_map.get(tid, {"count": 0, "spending": 0.0})
        if has_deposit == "yes" and dep_total <= 0:
            continue
        if has_deposit == "no" and dep_total > 0:
            continue
        if has_order == "yes" and stats["count"] <= 0:
            continue
        if has_order == "no" and stats["count"] > 0:
            continue
        user.pop("state", None)
        user.pop("state_data", None)
        user["total_deposit"] = dep_total
        user["order_count"] = stats["count"]
        user["total_spending"] = stats["spending"]
        result.append(user)

    return result


# ============ DISCOUNTS ============

class DiscountBody(BaseModel):
    name: str
    product_ids: list[str] = []
    mode: str = "percent"
    value: float = 0.0
    min_qty: int = 1
    max_qty: Optional[int] = None
    active: bool = True
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    priority: int = 0


@router.get("/discounts")
async def list_discounts():
    return await db.discounts.find().sort([("priority", -1), ("created_at", -1)]).to_list(500)


@router.post("/discounts")
async def create_discount(body: DiscountBody):
    if body.mode not in {"percent", "fixed"}:
        raise HTTPException(400, "Mode discount harus percent atau fixed")
    if body.value <= 0:
        raise HTTPException(400, "Nilai discount harus lebih dari 0")
    if body.mode == "percent" and body.value > 100:
        raise HTTPException(400, "Percent discount maksimal 100%")
    if body.min_qty < 1:
        raise HTTPException(400, "min_qty minimal 1")
    doc = {
        "_id": str(uuid.uuid4()),
        **body.model_dump(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.discounts.insert_one(doc)
    return doc


@router.put("/discounts/{did}")
async def update_discount(did: str, body: DiscountBody):
    await db.discounts.update_one({"_id": did}, {"$set": {**body.model_dump(), "updated_at": now_iso()}})
    doc = await db.discounts.find_one({"_id": did})
    if not doc:
        raise HTTPException(404, "Discount tidak ditemukan")
    return doc


@router.patch("/discounts/{did}/toggle")
async def toggle_discount(did: str):
    doc = await db.discounts.find_one({"_id": did})
    if not doc:
        raise HTTPException(404, "Discount tidak ditemukan")
    active = not doc.get("active", True)
    await db.discounts.update_one({"_id": did}, {"$set": {"active": active, "updated_at": now_iso()}})
    return {"active": active}


@router.delete("/discounts/{did}")
async def delete_discount(did: str):
    await db.discounts.delete_one({"_id": did})
    return {"ok": True}


# ============ BROADCAST ============

async def _broadcast_worker(
    broadcast_id: str,
    query: dict,
    text_body: str,
    photo_bytes: bytes | None,
    filename: str | None,
    button_text: str | None,
    button_url: str | None,
):
    success = failed = blocked = 0
    cursor = db.bot_users.find(query, {"telegram_id": 1})
    async for user in cursor:
        tid = user["telegram_id"]
        kb = None
        if button_text and button_url:
            kb = {"inline_keyboard": [[{"text": button_text, "url": button_url}]]}
        try:
            if photo_bytes:
                result = await send_photo_bytes(
                    tid,
                    photo_bytes,
                    filename or "broadcast.jpg",
                    caption=text_body,
                    kb=kb,
                )
            else:
                result = await send_message(tid, text_body, kb=kb)
            if result.get("ok"):
                success += 1
            else:
                failed += 1
                if result.get("error_code") == 403:
                    blocked += 1
                    await db.bot_users.update_one({"telegram_id": tid}, {"$set": {"blocked": True}})
        except Exception:
            failed += 1
        if (success + failed) % 20 == 0:
            await db.broadcasts.update_one(
                {"_id": broadcast_id},
                {"$set": {"success": success, "failed": failed, "blocked": blocked}},
            )
        await asyncio.sleep(0.08)

    await db.broadcasts.update_one(
        {"_id": broadcast_id},
        {
            "$set": {
                "status": "completed",
                "success": success,
                "failed": failed,
                "blocked": blocked,
                "finished_at": now_iso(),
            }
        },
    )


@router.post("/broadcasts")
async def create_broadcast(
    text: str = Form(...),
    lang: str = Form("all"),
    search: str = Form(""),
    status: str = Form("all"),
    button_text: str = Form(""),
    button_url: str = Form(""),
    photo: Optional[UploadFile] = File(None),
):
    if not text.strip():
        raise HTTPException(400, "Pesan broadcast kosong")
    query = {}
    if lang in ("id", "en"):
        query["lang"] = lang
    if status == "active":
        query["frozen"] = {"$ne": True}
        query["blocked"] = {"$ne": True}
    elif status == "frozen":
        query["frozen"] = True
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"username": {"$regex": s, "$options": "i"}},
            {"first_name": {"$regex": s, "$options": "i"}},
        ]

    photo_bytes = None
    filename = None
    if photo:
        photo_bytes = await photo.read()
        filename = photo.filename

    doc = {
        "_id": str(uuid.uuid4()),
        "text": text,
        "lang": lang,
        "status": "running",
        "success": 0,
        "failed": 0,
        "blocked": 0,
        "created_at": now_iso(),
        "finished_at": None,
    }
    await db.broadcasts.insert_one(doc)
    asyncio.create_task(_broadcast_worker(
        doc["_id"], query, text, photo_bytes, filename, button_text or None, button_url or None
    ))
    return doc


@router.get("/broadcasts")
async def list_broadcasts():
    return await db.broadcasts.find().sort("created_at", -1).to_list(100)

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
