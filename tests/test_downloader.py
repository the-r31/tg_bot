import unittest

from yt_dlp import YoutubeDL

import downloader


def video_format(format_id="video", *, width=None, height=None, audio=True, ext="mp4"):
    return {
        "format_id": format_id,
        "url": f"https://example.com/{format_id}.{ext}",
        "ext": ext,
        "vcodec": "h264" if ext == "mp4" else "vp9",
        "acodec": "aac" if audio else "none",
        "width": width,
        "height": height,
        # yt-dlp normalizes this field before sorting and selecting formats.
        "aspect_ratio": round(width / height, 2) if width and height else None,
    }


def audio_format():
    return {
        "format_id": "audio",
        "url": "https://example.com/audio.m4a",
        "ext": "m4a",
        "vcodec": "none",
        "acodec": "aac",
    }


class VideoFormatTests(unittest.TestCase):
    def select(self, formats):
        # Use yt-dlp's real selector without probing/downloading media or invoking ffmpeg.
        with YoutubeDL({"quiet": True, "check_formats": False, "format": downloader.VIDEO_FORMAT}) as ydl:
            ydl.sort_formats({"formats": formats})
            selector = ydl.build_format_selector(downloader.VIDEO_FORMAT)
            return ydl._select_formats(formats, selector)

    def test_portrait_and_landscape_up_to_1080p_are_available(self):
        for width, height in [(720, 1280), (1080, 1920), (1920, 1080)]:
            with self.subTest(width=width, height=height):
                result = self.select([video_format(width=width, height=height)])
                self.assertEqual([f["format_id"] for f in result], ["video"])

    def test_missing_dimensions_do_not_hide_playable_video(self):
        for dimensions in [{}, {"width": 1080}, {"height": 1080}]:
            with self.subTest(dimensions=dimensions):
                self.assertEqual(len(self.select([video_format(**dimensions)])), 1)

    def test_highest_quality_within_limit_wins_for_both_orientations(self):
        for portrait in [True, False]:
            for split in [True, False]:
                with self.subTest(portrait=portrait, split=split):
                    formats = [audio_format()] if split else []
                    for short_edge, long_edge in [(1080, 1920), (1440, 2560), (540, 960), (720, 1280)]:
                        width, height = (short_edge, long_edge) if portrait else (long_edge, short_edge)
                        formats.append(video_format(
                            str(short_edge), width=width, height=height, audio=not split,
                        ))
                    result = self.select(formats)
                    self.assertEqual(len(result), 1)
                    selected_video = result[0]["requested_formats"][0] if split else result[0]
                    self.assertEqual(selected_video["format_id"], "1080")

    def test_known_resolution_over_limit_is_rejected(self):
        for width, height in [(2560, 1440), (1440, 2560), (1440, 1440)]:
            with self.subTest(width=width, height=height):
                self.assertEqual(self.select([video_format(width=width, height=height)]), [])
                self.assertEqual(self.select([
                    audio_format(), video_format(width=width, height=height, audio=False),
                ]), [])

    def test_audio_only_is_not_reported_as_a_video(self):
        self.assertEqual(self.select([audio_format()]), [])

    def test_split_portrait_video_is_combined_with_audio(self):
        result = self.select([
            audio_format(), video_format(width=1080, height=1920, audio=False),
        ])
        self.assertEqual(len(result), 1)
        self.assertEqual([f["format_id"] for f in result[0]["requested_formats"]], ["video", "audio"])

    def test_combined_video_does_not_require_a_separate_audio_stream(self):
        result = self.select([video_format(width=1080, height=1920)])
        self.assertEqual(len(result), 1)
        self.assertNotIn("requested_formats", result[0])

    def test_non_mp4_portrait_remains_available_as_fallback(self):
        result = self.select([video_format(width=1080, height=1920, ext="webm")])
        self.assertEqual([f["format_id"] for f in result], ["video"])


class ErrorCategoryTests(unittest.TestCase):
    def test_actionable_extractor_failures(self):
        cases = [
            ("ERROR: Requested format is not available. Use --list-formats", "format_unavailable"),
            ("ERROR: Unable to extract universal data for rehydration", "extraction_failed"),
            ("ERROR: Unable to extract challenge data", "extraction_failed"),
            ("ERROR: Unable to download webpage: Connection reset by peer", "network_error"),
            ("ERROR: Unable to download webpage: The read operation timed out", "network_error"),
            ("ERROR: Video not available, status code 10217", "unavailable"),
            ("ERROR: Video is unavailable", "unavailable"),
        ]
        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(downloader.error_category(message.encode()), expected)

    def test_numeric_video_ids_are_not_http_errors(self):
        for video_id in ["7403123456789012345", "7429123456789012345", "7401123456789012345"]:
            with self.subTest(video_id=video_id):
                message = f"ERROR: [TikTok] {video_id}: Unable to extract universal data for rehydration"
                self.assertEqual(downloader.error_category(message.encode()), "extraction_failed")

    def test_http_statuses_still_have_specific_categories(self):
        for code, expected in [(401, "access_denied"), (403, "access_denied"), (429, "rate_limited")]:
            with self.subTest(code=code):
                self.assertEqual(downloader.error_category(f"ERROR: HTTP Error {code}: failed".encode()), expected)


if __name__ == "__main__":
    unittest.main()
