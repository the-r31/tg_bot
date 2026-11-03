import os
from pathlib import Path
from dotenv import load_dotenv

# .env faylini yuklash
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
def setting(name: str, default: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not 1 <= value <= maximum:
        raise ValueError(f"{name}: 1..{maximum} oralig'ida bo'lishi kerak")
    return value


MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "49"))
if MAX_FILE_SIZE_MB < 1:
    raise ValueError("MAX_FILE_SIZE_MB musbat bo'lishi kerak")
MAX_FILE_SIZE_MB = min(MAX_FILE_SIZE_MB, 49)
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# Fayllarni vaqtinchalik saqlash jildi
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
MAX_CONCURRENT_DOWNLOADS = setting("MAX_CONCURRENT_DOWNLOADS", 3, 10)
MAX_QUEUE_SIZE = setting("MAX_QUEUE_SIZE", 20, 100)
DOWNLOAD_TIMEOUT = setting("DOWNLOAD_TIMEOUT", 180, 600)
MAX_MEDIA_ITEMS = setting("MAX_MEDIA_ITEMS", 10, 20)
COOKIES_FILE = os.getenv("COOKIES_FILE", "").strip()
if COOKIES_FILE:
    COOKIES_FILE = str((BASE_DIR / COOKIES_FILE).resolve())
