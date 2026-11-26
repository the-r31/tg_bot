"""Bounded downloader processes: cancelling a job also stops its ffmpeg children."""
import asyncio
import logging
import os
import re
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import imageio_ffmpeg

from config import COOKIES_FILE, DOWNLOAD_TIMEOUT, MAX_FILE_SIZE_BYTES, MAX_MEDIA_ITEMS

DOMAINS = (
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com", "twitter.com",
    "t.co", "x.com", "threads.net", "threads.com", "reddit.com", "redd.it",
    "pinterest.com", "pin.it", "facebook.com", "fb.watch", "vimeo.com",
)
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".heic",
              ".mp4", ".webm", ".mkv", ".mov", ".m4v"}
# 1080p portrait is 1080x1920: either dimension may be the short edge.
# '?' also accepts formats whose extractor does not supply dimensions.
VIDEO_FORMAT = (
    "(bv[ext=mp4][width<=?1080][aspect_ratio<=?1]/bv[ext=mp4][height<=?1080][aspect_ratio>=?1])+ba[ext=m4a]/"
    "b[ext=mp4][vcodec!=none][width<=?1080][aspect_ratio<=?1]/"
    "b[ext=mp4][vcodec!=none][height<=?1080][aspect_ratio>=?1]/"
    "(bv[width<=?1080][aspect_ratio<=?1]/bv[height<=?1080][aspect_ratio>=?1])+ba/"
    "b[vcodec!=none][width<=?1080][aspect_ratio<=?1]/"
    "b[vcodec!=none][height<=?1080][aspect_ratio>=?1]"
)
RETRYABLE_ERRORS = {"extraction_failed", "network_error", "download_failed", "timeout"}


class MediaError(Exception):
    pass


class DownloaderError(MediaError):
    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


def error_category(output: bytes) -> str:
    text = output.decode("utf-8", errors="replace").lower()
    if "no module named" in text:
        return "missing_dependency"
    if re.search(r"\b429\b", text) or "too many requests" in text or "rate limit" in text:
        return "rate_limited"
    if re.search(r"\b(?:401|403)\b", text) or any(value in text for value in ("login", "log in", "sign in", "checkpoint")):
        return "access_denied"
    if "requested format is not available" in text:
        return "format_unavailable"
    if any(value in text for value in ("timed out", "timeout", "connection reset", "connection refused", "unable to connect", "name resolution", "getaddrinfo failed")):
        return "network_error"
    if any(value in text for value in ("video not available", "video is unavailable", "video unavailable", "post is unavailable", "not found", "has been removed", "private video")):
        return "unavailable"
    if any(value in text for value in ("unable to extract", "failed to extract", "rehydration", "challenge")):
        return "extraction_failed"
    return "download_failed"


async def read_diagnostics(stream) -> bytes:
    # Drain the pipe to avoid deadlocks; retain only a bounded tail in memory.
    tail = b""
    while chunk := await stream.read(4096):
        tail = (tail + chunk)[-16384:]
    return tail


def folder_size(folder: Path) -> int:
    total = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            # Downloaders rename fragments while the monitor runs.
            continue
    return total


def get_platform_url(text: str) -> str | None:
    for match in re.finditer(r"https?://\S+", text, re.I):
        try:
            parsed = urlsplit(match.group().rstrip(".,!?;:)]}>\"'"))
            host = (parsed.hostname or "").lower().rstrip(".")
            if parsed.username or parsed.password or parsed.port not in (None, 80, 443):
                continue
            if any(host == d or host.endswith("." + d) for d in DOMAINS):
                return urlunsplit((parsed.scheme.lower(), host, parsed.path, parsed.query, ""))
        except ValueError:
            continue
    return None


async def stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(process.pid), "/T", "/F",
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        await killer.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()


async def run_downloader(args: list[str], folder: Path, timeout: float) -> int:
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    # Do not retain cookies, tokens, or signed CDN URLs in logs.
    process = await asyncio.create_subprocess_exec(
        sys.executable, *args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **kwargs,
    )
    diagnostics = asyncio.create_task(read_diagnostics(process.stderr))
    try:
        async with asyncio.timeout(timeout):
            while process.returncode is None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=0.4)
                except TimeoutError:
                    pass
                # Includes fragments and incomplete files, not just final media.
                total = folder_size(folder)
                if total > MAX_FILE_SIZE_BYTES * MAX_MEDIA_ITEMS * 2:
                    raise MediaError("Yuklama juda katta. Qisqaroq video yoki kichikroq albom yuboring.")
            output = await diagnostics
        if process.returncode:
            # Only a fixed category is exposed, never raw URLs, cookies or tokens.
            raise DownloaderError(error_category(output))
        return process.returncode
    finally:
        await stop_process(process)
        diagnostics.cancel()
        await asyncio.gather(diagnostics, return_exceptions=True)


def media_files(folder: Path) -> list[Path]:
    paths = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS
                   and not re.search(r"\.f\d+\.", p.name))
    if any(p.stat().st_size > MAX_FILE_SIZE_BYTES for p in paths):
        raise MediaError("Fayl Telegram hajm limitidan katta. Qisqaroq video yuboring.")
    return [p for p in paths if p.stat().st_size > 0][:MAX_MEDIA_ITEMS]


