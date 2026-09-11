---
name: transcribe-bilibili-video
description: Download and transcribe Bilibili public or member-only videos into timestamped Chinese text and mobile-friendly HTML in a single-level project folder. Use when the user provides a Bilibili URL or BV ID and asks to 转文字、转写、形成文字稿、整理原话、生成移动端 HTML, including videos that require a Netscape cookies.txt login file. Always download audio and use local ASR by default; ignore platform subtitles unless the user explicitly reverses this preference. Preserve the speaker's wording and sequence; do not add summaries, evaluations, viewpoint extraction, or commentary unless explicitly requested.
---

# Transcribe Bilibili Video

Create a faithful transcript from Bilibili audio with resumable local speech recognition. Use `scripts/transcribe_bilibili.py` as the single production entry point; do not reproduce its metadata, download, or output-generation logic in ad hoc commands.

## Defaults

- Accept a Bilibili URL or BV ID.
- Save under the requested output root, or the current working directory when none is specified.
- Name the project and outputs `<标题>_<YYYY-MM-DD>`.
- Keep each project strictly single-level and retain the compressed source audio in its root.
- Generate `README.md`, complete timestamp Markdown, readable Markdown, and mobile HTML.
- Always download audio and run local ASR. Do not inspect, download, or use official or automatic subtitles unless the user explicitly requests subtitle input in the current conversation.
- Store resumable chunk checkpoints as hidden project-root files during an incomplete run. Remove them only after every final output succeeds.
- Create temporary WAV chunks only in the system temporary directory and remove them after their checkpoints are saved.

## Content Rules

- Preserve the original sequence, wording, examples, questions, repetitions, and spoken uncertainty.
- Add only neutral metadata, timestamps, paragraph boundaries, and punctuation.
- Restore Chinese punctuation in readable Markdown and HTML without paraphrasing the source.
- Correct only obvious homophones and proper nouns supported by context or an audio check.
- Mark genuinely unclear names as `[听不清]` or retain their phonetic form instead of guessing.
- Do not add summaries, core viewpoints, evaluations, interpretations, fact-checks, warnings, or conclusions unless the user separately requests them.

## Run

The CLI reads optional defaults from `~/.config/transcribe-bilibili-video/config.json`. Explicit command-line flags override the config. The supported config keys are `output_root`, `cookies_file`, `model`, `punctuation_model`, `deps_dir`, and `ffmpeg`.

Use the skill-local virtual environment when it exists:

```bash
"<skill-dir>/.venv/bin/python" "<skill-dir>/scripts/transcribe_bilibili.py" \
  "BILIBILI_URL_OR_BV_ID" \
  --output-root "/path/to/output" \
  --cookies "/path/to/cookies.txt"
```

Omit `--cookies` for videos that are publicly accessible without login. For member-only or login-protected videos, accept only an explicit Netscape cookies file or a configured `cookies_file`. Never read cookies from a browser, copy them into a project, print their contents, or commit them.

If dependencies are unavailable, ask before performing network installation and then run:

```bash
python3 "<skill-dir>/scripts/bootstrap.py"
```

The bootstrap creates `.venv`, installs the local ASR dependencies, and downloads the punctuation model into the ignored `.models` directory. Whisper model weights download to the normal user cache on first use and are never project outputs.

Use `--deps-dir` only for an existing external dependency directory. Useful advanced options include `--model`, `--punctuation-model`, `--ffmpeg`, `--initial-prompt`, `--chunk-seconds`, `--keep-checkpoints`, and `--no-punctuation-restoration`.

## Quality Control

After generation, verify:

- metadata and source URL match the requested video;
- the first, middle, and final intervals correspond to the downloaded audio;
- no chunk interval is missing or duplicated;
- the final timestamp is close to the last audible speech;
- repeated names and technical terms are consistent;
- readable outputs contain normal Chinese punctuation;
- no assistant-authored summary or evaluation appears;
- the project contains no subdirectories, cookies, subtitles, temporary WAV files, completed checkpoints, or failed download fragments.

Retain only the compressed audio, README, complete timestamp Markdown, readable Markdown, and mobile HTML. Return clickable links to the readable Markdown and mobile HTML, and disclose any names that remain uncertain.

## Examples

- `使用 $transcribe-bilibili-video 转写这个视频：https://www.bilibili.com/video/BV...`
- `用 $transcribe-bilibili-video 处理这个会员视频，cookies 在 /path/to/cookies.txt，只要原话，不要总结。`
- `用 $transcribe-bilibili-video 转写这个视频，忽略B站字幕，只用音频识别。`
