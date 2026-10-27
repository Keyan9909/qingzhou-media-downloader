from __future__ import annotations

import time
import unittest
from unittest.mock import patch

import media_cli


class XiaoetongPermissionTests(unittest.TestCase):
    def test_no_permission_page_fails_immediately_with_actionable_error(self) -> None:
        state = {
            "page_url": (
                "https://merchant.h5.xet.pomoho.com/p/course/noPermission/"
                "v_demo/3?course_id=course_demo"
            ),
            "timestamp": time.time(),
            "has_video": False,
        }
        with patch.object(media_cli, "current_page_state", return_value=state):
            with self.assertRaises(media_cli.CliError) as raised:
                media_cli.wait_for_media(None, 30)
        self.assertEqual(raised.exception.code, "XIAOETONG_NO_PERMISSION")
        self.assertIn("当前登录身份", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
