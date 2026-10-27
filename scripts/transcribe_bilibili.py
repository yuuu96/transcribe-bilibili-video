#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import Any

import transcribe_media


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path.home() / ".config" / "transcribe-bilibili-video" / "config.json"
ALLOWED_CONFIG_KEYS = {
    "cookies_file",
    "deps_dir",
    "ffmpeg",
    "model",
    "output_root",
    "punctuation_model",
}
BV_PATTERN = re.compile(r"BV[0-9A-Za-z]{10}", flags=re.IGNORECASE)


@dataclass(frozen=True)
class Settings:
    output_root: Path
    cookies_file: Path | None
    deps_dir: Path | None
    ffmpeg: Path | None
    model: str
    punctuation_model: Path | None


@dataclass(frozen=True)
class VideoMetadata:
    video_id: str
    title: str
    uploader: str
    upload_date: str
    duration: float | None
    source_url: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download Bilibili audio and create timestamped Chinese Markdown and "
            "mobile HTML with local ASR."
        )
    )
    parser.add_argument("video", help="Bilibili URL or BV ID")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--cookies", type=Path, help="Netscape cookies.txt file")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--punctuation-model", type=Path)
    parser.add_argument("--deps-dir", type=Path)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--chunk-seconds", type=int, default=600)
    parser.add_argument("--initial-prompt")
    parser.add_argument("--no-punctuation-restoration", action="store_true")
    parser.add_argument("--keep-checkpoints", action="store_true")
    return parser


def load_config(path: Path) -> dict[str, Any]:
    path = path.expanduser()
    if not path.exists():
        return {}
    if not path.is_file():
        raise ValueError(f"config path is not a file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON config: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("config root must be a JSON object")
    unknown = sorted(set(payload) - ALLOWED_CONFIG_KEYS)
    if unknown:
        raise ValueError(f"unknown config keys: {', '.join(unknown)}")
    for key, value in payload.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"config value must be a non-empty string: {key}")
    return payload


def _path_value(cli_value: Path | None, config: dict[str, Any], key: str) -> Path | None:
    value: Path | str | None = cli_value if cli_value is not None else config.get(key)
    if value is None:
        return None
    return Path(value).expanduser().resolve()


def resolve_settings(args: argparse.Namespace, config: dict[str, Any]) -> Settings:
    output_root = _path_value(args.output_root, config, "output_root") or Path.cwd()
    cookies_file = _path_value(args.cookies, config, "cookies_file")
    deps_dir = _path_value(args.deps_dir, config, "deps_dir")
    ffmpeg = _path_value(args.ffmpeg, config, "ffmpeg")
    punctuation_model = _path_value(
        args.punctuation_model, config, "punctuation_model"
    )
    model = args.model or config.get("model") or "small"

    if cookies_file is not None:
        if not cookies_file.is_file() or cookies_file.stat().st_size == 0:
            raise ValueError(f"cookies file is missing or empty: {cookies_file}")
    if deps_dir is not None and not deps_dir.is_dir():
        raise ValueError(f"dependencies directory not found: {deps_dir}")
    if ffmpeg is not None and not ffmpeg.is_file():
        raise ValueError(f"ffmpeg not found: {ffmpeg}")
    if punctuation_model is not None and not punctuation_model.is_file():
        raise ValueError(f"punctuation model not found: {punctuation_model}")

    return Settings(
        output_root=output_root.resolve(),
        cookies_file=cookies_file,
        deps_dir=deps_dir,
        ffmpeg=ffmpeg,
        model=str(model),
        punctuation_model=punctuation_model,
    )


def normalize_video_input(value: str) -> str:
    stripped = value.strip()
    match = BV_PATTERN.fullmatch(stripped)
    if match:
        return f"https://www.bilibili.com/video/{match.group(0)}/"
    if re.match(r"https?://", stripped, flags=re.IGNORECASE):
        return stripped
    raise ValueError("video must be a Bilibili URL or a BV ID")


def build_ydl_options(
    cookies_file: Path | None,
    *,
    output_template: str | None = None,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "noplaylist": True,
        "writesubtitles": False,
        "writeautomaticsub": False,
        "writethumbnail": False,
    }
    if cookies_file is not None:
        options["cookiefile"] = str(cookies_file)
    if output_template is None:
        options["skip_download"] = True
        options["quiet"] = True
    else:
        options.update(
            {
                "format": select_bilibili_audio,
                "outtmpl": output_template,
                "continuedl": True,
                "http_chunk_size": 512 * 1024,
                "overwrites": False,
                "retries": 20,
            }
        )
    return options


def select_bilibili_audio(context: dict[str, Any]) -> list[dict[str, Any]]:
    formats = context.get("formats") or []
    audio_only = [
        item
        for item in formats
        if item.get("vcodec") == "none"
        and item.get("acodec") not in (None, "none")
    ]
    candidates = audio_only or [
        item for item in formats if item.get("acodec") not in (None, "none")
    ]
    if not candidates:
        return []

    selected = max(
        candidates,
        key=lambda item: (
            float(item.get("quality") or -1),
            float(item.get("abr") or item.get("tbr") or -1),
            int(item.get("filesize") or item.get("filesize_approx") or -1),
        ),
    ).copy()
    headers = dict(selected.get("http_headers") or {})
    headers.setdefault("Range", "bytes=0-")
    selected["http_headers"] = headers
    return [selected]


