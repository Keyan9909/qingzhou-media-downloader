from __future__ import annotations

import importlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


class _FakeResponse:
    def __init__(self, url: str):
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        return False

    def geturl(self) -> str:
        return self.url


class DouyinCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        cls.old_data_dir = os.environ.get("QINGZHOU_DATA_DIR")
        os.environ["QINGZHOU_DATA_DIR"] = cls.temp.name
        import media_core
        import media_cli

        cls.core = importlib.reload(media_core)
        cls.cli = importlib.reload(media_cli)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.old_data_dir is None:
            os.environ.pop("QINGZHOU_DATA_DIR", None)
        else:
            os.environ["QINGZHOU_DATA_DIR"] = cls.old_data_dir
        cls.temp.cleanup()

    def test_resolves_official_short_link_to_canonical_video_url(self) -> None:
        redirected = (
            "https://www.iesdouyin.com/share/video/7646784767610860480/"
            "?from=web_code_link"
        )
        with patch.object(
            self.cli,
            "urlopen",
            return_value=_FakeResponse(redirected),
        ) as mocked:
            resolved = self.cli.resolve_douyin_share_url(
                "https://v.douyin.com/FHySPBJr4F8/"
            )
        self.assertEqual(
            resolved,
            "https://www.douyin.com/video/7646784767610860480",
        )
        self.assertEqual(mocked.call_count, 1)

    def test_does_not_make_network_request_for_full_video_url(self) -> None:
        full = "https://www.douyin.com/video/7646784767610860480"
        with patch.object(self.cli, "urlopen") as mocked:
            self.assertEqual(self.cli.resolve_douyin_share_url(full), full)
        mocked.assert_not_called()

    def test_exact_captured_metadata_counts_as_ready_media(self) -> None:
        full = "https://www.douyin.com/video/7646784767610860480"
        state = {
            "has_video": False,
            "page_url": full,
            "douyin_video_id": "7646784767610860480",
            "douyin_exact_media_ready": True,
            "timestamp": time.time(),
        }
        self.cli.VIDEO_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.cli.VIDEO_STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
        ready = self.cli.wait_for_media(full, 0.2)
        self.assertTrue(ready["douyin_exact_media_ready"])
        public = self.cli.public_page_state(ready)
        self.assertTrue(public["douyin_exact_media_ready"])


if __name__ == "__main__":
    unittest.main()
