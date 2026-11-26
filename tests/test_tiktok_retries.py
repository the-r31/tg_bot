import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import downloader
from config import DOWNLOADS_DIR


class TikTokRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch.object(downloader.imageio_ffmpeg, "get_ffmpeg_exe", return_value="ffmpeg"))
        self.enterContext(patch.object(downloader, "COOKIES_FILE", ""))
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(dir=DOWNLOADS_DIR)))

    async def test_transient_failure_retries_without_returning_old_partial_files(self):
        attempts = []

        async def run(args, folder, timeout):
            attempts.append(args)
            if len(attempts) == 1:
                target = folder / "video"
                target.mkdir()
                (target / "partial.mp4").write_bytes(b"partial album")
                raise downloader.DownloaderError("extraction_failed")
            if len(attempts) == 2:
                target = folder / "gallery"
                target.mkdir()
                (target / "partial.jpg").write_bytes(b"partial album")
                raise downloader.DownloaderError("network_error")
            target = Path(args[args.index("-o") + 1]).parent
            self.assertEqual(target.name, "video_retry")
            target.mkdir()
            (target / "complete.mp4").write_bytes(b"complete video")
            return 0

        with patch.object(downloader, "run_downloader", side_effect=run):
            files = await downloader.download_media("https://vt.tiktok.com/example/", self.folder)
        self.assertEqual(files, [self.folder / "video_retry" / "complete.mp4"])
        self.assertEqual(len(attempts), 3)
        self.assertIn("yt_dlp", attempts[0])
        self.assertIn("gallery_dl", attempts[1])
        self.assertIn("yt_dlp", attempts[2])

    async def test_rate_limited_failures_do_not_trigger_extra_retry(self):
        with patch.object(downloader, "run_downloader", side_effect=downloader.DownloaderError("rate_limited")) as run:
            with self.assertRaisesRegex(downloader.MediaError, "vaqtincha chekladi"):
                await downloader.download_media("https://vt.tiktok.com/example/", self.folder)
        self.assertEqual(run.await_count, 2)

    async def test_gallery_transient_failure_does_not_retry_denied_video(self):
        with patch.object(downloader, "run_downloader", side_effect=[
            downloader.DownloaderError("access_denied"),
            downloader.DownloaderError("extraction_failed"),
        ]) as run:
            with self.assertRaisesRegex(downloader.MediaError, "kirishni rad etdi"):
                await downloader.download_media("https://vt.tiktok.com/example/", self.folder)
        self.assertEqual(run.await_count, 2)

    async def test_gallery_rate_limit_suppresses_retry_of_transient_video_failure(self):
        with patch.object(downloader, "run_downloader", side_effect=[
            downloader.DownloaderError("extraction_failed"),
            downloader.DownloaderError("rate_limited"),
        ]) as run:
            with self.assertRaisesRegex(downloader.MediaError, "vaqtincha chekladi"):
                await downloader.download_media("https://vt.tiktok.com/example/", self.folder)
        self.assertEqual(run.await_count, 2)

    async def test_timeouts_reserve_budget_for_all_attempts(self):
        now = [100.0]
        budgets = []

        async def run(args, folder, timeout):
            budgets.append(timeout)
            now[0] += timeout
            raise TimeoutError

        with patch.object(downloader, "time", SimpleNamespace(monotonic=lambda: now[0])), \
                patch.object(downloader, "DOWNLOAD_TIMEOUT", 180), \
                patch.object(downloader, "run_downloader", side_effect=run):
            with self.assertRaises(TimeoutError):
                await downloader.download_media("https://vt.tiktok.com/example/", self.folder)
        self.assertEqual(budgets, [60, 60, 60])
        self.assertEqual(sum(budgets), 180)

    async def test_photo_link_uses_gallery_without_extra_audio(self):
        async def run(args, folder, timeout):
            self.assertIn("gallery_dl", args)
            self.assertIn("extractor.tiktok.audio=false", args)
            target = folder / "gallery"
            target.mkdir()
            (target / "photo.jpg").write_bytes(b"photo")
            return 0

        with patch.object(downloader, "run_downloader", side_effect=run) as runner:
            files = await downloader.download_media("https://www.tiktok.com/@someone/photo/123", self.folder)
        self.assertEqual(files, [self.folder / "gallery" / "photo.jpg"])
        runner.assert_awaited_once()

    async def test_short_photo_link_can_succeed_with_gallery_fallback(self):
        async def run(args, folder, timeout):
            if "yt_dlp" in args:
                raise downloader.DownloaderError("format_unavailable")
            target = folder / "gallery"
            target.mkdir()
            (target / "photo.jpg").write_bytes(b"photo")
            return 0

        with patch.object(downloader, "run_downloader", side_effect=run) as runner:
            files = await downloader.download_media("https://vm.tiktok.com/example/", self.folder)
        self.assertEqual(files, [self.folder / "gallery" / "photo.jpg"])
        self.assertEqual(runner.await_count, 2)

    async def test_non_tiktok_keeps_two_attempts(self):
        with patch.object(downloader, "run_downloader", side_effect=downloader.DownloaderError("extraction_failed")) as run:
            with self.assertRaises(downloader.MediaError):
                await downloader.download_media("https://youtu.be/example", self.folder)
        self.assertEqual(run.await_count, 2)

    async def test_cancellation_propagates_without_retry(self):
        with patch.object(downloader, "run_downloader", side_effect=asyncio.CancelledError) as run:
            with self.assertRaises(asyncio.CancelledError):
                await downloader.download_media("https://vt.tiktok.com/example/", self.folder)
        run.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
