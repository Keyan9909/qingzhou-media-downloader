from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path


class MediaCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        os.environ["QINGZHOU_DATA_DIR"] = cls.temp.name
        import media_core

        cls.core = importlib.reload(media_core)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_detects_supported_platforms_from_share_text(self) -> None:
        cases = {
            "youtube": "看看 https://youtu.be/abc123",
            "douyin": "复制 https://v.douyin.com/abc/",
            "xiaohongshu": "笔记 https://xhslink.com/abc",
            "bilibili": "视频 https://www.bilibili.com/video/BV123",
            "xiaoetong": "课程 https://demo.xiaoeknow.com/p/course",
            "xiaoetong_pomoho": (
                "https://appalpxdi5t3647.h5.xet.pomoho.com/p/course/video/"
                "v_665e9351e4b0d84daade069d?product_id="
                "course_2hmew5CgfjoUm7VNT7ZS43f1JQu"
            ),
            "xiaoetong_custom": (
                "https://courses.example.cn/video/v_665e9351e4b0d84daade069d"
                "?product_id=course_2hmew5CgfjoUm7VNT7ZS43f1JQu"
                "&course_id=course_2hmew5CgfjoUm7VNT7ZS43f1JQu"
            ),
        }
        for expected, value in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(
                    self.core.detect_platform(value),
                    "xiaoetong" if expected.startswith("xiaoetong_") else expected,
                )
        self.assertIn(
            "study.xiaoe-tech.com",
            str(self.core.PLATFORMS["xiaoetong"]["home"]),
        )

    def test_relative_xiaoetong_path_is_not_treated_as_complete_url(self) -> None:
        value = (
            "video/v_665e9351e4b0d84daade069d"
            "?product_id=course_2hmew5CgfjoUm7VNT7ZS43f1JQu"
        )
        self.assertEqual(self.core.detect_platform(value), "unknown")

    def test_progress_payload_formats_percent_speed_size_and_eta(self) -> None:
        payload = self.core.progress_payload(
            {
                "state": "downloading",
                "downloaded_bytes": 50 * 1024 * 1024,
                "total_bytes": 200 * 1024 * 1024,
                "speed_bps": 5 * 1024 * 1024,
                "eta_seconds": 30,
            }
        )
        self.assertTrue(payload["determinate"])
        self.assertEqual(payload["percent"], 25)
        self.assertIn("50.0 MB / 200.0 MB", payload["detail"])
        self.assertIn("5.0 MB/s", payload["detail"])
        self.assertIn("剩余约 30秒", payload["detail"])

    def test_progress_payload_keeps_unknown_total_indeterminate(self) -> None:
        payload = self.core.progress_payload(
            {"state": "starting", "received_bytes": 0, "total_bytes": 0}
        )
        self.assertFalse(payload["determinate"])
        self.assertIsNone(payload["percent"])

    def test_config_round_trip_and_platform_override(self) -> None:
        default_dir = Path(self.temp.name) / "default"
        youtube_dir = Path(self.temp.name) / "youtube"
        saved = self.core.save_config(
            {
                "default_output_dir": str(default_dir),
                "platform_output_dirs": {"youtube": str(youtube_dir)},
                "quality": "720",
            }
        )
        self.assertEqual(saved["quality"], "720")
        self.assertEqual(self.core.resolve_output_dir("youtube"), youtube_dir.resolve())
        self.assertEqual(self.core.resolve_output_dir("bilibili"), default_dir.resolve())

    def test_history_is_bounded_and_clear_does_not_touch_outputs(self) -> None:
        output = Path(self.temp.name) / "keep.mp4"
        output.write_bytes(b"test")
        self.core.append_history(
            {
                "platform": "youtube",
                "title": "demo",
                "output": str(output),
            }
        )
        self.assertEqual(len(self.core.list_history()), 1)
        self.assertEqual(self.core.clear_history(), 1)
        self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