async def download_media(url: str, folder: Path) -> list[Path]:
    yt = [
        "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--playlist-end", str(MAX_MEDIA_ITEMS),
        "--no-warnings", "--quiet", "--no-progress",
        "--socket-timeout", "20", "--retries", "1", "--extractor-retries", "1",
        "--fragment-retries", "1", "--abort-on-unavailable-fragments",
        "--concurrent-fragments", "4", "--max-filesize", str(MAX_FILE_SIZE_BYTES),
        "--match-filters", "!is_live", "--ffmpeg-location", imageio_ffmpeg.get_ffmpeg_exe(),
        "--merge-output-format", "mp4", "--restrict-filenames",
        "-f", VIDEO_FORMAT,
        "-o", str(folder / "video" / "%(playlist_index)03d-%(id)s.%(ext)s"),
    ]
    gallery = [
        "-m", "gallery_dl", "--config-ignore", "--no-input", "--quiet",
        "--retries", "1", "--http-timeout", "20", "--range", f"1-{MAX_MEDIA_ITEMS}",
        "--directory", str(folder / "gallery"), "--windows-filenames",
        "-o", "extractor.videos=true",
        "-o", "extractor.tiktok.audio=false",
    ]
    if COOKIES_FILE:
        if not Path(COOKIES_FILE).is_file():
            raise MediaError("COOKIES_FILE topilmadi. Bot sozlamasini tekshiring.")
        # Each process gets its own cookie jar; concurrent jobs cannot corrupt it.
        local_cookies = folder / "cookies.txt"
        shutil.copyfile(COOKIES_FILE, local_cookies)
        yt += ["--cookies", str(local_cookies)]
        gallery += ["--cookies", str(local_cookies)]
    host = urlsplit(url).hostname or ""
    is_tiktok = host == "tiktok.com" or host.endswith(".tiktok.com")
    photo_first = host.endswith(("instagram.com", "pinterest.com", "pin.it")) or "/photo/" in url
    strategies = [(gallery, "gallery"), (yt, "video")] if photo_first else [(yt, "video"), (gallery, "gallery")]
    if is_tiktok and not photo_first:
        # TikTok webpage/challenge failures are not retried by --extractor-retries.
        # Use a separate folder so an unsuccessful attempt cannot leak partial media.
        retry = yt.copy()
        retry[retry.index("-o") + 1] = str(folder / "video_retry" / "%(playlist_index)03d-%(id)s.%(ext)s")
        strategies.append((retry, "video_retry"))
    started = time.monotonic()
    failures = []
    video_retryable = False
    for index, (args, subfolder) in enumerate(strategies):
        if subfolder == "video_retry" and (not video_retryable or "rate_limited" in failures):
            continue
        remaining = DOWNLOAD_TIMEOUT - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError
        # Reserve time for the fallback even when the first extractor hangs.
        budget = remaining / (len(strategies) - index)
        attempt_started = time.monotonic()
        try:
            code = await run_downloader(args + [url], folder, budget)
        except (TimeoutError, DownloaderError) as exc:
            category = "timeout" if isinstance(exc, TimeoutError) else exc.category
            failures.append(category)
            if subfolder == "video":
                video_retryable = category in RETRYABLE_ERRORS
            logging.warning("Downloader=%s outcome=%s elapsed=%.1fs", subfolder, category, time.monotonic() - attempt_started)
            continue
        if code != 0:
            # Do not report a failed/partial download as a complete album.
            failures.append("download_failed")
            if subfolder == "video":
                video_retryable = True
            continue
        paths = media_files(folder / subfolder)
        if paths:
            return paths
        failures.append("empty_result")
        logging.warning("Downloader=%s outcome=empty_result elapsed=%.1fs", subfolder, time.monotonic() - attempt_started)
    if "rate_limited" in failures:
        raise MediaError("Sayt so'rovlarni vaqtincha chekladi. Linkni ketma-ket yubormang, keyinroq urinib ko'ring.")
    if "access_denied" in failures:
        raise MediaError("Sayt kirishni rad etdi yoki login talab qilyapti. Bot egasi cookies sozlamasini tekshirishi kerak.")
    if "missing_dependency" in failures:
        raise MediaError("Serverda yuklovchi o'rnatilmagan. Bot egasi requirements.txt paketlarini o'rnatishi kerak.")
    if "timeout" in failures:
        raise TimeoutError
    if "network_error" in failures:
        raise MediaError("Saytga ulanib bo'lmadi. Birozdan keyin linkni qayta yuboring.")
    if "extraction_failed" in failures:
        raise MediaError("Saytdan media ma'lumotini olib bo'lmadi. Birozdan keyin qayta urinib ko'ring. Davom etsa bot egasi yuklovchi paketlarini yangilashi kerak.")
    if "unavailable" in failures:
        raise MediaError("Post mavjud emas yoki yopiq. Linkni brauzerda ochib tekshiring.")
    if "format_unavailable" in failures:
        raise MediaError("Bu post uchun mos video format topilmadi. Rasmli post bo'lsa, bot egasi gallery-dl paketini tekshirishi kerak.")
    raise MediaError(
        "Media olinmadi. Alohida post yoki video linkini yuboring. Post o'chirilgan, yopiq, "
        "hajmi katta yoki sayt login talab qilayotgan bo'lishi mumkin."
    )
