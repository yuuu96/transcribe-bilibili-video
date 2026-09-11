#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import asdict, dataclass
import html
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


@dataclass(frozen=True)
class TranscriptEntry:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class OutputPaths:
    raw_markdown: Path
    reading_markdown: Path
    mobile_html: Path
    project_index: Path


PUNCTUATION = set("。！？；：，、,.!?;:")
SKILL_ROOT = Path(__file__).resolve().parent.parent


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f/:*?\"<>|]", "_", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).rstrip(". ")
    return cleaned or "B站视频"


def bootstrap_dependencies(project_dir: Path, explicit: Path | None) -> None:
    candidates = []
    if explicit:
        candidates.append(explicit)
    candidates.extend(
        [
            project_dir.parent / ".codex_tmp_deps",
            Path.cwd() / ".codex_tmp_deps",
        ]
    )
    for candidate in candidates:
        if candidate.is_dir():
            sys.path.insert(0, str(candidate))
            return


def resolve_ffmpeg(project_dir: Path, deps_dir: Path | None, explicit: Path | None) -> Path:
    if explicit:
        if explicit.is_file():
            return explicit
        raise FileNotFoundError(f"ffmpeg not found: {explicit}")

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return Path(system_ffmpeg)

    try:
        import imageio_ffmpeg

        packaged_ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if packaged_ffmpeg.is_file():
            return packaged_ffmpeg
    except (ImportError, RuntimeError):
        pass

    roots = []
    if deps_dir:
        roots.append(deps_dir)
    roots.extend([project_dir.parent / ".codex_tmp_deps", Path.cwd() / ".codex_tmp_deps"])
    for root in roots:
        binaries = root / "imageio_ffmpeg" / "binaries"
        if not binaries.is_dir():
            continue
        matches = sorted(binaries.glob("ffmpeg-*"))
        if matches:
            return matches[0]
    raise FileNotFoundError("ffmpeg is unavailable; install it or pass --ffmpeg")


def probe_duration(ffmpeg: Path, media: Path) -> float:
    result = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-i", str(media)],
        check=False,
        capture_output=True,
        text=True,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        raise RuntimeError(f"could not determine duration for {media}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_timestamp(value: str) -> float:
    normalized = value.strip().replace(",", ".")
    parts = normalized.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = "0"
        minutes, seconds = parts
    else:
        raise ValueError(f"invalid timestamp: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def clean_caption_text(lines: list[str]) -> str:
    value = " ".join(line.strip() for line in lines if line.strip())
    value = re.sub(r"<v[^>]*>", "", value, flags=re.IGNORECASE)
    value = re.sub(r"</v>", "", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    return " ".join(value.split())


def parse_subtitle(path: Path) -> list[TranscriptEntry]:
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    entries: list[TranscriptEntry] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue

        start_raw, end_raw = line.split("-->", 1)
        end_raw = end_raw.strip().split()[0]
        start = parse_timestamp(start_raw)
        end = parse_timestamp(end_raw)
        index += 1

        text_lines = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index])
            index += 1
        text = clean_caption_text(text_lines)
        if text and (not entries or text != entries[-1].text):
            entries.append(TranscriptEntry(start=start, end=end, text=text))
        index += 1

    if not entries:
        raise RuntimeError(f"no captions found in {path}")
    return entries


def create_wav_chunk(
    ffmpeg: Path,
    input_audio: Path,
    output_wav: Path,
    start: int,
    duration: int,
) -> None:
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(ffmpeg),
            "-nostdin",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-i",
            str(input_audio),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_wav),
        ],
        check=True,
    )


def load_chunk(path: Path) -> list[TranscriptEntry]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [TranscriptEntry(**item) for item in payload["entries"]]