def import_yt_dlp(settings: Settings):
    transcribe_media.bootstrap_dependencies(settings.output_root, settings.deps_dir)
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError(
            "yt-dlp is not installed; run scripts/bootstrap.py or use --deps-dir"
        ) from exc
    return yt_dlp


def format_upload_date(info: dict[str, Any]) -> str:
    for key in ("upload_date", "release_date"):
        value = str(info.get(key) or "")
        if re.fullmatch(r"\d{8}", value):
            return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    timestamp = info.get("timestamp") or info.get("release_timestamp")
    if timestamp is not None:
        return datetime.fromtimestamp(float(timestamp)).strftime("%Y-%m-%d")
    raise RuntimeError("Bilibili metadata does not include an upload date")


def parse_metadata(info: dict[str, Any], requested_url: str) -> VideoMetadata:
    raw_id = str(info.get("id") or info.get("display_id") or "")
    source_for_id = " ".join(
        [raw_id, str(info.get("webpage_url") or ""), requested_url]
    )
    match = BV_PATTERN.search(source_for_id)
    video_id = match.group(0) if match else transcribe_media.safe_name(raw_id)
    title = str(info.get("title") or "").strip()
    if not video_id or not title:
        raise RuntimeError("Bilibili metadata is missing the video ID or title")
    duration = info.get("duration")
    return VideoMetadata(
        video_id=video_id,
        title=title,
        uploader=str(info.get("uploader") or info.get("channel") or "未知"),
        upload_date=format_upload_date(info),
        duration=float(duration) if duration is not None else None,
        source_url=str(info.get("webpage_url") or requested_url),
    )


def fetch_metadata(yt_dlp, video_url: str, settings: Settings) -> VideoMetadata:
    try:
        with yt_dlp.YoutubeDL(build_ydl_options(settings.cookies_file)) as downloader:
            info = downloader.extract_info(video_url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        hint = (
            " Provide a valid Netscape cookies.txt file with --cookies for member-only "
            "or login-protected videos."
        )
        raise RuntimeError(f"could not read Bilibili metadata.{hint}") from exc
    if not isinstance(info, dict):
        raise RuntimeError("Bilibili returned invalid metadata")
    return parse_metadata(info, video_url)


def download_audio(
    yt_dlp,
    video_url: str,
    metadata: VideoMetadata,
    settings: Settings,
    project_dir: Path,
) -> Path:
    template = str(project_dir / f"{metadata.video_id}_audio.%(ext)s")
    options = build_ydl_options(settings.cookies_file, output_template=template)
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.extract_info(video_url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        hint = (
            " Check that the cookies file is current and that the signed-in account "
            "can play this video."
        )
        raise RuntimeError(f"could not download Bilibili audio.{hint}") from exc

    candidates = sorted(
        path
        for path in project_dir.glob(f"{metadata.video_id}_audio.*")
        if path.is_file() and path.suffix not in {".part", ".ytdl"}
    )
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected one downloaded audio file, found {len(candidates)}"
        )
    return candidates[0]


def build_transcription_args(
    cli_args: argparse.Namespace,
    settings: Settings,
    metadata: VideoMetadata,
    project_dir: Path,
    audio_path: Path,
) -> argparse.Namespace:
    return argparse.Namespace(
        input_audio=audio_path,
        input_subtitle=None,
        allow_subtitle_source=False,
        project_dir=project_dir,
        title=metadata.title,
        upload_date=metadata.upload_date,
        video_id=metadata.video_id,
        uploader=metadata.uploader,
        source_url=metadata.source_url,
        duration=metadata.duration,
        deps_dir=settings.deps_dir,
        ffmpeg=settings.ffmpeg,
        model=settings.model,
        device=cli_args.device,
        compute_type=cli_args.compute_type,
        language="zh",
        beam_size=cli_args.beam_size,
        initial_prompt=cli_args.initial_prompt,
        chunk_seconds=cli_args.chunk_seconds,
        section_seconds=300,
        paragraph_seconds=50,
        max_paragraph_chars=360,
        punctuation_model=settings.punctuation_model,
        punctuation_threads=2,
        no_punctuation_restoration=cli_args.no_punctuation_restoration,
        keep_wav=False,
        keep_checkpoints=cli_args.keep_checkpoints,
    )


def run(argv: list[str] | None = None) -> transcribe_media.OutputPaths:
    cli_args = build_parser().parse_args(argv)
    config = load_config(cli_args.config)
    settings = resolve_settings(cli_args, config)
    video_url = normalize_video_input(cli_args.video)
    yt_dlp = import_yt_dlp(settings)
    metadata = fetch_metadata(yt_dlp, video_url, settings)

    project_name = transcribe_media.safe_name(
        f"{metadata.title}_{metadata.upload_date}"
    )
    project_dir = settings.output_root / project_name
    project_dir.mkdir(parents=True, exist_ok=True)
    audio_path = download_audio(
        yt_dlp, video_url, metadata, settings, project_dir
    )
    transcription_args = build_transcription_args(
        cli_args, settings, metadata, project_dir, audio_path
    )
    outputs = transcribe_media.run_transcription(transcription_args)
    print(f"project_dir={project_dir}")
    return outputs


def main() -> int:
    try:
        run()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
