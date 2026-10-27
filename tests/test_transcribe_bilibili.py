from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import transcribe_bilibili


class BilibiliCliTests(unittest.TestCase):
    def test_bv_id_is_normalized_to_video_url(self) -> None:
        self.assertEqual(
            transcribe_bilibili.normalize_video_input("BV1wd4y1x7Q3"),
            "https://www.bilibili.com/video/BV1wd4y1x7Q3/",
        )

    def test_non_url_non_bv_input_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Bilibili URL or a BV ID"):
            transcribe_bilibili.normalize_video_input("not-a-video")

    def test_yt_dlp_options_never_request_subtitles(self) -> None:
        cookies = Path("/tmp/example-cookies.txt")
        metadata = transcribe_bilibili.build_ydl_options(cookies)
        download = transcribe_bilibili.build_ydl_options(
            cookies, output_template="/tmp/%(id)s.%(ext)s"
        )
        for options in (metadata, download):
            self.assertFalse(options["writesubtitles"])
            self.assertFalse(options["writeautomaticsub"])
            self.assertEqual(options["cookiefile"], str(cookies))
        self.assertTrue(metadata["skip_download"])
        self.assertTrue(callable(download["format"]))
        self.assertEqual(download["http_chunk_size"], 512 * 1024)
        self.assertEqual(download["retries"], 20)

    def test_audio_selector_adds_range_header_required_by_bilibili_cdn(self) -> None:
        selected = transcribe_bilibili.select_bilibili_audio(
            {
                "formats": [
                    {
                        "format_id": "30232",
                        "url": "https://upos-hz-mirrorakam.akamaized.net/audio.m4s",
                        "vcodec": "none",
                        "acodec": "mp4a.40.2",
                        "abr": 128,
                    },
                    {
                        "format_id": "30280",
                        "url": "https://upos-sz-mirrorcosov.bilivideo.com/audio.m4s",
                        "vcodec": "none",
                        "acodec": "mp4a.40.2",
                        "abr": 192,
                    },
                ]
            }
        )

        self.assertEqual(selected[0]["format_id"], "30280")
        self.assertEqual(selected[0]["http_headers"]["Range"], "bytes=0-")

    def test_cli_values_override_local_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            configured_output = root / "configured"
            command_output = root / "command"
            cookies = root / "cookies.txt"
            cookies.write_text("placeholder", encoding="utf-8")
            deps = root / "deps"
            deps.mkdir()
            config = {
                "output_root": str(configured_output),
                "cookies_file": str(cookies),
                "deps_dir": str(deps),
                "model": "base",
            }
            args = transcribe_bilibili.build_parser().parse_args(
                [
                    "BV1wd4y1x7Q3",
                    "--output-root",
                    str(command_output),
                    "--model",
                    "small",
                ]
            )

            settings = transcribe_bilibili.resolve_settings(args, config)

            self.assertEqual(settings.output_root, command_output.resolve())
            self.assertEqual(settings.cookies_file, cookies.resolve())
            self.assertEqual(settings.deps_dir, deps.resolve())
            self.assertEqual(settings.model, "small")

    def test_unknown_config_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.json"
            config.write_text('{"secret": "value"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown config keys"):
                transcribe_bilibili.load_config(config)

    def test_metadata_is_normalized(self) -> None:
        metadata = transcribe_bilibili.parse_metadata(
            {
                "id": "BV1wd4y1x7Q3",
                "title": "测试视频",
                "uploader": "测试UP主",
                "upload_date": "20260911",
                "duration": 42.5,
                "webpage_url": "https://www.bilibili.com/video/BV1wd4y1x7Q3/",
            },
            "https://www.bilibili.com/video/BV1wd4y1x7Q3/",
        )
        self.assertEqual(metadata.upload_date, "2026-09-11")
        self.assertEqual(metadata.video_id, "BV1wd4y1x7Q3")
        self.assertEqual(metadata.duration, 42.5)

    def test_pipeline_passes_cookie_to_audio_only_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cookies = root / "cookies.txt"
            cookies.write_text("placeholder", encoding="utf-8")
            calls = []
            info = {
                "id": "BV1wd4y1x7Q3",
                "title": "测试视频",
                "uploader": "测试UP主",
                "upload_date": "20260911",
                "duration": 42.5,
                "webpage_url": "https://www.bilibili.com/video/BV1wd4y1x7Q3/",
            }

            class FakeDownloader:
                def __init__(self, options):
                    self.options = options
                    calls.append(options)

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def extract_info(self, _url, download):
                    if download:
                        target = Path(self.options["outtmpl"].replace("%(ext)s", "m4a"))
                        target.write_bytes(b"audio")
                    return info

            class FakeDownloadError(Exception):
                pass

            class FakeYtDlp:
                YoutubeDL = FakeDownloader

                class utils:
                    DownloadError = FakeDownloadError

            output_paths = transcribe_bilibili.transcribe_media.OutputPaths(
                raw_markdown=root / "raw.md",
                reading_markdown=root / "reading.md",
                mobile_html=root / "mobile.html",
                project_index=root / "README.md",
            )
            with mock.patch.object(
                transcribe_bilibili, "import_yt_dlp", return_value=FakeYtDlp
            ), mock.patch.object(
                transcribe_bilibili.transcribe_media,
                "run_transcription",
                return_value=output_paths,
            ) as transcribe:
                result = transcribe_bilibili.run(
                    [
                        "BV1wd4y1x7Q3",
                        "--config",
                        str(root / "missing-config.json"),
                        "--cookies",
                        str(cookies),
                        "--output-root",
                        str(root),
                    ]
                )

            self.assertEqual(result, output_paths)
            self.assertEqual(len(calls), 2)
            for options in calls:
                self.assertEqual(options["cookiefile"], str(cookies.resolve()))
                self.assertFalse(options["writesubtitles"])
                self.assertFalse(options["writeautomaticsub"])
            self.assertNotIn("format", calls[0])
            self.assertTrue(callable(calls[1]["format"]))
            transcribe_args = transcribe.call_args.args[0]
            self.assertIsNone(transcribe_args.input_subtitle)
            self.assertEqual(transcribe_args.input_audio.suffix, ".m4a")


if __name__ == "__main__":
    unittest.main()