def chunk_is_valid(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return isinstance(payload.get("entries"), list)
    except (OSError, json.JSONDecodeError):
        return False


def transcribe_audio(
    args: argparse.Namespace,
    ffmpeg: Path,
) -> tuple[list[TranscriptEntry], float, list[Path]]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper is unavailable; install it or pass the correct --deps-dir"
        ) from exc

    input_audio = args.input_audio.resolve()
    duration = args.duration or probe_duration(ffmpeg, input_audio)
    total_chunks = math.ceil(duration / args.chunk_seconds)
    checkpoint_tag = re.sub(r"[^A-Za-z0-9_-]", "_", args.video_id or "video")
    checkpoint_pattern = f".{checkpoint_tag}_transcribe_chunk_*.json"
    wav_dir = Path(tempfile.gettempdir()) / f"bilibili-transcribe-{checkpoint_tag}"

    model = WhisperModel(
        args.model,
        device=args.device,
        compute_type=args.compute_type,
    )

    for chunk_index in range(total_chunks):
        offset = chunk_index * args.chunk_seconds
        stem = f"chunk_{chunk_index:03d}_{format_time(offset).replace(':', '-')}"
        wav_path = wav_dir / f"{stem}.wav"
        json_path = args.project_dir / f".{checkpoint_tag}_transcribe_{stem}.json"

        if chunk_is_valid(json_path):
            print(f"skip {chunk_index + 1}/{total_chunks}: {json_path.name}", flush=True)
            continue

        print(f"prepare {chunk_index + 1}/{total_chunks}: {wav_path.name}", flush=True)
        create_wav_chunk(
            ffmpeg,
            input_audio,
            wav_path,
            offset,
            min(args.chunk_seconds, max(1, math.ceil(duration - offset))),
        )
        print(f"transcribe {chunk_index + 1}/{total_chunks}", flush=True)
        segments, info = model.transcribe(
            str(wav_path),
            language=args.language,
            beam_size=args.beam_size,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 700},
            initial_prompt=args.initial_prompt,
        )

        entries = []
        for segment in segments:
            text = " ".join(segment.text.replace("\ufffd", "").strip().split())
            if not text:
                continue
            entries.append(
                TranscriptEntry(
                    start=offset + segment.start,
                    end=offset + segment.end,
                    text=text,
                )
            )
        payload = {
            "offset": offset,
            "language": info.language,
            "language_probability": info.language_probability,
            "entries": [asdict(entry) for entry in entries],
        }
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if not args.keep_wav:
            wav_path.unlink(missing_ok=True)

    all_entries = []
    checkpoint_paths = sorted(args.project_dir.glob(checkpoint_pattern))
    for json_path in checkpoint_paths:
        all_entries.extend(load_chunk(json_path))
    if len(checkpoint_paths) != total_chunks:
        raise RuntimeError("transcription is incomplete; rerun the same command to resume")
    if not all_entries:
        raise RuntimeError("transcription produced no text")

    if not args.keep_wav and wav_dir.is_dir():
        for wav_path in wav_dir.glob("*.wav"):
            wav_path.unlink(missing_ok=True)
        try:
            wav_dir.rmdir()
        except OSError:
            pass
    return (
        sorted(all_entries, key=lambda item: (item.start, item.end)),
        duration,
        checkpoint_paths,
    )


def smart_join(parts: list[str]) -> str:
    output = ""
    for part in parts:
        text = part.strip()
        if not text:
            continue
        if not output:
            output = text
            continue
        needs_space = output[-1].isascii() and text[0].isascii()
        if output[-1] in PUNCTUATION or text[0] in PUNCTUATION:
            needs_space = False
        output += (" " if needs_space else "") + text
    return output


def text_needs_punctuation(text: str) -> bool:
    visible_chars = sum(not char.isspace() for char in text)
    punctuation_count = sum(char in PUNCTUATION for char in text)
    return visible_chars >= 40 and punctuation_count * 80 < visible_chars


def normalize_reading_punctuation(text: str) -> str:
    return text.translate(str.maketrans(",!?;", "，！？；"))


def needs_punctuation_restoration(entries: list[TranscriptEntry]) -> bool:
    buffer = ""
    for entry in entries:
        buffer = smart_join([buffer, entry.text])
        if len(buffer) >= 200:
            if text_needs_punctuation(buffer):
                return True
            buffer = ""
    return text_needs_punctuation(buffer)


def resolve_punctuation_model(args: argparse.Namespace) -> Path | None:
    if args.punctuation_model:
        model_path = args.punctuation_model.resolve()
        if not model_path.is_file():
            raise FileNotFoundError(f"punctuation model not found: {model_path}")
        return model_path

    bundled_candidate = SKILL_ROOT / ".models" / "punctuation" / "model.int8.onnx"
    if bundled_candidate.is_file():
        return bundled_candidate

    roots: list[Path] = []
    if args.deps_dir:
        roots.append(args.deps_dir)
    roots.extend(
        [
            args.project_dir.parent / ".codex_tmp_deps",
            Path.cwd() / ".codex_tmp_deps",
        ]
    )
    for root in roots:
        candidate = root / "punctuation_model" / "model.int8.onnx"
        if candidate.is_file():
            return candidate
    return None


