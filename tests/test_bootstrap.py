from __future__ import annotations

from pathlib import Path
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import bootstrap


class BootstrapTests(unittest.TestCase):
    def test_extract_model_selects_only_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            source.mkdir()
            model = source / "model.int8.onnx"
            tokens = source / "tokens.json"
            ignored = source / "README.md"
            model.write_bytes(b"model")
            tokens.write_text("{}", encoding="utf-8")
            ignored.write_text("ignore", encoding="utf-8")
            archive = root / "model.tar.bz2"
            with tarfile.open(archive, "w:bz2") as bundle:
                bundle.add(model, arcname="release/model.int8.onnx")
                bundle.add(tokens, arcname="release/tokens.json")
                bundle.add(ignored, arcname="release/README.md")
            destination = root / "output"

            bootstrap.extract_model(archive, destination)

            self.assertEqual((destination / "model.int8.onnx").read_bytes(), b"model")
            self.assertEqual((destination / "tokens.json").read_text(), "{}")
            self.assertFalse((destination / "README.md").exists())


if __name__ == "__main__":
    unittest.main()
