from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RepositoryTests(unittest.TestCase):
    def test_skill_frontmatter_and_entrypoint_exist(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(skill.startswith("---\n"))
        self.assertRegex(skill, r"(?m)^name: transcribe-bilibili-video$")
        self.assertRegex(skill, r"(?m)^description: .+$")
        self.assertTrue((ROOT / "scripts" / "transcribe_bilibili.py").is_file())

    def test_public_files_do_not_contain_personal_absolute_paths(self) -> None:
        candidates = [
            ROOT / "SKILL.md",
            ROOT / "README.md",
            ROOT / "agents" / "openai.yaml",
            *sorted((ROOT / "scripts").glob("*.py")),
        ]
        mac_users_prefix = chr(47) + "Users" + chr(47)
        cookie_names = ("SESS" + "DATA", "bili" + "_jct")
        for path in candidates:
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(mac_users_prefix, text)
                for cookie_name in cookie_names:
                    self.assertIsNone(
                        re.search(re.escape(cookie_name) + r"\s*[=:]", text)
                    )


if __name__ == "__main__":
    unittest.main()