def load_punctuation_restorer(
    args: argparse.Namespace,
) -> Callable[[str], str] | None:
    if args.no_punctuation_restoration:
        return None

    model_path = resolve_punctuation_model(args)
    if model_path is None:
        print(
            "warning=source has almost no punctuation and no punctuation model is available",
            flush=True,
        )
        return None

    try:
        import sherpa_onnx
    except ImportError as exc:
        raise RuntimeError(
            "sherpa-onnx is required for punctuation restoration"
        ) from exc

    model_config = sherpa_onnx.OfflinePunctuationModelConfig(
        ct_transformer=str(model_path),
        num_threads=max(1, args.punctuation_threads),
    )
    config = sherpa_onnx.OfflinePunctuationConfig(model=model_config)
    punctuator = sherpa_onnx.OfflinePunctuation(config)

    def restore(text: str) -> str:
        restored = punctuator.add_punctuation(text).strip()
        restored = re.sub(r"\s+([，。！？；：、])", r"\1", restored)
        return restored or text

    return restore


def make_sections(
    entries: list[TranscriptEntry],
    duration: float,
    section_seconds: int,
    paragraph_seconds: int,
    max_chars: int,
    punctuate: Callable[[str], str] | None = None,
) -> list[tuple[int, int, list[tuple[float, str]]]]:
    sections = []
    section_start = 0
    while section_start < duration:
        section_end = min(math.ceil(duration), section_start + section_seconds)
        section_entries = [
            entry
            for entry in entries
            if section_start <= entry.start < section_start + section_seconds
        ]
        paragraphs: list[tuple[float, str]] = []
        buffer: list[str] = []
        buffer_start = 0.0
        buffer_end = 0.0

        for entry in section_entries:
            if not buffer:
                buffer_start = entry.start
            buffer.append(entry.text)
            buffer_end = entry.end
            joined = smart_join(buffer)
            if buffer_end - buffer_start >= paragraph_seconds or len(joined) >= max_chars:
                if punctuate and text_needs_punctuation(joined):
                    joined = punctuate(joined)
                joined = normalize_reading_punctuation(joined)
                paragraphs.append((buffer_start, joined))
                buffer = []
        if buffer:
            joined = smart_join(buffer)
            if punctuate and text_needs_punctuation(joined):
                joined = punctuate(joined)
            joined = normalize_reading_punctuation(joined)
            paragraphs.append((buffer_start, joined))
        if paragraphs:
            sections.append((section_start, section_end, paragraphs))
        section_start += section_seconds
    return sections


def metadata_lines(args: argparse.Namespace, duration: float) -> list[str]:
    lines = [
        f"- 视频标题：{args.title}",
        f"- BV号：{args.video_id or '未知'}",
        f"- UP主：{args.uploader or '未知'}",
        f"- 上传时间：{args.upload_date}",
        f"- 视频时长：{format_time(duration)}",
    ]
    if args.source_url:
        lines.append(f"- 视频链接：{args.source_url}")
    return lines


