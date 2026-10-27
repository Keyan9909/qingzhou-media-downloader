from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(dir=ROOT)
        cls.environment = {
            **os.environ,
            "QINGZHOU_DATA_DIR": cls.temp.name,
        }

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def run_cli(self, *arguments: str) -> tuple[int, dict]:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "media_cli.py"), *arguments],
            cwd=ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30,
        )
        return completed.returncode, json.loads(completed.stdout)

    def test_platforms_returns_stable_json(self) -> None:
        code, payload = self.run_cli("platforms")
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(
            {item["id"] for item in payload["platforms"]},
            {"youtube", "douyin", "xiaohongshu", "bilibili", "xiaoetong"},
        )

    def test_detect_rejects_unknown_host(self) -> None:
        code, payload = self.run_cli("detect", "https://example.com/video")
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["platform"]["id"], "unknown")

    def test_download_requires_explicit_rights_confirmation(self) -> None:
        code, payload = self.run_cli(
            "auto-download",
            "https://www.youtube.com/watch?v=abc123",
        )
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"]["code"], "RIGHTS_CONFIRMATION_REQUIRED")

    def test_platform_override_still_requires_complete_http_url(self) -> None:
        code, payload = self.run_cli(
            "auto-download",
            "video/v_demo?course_id=course_demo",
            "--platform",
            "xiaoetong",
            "--confirm-rights",
        )
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"]["code"], "INVALID_URL")

    def test_config_and_history_commands(self) -> None:
        output = str(Path(self.temp.name) / "downloads")
        code, payload = self.run_cli(
            "config-set",
            "--default-output-dir",
            output,
            "--quality",
            "720",
            "--accept-terms",
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["config"]["quality"], "720")
        code, payload = self.run_cli("history", "--limit", "5")
        self.assertEqual(code, 0)
        self.assertEqual(payload["items"], [])

    def test_cancel_download_is_safe_when_nothing_is_running(self) -> None:
        code, payload = self.run_cli("cancel-download", "--timeout", "0.1")
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["cancelled"])
        self.assertFalse(payload["browser_running"])


if __name__ == "__main__":
    unittest.main()
