import io
import csv
import logging
import uuid
import asyncio
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Response
from pydantic import BaseModel
from i18n import t, message_catalog, set_override, reset_override, STRINGS
from db import db, get_settings
from auth import get_current_admin, verify_password
from rates import get_rate
from pricing import price_for_product
from services import credit_deposit, reject_deposit, cancel_deposit, fmt_amount, now_iso, notify_product_created
from tgapi import download_telegram_file, send_message, send_photo_bytes, tg
from html import escape
from inventory import (
    validate_records,
    add_records,
    available_count,
    decrypt_items,
    encryption_status,
    InventoryError,
)
from reporting import router as reports_router

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", dependencies=[Depends(get_current_admin)])


# ============ STATS ============

@router.get("/stats")
async def stats():
    s = await get_settings()
    cutoff = s.get("stats_reset_at")

    def after_cutoff(match):
        if cutoff:
            return {**match, "created_at": {"$gte": cutoff}}
        return match

    async def sum_by(coll, match, field, currency):
        pipeline = [
            {"$match": {**after_cutoff(match), "currency": currency}},
            {"$group": {"_id": None, "t": {"$sum": "$" + field}}},
        ]
        res = await coll.aggregate(pipeline).to_list(1)
        return res[0]["t"] if res else 0

    dep_usd = await sum_by(db.deposits, {"status": "approved"}, "credited_amount", "USD")
    dep_idr = await sum_by(db.deposits, {"status": "approved"}, "credited_amount", "IDR")
    sales_usd = await sum_by(db.purchases, {}, "total", "USD")
    sales_idr = await sum_by(db.purchases, {}, "total", "IDR")
    circ = await db.bot_users.aggregate([
        {"$group": {"_id": None, "usd": {"$sum": "$balance_usd"}, "idr": {"$sum": "$balance_idr"}}}
    ]).to_list(1)
    pending = await db.deposits.count_documents(after_cutoff({"status": "pending"}))
    recent = await db.deposits.find(after_cutoff({})).sort("created_at", -1).to_list(8)
    rate = await get_rate()
    return {
        "total_deposit_usd": dep_usd,
        "total_deposit_idr": dep_idr,
        "total_sales_usd": sales_usd,
        "total_sales_idr": sales_idr,
        "circulating_usd": circ[0]["usd"] if circ else 0,
        "circulating_idr": circ[0]["idr"] if circ else 0,
        "pending_deposits": pending,
        "recent_deposits": recent,
        "rate": rate,
        "rate_mode": s.get("rate_mode"),
        "users_count": await db.bot_users.count_documents({}),
        "products_count": await db.products.count_documents({}),
        "stats_reset_at": cutoff,
    }


class ResetStatsBody(BaseModel):
    password: str


@router.post("/stats/reset")
async def reset_stats(body: ResetStatsBody, admin: dict = Depends(get_current_admin)):
    if not body.password:
        raise HTTPException(400, "Password wajib diisi.")
    stored = await db.admins.find_one({"_id": admin["_id"]}, {"password_hash": 1})
    if not stored or not verify_password(body.password, stored.get("password_hash", "")):
        raise HTTPException(401, "Password admin salah.")

    reset_at = now_iso()
    await db.settings.update_one(
        {"_id": "main"},
        {"$set": {"stats_reset_at": reset_at}},
        upsert=True,
    )
    return {"ok": True, "stats_reset_at": reset_at}


# ============ PRODUCTS ============

def _normalized_product_kind(product: dict) -> str:
    kind = product.get("product_kind")
    if kind in {"digital", "service"}:
        return kind
    return "digital" if (
        product.get("delivery_type") == "inventory"
        or product.get("inventory_enabled")
    ) else "service"


def _is_inventory_product(product: dict) -> bool:
    return _normalized_product_kind(product) == "digital"


def _effective_admin_stock(product: dict, inventory_stock: int) -> int | None:
    if not _is_inventory_product(product):
        return None
    mode = product.get("stock_mode", "auto")
    if mode == "manual" and product.get("manual_stock") is not None:
        return max(0, int(product.get("manual_stock") or 0))
    return inventory_stock


@router.get("/products")
async def list_products():
    products = await db.products.find().sort("created_at", -1).to_list(500)
    for product in products:
        kind = _normalized_product_kind(product)
        product["product_kind"] = kind
        if kind == "digital":
            actual = await available_count(product["_id"])
            product["inventory_stock"] = actual
            product["stock_mode"] = product.get("stock_mode", "auto")
            product["stock"] = _effective_admin_stock(product, actual)
            product["inventory_enabled"] = True
        else:
            product["inventory_stock"] = 0
            product["stock"] = None
            product["inventory_enabled"] = False
    return products


def _validate_product_kind(value: str) -> str:
    value = (value or "digital").strip().lower()
    if value not in {"digital", "service"}:
        raise HTTPException(400, "Jenis product tidak valid.")
    return value


@router.post("/products")
async def create_product(
    name: str = Form(...),
    description: str = Form(""),
    price_usd: float = Form(...),
    price_idr: Optional[float] = Form(None),
    delivery_type: str = Form("link"),
    content: str = Form(""),
    active: bool = Form(True),
    stock: Optional[int] = Form(None),
    product_kind: str = Form("digital"),
    stock_mode: str = Form("auto"),
    inventory_mode: str = Form("table"),
    service_wait_minutes: Optional[int] = Form(None),
    service_message_template: str = Form(""),
    file: Optional[UploadFile] = File(None),
):
    product_kind = _validate_product_kind(product_kind)
    if stock_mode not in {"auto", "manual"}:
        raise HTTPException(400, "Mode stok tidak valid.")
    if inventory_mode not in {"table", "telegram_session"}:
        raise HTTPException(400, "Mode inventory tidak valid.")

    storage_path, original_filename = None, None
    if product_kind == "service":
        wait_minutes = int(service_wait_minutes or 5)
        if wait_minutes not in {1, 5, 10, 25, 60}:
            raise HTTPException(400, "Waktu tunggu jasa harus 1, 5, 10, 25, atau 60 menit.")
        manual_stock = None
        stored_stock = None
        inventory_enabled = False
        stored_delivery = "service"
        stored_content = ""
        message_template = (service_message_template or "Jasa {product_name} sedang dalam antrean, harap tunggu {wait_minutes} untuk dapat menghubungi admin.").strip()
        if not message_template:
            raise HTTPException(400, "Pesan antrean jasa wajib diisi.")
    else:
        manual_stock = None if stock_mode == "auto" else max(0, int(stock or 0))
        stored_stock = manual_stock
        inventory_enabled = True
        stored_delivery = "inventory"
        stored_content = ""
        wait_minutes = None
        message_template = ""

    prod = {
        "_id": str(uuid.uuid4()),
        "name": name.strip(),
        "description": description,
        "price_usd": price_usd,
        "price_idr": price_idr,
        "product_kind": product_kind,
        "delivery_type": stored_delivery,
        "content": stored_content,
        "storage_path": storage_path,
        "original_filename": original_filename,
        "service_wait_minutes": wait_minutes,
        "service_message_template": message_template,
        "active": active,
        "stock": stored_stock,
        "stock_mode": stock_mode if product_kind == "digital" else "unlimited",
        "manual_stock": manual_stock,
        "inventory_enabled": inventory_enabled,
        "inventory_schema": [],
        "inventory_mode": inventory_mode if product_kind == "digital" else "table",
        "created_at": now_iso(),
    }
    await db.products.insert_one(prod)
    try:
        await notify_product_created(prod)
    except Exception:
        logger.exception("Auto broadcast product baru gagal")
    return prod


