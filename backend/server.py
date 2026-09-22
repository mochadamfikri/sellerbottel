from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import asyncio
import logging
from fastapi import FastAPI, APIRouter, Request, HTTPException, Depends
from starlette.middleware.cors import CORSMiddleware

from db import client, db, ensure_settings, ensure_indexes
from auth import get_current_admin
from auth import router as auth_router, seed_admin
from admin_routes import router as admin_router
from admin_user_routes import router as admin_user_router
from promo_routes import router as promo_router
from promo_routes_accounts import router as promo_accounts_router
from promo_campaign_routes import router as promo_campaign_router
from promo_reply_routes import router as promo_reply_router
from promo_runtime import start_promo_runtime, stop_promo_runtime
from error_handlers import register_error_handlers
from inventory import encryption_status
from bot import process_update, resume_service_waiters
from bot2 import process_update2, run_bot2_payment_monitor
from i18n import load_overrides
from tgapi import tg
from gopay_provider import run_gopay_monitor

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI()
api_router = APIRouter(prefix="/api")


@api_router.get("/")
async def root():
    return {"message": "Toko Digital Bot API"}


@api_router.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    import hmac

    expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    received = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not expected or not received or not hmac.compare_digest(received, expected):
        raise HTTPException(status_code=403, detail="Invalid webhook token")

    update = await request.json()
    update_id = update.get("update_id")
    if update_id is not None:
        try:
            await db.processed_updates.insert_one({"_id": str(update_id), "update_id": update_id})
        except Exception:
            return {"ok": True, "duplicate": True}

    asyncio.create_task(process_update(update))
    return {"ok": True}


@api_router.post("/telegram/bot2/webhook")
async def telegram_webhook_bot2(request: Request):
    import hmac

    expected = os.environ.get("BOT2_WEBHOOK_SECRET", "")
    received = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not expected or not received or not hmac.compare_digest(received, expected):
        raise HTTPException(status_code=403, detail="Invalid Bot 2 webhook token")

    update = await request.json()
    update_id = update.get("update_id")
    if update_id is not None:
        try:
            await db.processed_updates_bot2.insert_one({"_id": str(update_id), "update_id": update_id})
        except Exception:
            return {"ok": True, "duplicate": True}

    asyncio.create_task(process_update2(update))
    return {"ok": True}


app.include_router(api_router)
app.include_router(auth_router)
app.include_router(admin_user_router)
app.include_router(admin_router)

if os.environ.get("PROMOTION_ENABLED", "").lower() in {"1", "true", "yes"}:
    app.include_router(promo_router)
    app.include_router(promo_accounts_router, prefix="/api/admin/promo", dependencies=[Depends(get_current_admin)])
    app.include_router(promo_campaign_router, prefix="/api/admin/promo", dependencies=[Depends(get_current_admin)])
    app.include_router(promo_reply_router, prefix="/api/admin/promo", dependencies=[Depends(get_current_admin)])

register_error_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    if not os.environ.get("CORS_ORIGINS"):
        raise RuntimeError("CORS_ORIGINS wajib di-set.")
    if not os.environ.get("TELEGRAM_WEBHOOK_SECRET"):
        raise RuntimeError("TELEGRAM_WEBHOOK_SECRET wajib di-set.")

    logger.info(
        "Promotion module: %s",
        "enabled" if os.environ.get("PROMOTION_ENABLED", "").lower() in {"1", "true", "yes"} else "disabled",
    )
    logger.info(
        "Bot 2: %s",
        "enabled" if os.environ.get("BOT2_ENABLED", "").lower() in {"1", "true", "yes"} else "disabled",
    )

    await ensure_settings()
    await ensure_indexes()
    await load_overrides(db.bot_messages)
    await seed_admin()

    try:
        inv = await encryption_status()
        if not inv["valid"] or inv["data_readable"] is False:
            logger.error("INVENTORY: %s", inv["message"])
    except Exception as exc:
        logger.error("INVENTORY: gagal mengecek konfigurasi enkripsi: %s", exc)

    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

    if os.environ.get("GOPAY_ENABLED", "").lower() in {"1", "true", "yes"}:
        app.state.gopay_stop = asyncio.Event()
        app.state.gopay_task = asyncio.create_task(run_gopay_monitor(app.state.gopay_stop))

    if (
        os.environ.get("BOT2_ENABLED", "").lower() in {"1", "true", "yes"}
        and os.environ.get("BOT2_TELEGRAM_TOKEN")
        and os.environ.get("GOPAY_ENABLED", "").lower() in {"1", "true", "yes"}
    ):
        app.state.bot2_payment_stop = asyncio.Event()
        app.state.bot2_payment_task = asyncio.create_task(
            run_bot2_payment_monitor(app.state.bot2_payment_stop)
        )

    await resume_service_waiters()
    await start_promo_runtime()

    if base and os.environ.get("TELEGRAM_TOKEN"):
        try:
            res = await tg(
                "setWebhook",
                url=f"{base}/api/telegram/webhook",
                secret_token=os.environ["TELEGRAM_WEBHOOK_SECRET"],
                allowed_updates=["message", "callback_query"],
            )
            logger.info("Bot 1 webhook set: %s", res)
        except Exception as exc:
            logger.error("Bot 1 webhook setup failed: %s", exc)

    bot2_base = os.environ.get("BOT2_PUBLIC_BASE_URL", "").rstrip("/") or base
    if (
        os.environ.get("BOT2_ENABLED", "").lower() in {"1", "true", "yes"}
        and bot2_base
        and os.environ.get("BOT2_TELEGRAM_TOKEN")
        and os.environ.get("BOT2_WEBHOOK_SECRET")
    ):
        try:
            from bot2 import tg2
            res = await tg2(
                "setWebhook",
                url=f"{bot2_base}/api/telegram/bot2/webhook",
                secret_token=os.environ["BOT2_WEBHOOK_SECRET"],
                allowed_updates=["message", "callback_query"],
            )
            logger.info("Bot 2 webhook set: %s", res)
            commands = await tg2(
                "setMyCommands",
                commands=[
                    {"command": "start", "description": "Mulai / buka menu"},
                    {"command": "stock", "description": "Lihat stok produk"},
                    {"command": "help", "description": "Cara order"},
                    {"command": "deposit", "description": "Deposit saldo IDR"},
                ],
            )
            logger.info("Bot 2 commands set: %s", commands)
        except Exception as exc:
            logger.error("Bot 2 webhook setup failed: %s", exc)


@app.on_event("shutdown")
async def shutdown_db_client():
    await stop_promo_runtime()

    stop = getattr(app.state, "gopay_stop", None)
    task = getattr(app.state, "gopay_task", None)
    if stop:
        stop.set()
    if task:
        task.cancel()

    bot2_stop = getattr(app.state, "bot2_payment_stop", None)
    bot2_task = getattr(app.state, "bot2_payment_task", None)
    if bot2_stop:
        bot2_stop.set()
    if bot2_task:
        bot2_task.cancel()

    client.close()
