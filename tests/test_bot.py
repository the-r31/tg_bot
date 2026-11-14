import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import downloader
import main


def sent(kind="video", file_id="telegram-id"):
    result = SimpleNamespace(photo=None, video=None, document=None)
    setattr(result, kind, [SimpleNamespace(file_id=file_id)] if kind == "photo" else SimpleNamespace(file_id=file_id))
    return result


class UnitTests(unittest.TestCase):
    def tearDown(self):
        main.cache.clear()

    def test_urls(self):
        for url in ["https://youtube.com.evil.org/x", "https://evil-youtube.com/x",
                    "https://user:pass@youtube.com/x", "https://youtube.com:123/x", "http://127.0.0.1/x"]:
            self.assertIsNone(downloader.get_platform_url(url))
        self.assertEqual(downloader.get_platform_url("https://example.com https://youtu.be/abc)."), "https://youtu.be/abc")
        self.assertEqual(downloader.get_platform_url("HTTPS://WWW.TIKTOK.COM/a#frag"), "https://www.tiktok.com/a")

    def test_safe_error_categories(self):
        self.assertEqual(downloader.error_category(b"HTTP 429 secret-url"), "rate_limited")
        self.assertEqual(downloader.error_category(b"Redirect to login"), "access_denied")
        self.assertEqual(downloader.error_category(b"No module named gallery_dl"), "missing_dependency")
        self.assertEqual(downloader.error_category(b"private signed URL"), "download_failed")

    def test_cache_expiry_and_capacity(self):
        with patch.object(main, "CACHE_SIZE", 2):
            for url in ["a", "b", "c"]:
                main.cache_put(url, [("video", url)])
            self.assertIsNone(main.cache_get("a"))
            self.assertEqual(main.cache_get("b"), [("video", "b")])
            with patch.object(main.time, "monotonic", return_value=10**15):
                self.assertIsNone(main.cache_get("b"))

    def test_incomplete_and_large_files(self):
        with tempfile.TemporaryDirectory(dir=main.DOWNLOADS_DIR) as name:
            folder = Path(name)
            for filename in ["clip.mp4.part", "clip.f137.mp4", "empty.mp4"]:
                (folder / filename).touch()
            self.assertEqual(downloader.media_files(folder), [])
            (folder / "photo.jpg").write_bytes(b"abc")
            with patch.object(downloader, "MAX_FILE_SIZE_BYTES", 2):
                with self.assertRaises(downloader.MediaError):
                    downloader.media_files(folder)


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main.cache.clear()
        main.active_users.clear()
        main.url_locks.clear()
        main.download_slots = asyncio.Semaphore(3)

    async def test_album_and_single_media(self):
        message = SimpleNamespace(
            answer_media_group=AsyncMock(return_value=[sent("photo"), sent()]),
            answer_video=AsyncMock(return_value=sent()),
            answer_document=AsyncMock(return_value=sent("document")),
        )
        result = await main.send_media(message, [("photo", "p"), ("video", "v"), ("document", "d")])
        self.assertEqual([kind for kind, _ in result], ["photo", "video", "document"])
        message.answer_media_group.assert_awaited_once()
        message.answer_document.assert_awaited_once()

    async def test_fallback_after_partial_failure(self):
        async def fake_run(args, folder, timeout):
            target = folder / ("video" if "yt_dlp" in args else "gallery")
            target.mkdir()
            (target / "image.jpg").write_bytes(b"media")
            return 1 if "yt_dlp" in args else 0
        with tempfile.TemporaryDirectory(dir=main.DOWNLOADS_DIR) as name, patch.object(downloader, "run_downloader", side_effect=fake_run), patch.object(downloader, "COOKIES_FILE", ""):
            paths = await downloader.download_media("https://youtu.be/test", Path(name))
            self.assertEqual(paths[0].parent.name, "gallery")

    async def test_failure_cleans_user_and_lock(self):
        status = SimpleNamespace(edit_text=AsyncMock())
        message = SimpleNamespace(text="https://youtu.be/test", from_user=SimpleNamespace(id=1), answer=AsyncMock(return_value=status))
        with patch.object(main, "download_media", side_effect=TimeoutError):
            await main.handle_link(message)
        self.assertFalse(main.active_users)
        self.assertFalse(main.url_locks)
        self.assertIn("uzoq", status.edit_text.call_args.args[0])

    async def test_timeout_still_tries_fallback(self):
        budgets = []
        async def fake_run(args, folder, timeout):
            budgets.append(timeout)
            if "gallery_dl" in args:
                raise TimeoutError
            target = folder / "video"
            target.mkdir()
            (target / "clip.mp4").write_bytes(b"video")
            return 0
        with tempfile.TemporaryDirectory(dir=main.DOWNLOADS_DIR) as name, patch.object(downloader, "run_downloader", side_effect=fake_run), patch.object(downloader, "COOKIES_FILE", ""):
            paths = await downloader.download_media("https://www.instagram.com/p/test/", Path(name))
        self.assertEqual(paths[0].name, "clip.mp4")
        self.assertEqual(len(budgets), 2)
        self.assertLessEqual(budgets[0], downloader.DOWNLOAD_TIMEOUT / 2)

    async def test_access_error_is_actionable(self):
        with tempfile.TemporaryDirectory(dir=main.DOWNLOADS_DIR) as name, patch.object(downloader, "run_downloader", side_effect=downloader.DownloaderError("access_denied")), patch.object(downloader, "COOKIES_FILE", ""):
            with self.assertRaisesRegex(downloader.MediaError, "login"):
                await downloader.download_media("https://www.instagram.com/p/test/", Path(name))

    async def test_diagnostics_are_bounded(self):
        stream = asyncio.StreamReader()
        stream.feed_data(b"x" * 50000 + b"HTTP 429")
        stream.feed_eof()
        result = await downloader.read_diagnostics(stream)
        self.assertEqual(len(result), 16384)
        self.assertEqual(downloader.error_category(result), "rate_limited")

    async def test_same_link_downloaded_once(self):
        async def fake_download(url, folder):
            await asyncio.sleep(0.02)
            path = folder / "video.mp4"
            path.write_bytes(b"video")
            return [path]
        def message(user):
            return SimpleNamespace(text="https://youtu.be/test", from_user=SimpleNamespace(id=user),
                                   answer=AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())),
                                   answer_video=AsyncMock(return_value=sent()))
        first, second = message(1), message(2)
        with patch.object(main, "download_media", side_effect=fake_download) as download:
            await asyncio.gather(main.handle_link(first), main.handle_link(second))
            self.assertEqual(download.await_count, 1)
        self.assertEqual(second.answer_video.call_args.args[0], "telegram-id")
        for item in (first, second):
            self.assertEqual(item.answer_video.call_args.kwargs["caption"], "https://youtu.be/test")
            item.answer.return_value.delete.assert_awaited_once()
        self.assertFalse(main.active_users)
        self.assertFalse(main.url_locks)

    async def test_timeout_stops_process(self):
        process = SimpleNamespace(returncode=None)
        process.stderr = asyncio.StreamReader()
        process.stderr.feed_eof()
        async def wait():
            await asyncio.sleep(10)
        process.wait = wait
        with tempfile.TemporaryDirectory(dir=main.DOWNLOADS_DIR) as name, patch.object(downloader.asyncio, "create_subprocess_exec", return_value=process), patch.object(downloader, "stop_process", new_callable=AsyncMock) as stop:
            with self.assertRaises(TimeoutError):
                await downloader.run_downloader(["ignored"], Path(name), 0.02)
            stop.assert_awaited_once_with(process)


if __name__ == "__main__":
    unittest.main()
