from fastapi import APIRouter, Depends

from auth import get_current_admin
from db import db

router = APIRouter(prefix="/api/admin", dependencies=[Depends(get_current_admin)])


@router.get("/users/all")
async def list_all_users():
    purchase_counts = {
        row["_id"]: row["n"]
        for row in await db.purchases.aggregate([
            {"$group": {"_id": "$user_tid", "n": {"$sum": 1}}}
        ]).to_list(10000)
    }

    users = []
    async for user in db.bot_users.find({}).sort("created_at", -1):
        user.pop("state", None)
        user.pop("state_data", None)
        user["purchase_count"] = purchase_counts.get(user.get("telegram_id"), 0)
        users.append(user)

    return users
