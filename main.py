import asyncio
import logging
import importlib.util
import tempfile
import time
from collections import OrderedDict
from contextlib import suppress
from pathlib import Path

from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo, Message

from config import BOT_TOKEN, DOWNLOADS_DIR, MAX_CONCURRENT_DOWNLOADS, MAX_FILE_SIZE_MB, MAX_MEDIA_ITEMS, MAX_QUEUE_SIZE
from downloader import MediaError, download_media, get_platform_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
router = Router()
download_slots = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
active_users: set[int] = set()
cache: OrderedDict[str, tuple[float, list[tuple[str, str]]]] = OrderedDict()
url_locks: dict[str, tuple[asyncio.Lock, int]] = {}
CACHE_TTL = 6 * 60 * 60
CACHE_SIZE = 512


def cache_get(url: str) -> list[tuple[str, str]] | None:
    entry = cache.get(url)
    if entry is None:
        return None
    if time.monotonic() - entry[0] >= CACHE_TTL:
        cache.pop(url, None)
        return None
    cache.move_to_end(url)
    return entry[1]


def cache_put(url: str, media: list[tuple[str, str]]) -> None:
    cache[url] = (time.monotonic(), media)
    cache.move_to_end(url)
    while len(cache) > CACHE_SIZE:
        cache.popitem(last=False)


async def edit_status(message: Message, text: str) -> None:
    with suppress(TelegramAPIError):
        await message.edit_text(text)


def file_kind(path: Path) -> str:
    if path.suffix.lower() in {".jpg", ".jpeg", ".png"} and path.stat().st_size <= 10 * 1024 * 1024:
        return "photo"
    if path.suffix.lower() == ".mp4":
        return "video"
    return "document"


async def send_media(message: Message, items: list[tuple[str, object]]) -> list[tuple[str, str]]:
    sent = []
    index = 0
    while index < len(items):
        batch = []
        while index + len(batch) < len(items) and len(batch) < 10:
            kind, value = items[index + len(batch)]
            if kind == "document":
                break
            batch.append((kind, value))
        if len(batch) >= 2:
            media = [InputMediaPhoto(media=value) if kind == "photo" else InputMediaVideo(media=value)
                     for kind, value in batch]
            try:
                sent.extend(await message.answer_media_group(media=media))
                index += len(batch)
                continue
            except TelegramBadRequest:
                pass
        kind, value = items[index]
        try:
            if kind == "photo":
                result = await message.answer_photo(value)
            elif kind == "video":
                result = await message.answer_video(value)
            else:
                result = await message.answer_document(value)
        except TelegramBadRequest:
            if not isinstance(value, FSInputFile) or kind == "document":
                raise
            result = await message.answer_document(value)
        sent.append(result)
        index += 1
    result = []
    for item in sent:
        if item.photo:
            result.append(("photo", item.photo[-1].file_id))
        elif item.video:
            result.append(("video", item.video.file_id))
        elif item.document:
            result.append(("document", item.document.file_id))
    return result


@router.message(CommandStart())
@router.message(Command("help"))
async def start(message: Message) -> None:
    await message.answer(
        "Salom! Rasm yoki video yuklash uchun post linkini yuboring.\n\n"
        "YouTube, TikTok, Instagram, X/Twitter, Threads, Reddit, Pinterest, Facebook va Vimeo.\n"
        f"Video: 720p gacha. Har bir fayl: {MAX_FILE_SIZE_MB} MB gacha. "
        f"Bitta postdan {MAX_MEDIA_ITEMS} tagacha media.\n\n"
        "Profil yoki kanal emas, alohida post/video linkini yuboring. "
        "Ayrim postlar sayt cheklovlari sabab ishlamasligi mumkin."
    )


@router.message()
async def handle_link(message: Message) -> None:
    url = get_platform_url(message.text or message.caption or "")
    if not url:
        await message.answer("Post yoki video linkini yuboring. Saytlar ro'yxati: /help")
        return
    user_id = message.from_user.id if message.from_user else message.chat.id
    if user_id in active_users:
        await message.answer("Oldingi linkingiz hali tayyorlanmoqda. Tugashini kuting.")
        return
    if len(active_users) >= MAX_QUEUE_SIZE:
        await message.answer("Navbat to'lgan. Birozdan keyin qayta yuboring.")
        return
    active_users.add(user_id)
    lock, count = url_locks.get(url, (asyncio.Lock(), 0))
    url_locks[url] = (lock, count + 1)
    status = None
    started = time.monotonic()
    try:
        status = await message.answer("Link qabul qilindi. Navbatda...")
        # Same-link requests reuse the first successful Telegram upload.
        async with lock:
            async with download_slots:
                cached = cache_get(url)
                if cached:
                    await edit_status(status, "Tayyor media yuborilmoqda...")
                    try:
                        await send_media(message, cached)
                    except TelegramBadRequest:
                        cache.pop(url, None)
                        await edit_status(status, "Kesh yangilanmoqda. Linkni qayta yuboring.")
                        return
                else:
                    await edit_status(status, "Rasm/video yuklanmoqda (720p gacha)...")
                    with tempfile.TemporaryDirectory(dir=DOWNLOADS_DIR) as directory:
                        paths = await download_media(url, Path(directory))
                        await edit_status(status, f"{len(paths)} ta media tayyor. Telegramga yuborilmoqda...")
                        uploaded = await send_media(message, [(file_kind(p), FSInputFile(p)) for p in paths])
                        if uploaded:
                            cache_put(url, uploaded)
                await edit_status(status, f"Tayyor! ✅ {time.monotonic() - started:.1f} soniya")
                logging.info("Request completed in %.1fs; cache=%s", time.monotonic() - started, bool(cached))
    except TimeoutError:
        if status:
            await edit_status(status, "Sayt uzoq javob bermadi. Birozdan keyin qayta urinib ko'ring.")
    except MediaError as exc:
        if status:
            await edit_status(status, str(exc))
    except TelegramRetryAfter as exc:
        if status:
            await edit_status(status, f"Telegram cheklovi: {exc.retry_after} soniyadan keyin qayta urinib ko'ring.")
    except Exception as exc:
        logging.error("Request failed: %s", type(exc).__name__)
        if status:
            await edit_status(status, "Yuborishda xatolik. Birozdan keyin qayta urinib ko'ring.")
    finally:
        active_users.discard(user_id)
        current_lock, users = url_locks[url]
        if users == 1:
            del url_locks[url]
        else:
            url_locks[url] = (current_lock, users - 1)


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(".env fayliga BOT_TOKEN=... kiriting.")
    if importlib.util.find_spec("gallery_dl") is None:
        logging.warning("gallery-dl o'rnatilmagan: rasm/albom yuklash cheklangan. pip install -r requirements.txt bajaring.")
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    async with Bot(token=BOT_TOKEN, session=AiohttpSession(timeout=180)) as bot:
        dispatcher = Dispatcher()
        dispatcher.include_router(router)
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types(), close_bot_session=False)


if __name__ == "__main__":
    asyncio.run(main())
