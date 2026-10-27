"""Render the real Tk window once and save a visual QA screenshot."""

from __future__ import annotations

import os
import sys
import time
import ctypes
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
os.environ["QINGZHOU_DATA_DIR"] = str(ROOT / ".visual-test-data")
sys.path.insert(0, str(ROOT))

from PIL import ImageGrab

from app import DownloaderApp, enable_windows_dpi
from media_core import save_config


page = sys.argv[1] if len(sys.argv) > 1 else "download"
save_config({"accepted_terms": True})
enable_windows_dpi()
app = DownloaderApp()
app.geometry("1220x800+0+0")
app.attributes("-topmost", True)
app.update_idletasks()
app.update()
app.state("normal")
app.geometry("1220x800+0+0")
if page == "guide":
    app.show_page("guide")
else:
    app.progress_stage_var.set("正在下载 YouTube 视频…")
    app.progress_percent_var.set("42.6%")
    app.progress_detail_var.set("85.2 MB / 200.0 MB　·　5.0 MB/s　·　剩余约 23秒")
    app.progress.configure(mode="determinate", value=42.6)
time.sleep(0.4)
app.update()

class Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


hwnd = app.winfo_id()
while True:
    parent = ctypes.windll.user32.GetParent(hwnd)
    if not parent:
        break
    hwnd = parent
rect = Rect()
ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
output = ROOT / "artifacts" / ("gui-guide.png" if page == "guide" else "gui-home.png")
output.parent.mkdir(parents=True, exist_ok=True)
ImageGrab.grab(
    bbox=(rect.left, rect.top, rect.right, rect.bottom),
    all_screens=True,
).save(output)
app.destroy()
print(output)