@router.put("/products/{pid}")
async def update_product(
    pid: str,
    name: str = Form(...),
    description: str = Form(""),
    price_usd: float = Form(...),
    price_idr: Optional[float] = Form(None),
    delivery_type: str = Form("link"),
    content: str = Form(""),
    active: bool = Form(True),
    stock: Optional[int] = Form(None),
    product_kind: str = Form("digital"),
    stock_mode: str = Form("auto"),
    service_wait_minutes: Optional[int] = Form(None),
    service_message_template: str = Form(""),
    file: Optional[UploadFile] = File(None),
    files: Optional[list[UploadFile]] = File(None),
):
    product = await db.products.find_one({"_id": pid})
    if not product:
        raise HTTPException(404, "Produk tidak ditemukan")

    product_kind = _validate_product_kind(product_kind)
    if stock_mode not in {"auto", "manual"}:
        raise HTTPException(400, "Mode stok tidak valid.")

    updates = {
        "name": name.strip(),
        "description": description,
        "price_usd": price_usd,
        "price_idr": price_idr,
        "product_kind": product_kind,
        "active": active,
        "updated_at": now_iso(),
    }

    if product_kind == "service":
        wait_minutes = int(service_wait_minutes or 5)
        if wait_minutes not in {1, 5, 10, 25, 60}:
            raise HTTPException(400, "Waktu tunggu jasa harus 1, 5, 10, 25, atau 60 menit.")
        message_template = (service_message_template or "Jasa {product_name} sedang dalam antrean, harap tunggu {wait_minutes} untuk dapat menghubungi admin.").strip()
        if not message_template:
            raise HTTPException(400, "Pesan antrean jasa wajib diisi.")
        updates.update({
            "delivery_type": "service",
            "content": "",
            "service_wait_minutes": wait_minutes,
            "service_message_template": message_template,
            "storage_path": None,
            "original_filename": None,
            "stock": None,
            "stock_mode": "unlimited",
            "manual_stock": None,
            "inventory_enabled": False,
            "inventory_mode": "table",
        })
    else:
        manual_stock = None if stock_mode == "auto" else max(0, int(stock or 0))
        updates.update({
            "delivery_type": "inventory",
            "content": "",
            "service_wait_minutes": None,
            "service_message_template": "",
            "storage_path": None,
            "original_filename": None,
            "stock": manual_stock,
            "stock_mode": stock_mode,
            "manual_stock": manual_stock,
            "inventory_enabled": True,
            "inventory_mode": inventory_mode,
        })

    await db.products.update_one({"_id": pid}, {"$set": updates})
    result = await db.products.find_one({"_id": pid})
    if result and _is_inventory_product(result):
        result["inventory_stock"] = await available_count(pid)
        result["stock"] = _effective_admin_stock(result, result["inventory_stock"])
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


@router.get("/products/import-template")
async def product_import_template():
    """Generate the multi-sheet product + inventory workbook."""
    try:
        from openpyxl import Workbook
        from openpyxl.worksheet.datavalidation import DataValidation

        wb = Workbook()
        ws = wb.active
        ws.title = "product"
        headers = [
            "Nama Product",
            "Deskripsi Singkat / Poin",
            "Harga USD",
            "Harga IDR",
            "Jenis Product",
            "Waktu Tunggu (menit)",
            "Pesan Jasa",
        ]
        ws.append(headers)
        for cell in ws[1]:
            cell.font = cell.font.copy(bold=True)

        type_validation = DataValidation(
            type="list",
            formula1='"A. Produk Digital / sudah ada datanya,B. Produk Jasa"',
            allow_blank=False,
        )
        wait_validation = DataValidation(
            type="list",
            formula1='"1,5,10,25,60"',
            allow_blank=True,
        )
        ws.add_data_validation(type_validation)
        ws.add_data_validation(wait_validation)
        type_validation.add("E2:E1000")
        wait_validation.add("F2:F1000")
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = "A1:G1000"

        widths = [30, 55, 16, 18, 34, 24, 65]
        for i, width in enumerate(widths, 1):
            ws.column_dimensions[chr(64 + i)].width = width

        info = wb.create_sheet("Petunjuk")
        info_rows = [
            ["Bagian", "Aturan"],
            ["Sheet product", "Wajib. Berisi daftar product."],
            ["Nama Product", "Wajib. Untuk product digital, nama ini juga menjadi nama sheet inventory."],
            ["Deskripsi Singkat / Poin", "Opsional. Sistem otomatis menyusun deskripsi lengkap berdasarkan nama + poin ini."],
            ["Harga USD / Harga IDR", "Minimal salah satu wajib diisi."],
            ["Jenis Product", "A = digital/inventory, B = jasa tanpa inventory."],
            ["Sheet inventory", "Untuk product A, buat sheet dengan nama PERSIS sama seperti Nama Product."],
            ["Header inventory", "Baris pertama sheet inventory menjadi schema product tersebut. Tidak ada schema global."],
            ["Isi inventory", "Mulai baris kedua. Setiap baris adalah satu item inventory."],
            ["Contoh", "product: Gmail Aged -> sheet: Gmail Aged -> header: email | password | recovery"],
        ]
        for row in info_rows:
            info.append(row)
        info.freeze_panes = "A2"
        info.column_dimensions["A"].width = 32
        info.column_dimensions["B"].width = 100

        sample = wb.create_sheet("Contoh Digital")
        sample.append(["email", "password", "recovery", "2fa"])
        sample.append(["example@gmail.com", "password123", "recovery@example.com", "enabled"])
        sample.append(["akun2@gmail.com", "password456", "recovery2@example.com", "enabled"])
        for cell in sample[1]:
            cell.font = cell.font.copy(bold=True)

        output = io.BytesIO()
        wb.save(output)
        return Response(
            content=output.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="template-bulk-product.xlsx"'},
        )
    except Exception as exc:
        logger.exception("Gagal membuat template bulk product")
        raise HTTPException(500, f"Gagal membuat template Excel: {type(exc).__name__}")


