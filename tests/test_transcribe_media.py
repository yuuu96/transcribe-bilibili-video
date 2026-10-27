from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import transcribe_media


class TranscriptFormattingTests(unittest.TestCase):
    def test_safe_name_replaces_only_forbidden_path_characters(self) -> None:
        self.assertEqual(transcribe_media.safe_name(' 标题:/?*"<>| '), "标题________")

    def test_sparse_punctuation_is_restored_per_paragraph(self) -> None:
        entries = [
            transcribe_media.TranscriptEntry(0, 45, "这是一段，已有逗号的中文内容" * 8),
            transcribe_media.TranscriptEntry(45, 60, "下一句话"),
        ]
        seen: list[str] = []

        def punctuate(text: str) -> str:
            seen.append(text)
            return text + ",结束!"

        sections = transcribe_media.make_sections(
            entries,
            duration=60,
            section_seconds=300,
            paragraph_seconds=40,
            max_chars=360,
            punctuate=punctuate,
        )
        self.assertEqual(len(seen), 2)
        self.assertTrue(all("，" not in text for text in seen))
        self.assertIn("，结束！", sections[0][2][0][1])

    def test_light_clean_verbatim_is_conservative(self) -> None:
        source = (
            "呃，我我觉得我觉得这个这个这个方案真的真的可以，"
            "所有有价值的信息都保留，对吧？好 拜拜"
        )

        cleaned = transcribe_media.light_clean_verbatim(source)

        self.assertEqual(
            cleaned,
            "我觉得这个这个方案真的真的可以，所有有价值的信息都保留，"
            "对吧？好，拜拜",
        )
        self.assertEqual(
            transcribe_media.light_clean_verbatim("嗯，我同意"),
            "嗯，我同意",
        )
        self.assertEqual(
            transcribe_media.light_clean_verbatim("你看你看，这很重要"),
            "你看你看，这很重要",
        )

    def test_asr_boundaries_add_conservative_punctuation(self) -> None:
        entries = [
            transcribe_media.TranscriptEntry(0, 2, "这是第一个问题吗"),
            transcribe_media.TranscriptEntry(2, 4, "这是回答"),
            transcribe_media.TranscriptEntry(5, 7, "但是还要继续分析"),
            transcribe_media.TranscriptEntry(10, 12, "最后一句"),
        ]

        joined = transcribe_media.join_reading_entries(entries)

        self.assertEqual(
            joined,
            "这是第一个问题吗？这是回答，但是还要继续分析。最后一句",
        )

    def test_punctuation_inference_can_be_disabled(self) -> None:
        entries = [
            transcribe_media.TranscriptEntry(0, 2, "第一句 保留空格"),
            transcribe_media.TranscriptEntry(2, 4, "第二句"),
        ]

        sections = transcribe_media.make_sections(
            entries,
            duration=4,
            section_seconds=300,
            paragraph_seconds=35,
            max_chars=260,
            infer_punctuation=False,
        )

        self.assertEqual(sections[0][2][0][1], "第一句 保留空格第二句")

    def test_semantic_paragraphs_preserve_order_and_timestamps(self) -> None:
        entries = [
            transcribe_media.TranscriptEntry(
                10,
                20,
                "第一句话讲清楚背景。第二句话继续分析原因。",
            ),
            transcribe_media.TranscriptEntry(
                20,
                30,
                "第三句话提出问题？第四句话保留情绪！",
            ),
        ]

        sections = transcribe_media.make_sections(
            entries,
            duration=30,
            section_seconds=300,
            paragraph_seconds=60,
            max_chars=24,
        )
        paragraphs = sections[0][2]

        self.assertGreater(len(paragraphs), 1)
        self.assertEqual(paragraphs[0][0], 10)
        self.assertEqual(
            "".join(text for _timestamp, text in paragraphs),
            "第一句话讲清楚背景。第二句话继续分析原因。"
            "第三句话提出问题？第四句话保留情绪！",
        )
        self.assertEqual(
            [timestamp for timestamp, _text in paragraphs],
            sorted(timestamp for timestamp, _text in paragraphs),
        )

    def test_subtitle_source_requires_explicit_opt_in(self) -> None:
        args = argparse.Namespace(input_subtitle=Path("captions.vtt"), allow_subtitle_source=False)
        with self.assertRaisesRegex(ValueError, "subtitle input is disabled"):
            transcribe_media.run_transcription(args)

    def test_explicit_caption_fixture_generates_flat_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subtitle = root / "captions.vtt"
            subtitle.write_text(
                "WEBVTT\n\n"
                "00:00:00.000 --> 00:00:02.000\n"
                "嗯，我我觉得我觉得第一句,测试\n\n"
                "00:00:02.000 --> 00:00:05.000\n第二句没有标点\n",
                encoding="utf-8",
            )
            project = root / "result"
            args = argparse.Namespace(
                input_audio=None,
                input_subtitle=subtitle,
                allow_subtitle_source=True,
                project_dir=project,
                title="测试标题",
                upload_date="2026-09-11",
                video_id="BV1wd4y1x7Q3",
                uploader="测试UP主",
                source_url="https://www.bilibili.com/video/BV1wd4y1x7Q3/",
                duration=5.0,
                deps_dir=None,
                ffmpeg=None,
                punctuation_model=None,
                punctuation_threads=2,
                no_punctuation_restoration=True,
                section_seconds=300,
                paragraph_seconds=50,
                max_paragraph_chars=360,
                keep_checkpoints=False,
            )

            outputs = transcribe_media.run_transcription(args)

            self.assertTrue(outputs.raw_markdown.is_file())
            self.assertTrue(outputs.reading_markdown.is_file())
            self.assertTrue(outputs.mobile_html.is_file())
            self.assertTrue(outputs.project_index.is_file())
            self.assertFalse(any(path.is_dir() for path in project.iterdir()))
            raw = outputs.raw_markdown.read_text(encoding="utf-8")
            reading = outputs.reading_markdown.read_text(encoding="utf-8")
            self.assertIn("嗯，我我觉得我觉得第一句,测试", raw)
            self.assertIn("第一句，测试", reading)
            self.assertNotIn("我我觉得我觉得", reading)
            index = outputs.project_index.read_text(encoding="utf-8")
            self.assertIn("可读性整理稿（默认阅读）", index)
            self.assertIn("width=device-width", outputs.mobile_html.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
