#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import venv


SKILL_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = SKILL_ROOT / "requirements.txt"
DEFAULT_VENV = SKILL_ROOT / ".venv"
MODEL_DIR = SKILL_ROOT / ".models" / "punctuation"
MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/"
    "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8.tar.bz2"
)
MODEL_ARCHIVE_SHA256 = (
    "c0d5aa5f8eeb686032345e180bedf39319dc2e0556781c6264bcadba8328a6e1"
)
MODEL_FILES = ("model.int8.onnx", "tokens.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install Python dependencies and the optional punctuation model."
    )
    parser.add_argument("--venv", type=Path, default=DEFAULT_VENV)
    parser.add_argument("--skip-python-deps", action="store_true")
    parser.add_argument("--skip-punctuation-model", action="store_true")
    return parser


def venv_python(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def install_python_dependencies(venv_dir: Path) -> Path:
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10 or newer is required")
    python = venv_python(venv_dir)
    if not python.is_file():
        print(f"create_venv={venv_dir}", flush=True)
        venv.EnvBuilder(with_pip=True).create(venv_dir)
    print(f"install_requirements={REQUIREMENTS}", flush=True)
    subprocess.run(
        [str(python), "-m", "pip", "install", "--upgrade", "pip"], check=True
    )
    subprocess.run(
        [str(python), "-m", "pip", "install", "-r", str(REQUIREMENTS)],
        check=True,
    )
    return python


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_archive(destination: Path) -> None:
    request = urllib.request.Request(
        MODEL_URL, headers={"User-Agent": "transcribe-bilibili-video-bootstrap"}
    )
    with urllib.request.urlopen(request, timeout=120) as response, destination.open(
        "wb"
    ) as output:
        shutil.copyfileobj(response, output)
    actual = sha256(destination)
    if actual != MODEL_ARCHIVE_SHA256:
        raise RuntimeError(
            f"punctuation model checksum mismatch: expected {MODEL_ARCHIVE_SHA256}, "
            f"got {actual}"
        )


def extract_model(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, mode="r:bz2") as bundle:
        members = {
            Path(member.name).name: member
            for member in bundle.getmembers()
            if member.isfile() and Path(member.name).name in MODEL_FILES
        }
        missing = sorted(set(MODEL_FILES) - set(members))
        if missing:
            raise RuntimeError(f"punctuation archive is missing: {', '.join(missing)}")
        for filename in MODEL_FILES:
            source = bundle.extractfile(members[filename])
            if source is None:
                raise RuntimeError(f"could not read {filename} from punctuation archive")
            target = destination / filename
            temporary = target.with_suffix(target.suffix + ".tmp")
            with source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output)
            temporary.replace(target)


def install_punctuation_model() -> Path:
    model_path = MODEL_DIR / "model.int8.onnx"
    if model_path.is_file():
        print(f"punctuation_model={model_path}", flush=True)
        return model_path
    with tempfile.TemporaryDirectory(prefix="bilibili-punctuation-") as temp_dir:
        archive = Path(temp_dir) / "punctuation-model.tar.bz2"
        print(f"download_punctuation_model={MODEL_URL}", flush=True)
        download_archive(archive)
        extract_model(archive, MODEL_DIR)
    print(f"punctuation_model={model_path}", flush=True)
    return model_path


def main() -> int:
    args = build_parser().parse_args()
    python = venv_python(args.venv.expanduser().resolve())
    if not args.skip_python_deps:
        python = install_python_dependencies(args.venv.expanduser().resolve())
    if not args.skip_punctuation_model:
        install_punctuation_model()
    print(f"python={python}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
