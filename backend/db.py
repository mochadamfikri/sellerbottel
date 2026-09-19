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
}


async def ensure_settings():
    existing = await db.settings.find_one({"_id": "main"})
    if not existing:
        await db.settings.insert_one(DEFAULT_SETTINGS)


async def get_settings() -> dict:
    s = await db.settings.find_one({"_id": "main"})
    return s or DEFAULT_SETTINGS
