from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import asyncio
import logging
from fastapi import FastAPI, APIRouter, Request, HTTPException
from starlette.middleware.cors import CORSMiddleware

from db import client, ensure_settings
from auth import router as auth_router, seed_admin
from admin_routes import router as admin_router
from bot import process_update
from storage import init_storage
from tgapi import tg

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI()
api_router = APIRouter(prefix="/api")


@api_router.get("/")
async def root():
    return {"message": "Toko Digital Bot API"}


@api_router.post("/telegram/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != os.environ.get("WEBHOOK_SECRET"):
        raise HTTPException(status_code=403, detail="Invalid secret")
    update = await request.json()
    asyncio.create_task(process_update(update))
    return {"ok": True}


app.include_router(api_router)
app.include_router(auth_router)
app.include_router(admin_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    await ensure_settings()
    await seed_admin()
    try:
        await init_storage()
        logger.info("Object storage initialized")
    except Exception as e:
        logger.error(f"Storage init failed: {e}")
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if base and os.environ.get("TELEGRAM_TOKEN"):
        try:
            res = await tg("setWebhook", url=f"{base}/api/telegram/webhook/{os.environ['WEBHOOK_SECRET']}",
                           allowed_updates=["message", "callback_query"])
            logger.info(f"Webhook set: {res}")
        except Exception as e:
            logger.error(f"Webhook setup failed: {e}")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
