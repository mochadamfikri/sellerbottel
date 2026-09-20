import asyncio
import base64
import json
import os
import subprocess
import uuid
import secrets
from datetime import datetime, timedelta, timezone

from pymongo.errors import DuplicateKeyError

from db import db, get_settings
from services import credit_deposit, now_iso


NODE_DIR = os.path.join(os.path.dirname(__file__), "gobiz")


def _parse_node_json(stdout):
    text = (stdout or "").strip()
    if not text:
        raise ValueError("GoPay provider tidak mengembalikan JSON.")

    try:
        return json.loads(text)
    except json.JSONDecodeError as first_error:
        decoder = json.JSONDecoder()
        best_value = None
        best_end = -1

        # Node SDK logs may appear before the JSON payload. Find the
        # candidate JSON value that consumes the furthest part of stdout.
        # This avoids accidentally selecting a nested object/array from
        # inside the real top-level payload.
        for idx, char in enumerate(text):
            if char not in "[{":
                continue
            try:
                value, end = decoder.raw_decode(text[idx:])
            except json.JSONDecodeError:
                continue

            absolute_end = idx + end
            if text[absolute_end:].strip():
                continue
            if absolute_end > best_end:
                best_value = value
                best_end = absolute_end

        if best_value is not None:
            return best_value
        raise first_error


def _run_node(script, args=None, timeout=90):
    cmd = ["node", os.path.join(NODE_DIR, script), *(args or [])]
    env = os.environ.copy()
    env["NODE_PATH"] = os.path.join(NODE_DIR, "node_modules")

    result = subprocess.run(
        cmd,
        cwd=NODE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "GoPay provider gagal.")
    return _parse_node_json(result.stdout)


async def create_gopay_payment(user, amount, platform_code=None):
    amount = int(round(amount))
    if amount < 1:
        raise ValueError("Nominal IDR tidak valid.")

    admin_fee = int(round(amount * 0.007))
    if admin_fee < 1:
        admin_fee = 1
    deposit_total = amount + admin_fee

    deposit_id = str(uuid.uuid4())
    payment_id = str(uuid.uuid4())
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)

    active_amount = None
    selected_code = None
    for _ in range(200):
        suffix = int(platform_code) if platform_code is not None else (secrets.randbelow(900) + 100)
        if not 100 <= suffix <= 999:
            suffix = secrets.randbelow(900) + 100
        candidate = deposit_total + suffix
        try:
            await db.gopay_payments.insert_one({
                "_id": payment_id,
                "deposit_id": deposit_id,
                "user_tid": user["telegram_id"],
                "base_amount": amount,
                "payment_amount": candidate,
                "active_payment_amount": candidate,
                "status": "pending",
                "tx_id": None,
                "created_at": now_iso(),
                "expires_at": expires.isoformat(),
                "confirmed_at": None,
            })
            active_amount = candidate
            selected_code = suffix
            break
        except DuplicateKeyError:
            continue

    if active_amount is None:
        raise RuntimeError("Tidak menemukan nominal QR GoPay yang unik.")

    deposit = {
        "_id": deposit_id,
        "user_tid": user["telegram_id"],
        "username": user.get("username", ""),
        "first_name": user.get("first_name", ""),
        "method": "gopay",
        "coin": None,
        "network": None,
        "currency": "IDR",
        "amount": amount,
        "admin_fee": admin_fee,
        "platform_code": selected_code,
        "payment_amount": active_amount,
        "credited_amount": amount,
        "tx_hash": None,
        "gopay_tx_id": None,
        "proof_file_id": None,
        "status": "pending",
        "auto_verified": True,
        "note": "Menunggu pembayaran GoPay QR",
        "created_at": now_iso(),
        "decided_at": None,
        "expires_at": expires.isoformat(),
    }

    try:
        await db.deposits.insert_one(deposit)
        data = await asyncio.to_thread(
            _run_node,
            "create_qris.mjs",
            [str(active_amount)],
        )
        image = base64.b64decode(data["image_base64"])
        return {
            "deposit": deposit,
            "payment_amount": active_amount,
            "admin_fee": admin_fee,
            "platform_code": selected_code,
            "image": image,
            "expires_at": expires,
        }
    except Exception:
        await db.gopay_payments.delete_one({"_id": payment_id})
        await db.deposits.delete_one({"_id": deposit_id})
        raise


async def _history():
    return await asyncio.to_thread(_run_node, "history.mjs", [], 90)


async def poll_gopay_once():
    if os.environ.get("GOPAY_ENABLED", "").lower() not in {"1", "true", "yes"}:
        return {"checked": False, "matched": 0}
    settings = await get_settings()
    if not settings.get("qris_enabled", False):
        return {"checked": False, "matched": 0}

    now = datetime.now(timezone.utc)
    await db.gopay_payments.update_many(
        {"status": "pending", "expires_at": {"$lte": now.isoformat()}},
        {"$set": {"status": "expired", "expired_at": now_iso()}, "$unset": {"active_payment_amount": ""}},
    )
    await db.deposits.update_many(
        {"method": "gopay", "status": "pending", "expires_at": {"$lte": now.isoformat()}},
        {"$set": {"status": "expired", "decided_at": now_iso()}},
    )

    histories = await _history()
    matched = 0

    for tx in histories:
        tx_id = tx.get("tx_id")
        tx_amount = int(round(float(tx.get("amount") or 0)))
        status = str(tx.get("status") or "").lower()
        tx_type = str(tx.get("type") or "").lower()
        if not tx_id or tx_amount <= 0:
            continue
        if tx_type and tx_type != "payin":
            continue
        if status and status not in {"settlement", "capture", "paid", "success", "successful"}:
            continue

        payment = await db.gopay_payments.find_one_and_update(
            {
                "status": "pending",
                "active_payment_amount": tx_amount,
                "expires_at": {"$gt": now.isoformat()},
            },
            {
                "$set": {
                    "status": "confirmed",
                    "tx_id": tx_id,
                    "confirmed_at": now_iso(),
                },
                "$unset": {"active_payment_amount": ""},
            },
        )

        if not payment:
            continue

        deposit = await db.deposits.find_one({"_id": payment["deposit_id"]})
        if not deposit or deposit.get("status") != "pending":
            continue

        deposit["credited_amount"] = float(payment["base_amount"])
        deposit["gopay_tx_id"] = tx_id
        await credit_deposit(deposit, note=f"GoPay QR terverifikasi. TX {tx_id}")
        matched += 1

    return {"checked": True, "matched": matched}


async def run_gopay_monitor(stop_event: asyncio.Event):
    interval = max(10, int(os.environ.get("GOPAY_POLL_INTERVAL", "15")))
    while not stop_event.is_set():
        try:
            result = await poll_gopay_once()
            if result.get("checked") and result.get("matched"):
                print(f"[GoPay] {result['matched']} pembayaran terverifikasi.")
        except Exception as exc:
            print(f"[GoPay] monitor error: {exc}")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
