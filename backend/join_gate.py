import logging
import time

from db import get_settings
from tgapi import tg

logger = logging.getLogger("join_gate")

_CACHE = {}
_CACHE_TTL = 60


def _join_url(channel):
    if channel.get("invite_link"):
        return channel["invite_link"]
    raw = str(channel.get("username") or "").strip()
    if raw.startswith("@"):
        raw = raw[1:]
    return f"https://t.me/{raw}" if raw else None


async def check_user_membership(user_tid: int):
    settings = await get_settings()
    if not settings.get("join_gate_enabled", False):
        return True, []

    channels = settings.get("required_channels") or []
    channels = [c for c in channels if c.get("enabled", True) and c.get("channel_id")]
    if not channels:
        return True, []

    missing = []
    for channel in channels[:3]:
        key = f"{channel['channel_id']}:{user_tid}"
        cached = _CACHE.get(key)
        if cached and time.time() - cached[0] < _CACHE_TTL:
            joined = cached[1]
        else:
            try:
                result = await tg(
                    "getChatMember",
                    chat_id=channel["channel_id"],
                    user_id=user_tid,
                )
                member = result.get("result") or {}
                status = member.get("status")
                joined = status in {"creator", "administrator", "member"} or (
                    status == "restricted" and member.get("is_member", False)
                )
                _CACHE[key] = (time.time(), joined)
            except Exception as exc:
                logger.warning("Join-gate Telegram error for %s: %s", channel["channel_id"], exc)
                if settings.get("join_gate_fail_open", True):
                    continue
                joined = False

        if not joined:
            missing.append(channel)

    return len(missing) == 0, missing


def build_gate_keyboard(channels):
    rows = []
    for channel in channels[:3]:
        url = _join_url(channel)
        if url:
            rows.append([{
                "text": f"📢 {channel.get('title') or channel.get('username') or 'Join Channel'}",
                "url": url,
            }])
    rows.append([{"text": "✅ Saya sudah join", "callback_data": "gate:check"}])
    return {"inline_keyboard": rows}


def clear_cache_for_user(user_tid):
    prefix = f":{user_tid}"
    for key in list(_CACHE):
        if key.endswith(prefix):
            _CACHE.pop(key, None)