def write_raw_markdown(
    path: Path,
    args: argparse.Namespace,
    entries: list[TranscriptEntry],
    duration: float,
) -> None:
    lines = [
        f"# {args.title}",
        "",
        "## 视频信息",
        "",
        *metadata_lines(args, duration),
        "",
        "## 完整时间戳转写",
        "",
    ]
    for entry in entries:
        lines.append(
            f"- [{format_time(entry.start)} - {format_time(entry.end)}] {entry.text}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_reading_markdown(
    path: Path,
    args: argparse.Namespace,
    sections: list[tuple[int, int, list[tuple[float, str]]]],
    duration: float,
) -> None:
    lines = [
        f"# {args.title}",
        "",
        "## 视频信息",
        "",
        *metadata_lines(args, duration),
        "",
    ]
    for start, end, paragraphs in sections:
        lines.extend(["", f"## {format_time(start)}-{format_time(end)}", ""])
        for timestamp, text in paragraphs:
            lines.append(f"**[{format_time(timestamp)}]** {text}")
            lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def render_html(
    path: Path,
    args: argparse.Namespace,
    sections: list[tuple[int, int, list[tuple[float, str]]]],
    duration: float,
) -> None:
    escaped_title = html.escape(args.title)
    info_items = "\n".join(
        f"<li>{html.escape(line[2:])}</li>" for line in metadata_lines(args, duration)
    )
    toc_items = []
    section_items = []
    for index, (start, end, paragraphs) in enumerate(sections):
        section_id = f"section-{index + 1}"
        label = f"{format_time(start)}-{format_time(end)}"
        toc_items.append(f'<a href="#{section_id}">{label}</a>')
        body = "\n".join(
            (
                '<p><time datetime="'
                + format_time(timestamp)
                + '">['
                + format_time(timestamp)
                + "]</time> "
                + html.escape(text)
                + "</p>"
            )
            for timestamp, text in paragraphs
        )
        section_items.append(
            f'<section><h2 id="{section_id}">{label}</h2>{body}</section>'
        )

    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" media="(prefers-color-scheme: light)" content="#f4f6f7">
  <meta name="theme-color" media="(prefers-color-scheme: dark)" content="#111416">
  <title>{escaped_title} - 纯文字稿</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f4f6f7;
      --paper: #ffffff;
      --text: #202428;
      --muted: #65707a;
      --line: #dfe4e7;
      --accent: #0d6673;
      --soft: #e7f3f4;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #111416;
        --paper: #181c1f;
        --text: #edf1f3;
        --muted: #adb5bd;
        --line: #30363a;
        --accent: #83cbd2;
        --soft: #203437;
      }}
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; -webkit-text-size-adjust: 100%; }}
    body {{
      margin: 0;
      overflow-x: hidden;
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "SF Pro Text",
        "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", "Microsoft YaHei",
        sans-serif;
      font-size: 18px;
      line-height: 1.88;
      letter-spacing: 0;
    }}
    .progress {{ position: fixed; inset: 0 0 auto; height: 3px; z-index: 10; }}
    .progress span {{ display: block; width: 0; height: 100%; background: var(--accent); }}
    main {{
      width: min(100%, 820px);
      margin: 0 auto;
      padding: 30px 42px 60px;
      background: var(--paper);
      min-height: 100vh;
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 32px;
      line-height: 1.35;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    .label {{ margin: 0 0 24px; color: var(--muted); font-size: 15px; }}
    ul {{ padding-left: 1.3em; }}
    li {{ margin: 0.35em 0; overflow-wrap: anywhere; }}
    details {{
      margin: 24px 0;
      padding: 10px 14px;
      border: 1px solid var(--line);
      border-radius: 5px;
      background: var(--soft);
    }}
    summary {{ cursor: pointer; font-weight: 700; }}
    nav {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px 14px; margin-top: 10px; }}
    nav a {{ color: var(--accent); text-decoration: none; }}
    h2 {{
      margin: 2.3em 0 0.7em;
      padding-top: 0.55em;
      border-top: 1px solid var(--line);
      font-size: 23px;
      line-height: 1.45;
      scroll-margin-top: 12px;
    }}
    p {{ margin: 0.95em 0; overflow-wrap: anywhere; }}
    time {{ color: var(--accent); font-size: 0.88em; font-weight: 700; white-space: nowrap; }}
    .top {{
      position: fixed;
      right: max(14px, env(safe-area-inset-right));
      bottom: max(18px, env(safe-area-inset-bottom));
      width: 42px;
      height: 42px;
      display: grid;
      place-items: center;
      border-radius: 50%;
      background: var(--accent);
      color: #ffffff;
      text-decoration: none;
      box-shadow: 0 8px 20px rgba(0, 0, 0, 0.2);
    }}
    @media (max-width: 680px) {{
      body {{ font-size: 17px; line-height: 1.9; }}
      main {{ padding: 25px 19px 52px; }}
      h1 {{ font-size: 27px; }}
      h2 {{ font-size: 21px; }}
      nav {{ grid-template-columns: repeat(2, 1fr); }}
    }}
    @media print {{
      body {{ background: #ffffff; color: #111111; font-size: 11pt; }}
      main {{ width: 100%; padding: 0; }}
      .progress, .top {{ display: none; }}
    }}
  </style>
</head>
<body>
  <div class="progress" aria-hidden="true"><span id="progress"></span></div>
  <main id="top">
    <header>
      <h1>{escaped_title}</h1>
      <p class="label">视频纯文字稿</p>
      <ul>{info_items}</ul>
    </header>
    <details>
      <summary>时间目录</summary>
      <nav aria-label="时间目录">{''.join(toc_items)}</nav>
    </details>
    {''.join(section_items)}
  </main>
  <a class="top" href="#top" aria-label="回到顶部" title="回到顶部">↑</a>
  <script>
    const bar = document.getElementById("progress");
    const update = () => {{
      const root = document.documentElement;
      const total = root.scrollHeight - root.clientHeight;
      bar.style.width = `${{total > 0 ? Math.min(100, root.scrollTop / total * 100) : 0}}%`;
    }};
    document.addEventListener("scroll", update, {{ passive: true }});
    update();
  </script>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")


def write_project_index(
    path: Path,
    args: argparse.Namespace,
    raw_path: Path,
    reading_path: Path,
    html_path: Path,
    punctuation_restored: bool,
) -> None:
    lines = [
        f"# {args.title}",
        "",
        f"- 上传时间：{args.upload_date}",
        f"- BV号：{args.video_id or '未知'}",
        "",
        "## 文件",
        "",
        f"- 完整时间戳转写：`{raw_path.relative_to(args.project_dir)}`",
        f"- 纯文字稿：`{reading_path.relative_to(args.project_dir)}`",
        f"- 手机阅读版：`{html_path.relative_to(args.project_dir)}`",
        "",
        "转写只保留原视频内容顺序，不添加总结、评价或观点提炼。",
        "自动识别可能存在同音字和专有名词错误。",
    ]
    if punctuation_restored:
        lines.append("纯文字稿和手机阅读版已恢复中文标点；完整时间戳转写保留原始识别文本。")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert Bilibili audio or subtitles into timestamped text and mobile HTML."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input-audio", type=Path)
    source.add_argument("--input-subtitle", type=Path)
    parser.add_argument("--allow-subtitle-source", action="store_true")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--upload-date", required=True)
    parser.add_argument("--video-id", default="")
    parser.add_argument("--uploader", default="")
    parser.add_argument("--source-url", default="")
    parser.add_argument("--duration", type=float)
    parser.add_argument("--deps-dir", type=Path)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--model", default="small")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--initial-prompt")
    parser.add_argument("--chunk-seconds", type=int, default=600)
    parser.add_argument("--section-seconds", type=int, default=300)
    parser.add_argument("--paragraph-seconds", type=int, default=50)
    parser.add_argument("--max-paragraph-chars", type=int, default=360)
    parser.add_argument("--punctuation-model", type=Path)
    parser.add_argument("--punctuation-threads", type=int, default=2)
    parser.add_argument("--no-punctuation-restoration", action="store_true")
    parser.add_argument("--keep-wav", action="store_true")
    parser.add_argument("--keep-checkpoints", action="store_true")
    return parser


def run_transcription(args: argparse.Namespace) -> OutputPaths:
    if args.input_subtitle and not args.allow_subtitle_source:
        raise ValueError(
            "subtitle input is disabled by default; use audio ASR or pass "
            "--allow-subtitle-source only after an explicit user request"
        )
    args.project_dir = args.project_dir.resolve()
    args.project_dir.mkdir(parents=True, exist_ok=True)
    bootstrap_dependencies(args.project_dir, args.deps_dir)

    if args.input_subtitle:
        entries = parse_subtitle(args.input_subtitle.resolve())
        duration = args.duration or max(entry.end for entry in entries)
        checkpoint_paths: list[Path] = []
    else:
        ffmpeg = resolve_ffmpeg(args.project_dir, args.deps_dir, args.ffmpeg)
        entries, duration, checkpoint_paths = transcribe_audio(args, ffmpeg)

    base = f"{safe_name(args.title)}_{args.upload_date}"
    raw_path = args.project_dir / f"{base}_完整转写.md"
    reading_path = args.project_dir / f"{base}_纯文字稿.md"
    html_path = args.project_dir / f"{base}_纯文字稿_移动端.html"

    punctuation_restored = needs_punctuation_restoration(entries)
    punctuate = None
    if punctuation_restored:
        punctuate = load_punctuation_restorer(args)
        punctuation_restored = punctuate is not None

    sections = make_sections(
        entries,
        duration=duration,
        section_seconds=args.section_seconds,
        paragraph_seconds=args.paragraph_seconds,
        max_chars=args.max_paragraph_chars,
        punctuate=punctuate,
    )
    write_raw_markdown(raw_path, args, entries, duration)
    write_reading_markdown(reading_path, args, sections, duration)
    render_html(html_path, args, sections, duration)
    write_project_index(
        args.project_dir / "README.md",
        args,
        raw_path,
        reading_path,
        html_path,
        punctuation_restored,
    )
    if not args.keep_checkpoints:
        for checkpoint_path in checkpoint_paths:
            checkpoint_path.unlink(missing_ok=True)

    output_paths = OutputPaths(
        raw_markdown=raw_path,
        reading_markdown=reading_path,
        mobile_html=html_path,
        project_index=args.project_dir / "README.md",
    )
    print(f"raw_markdown={output_paths.raw_markdown}")
    print(f"reading_markdown={output_paths.reading_markdown}")
    print(f"mobile_html={output_paths.mobile_html}")
    print(f"punctuation={'restored' if punctuation_restored else 'source'}")
    return output_paths


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        run_transcription(args)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
