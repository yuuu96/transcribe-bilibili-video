# transcribe-bilibili-video

一个面向 Codex 和普通终端用户的 B 站视频本地转写工具。它只下载音频，使用 `faster-whisper` 在本机识别，并输出带时间戳的 Markdown、适合连续阅读的 Markdown 和移动端 HTML。

默认不使用 B 站官方字幕或自动字幕，也不会自动生成总结、评价和观点提炼。

## 功能

- 支持完整 B 站链接和 BV 号。
- 支持公开内容，以及通过 Netscape 格式 `cookies.txt` 访问账号有权播放的会员内容。
- 音频识别采用十分钟分块，异常中断后可以从已有检查点继续。
- 下载时会为 B 站音频 CDN 补齐分段请求头，并使用可续传的小分块，避免近期出现的 HTTP 567 和长连接中断。
- 为中文阅读稿恢复标点，同时保留完整时间戳识别稿。
- 所有结果直接放在 `<标题>_<上传日期>` 单层目录中。
- cookies、音视频、模型、检查点和生成结果均被 Git 忽略。

## 环境要求

- Python 3.10 或更高版本。
- macOS、Linux 或 Windows。
- 首次运行需要联网安装 Python 依赖并下载模型。
- Whisper `small` 模型首次识别时下载到用户缓存；CPU 也能运行，但长视频需要较长时间。

## 安装为 Codex Skill

使用 Codex 自带的 Skill 安装器：

```bash
python "$HOME/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py" \
  --repo yuuu96/transcribe-bilibili-video \
  --path . \
  --name transcribe-bilibili-video
```

安装依赖和中英文标点模型：

```bash
python3 "$HOME/.codex/skills/transcribe-bilibili-video/scripts/bootstrap.py"
```

下一次对话中可以直接调用：

```text
使用 $transcribe-bilibili-video 转写这个会员视频：https://www.bilibili.com/video/BV...
```

## 作为命令行工具使用

克隆并初始化环境：

```bash
git clone https://github.com/yuuu96/transcribe-bilibili-video.git
cd transcribe-bilibili-video
python3 scripts/bootstrap.py
```

公开内容：

```bash
.venv/bin/python scripts/transcribe_bilibili.py \
  "https://www.bilibili.com/video/BV..." \
  --output-root "$HOME/Downloads"
```

会员或登录保护内容：

```bash
.venv/bin/python scripts/transcribe_bilibili.py \
  "BVxxxxxxxxxx" \
  --cookies "/path/to/cookies.txt" \
  --output-root "$HOME/Downloads"
```

Windows 使用 `.venv\Scripts\python.exe` 代替 `.venv/bin/python`。

## 本地配置

可以在 `~/.config/transcribe-bilibili-video/config.json` 保存个人默认路径。命令行参数始终优先于配置文件。

```json
{
  "output_root": "/path/to/output",
  "cookies_file": "/path/to/cookies.txt",
  "model": "small",
  "punctuation_model": "/path/to/model.int8.onnx",
  "deps_dir": "/path/to/python/site-packages",
  "ffmpeg": "/path/to/ffmpeg"
}
```

所有字段都可省略。使用 `scripts/bootstrap.py` 后通常不需要配置 `deps_dir`、`ffmpeg` 或 `punctuation_model`。

不要把 cookies 内容写入配置文件；这里只保存 cookies 文件的路径。不要上传、分享或提交登录文件。

## 输出结构

```text
<标题>_<YYYY-MM-DD>/
├── README.md
├── <BV号>_audio.m4a
├── <标题>_<日期>_完整转写.md
├── <标题>_<日期>_纯文字稿.md
└── <标题>_<日期>_纯文字稿_移动端.html
```

音频扩展名由 B 站实际提供的最佳音频格式决定，不保证始终为 `.m4a`。

## 常用参数

```text
--cookies PATH                 Netscape cookies.txt
--output-root PATH             结果根目录，默认为当前目录
--model NAME                   faster-whisper 模型，默认为 small
--punctuation-model PATH       sherpa-onnx 标点模型
--deps-dir PATH                已有 Python 依赖目录
--ffmpeg PATH                  ffmpeg 可执行文件
--initial-prompt TEXT          ASR 专有名词提示
--chunk-seconds N              识别分块长度，默认 600 秒
--keep-checkpoints             成功完成后仍保留检查点
--no-punctuation-restoration   禁用额外标点恢复
```

## 安全与边界

- 工具不会主动读取浏览器 cookies，只接受用户明确提供的 Netscape cookies 文件。
- cookies 内容不会被复制到结果目录或打印到终端。
- 模型和媒体不会提交到本仓库。
- 请仅处理自己有权访问和转写的内容，并遵守内容平台规则和适用法律。

## 开发与验证

```bash
python3 -m py_compile scripts/*.py
python3 -m unittest discover -s tests -v
python3 "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" .
```

## 依赖来源

- 下载与元信息：`yt-dlp`
- 本地语音识别：`faster-whisper`
- 音频转换：`ffmpeg` / `imageio-ffmpeg`
- 标点恢复：`sherpa-onnx` CT-Transformer 中英文标点模型

## License

[MIT](LICENSE)
