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
    "join_gate_fail_open": False,
    "required_channels": [],
    "message_version": 1,
    "auto_broadcast_new_product": False,
    "transaction_success_channel_enabled": False,
    "broadcast_auto_image_enabled": False,
    "broadcast_channel_id": "",
    "broadcast_group_ids": "",
    "stock_notifications_enabled": True,
    "daily_recap_enabled": False,
    "daily_recap_time": "00:05",
    "daily_recap_target": "chats",
    "reseller_enabled": False,
    "reseller_bot_price_idr": 0,
    "reseller_admin_fee_idr": 0,
    "reseller_platform_fee_idr": 0,
    "reseller_wholesale_reduction_idr": 2000,
    "join_group_target": "",
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
    await db.stock_events.create_index([("status", 1), ("created_at", 1)])
    await db.processed_updates.create_index("update_id", unique=True)
    await db.processed_updates_bot2.create_index("update_id", unique=True)
    await db.reseller_bots.create_index("telegram_bot_id", unique=True)
    await db.reseller_bots.create_index([("owner_tid", 1), ("created_at", -1)])
    await db.reseller_bot_users.create_index([("bot_id", 1), ("telegram_id", 1)], unique=True)
    await db.reseller_updates.create_index([("bot_id", 1), ("update_id", 1)], unique=True)
    await db.purchases.create_index([("reseller_bot_id", 1), ("created_at", -1)])
    await db.reseller_commissions.create_index([("bot_id", 1), ("status", 1)])
    await db.reseller_payouts.create_index([("status", 1), ("created_at", -1)])
    try:
        await db.gopay_payments.drop_index("active_payment_amount_1")
    except Exception:
        pass
    try:
        await db.gopay_payments.drop_index("payment_scope_1_active_payment_amount_1")
    except Exception:
        pass
    await db.gopay_payments.create_index(
        [("payment_scope", 1), ("active_payment_amount", 1)],
        unique=True,
        partialFilterExpression={
            "active_payment_amount": {"$type": ["int", "long", "double", "decimal"]},
        },
    )
    await db.bot2_restock_requests.create_index(
        [("user_tid", 1), ("product_id", 1)],
        unique=True,
    )
    await db.gopay_payments.create_index([("status", 1), ("expires_at", 1)])
    await db.gopay_payments.create_index(
        "tx_id",
        unique=True,
        partialFilterExpression={"tx_id": {"$type": "string"}},
    )

    # Promotion / CRM module.
    await db.promo_coupons.create_index("code", unique=True)
    await db.promo_coupon_redemptions.create_index(
        [("coupon_id", 1), ("order_id", 1)],
        unique=True,
    )
    await db.promo_coupon_redemptions.create_index([("coupon_id", 1), ("user_tid", 1)])
    await db.prospects.create_index(
        [("owner_account_id", 1), ("tg_user_id", 1)],
        unique=True,
    )
    await db.prospects.create_index([("status", 1), ("created_at", -1)])
    await db.tg_accounts.create_index("tg_user_id", unique=True, sparse=True)
    await db.tg_accounts.create_index("status")
    await db.tg_groups.create_index([("account_id", 1), ("chat_id", 1)], unique=True)
    await db.outreach_jobs.create_index([("status", 1), ("scheduled_at", 1)])
    await db.outreach_campaigns.create_index([("status", 1), ("created_at", -1)])
    await db.traffic_sources.create_index("code", unique=True)
    await db.promo_campaigns.create_index([("status", 1), ("created_at", -1)])
    await db.promo_suppressions.create_index("tg_user_id", unique=True)
    await db.promo_events.create_index([("type", 1), ("created_at", -1)])
    await db.outreach_jobs.create_index([("campaign_id", 1), ("prospect_id", 1), ("status", 1)])


async def get_settings() -> dict:
    s = await db.settings.find_one({"_id": "main"})
    if not s:
        return dict(DEFAULT_SETTINGS)
    merged = dict(DEFAULT_SETTINGS)
    merged.update(s)
    return merged
