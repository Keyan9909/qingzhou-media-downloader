from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import media_core


ROOT = Path(__file__).resolve().parents[1]


class GuideContentTests(unittest.TestCase):
    def test_beginner_guide_covers_every_platform_and_firefox_flow(self) -> None:
        guide = (ROOT / "详细使用教程.md").read_text(encoding="utf-8")
        required = (
            "YouTube 下载（必须重点阅读 Firefox 步骤）",
            "关闭所有 Firefox 窗口",
            "哔哩哔哩（B站）视频下载",
            "抖音单条视频下载",
            "小红书视频笔记下载",
            "小红书图文笔记下载",
            "小鹅通已购课程视频下载",
            "进度条、取消和完成状态",
            "让 AI 或脚本控制软件",
        )
        for text in required:
            with self.subTest(text=text):
                self.assertIn(text, guide)

    def test_firefox_detection_uses_windows_install_roots(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            executable = root / "Mozilla Firefox" / "firefox.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"test")
            with patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": str(root),
                    "PROGRAMFILES(X86)": "",
                    "LOCALAPPDATA": "",
                },
            ):
                self.assertEqual(media_core.find_firefox_executable(), executable)


if __name__ == "__main__":
    unittest.main()