@router.post("/products/import")
async def import_products(file: UploadFile = File(...)):
    """Import products from the new multi-sheet workbook.

    Sheet product contains product metadata. Each digital product gets its
    own inventory sheet named exactly like the product. The first row of that
    sheet is the product-specific inventory schema.
    """
    from bulk_product_import import import_workbook

    data = await file.read()
    filename = (file.filename or "").lower()
    if not filename.endswith(".xlsx"):
        raise HTTPException(400, "Gunakan file .xlsx dengan format multi-sheet product.")

    try:
        return await import_workbook(data)
    except HTTPException:
        raise
    except InventoryError:
        raise
    except Exception as exc:
        logger.exception("Bulk product import gagal")
        raise HTTPException(500, f"Bulk product import gagal ({type(exc).__name__}). Lihat log backend.")


def _stringify_cell(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _detect_delimiter(line: str):
    candidates = ["|", ",", ";", "\t"]
    return max(candidates, key=lambda d: line.count("\t" if d == "\\t" else d))


def _xlsx_col_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return max(0, index - 1)


def _xlsx_text(element):
    return "".join(element.itertext()) if element is not None else ""


def _read_xlsx_rows_fallback(data: bytes):
    """Read the first worksheet without parsing styles.

    Some XLSX files produced by spreadsheet/mobile apps contain a broken or
    missing styles.xml. openpyxl rejects those files even when the worksheet
    data itself is readable. XLSX is a ZIP of XML parts, so we can safely
    recover the cell values without loading the style information.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if "xl/workbook.xml" not in names:
                raise ValueError("workbook.xml tidak ditemukan")

            main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
            rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            package_rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"

            workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
            rels = {}
            if "xl/_rels/workbook.xml.rels" in names:
                rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
                for rel in rels_root:
                    rel_id = rel.attrib.get("Id")
                    target = rel.attrib.get("Target")
                    if rel_id and target:
                        rels[rel_id] = target

            sheets = workbook_root.find(f"{{{main_ns}}}sheets")
            if sheets is None:
                raise ValueError("Tidak ada worksheet di workbook")

            first_sheet = next(iter(sheets), None)
            if first_sheet is None:
                raise ValueError("Worksheet kosong")

            rel_id = first_sheet.attrib.get(f"{{{rel_ns}}}id")
            target = rels.get(rel_id) if rel_id else None
            if target:
                target = target.lstrip("/")
                if not target.startswith("xl/"):
                    target = f"xl/{target}"
            else:
                target = "xl/worksheets/sheet1.xml"

            if target not in names:
                raise ValueError(f"Worksheet XML tidak ditemukan: {target}")

            shared_strings = []
            if "xl/sharedStrings.xml" in names:
                shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                for item in shared_root.findall(f"{{{main_ns}}}si"):
                    shared_strings.append(_xlsx_text(item))

            sheet_root = ET.fromstring(archive.read(target))
            sheet_data = sheet_root.find(f"{{{main_ns}}}sheetData")
            if sheet_data is None:
                return []

            parsed_rows = []
            for row_node in sheet_data.findall(f"{{{main_ns}}}row"):
                cells = {}
                max_col = -1
                for cell in row_node.findall(f"{{{main_ns}}}c"):
                    ref = cell.attrib.get("r", "")
                    col = _xlsx_col_index(ref)
                    if col < 0:
                        continue

                    cell_type = cell.attrib.get("t")
                    value = ""
                    if cell_type == "inlineStr":
                        inline = cell.find(f"{{{main_ns}}}is")
                        value = _xlsx_text(inline)
                    else:
                        value_node = cell.find(f"{{{main_ns}}}v")
                        raw = _xlsx_text(value_node)
                        if cell_type == "s" and raw:
                            try:
                                value = shared_strings[int(raw)]
                            except (ValueError, IndexError):
                                value = raw
                        elif cell_type == "b":
                            value = "TRUE" if raw == "1" else "FALSE"
                        else:
                            value = raw

                    cells[col] = value
                    max_col = max(max_col, col)

                parsed_rows.append([cells.get(i, "") for i in range(max_col + 1)])

            return parsed_rows
    except zipfile.BadZipFile as exc:
        raise ValueError("File bukan XLSX/ZIP yang valid") from exc
    except ET.ParseError as exc:
        raise ValueError("XML workbook/worksheet rusak") from exc


async def _parse_inventory_input(file: Optional[UploadFile], content: str, product: dict, files: Optional[list[UploadFile]] = None):
    schema = [str(x).strip() for x in (product.get("inventory_schema") or []) if str(x).strip()]
    records = []
    source_name = (file.filename or "").lower() if file else ""

    # Telegram Session mode supports selecting multiple real .session files.
    if files:
        if product.get("inventory_mode") != "telegram_session":
            raise HTTPException(400, "Upload banyak file .session hanya tersedia untuk product Telegram Session.")
        import base64
        schema_from_file = ["Session File"]
        for upload in files:
            name = (upload.filename or "").strip()
            if not name.lower().endswith(".session"):
                raise HTTPException(400, f"File {name or '(tanpa nama)'} bukan file .session.")
            data = await upload.read()
            if not data:
                continue
            if len(data) > 10 * 1024 * 1024:
                raise HTTPException(400, f"File {name} melebihi batas 10 MB.")
            records.append({
                "Session File": name,
                "__file_name": name,
                "__file_data_b64": base64.b64encode(data).decode("ascii"),
            })
        if not records:
            raise HTTPException(400, "Tidak ada file .session yang berisi data.")
        return schema_from_file, records

    if file:
        data = await file.read()
        if source_name.endswith(".xlsx"):
            rows = None
            openpyxl_error = None
            try:
                import openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
                rows = [list(row) for row in wb.active.iter_rows(values_only=True)]
            except Exception as exc:
                openpyxl_error = exc

            # Fallback for valid XLSX files whose styles.xml is malformed or
            # unsupported by openpyxl. This reads worksheet values directly.
            if rows is None:
                try:
                    rows = _read_xlsx_rows_fallback(data)
                except Exception as fallback_exc:
                    raise HTTPException(
                        400,
                        f"File XLSX inventory tidak valid: {fallback_exc}. "
                        f"openpyxl: {openpyxl_error}",
                    )

            rows = [row for row in rows if any(value not in (None, "") for value in row)]
            if not rows:
                raise HTTPException(400, "File inventory kosong.")

            # Spreadsheet apps often keep formatted/empty columns to the right
            # of the real table (e.g. A1 has "Session File" while B/C are
            # visually blank). Those trailing empty cells are not schema fields.
            first_row = list(rows[0])
            while first_row and first_row[-1] in (None, ""):
                first_row.pop()
            headers = [_stringify_cell(v) for v in first_row]
            if not headers or any(not header for header in headers) or len(set(headers)) != len(headers):
                raise HTTPException(400, "Header inventory tidak boleh kosong atau duplikat.")
            schema_from_file = headers
            for row in rows[1:]:
                values = list(row[:len(headers)])
                if len(values) < len(headers):
                    values.extend([""] * (len(headers) - len(values)))
                record = {headers[i]: _stringify_cell(values[i]) for i in range(len(headers))}
                if any(record.values()):
                    records.append(record)
        elif source_name.endswith(".csv"):
            text_data = data.decode("utf-8-sig", errors="ignore")
            rows = list(csv.reader(io.StringIO(text_data), delimiter=_detect_delimiter(text_data.splitlines()[0] if text_data.splitlines() else "")))
            rows = [row for row in rows if any(str(v or "").strip() for v in row)]
            if not rows:
                raise HTTPException(400, "File inventory kosong.")
            headers = [str(v or "").strip() for v in rows[0]]
            if not all(headers) or len(set(headers)) != len(headers):
                raise HTTPException(400, "Header inventory tidak boleh kosong atau duplikat.")
            schema_from_file = headers
            for row in rows[1:]:
                record = {headers[i]: str(row[i] if i < len(row) else "").strip() for i in range(len(headers))}
                if any(record.values()):
                    records.append(record)
        elif source_name.endswith(".txt"):
            text_data = data.decode("utf-8-sig", errors="ignore")
            lines = [line.strip() for line in text_data.splitlines() if line.strip()]
            schema_from_file = schema or ["value"]
            for line in lines:
                parts = [part.strip() for part in line.split("|")]
                if len(schema_from_file) == 1:
                    records.append({schema_from_file[0]: line})
                elif len(parts) == len(schema_from_file):
                    records.append({schema_from_file[i]: parts[i] for i in range(len(schema_from_file))})
                else:
                    raise HTTPException(400, f"Format TXT tidak cocok dengan schema inventory ({len(schema_from_file)} kolom).")
        else:
            if product.get("inventory_mode") != "telegram_session":
                raise HTTPException(
                    400,
                    "Produk ini memakai inventory tabel. Gunakan XLSX/CSV/TXT sesuai schema product. "
                    "File .session hanya tersedia untuk product dengan mode Telegram Session.",
                )
            if not source_name.endswith(".session"):
                raise HTTPException(400, "Mode Telegram Session hanya menerima file .session.")
            import base64
            if not data:
                raise HTTPException(400, "File inventory kosong.")
            if len(data) > 10 * 1024 * 1024:
                raise HTTPException(400, "Ukuran satu file inventory maksimal 10 MB.")
            filename = (file.filename or "inventory.session").strip() or "inventory.session"
            schema_from_file = ["Session File"]
            records.append({
                "Session File": filename,
                "__file_name": filename,
                "__file_data_b64": base64.b64encode(data).decode("ascii"),
            })
    else:
        lines = [line.strip() for line in (content or "").splitlines() if line.strip()]
        schema_from_file = schema or ["value"]
        for line in lines:
            parts = [part.strip() for part in line.split("|")]
            if len(schema_from_file) == 1:
                records.append({schema_from_file[0]: line})
            elif len(parts) == len(schema_from_file):
                records.append({schema_from_file[i]: parts[i] for i in range(len(schema_from_file))})
            else:
                raise HTTPException(400, f"Input manual tidak cocok dengan schema inventory ({len(schema_from_file)} kolom).")

    # Product-specific schema: the uploaded file/header is authoritative.
    # We intentionally do NOT compare against an older schema here. This lets
    # every product use its own fields and lets an updated XLSX header redefine
    # that product's schema.
    return schema_from_file, records


@router.post("/products/{pid}/inventory/validate")
async def validate_inventory(
    pid: str,
    content: str = Form(""),
    file: Optional[UploadFile] = File(None),
    files: Optional[list[UploadFile]] = File(None),
):
    product = await db.products.find_one({"_id": pid})
    if not product:
        raise HTTPException(404, "Produk tidak ditemukan")
    if not _is_inventory_product(product):
        raise HTTPException(400, "Produk jasa tidak memiliki inventory.")

    schema, records = await _parse_inventory_input(file, content, product, files)
    try:
        check = await validate_records(pid, records, schema)
        # Validation is the point where a product's first inventory file
        # establishes its schema. Existing schemas are never overwritten.
        if not product.get("inventory_schema") and schema:
            await db.products.update_one(
                {"_id": pid},
                {"$set": {"inventory_schema": schema, "updated_at": now_iso()}},
            )
    except InventoryError:
        raise
    except Exception as exc:
        logger.exception("Validasi inventory gagal untuk produk %s", pid)
        raise InventoryError(f"Validasi inventory gagal ({type(exc).__name__}). Lihat log backend.") from exc
    return {
        "schema": schema,
        "valid_count": check["valid_count"],
        "duplicate_count": check["duplicate_count"],
        "preview": check["valid"][:5],
        "duplicates": check["duplicates"][:5],
    }


@router.post("/products/{pid}/inventory/import")
async def import_inventory(
    pid: str,
    content: str = Form(""),
    file: Optional[UploadFile] = File(None),
    files: Optional[list[UploadFile]] = File(None),
):
    product = await db.products.find_one({"_id": pid})
    if not product:
        raise HTTPException(404, "Produk tidak ditemukan")
    if not _is_inventory_product(product):
        raise HTTPException(400, "Produk jasa tidak memiliki inventory.")

    schema, records = await _parse_inventory_input(file, content, product)
    try:
        result = await add_records(pid, records, schema)
    except InventoryError:
        raise
    except Exception as exc:
        logger.exception("Import inventory gagal untuk produk %s", pid)
        raise InventoryError(f"Import inventory gagal ({type(exc).__name__}). Lihat log backend.") from exc
    result.pop("valid", None)
    result.pop("duplicates", None)
    result["stock"] = await available_count(pid)
    result["schema"] = schema
    return result


@router.get("/inventory/status")
async def inventory_status():
    """Diagnosa konfigurasi enkripsi inventory (tanpa membocorkan key)."""
    return {"encryption": await encryption_status()}


class InventoryManualBody(BaseModel):
    data: dict[str, str]


@router.post("/products/{pid}/inventory/manual")
async def add_inventory_manual(pid: str, body: InventoryManualBody):
    product = await db.products.find_one({"_id": pid})
    if not product:
        raise HTTPException(404, "Produk tidak ditemukan")
    if not _is_inventory_product(product):
        raise HTTPException(400, "Produk jasa tidak memiliki inventory.")
    schema = [str(x).strip() for x in (product.get("inventory_schema") or []) if str(x).strip()]
    if not schema:
        raise HTTPException(400, "Schema inventory belum tersedia. Upload file XLSX/CSV pertama kali untuk menentukan header.")
    try:
        result = await add_records(pid, [body.data], schema)
    except InventoryError:
        raise
    except Exception as exc:
        logger.exception("Input manual inventory gagal untuk produk %s", pid)
        raise InventoryError(f"Simpan data inventory gagal ({type(exc).__name__}). Lihat log backend.") from exc
    if result["created"] != 1:
        raise HTTPException(409, "Data inventory sudah ada atau tidak valid.")
    return {
        "ok": True,
        "schema": schema,
        "stock": await available_count(pid),
    }


@router.get("/products/{pid}/inventory")
async def inventory_list(pid: str, status: str = "available"):
    product = await db.products.find_one({"_id": pid})
    if not product:
        raise HTTPException(404, "Produk tidak ditemukan")
    if not _is_inventory_product(product):
        raise HTTPException(400, "Produk jasa tidak memiliki inventory.")

    q = {"product_id": pid}
    if status != "all":
        q["status"] = status
    cursor = db.inventory_items.find(q).sort("created_at", -1).limit(1000)
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
        if status != "sold" and item.get("secret"):
            decrypted = decrypt_items([item])[0]
            if decrypted.get("__file_data_b64"):
                row["item"] = {
                    "file": decrypted.get("__file_name") or decrypted.get("file") or "inventory.bin",
                    "is_file": True,
                }
            else:
                row["item"] = decrypted
        rows.append(row)
    return {
        "schema": product.get("inventory_schema") or ["value"],
        "items": rows,
        "available": await db.inventory_items.count_documents({"product_id": pid, "status": "available"}),
        "reserved": await db.inventory_items.count_documents({"product_id": pid, "status": "reserved"}),
        "sold": await db.inventory_items.count_documents({"product_id": pid, "status": "sold"}),
    }


@router.delete("/products/{pid}/inventory/{item_id}")
async def delete_inventory_item(pid: str, item_id: str):
    product = await db.products.find_one({"_id": pid})
    if not product:
        raise HTTPException(404, "Produk tidak ditemukan")
    result = await db.inventory_items.delete_one({
        "_id": item_id,
        "product_id": pid,
        "status": "available",
    })
    if result.deleted_count != 1:
        raise HTTPException(400, "Item hanya bisa dihapus saat masih tersedia.")
    return {"ok": True, "stock": await available_count(pid)}


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


@router.get("/users/search")
async def search_users(
    search: str = "", status: str = "all", lang: str = "all",
    has_deposit: str = "all", has_order: str = "all",
    min_deposit: float | None = None, max_deposit: float | None = None,
    min_purchase: float | None = None, max_purchase: float | None = None,
    min_balance: float | None = None, max_balance: float | None = None,
    product_id: str = "", registered_from: str = "", registered_to: str = "",
):
    if status not in {"all", "active", "frozen"} or lang not in {"all", "id", "en"}:
        raise HTTPException(400, "Filter pengguna tidak valid.")
    if has_deposit not in {"all", "yes", "no"} or has_order not in {"all", "yes", "no"}:
        raise HTTPException(400, "Filter aktivitas tidak valid.")
    user_q = {}
    if status == "active": user_q["frozen"] = {"$ne": True}
    elif status == "frozen": user_q["frozen"] = True
    if lang != "all": user_q["lang"] = lang
    if registered_from or registered_to:
        user_q["created_at"] = {}
        if registered_from: user_q["created_at"]["$gte"] = registered_from
        if registered_to: user_q["created_at"]["$lt"] = registered_to
    if search.strip():
        s = re.escape(search.strip())
        clauses = [{"username": {"$regex": s, "$options": "i"}}, {"first_name": {"$regex": s, "$options": "i"}}]
        if search.strip().isdigit(): clauses.append({"telegram_id": int(search.strip())})
        user_q["$or"] = clauses
    users = await db.bot_users.find(user_q).sort("created_at", -1).limit(2000).to_list(2000)
    tids = [u["telegram_id"] for u in users]
    if not tids: return []
    deposits = await db.deposits.find({"user_tid": {"$in": tids}, "status": "approved"}, {"user_tid": 1, "credited_amount": 1, "amount": 1}).to_list(10000)
    orders = await db.purchases.find({"user_tid": {"$in": tids}}, {"user_tid": 1, "total": 1, "status": 1, "items": 1}).to_list(20000)
    dep_by_user = {}
    for d in deposits: dep_by_user[d["user_tid"]] = dep_by_user.get(d["user_tid"], 0.0) + float(d.get("credited_amount") or d.get("amount") or 0)
    order_by_user, products_by_user = {}, {}
    for o in orders:
        tid = o["user_tid"]; order_by_user.setdefault(tid, {"count": 0, "spending": 0.0})
        if o.get("status") not in {"pending", "failed", "delivery_failed"}:
            order_by_user[tid]["count"] += 1; order_by_user[tid]["spending"] += float(o.get("total") or 0)
        for item in o.get("items", []):
            if item.get("product_id"): products_by_user.setdefault(tid, set()).add(item["product_id"])
    result = []
    for u in users:
        tid = u["telegram_id"]; dep_total = dep_by_user.get(tid, 0.0); stats = order_by_user.get(tid, {"count": 0, "spending": 0.0})
        balance = max(float(u.get("balance_usd") or 0), float(u.get("balance_idr") or 0))
        if has_deposit == "yes" and dep_total <= 0: continue
        if has_deposit == "no" and dep_total > 0: continue
        if has_order == "yes" and stats["count"] <= 0: continue
        if has_order == "no" and stats["count"] > 0: continue
        if min_deposit is not None and dep_total < min_deposit: continue
        if max_deposit is not None and dep_total > max_deposit: continue
        if min_purchase is not None and stats["spending"] < min_purchase: continue
        if max_purchase is not None and stats["spending"] > max_purchase: continue
        if min_balance is not None and balance < min_balance: continue
        if max_balance is not None and balance > max_balance: continue
        if product_id and product_id not in products_by_user.get(tid, set()): continue
        u["total_deposit"] = dep_total; u["order_count"] = stats["count"]; u["total_spending"] = stats["spending"]
        u["telegram_account_connected"] = bool(await db.tg_accounts.find_one({
            "tg_user_id": tid,
            "status": "active",
            "session_encrypted": {"$type": "string"},
        }))
        u["purchased_product_ids"] = list(products_by_user.get(tid, set()))
        u.pop("state", None); u.pop("state_data", None)
        u.pop("deposit_credit_ids", None); u.pop("checkout_refund_ids", None); u.pop("deposit_debit_ids", None)
        result.append(u)
    return result

class AdjustBody(BaseModel):
    currency: str
    amount: float
    reason: str = ""


@router.post("/users/{tid}/adjust")
async def adjust_balance(tid: int, body: AdjustBody):
    user = await db.bot_users.find_one({"telegram_id": tid})
    if not user:
        raise HTTPException(404, "Pengguna tidak ditemukan")
    if body.currency not in ("USD", "IDR"):
        raise HTTPException(400, "Currency harus USD atau IDR")
    if not __import__("math").isfinite(body.amount) or body.amount == 0:
        raise HTTPException(400, "Jumlah adjustment tidak valid.")

    field = "balance_usd" if body.currency == "USD" else "balance_idr"
    if body.amount < 0:
        result = await db.bot_users.update_one(
            {
                "telegram_id": tid,
                field: {"$gte": abs(body.amount)},
            },
            {"$inc": {field: body.amount}},
        )
        if result.modified_count != 1:
            raise HTTPException(409, "Saldo pengguna tidak cukup untuk adjustment negatif.")
    else:
        await db.bot_users.update_one(
            {"telegram_id": tid},
            {"$inc": {field: body.amount}},
        )

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
    qris_enabled: bool = False
    bank_enabled: bool = True
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
    auto_broadcast_new_product: bool = False
    transaction_success_channel_enabled: bool = False
    broadcast_auto_image_enabled: bool = False
    broadcast_channel_id: str = ""
    join_group_target: str = ""


def _normalize_required_channel(channel: dict) -> dict:
    channel = dict(channel or {})
    channel_id = str(channel.get("channel_id") or channel.get("id") or "").strip()
    title = str(channel.get("title") or channel.get("name") or "").strip()
    username = str(channel.get("username") or "").strip()
    invite_link = str(channel.get("invite_link") or channel.get("join_link") or "").strip()
    return {
        "channel_id": channel_id,
        "title": title,
        "username": username,
        "invite_link": invite_link,
        "enabled": channel.get("enabled", True) is not False,
    }


@router.put("/settings")
async def update_settings(body: SettingsBody):
    data = body.model_dump()
    channels = [_normalize_required_channel(ch) for ch in data.get("required_channels", [])]
    channels = [ch for ch in channels if ch["channel_id"]]
    if len(channels) > 3:
        raise HTTPException(400, "Maksimal 3 channel wajib join.")
    if data.get("join_gate_enabled") and not channels:
        data["join_gate_enabled"] = False
    data["required_channels"] = channels
    data["broadcast_channel_id"] = str(data.get("broadcast_channel_id") or "").strip()
    data["join_group_target"] = str(data.get("join_group_target") or "").strip()
    await db.settings.update_one({"_id": "main"}, {"$set": data}, upsert=True)
    s = await get_settings()
    s["current_rate"] = await get_rate()
    return s


@router.post("/settings/join-gate/test")
async def test_required_channel(channel_id: str):
    channel_id = str(channel_id or "").strip()
    if not channel_id:
        raise HTTPException(400, "Channel ID wajib diisi.")
    try:
        chat = await tg("getChat", chat_id=channel_id)
        if not chat.get("ok"):
            raise HTTPException(400, f"Telegram tidak bisa mengakses channel: {chat.get('description', 'unknown error')}")
        me = await tg("getMe")
        bot_id = (me.get("result") or {}).get("id")
        member = await tg("getChatMember", chat_id=channel_id, user_id=bot_id)
        if not member.get("ok"):
            raise HTTPException(400, f"Gagal membaca status bot: {member.get('description', 'unknown error')}")
        status = (member.get("result") or {}).get("status")
        if status not in {"creator", "administrator"}:
            raise HTTPException(400, "Bot harus menjadi administrator di channel agar wajib join bisa bekerja.")
        return {
            "ok": True,
            "title": (chat.get("result") or {}).get("title") or "",
            "username": (chat.get("result") or {}).get("username") or "",
            "bot_status": status,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Telegram error: {exc}")


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

    current = await db.bot_messages.find_one({"lang": lang, "key": key})
    next_version = int((current or {}).get("version", 0)) + 1
    previous_text = (
        current.get("text") if current
        else STRINGS[lang].get(key, STRINGS["id"].get(key, key))
    )

    await db.bot_message_history.insert_one({
        "_id": str(uuid.uuid4()),
        "lang": lang,
        "key": key,
        "version": next_version,
        "text": body.text,
        "previous_text": previous_text,
        "created_at": now_iso(),
    })

    await db.bot_messages.update_one(
        {"lang": lang, "key": key},
        {
            "$set": {
                "text": body.text,
                "active": True,
                "version": next_version,
                "updated_at": now_iso(),
            }
        },
        upsert=True,
    )
    set_override(lang, key, body.text)
    return {
        "lang": lang,
        "key": key,
        "text": body.text,
        "version": next_version,
        "custom": True,
    }


@router.get("/messages/{lang}/{key}/history")
async def message_history(lang: str, key: str):
    if lang not in ("id", "en") or key not in message_catalog():
        raise HTTPException(404, "Message key tidak ditemukan")
    return await db.bot_message_history.find(
        {"lang": lang, "key": key}
    ).sort("version", -1).limit(50).to_list(50)


class RollbackMessageBody(BaseModel):
    version: int


@router.post("/messages/{lang}/{key}/rollback")
async def rollback_message(lang: str, key: str, body: RollbackMessageBody):
    if lang not in ("id", "en") or key not in message_catalog():
        raise HTTPException(404, "Message key tidak ditemukan")

    source = await db.bot_message_history.find_one({
        "lang": lang,
        "key": key,
        "version": body.version,
    })
    if not source:
        raise HTTPException(404, "Version tidak ditemukan")

    validate_bot_message(source["text"], lang, key)
    current = await db.bot_messages.find_one({"lang": lang, "key": key})
    next_version = int((current or {}).get("version", 0)) + 1

    await db.bot_message_history.insert_one({
        "_id": str(uuid.uuid4()),
        "lang": lang,
        "key": key,
        "version": next_version,
        "text": source["text"],
        "previous_text": current.get("text") if current else None,
        "rollback_from": body.version,
        "created_at": now_iso(),
    })

    await db.bot_messages.update_one(
        {"lang": lang, "key": key},
        {"$set": {
            "text": source["text"],
            "active": True,
            "version": next_version,
            "updated_at": now_iso(),
        }},
        upsert=True,
    )
    set_override(lang, key, source["text"])
    return {"ok": True, "version": next_version, "rollback_from": body.version}


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
    fixed_currency: Optional[str] = None
    active: bool = True
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    priority: int = 0


@router.get("/discounts")
async def list_discounts():
    return await db.discounts.find().sort([("priority", -1), ("created_at", -1)]).to_list(500)


def _validate_discount(body: DiscountBody):
    if body.mode not in {"percent", "fixed"}:
        raise HTTPException(400, "Mode discount harus percent atau fixed")
    if body.value <= 0:
        raise HTTPException(400, "Nilai discount harus lebih dari 0")
    if body.mode == "percent" and body.value > 100:
        raise HTTPException(400, "Persentase discount maksimal 100%")
    if body.min_qty < 1:
        raise HTTPException(400, "Quantity minimal 1")
    if body.max_qty is not None and body.max_qty < body.min_qty:
        raise HTTPException(400, "Max quantity tidak boleh lebih kecil dari min quantity")
    if body.mode == "fixed" and body.fixed_currency not in {"IDR", "USD"}:
        raise HTTPException(400, "Nominal/unit harus memilih mata uang IDR atau USD")
    if body.mode == "percent":
        body.fixed_currency = None


@router.post("/discounts")
async def create_discount(body: DiscountBody):
    _validate_discount(body)
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
    _validate_discount(body)
    result = await db.discounts.update_one(
        {"_id": did},
        {"$set": {**body.model_dump(), "updated_at": now_iso()}},
    )
    if result.matched_count != 1:
        raise HTTPException(404, "Discount tidak ditemukan")
    return await db.discounts.find_one({"_id": did})


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


@router.post("/broadcasts/preview")
async def broadcast_preview(lang: str = Form("all"), search: str = Form(""), status: str = Form("all")):
    query = {}
    if lang in ("id", "en"): query["lang"] = lang
    if status == "active": query.update({"frozen": {"$ne": True}, "blocked": {"$ne": True}})
    elif status == "frozen": query["frozen"] = True
    if search.strip():
        s = re.escape(search.strip()); query["$or"] = [{"username": {"$regex": s, "$options": "i"}}, {"first_name": {"$regex": s, "$options": "i"}}]
    return {"total": await db.bot_users.count_documents(query)}


@router.post("/broadcasts")
async def create_broadcast(
    text: str = Form(...),
    lang: str = Form("all"),
    search: str = Form(""),
    status: str = Form("all"),
    button_text: str = Form(""),
    button_url: str = Form(""),
    photo: Optional[UploadFile] = File(None),
    product_id: str = Form(""),
    auto_image: bool = Form(False),
):
    if not text.strip():
        raise HTTPException(400, "Pesan broadcast kosong")
    if auto_image and not (await get_settings()).get("broadcast_auto_image_enabled", False):
        raise HTTPException(400, "Auto Generate Picture sedang OFF di Pengaturan.")
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

    if button_url and not re.match(r"^(https?://|tg://)", button_url.strip(), re.I):
        raise HTTPException(400, "URL tombol harus http(s) atau tg://")
    total = await db.bot_users.count_documents(query)

    photo_bytes = None
    filename = None
    if photo:
        photo_bytes = await photo.read()
        filename = photo.filename
    elif auto_image and product_id:
        product = await db.products.find_one({"_id": product_id, "active": True})
        if not product:
            raise HTTPException(400, "Produk untuk gambar otomatis tidak ditemukan.")
        try:
            from broadcast_image import render_product_image
            stock = await available_count(product["_id"]) if _is_inventory_product(product) else None
            price = fmt_amount(
                product.get("price_idr") if product.get("price_idr") is not None else product.get("price_usd") or 0,
                "IDR" if product.get("price_idr") is not None else "USD",
            )
            photo_bytes = render_product_image(product.get("name") or "Product", price, stock, product.get("description") or "")
            filename = "product-broadcast.jpg"
        except Exception as exc:
            logger.exception("Generate broadcast image gagal")
            raise HTTPException(500, f"Gagal membuat gambar otomatis: {type(exc).__name__}")

    doc = {
        "_id": str(uuid.uuid4()),
        "text": text,
        "lang": lang,
        "status": "running",
        "success": 0,
        "failed": 0,
        "blocked": 0,
        "total": total,
        "created_at": now_iso(),
        "finished_at": None,
        "product_id": product_id or None,
        "auto_image": bool(auto_image),
    }
    await db.broadcasts.insert_one(doc)
    asyncio.create_task(_broadcast_worker(
        doc["_id"], query, text, photo_bytes, filename, button_text or None, button_url or None
    ))
    return doc


async def _broadcast_channel_id():
    import os
    configured = str(os.environ.get("BROADCAST_CHANNEL_ID", "")).strip()
    if configured:
        return configured
    settings = await get_settings()
    configured = str(settings.get("broadcast_channel_id") or "").strip()
    if configured:
        return configured
    for channel in settings.get("required_channels") or []:
        if channel.get("enabled", True) and channel.get("channel_id"):
            return str(channel["channel_id"])
    return ""


async def _broadcast_channel_target():
    channel_id = await _broadcast_channel_id()
    if not channel_id:
        raise HTTPException(400, "Channel broadcast belum dikonfigurasi. Isi BROADCAST_CHANNEL_ID atau required channel di settings.")
    return channel_id


async def _build_product_broadcast():
    products = await db.products.find({"active": True}).sort("created_at", 1).to_list(500)
    digital_lines = []
    service_lines = []
    for p in products:
        kind = _normalized_product_kind(p)
        if kind == "digital":
            stock = await available_count(p["_id"])
            if stock > 0:
                digital_lines.append(f"▫️ <b>{escape(str(p.get('name') or 'Product'))}</b> → <b>{stock}</b>")
        else:
            service_lines.append(f"▫️ <b>{escape(str(p.get('name') or 'Jasa'))}</b>")
    lines = ["📢 <b>PRODUCT UPDATE — IDSE NETWORK CONNECT HUB</b>", ""]
    if digital_lines:
        lines += ["💻 <b>PRODUCT DIGITAL TERSEDIA • AVAILABLE STOCK</b>", *digital_lines, ""]
    if service_lines:
        lines += ["🛠️ <b>PRODUCT JASA TERSEDIA</b>", "✨ Tersedia sesuai permintaan • Unlimited", *service_lines, ""]
    if not digital_lines and not service_lines:
        lines += ["⚠️ <b>Saat ini belum ada product aktif yang tersedia.</b>", ""]
    lines += ["🚀 <b>Siap diproses • Cepat • Profesional</b>", "🛒 Silakan order melalui bot:", "🤖 @Idse_MarketBot"]
    return "\n".join(lines).strip()


async def _build_auto_broadcast(content: str):
    content = (content or "both").strip().lower()
    if content not in {"discount", "stock", "both"}:
        raise HTTPException(400, "Isi broadcast otomatis harus discount, stock, atau both.")

    products = await db.products.find({"active": True}).sort("created_at", 1).to_list(500)
    discount_lines = []
    stock_lines = []

    for p in products:
        name = escape(str(p.get("name") or "Product"))
        pricing = await price_for_product(p, "USD", 1)
        has_discount = float(pricing.get("discount_per_unit") or 0) > 0
        if has_discount:
            normal = fmt_amount(pricing["base_unit_price"], "USD")
            sale = fmt_amount(pricing["unit_price"], "USD")
            saved = fmt_amount(pricing["discount_per_unit"], "USD")
            discount_lines.append(f"▫️ <b>{name}</b> → {normal} ➜ <b>{sale}</b> (hemat {saved}/unit)")

        kind = _normalized_product_kind(p)
        if kind == "digital":
            stock = await available_count(p["_id"])
            if stock > 0:
                stock_lines.append(f"▫️ <b>{name}</b> → <b>{stock}</b>")
        else:
            stock_lines.append(f"▫️ <b>{name}</b> → <b>Unlimited</b>")

    lines = ["📢 <b>PROMO & STOCK UPDATE — IDSE NETWORK CONNECT HUB</b>", ""]
    if content in {"discount", "both"}:
        lines += ["🏷️ <b>HARGA DISKON TERSEDIA</b>"]
        lines += discount_lines or ["▫️ Belum ada product dengan harga diskon aktif."]
        lines.append("")
    if content in {"stock", "both"}:
        lines += ["📦 <b>STOCK TERSEDIA</b>"]
        lines += stock_lines or ["▫️ Saat ini belum ada stock tersedia."]
        lines.append("")
    lines += ["🚀 <b>Siap diproses • Cepat • Profesional</b>", "🛒 Silakan order melalui bot:", "🤖 @Idse_MarketBot"]
    return "\n".join(lines).strip()


@router.get("/broadcasts/channel-product-preview")
async def broadcast_channel_product_preview():
    return {"text": await _build_product_broadcast()}


@router.get("/broadcasts/auto-preview")
async def broadcast_auto_preview(content: str = "both"):
    return {"text": await _build_auto_broadcast(content)}


@router.post("/broadcasts/channel")
async def broadcast_channel(
    mode: str = Form(...),
    text: str = Form(""),
    target: str = Form("channel"),
    content: str = Form("both"),
    product_id: str = Form(""),
    auto_image: bool = Form(False),
):
    mode = (mode or "").strip().lower()
    target = (target or "channel").strip().lower()
    if auto_image and not (await get_settings()).get("broadcast_auto_image_enabled", False):
        raise HTTPException(400, "Auto Generate Picture sedang OFF di Pengaturan.")
    content = (content or "both").strip().lower()

    if mode == "manual":
        channel_id = await _broadcast_channel_target()
        if not text.strip():
            raise HTTPException(400, "Pesan manual wajib diisi.")
        body = text.strip()
        result = await send_message(channel_id, body)
        if not result.get("ok"):
            raise HTTPException(502, f"Telegram gagal mengirim ke channel: {result.get('description', 'unknown error')}")
        return {"ok": True, "mode": mode, "target": "channel", "channel_id": channel_id, "text": body}

    if mode == "product":
        channel_id = await _broadcast_channel_target()
        product = await db.products.find_one({"_id": product_id, "active": True}) if product_id else None
        if not product:
            raise HTTPException(400, "Pilih product terlebih dahulu.")
        stock = await available_count(product["_id"]) if _is_inventory_product(product) else None
        price = fmt_amount(
            product.get("price_idr") if product.get("price_idr") is not None else product.get("price_usd") or 0,
            "IDR" if product.get("price_idr") is not None else "USD",
        )
        body = (
            "🛒 <b>" + escape(str(product.get("name") or "Product")) + "</b>\n\n"
            + escape(str(product.get("description") or "").strip()) + "\n\n"
            + "Harga: <b>" + escape(price) + "</b>"
        )
        image = None
        if auto_image:
            try:
                from broadcast_image import render_product_image
                image = render_product_image(product.get("name") or "Product", price, stock, product.get("description") or "")
            except Exception as exc:
                raise HTTPException(500, f"Gagal membuat gambar otomatis: {type(exc).__name__}")
        result = await (send_photo_bytes(channel_id, image, "product-broadcast.jpg", caption=body) if image else send_message(channel_id, body))
        if not result.get("ok"):
            raise HTTPException(502, f"Telegram gagal mengirim ke channel: {result.get('description', 'unknown error')}")
        return {"ok": True, "mode": mode, "target": "channel", "channel_id": channel_id, "text": body, "auto_image": bool(image)}

    if mode == "products":
        channel_id = await _broadcast_channel_target()
        body = await _build_product_broadcast()
        result = await send_message(channel_id, body)
        if not result.get("ok"):
            raise HTTPException(502, f"Telegram gagal mengirim ke channel: {result.get('description', 'unknown error')}")
        return {"ok": True, "mode": mode, "target": "channel", "channel_id": channel_id, "text": body}

    if mode == "auto":
        if target not in {"channel", "users"}:
            raise HTTPException(400, "Target broadcast otomatis tidak valid.")
        body = await _build_auto_broadcast(content)

        if target == "channel":
            channel_id = await _broadcast_channel_target()
            result = await send_message(channel_id, body)
            if not result.get("ok"):
                raise HTTPException(502, f"Telegram gagal mengirim ke channel: {result.get('description', 'unknown error')}")
            return {
                "ok": True,
                "mode": mode,
                "target": "channel",
                "content": content,
                "channel_id": channel_id,
                "text": body,
            }

        query = {"blocked": {"$ne": True}}
        total = await db.bot_users.count_documents(query)
        doc = {
            "_id": str(uuid.uuid4()),
            "text": body,
            "lang": "all",
            "status": "running",
            "success": 0,
            "failed": 0,
            "blocked": 0,
            "total": total,
            "created_at": now_iso(),
            "finished_at": None,
            "broadcast_type": "auto",
            "broadcast_target": "users",
            "broadcast_content": content,
        }
        photo_bytes = None
        filename = None
        if auto_image and product_id:
            product = await db.products.find_one({"_id": product_id, "active": True})
            if not product:
                raise HTTPException(400, "Produk untuk gambar otomatis tidak ditemukan.")
            from broadcast_image import render_product_image
            stock = await available_count(product["_id"]) if _is_inventory_product(product) else None
            price = fmt_amount(
                product.get("price_idr") if product.get("price_idr") is not None else product.get("price_usd") or 0,
                "IDR" if product.get("price_idr") is not None else "USD",
            )
            photo_bytes = render_product_image(product.get("name") or "Product", price, stock, product.get("description") or "")
            filename = "product-broadcast.jpg"
        await db.broadcasts.insert_one(doc)
        asyncio.create_task(_broadcast_worker(
            doc["_id"], query, body, photo_bytes, filename, None, None
        ))
        return {
            "ok": True,
            "mode": mode,
            "target": "users",
            "content": content,
            "queued": True,
            "total": total,
            "text": body,
        }

    raise HTTPException(400, "Mode broadcast channel tidak valid.")



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


# ============ REPORTS ============
# Mounted under /api/admin/reports with the same admin authentication dependency.
router.include_router(reports_router)
