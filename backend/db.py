import os
from pathlib import Path
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(Path(__file__).parent / '.env')

client = AsyncIOMotorClient(os.environ['MONGO_URL'])
db = client[os.environ['DB_NAME']]

DEFAULT_SETTINGS = {
    "_id": "main",
    "crypto_addresses": {
        "USDT_SOL": "", "USDT_POL": "", "USDT_BNB": "", "USDT_AVAX": "",
        "USDC_SOL": "", "USDC_POL": "", "USDC_BNB": "", "USDC_AVAX": "",
    },
    "bank_name": "",
    "bank_account_number": "",
    "bank_account_holder": "",
    "min_deposit_usd": 15.0,
    "min_deposit_idr": 50000.0,
    "admin_telegram_id": os.environ.get("ADMIN_TELEGRAM_ID", ""),
    "rate_mode": "auto",
    "manual_rate": 16000.0,
    "cached_rate": 16000.0,
    "rate_updated_at": None,
    "qris_enabled": os.environ.get("GOPAY_ENABLED", "").lower() in {"1", "true", "yes"},
    "bank_enabled": True,
    "stats_reset_at": None,
    "join_gate_enabled": True,
    "join_gate_fail_open": True,
    "required_channels": [],
    "message_version": 1,
}


async def ensure_settings():
    existing = await db.settings.find_one({"_id": "main"})
    if not existing:
        await db.settings.insert_one(DEFAULT_SETTINGS)


async def ensure_indexes():
    try:
        await db.inventory_items.drop_index("fingerprint_1")
    except Exception:
        pass

    await db.bot_users.create_index("telegram_id", unique=True)
    await db.deposits.create_index(
        "tx_hash",
        unique=True,
        partialFilterExpression={"tx_hash": {"$type": "string"}},
    )
    await db.deposits.create_index([("user_tid", 1), ("created_at", -1)])
    await db.purchases.create_index("invoice_id", unique=True, sparse=True)
    await db.purchases.create_index([("user_tid", 1), ("created_at", -1)])
    await db.login_attempts.create_index("identifier", unique=True)
    await db.products.create_index([("active", 1), ("created_at", -1)])
    await db.inventory_items.create_index([("product_id", 1), ("status", 1)])
    await db.inventory_items.create_index(
        [("product_id", 1), ("fingerprint", 1)],
        unique=True,
    )
    await db.discounts.create_index([("active", 1), ("priority", -1)])
    await db.coupons.create_index("code", unique=True)
    await db.bot_messages.create_index([("key", 1), ("lang", 1)], unique=True)
    await db.bot_message_history.create_index([("lang", 1), ("key", 1), ("version", -1)])
    await db.required_channels.create_index("channel_id", unique=True)
    await db.broadcasts.create_index([("created_at", -1)])
    await db.processed_updates.create_index("update_id", unique=True)
    await db.gopay_payments.create_index("active_payment_amount", unique=True, sparse=True)
    await db.gopay_payments.create_index([("status", 1), ("expires_at", 1)])
    await db.gopay_payments.create_index(
        "tx_id",
        unique=True,
        partialFilterExpression={"tx_id": {"$type": "string"}},
    )


async def get_settings() -> dict:
    s = await db.settings.find_one({"_id": "main"})
    return s or DEFAULT_SETTINGS
