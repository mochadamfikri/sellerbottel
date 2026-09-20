import os
import json
import httpx

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{TOKEN}"


async def tg(method: str, **payload):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{API}/{method}", json=payload)
        return r.json()


async def send_message(chat_id, text, kb=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if kb:
        payload["reply_markup"] = kb
    return await tg("sendMessage", **payload)


async def edit_message(chat_id, message_id, text, kb=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if kb:
        payload["reply_markup"] = kb
    return await tg("editMessageText", **payload)


async def delete_message(chat_id, message_id):
    return await tg("deleteMessage", chat_id=chat_id, message_id=message_id)


async def answer_callback(cb_id, text=None):
    payload = {"callback_query_id": cb_id}
    if text:
        payload["text"] = text
    return await tg("answerCallbackQuery", **payload)


async def send_photo_by_id(chat_id, file_id, caption=None, kb=None):
    payload = {"chat_id": chat_id, "photo": file_id, "parse_mode": "HTML"}
    if caption:
        payload["caption"] = caption
    if kb:
        payload["reply_markup"] = kb
    return await tg("sendPhoto", **payload)


async def send_photo_bytes(chat_id, data: bytes, filename: str = "photo.jpg", caption=None, kb=None):
    payload = {"chat_id": str(chat_id)}
    if caption:
        payload["caption"] = caption
        payload["parse_mode"] = "HTML"
    if kb:
        payload["reply_markup"] = json.dumps(kb, ensure_ascii=False, separators=(",", ":"))
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(
            f"{API}/sendPhoto",
            data=payload,
            files={"photo": (filename, data, "image/jpeg")},
        )
        return r.json()


async def send_document(chat_id, data: bytes, filename: str, caption=None):
    payload = {"chat_id": str(chat_id)}
    if caption:
        payload["caption"] = caption
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{API}/sendDocument", data=payload, files={"document": (filename, data)})
        return r.json()


async def download_telegram_file(file_id: str):
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{API}/getFile", json={"file_id": file_id})
        info = r.json()
        if not info.get("ok"):
            return None
        path = info["result"]["file_path"]
        f = await c.get(f"{FILE_API}/{path}")
        return f.content
